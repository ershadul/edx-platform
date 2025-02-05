"""
test views
"""
import datetime
import json
import re
import pytz
import ddt
import urlparse
from mock import patch, MagicMock
from nose.plugins.attrib import attr

from capa.tests.response_xml_factory import StringResponseXMLFactory
from courseware.courses import get_course_by_id
from courseware.tests.factories import StudentModuleFactory
from courseware.tests.helpers import LoginEnrollmentTestCase
from courseware.tabs import get_course_tab_list
from instructor.access import list_with_level, allow_access

from django.conf import settings
from django.core.urlresolvers import reverse, resolve
from django.utils.timezone import UTC
from django.test.utils import override_settings
from django.test import RequestFactory
from edxmako.shortcuts import render_to_response
from request_cache.middleware import RequestCache
from opaque_keys.edx.keys import CourseKey
from student.roles import (
    CourseCcxCoachRole,
    CourseInstructorRole,
    CourseStaffRole,
)
from student.models import (
    CourseEnrollment,
    CourseEnrollmentAllowed,
)
from student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    UserFactory,
)

from xmodule.x_module import XModuleMixin
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
    TEST_DATA_SPLIT_MODULESTORE)
from xmodule.modulestore.tests.factories import (
    CourseFactory,
    ItemFactory,
)
from ccx_keys.locator import CCXLocator

from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import get_override_for_ccx, override_field_for_ccx
from lms.djangoapps.ccx.views import ccx_course
from lms.djangoapps.ccx.tests.factories import CcxFactory
from lms.djangoapps.ccx.tests.utils import (
    CcxTestCase,
    flatten,
)
from lms.djangoapps.ccx.utils import is_email
from lms.djangoapps.ccx.views import get_date


def intercept_renderer(path, context):
    """
    Intercept calls to `render_to_response` and attach the context dict to the
    response for examination in unit tests.
    """
    # I think Django already does this for you in their TestClient, except
    # we're bypassing that by using edxmako.  Probably edxmako should be
    # integrated better with Django's rendering and event system.
    response = render_to_response(path, context)
    response.mako_context = context
    response.mako_template = path
    return response


def ccx_dummy_request():
    """
    Returns dummy request object for CCX coach tab test
    """
    factory = RequestFactory()
    request = factory.get('ccx_coach_dashboard')
    request.user = MagicMock()

    return request


def setup_students_and_grades(context):
    """
    Create students and set their grades.
    :param context:  class reference
    """
    if context.course:
        context.student = student = UserFactory.create()
        CourseEnrollmentFactory.create(user=student, course_id=context.course.id)

        context.student2 = student2 = UserFactory.create()
        CourseEnrollmentFactory.create(user=student2, course_id=context.course.id)

        # create grades for self.student as if they'd submitted the ccx
        for chapter in context.course.get_children():
            for i, section in enumerate(chapter.get_children()):
                for j, problem in enumerate(section.get_children()):
                    # if not problem.visible_to_staff_only:
                    StudentModuleFactory.create(
                        grade=1 if i < j else 0,
                        max_grade=1,
                        student=context.student,
                        course_id=context.course.id,
                        module_state_key=problem.location
                    )

                    StudentModuleFactory.create(
                        grade=1 if i > j else 0,
                        max_grade=1,
                        student=context.student2,
                        course_id=context.course.id,
                        module_state_key=problem.location
                    )


@attr('shard_1')
@ddt.ddt
class TestCoachDashboard(CcxTestCase, LoginEnrollmentTestCase):
    """
    Tests for Custom Courses views.
    """

    @classmethod
    def setUpClass(cls):
        super(TestCoachDashboard, cls).setUpClass()

    def setUp(self):
        """
        Set up tests
        """
        super(TestCoachDashboard, self).setUp()
        # Login with the instructor account
        self.client.login(username=self.coach.username, password="test")

        # adding staff to master course.
        staff = UserFactory()
        allow_access(self.course, staff, 'staff')
        self.assertTrue(CourseStaffRole(self.course.id).has_user(staff))

        # adding instructor to master course.
        instructor = UserFactory()
        allow_access(self.course, instructor, 'instructor')
        self.assertTrue(CourseInstructorRole(self.course.id).has_user(instructor))

    def assert_elements_in_schedule(self, url, n_chapters=2, n_sequentials=4, n_verticals=8):
        """
        Helper function to count visible elements in the schedule
        """
        response = self.client.get(url)
        # the schedule contains chapters
        chapters = json.loads(response.mako_context['schedule'])  # pylint: disable=no-member
        sequentials = flatten([chapter.get('children', []) for chapter in chapters])
        verticals = flatten([sequential.get('children', []) for sequential in sequentials])
        # check that the numbers of nodes at different level are the expected ones
        self.assertEqual(n_chapters, len(chapters))
        self.assertEqual(n_sequentials, len(sequentials))
        self.assertEqual(n_verticals, len(verticals))
        # extract the locations of all the nodes
        all_elements = chapters + sequentials + verticals
        return [elem['location'] for elem in all_elements if 'location' in elem]

    def hide_node(self, node):
        """
        Helper function to set the node `visible_to_staff_only` property
        to True and save the change
        """
        node.visible_to_staff_only = True
        self.mstore.update_item(node, self.coach.id)

    def test_not_a_coach(self):
        """
        User is not a coach, should get Forbidden response.
        """
        ccx = self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_no_ccx_created(self):
        """
        No CCX is created, coach should see form to add a CCX.
        """
        self.make_coach()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': unicode(self.course.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(re.search(
            '<form action=".+create_ccx"',
            response.content))

    def test_create_ccx(self):
        """
        Create CCX. Follow redirect to coach dashboard, confirm we see
        the coach dashboard for the new CCX.
        """

        self.make_coach()
        url = reverse(
            'create_ccx',
            kwargs={'course_id': unicode(self.course.id)})

        response = self.client.post(url, {'name': 'New CCX'})
        self.assertEqual(response.status_code, 302)
        url = response.get('location')  # pylint: disable=no-member
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Get the ccx_key
        path = urlparse.urlparse(url).path
        resolver = resolve(path)
        ccx_key = resolver.kwargs['course_id']

        course_key = CourseKey.from_string(ccx_key)

        self.assertTrue(CourseEnrollment.is_enrolled(self.coach, course_key))
        self.assertTrue(re.search('id="ccx-schedule"', response.content))

        # check if the max amount of student that can be enrolled has been overridden
        ccx = CustomCourseForEdX.objects.get()
        course_enrollments = get_override_for_ccx(ccx, self.course, 'max_student_enrollments_allowed')
        self.assertEqual(course_enrollments, settings.CCX_MAX_STUDENTS_ALLOWED)

        # assert ccx creator has role=ccx_coach
        role = CourseCcxCoachRole(course_key)
        self.assertTrue(role.has_user(self.coach, refresh=True))

        # assert that staff and instructors of master course has staff and instructor roles on ccx
        list_staff_master_course = list_with_level(self.course, 'staff')
        list_instructor_master_course = list_with_level(self.course, 'instructor')

        with ccx_course(course_key) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx, 'staff')
            self.assertEqual(len(list_staff_master_course), len(list_staff_ccx_course))
            self.assertEqual(list_staff_master_course[0].email, list_staff_ccx_course[0].email)

            list_instructor_ccx_course = list_with_level(course_ccx, 'instructor')
            self.assertEqual(len(list_instructor_ccx_course), len(list_instructor_master_course))
            self.assertEqual(list_instructor_ccx_course[0].email, list_instructor_master_course[0].email)

    def test_get_date(self):
        """
        Assert that get_date returns valid date.
        """
        ccx = self.make_ccx()
        for section in self.course.get_children():
            self.assertEqual(get_date(ccx, section, 'start'), self.mooc_start)
            self.assertEqual(get_date(ccx, section, 'due'), None)
            for subsection in section.get_children():
                self.assertEqual(get_date(ccx, subsection, 'start'), self.mooc_start)
                self.assertEqual(get_date(ccx, subsection, 'due'), self.mooc_due)
                for unit in subsection.get_children():
                    self.assertEqual(get_date(ccx, unit, 'start', parent_node=subsection), self.mooc_start)
                    self.assertEqual(get_date(ccx, unit, 'due', parent_node=subsection), self.mooc_due)

    @SharedModuleStoreTestCase.modifies_courseware
    @patch('ccx.views.render_to_response', intercept_renderer)
    @patch('ccx.views.TODAY')
    def test_get_ccx_schedule(self, today):
        """
        Gets CCX schedule and checks number of blocks in it.
        Hides nodes at a different depth and checks that these nodes
        are not in the schedule.
        """
        today.return_value = datetime.datetime(2014, 11, 25, tzinfo=pytz.UTC)
        self.make_coach()
        ccx = self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={
                'course_id': CCXLocator.from_course_locator(
                    self.course.id, ccx.id)
            }
        )
        # all the elements are visible
        self.assert_elements_in_schedule(url)
        # hide a vertical
        vertical = self.verticals[0]
        self.hide_node(vertical)
        locations = self.assert_elements_in_schedule(url, n_verticals=7)
        self.assertNotIn(unicode(vertical.location), locations)
        # hide a sequential
        sequential = self.sequentials[0]
        self.hide_node(sequential)
        locations = self.assert_elements_in_schedule(url, n_sequentials=3, n_verticals=6)
        self.assertNotIn(unicode(sequential.location), locations)
        # hide a chapter
        chapter = self.chapters[0]
        self.hide_node(chapter)
        locations = self.assert_elements_in_schedule(url, n_chapters=1, n_sequentials=2, n_verticals=4)
        self.assertNotIn(unicode(chapter.location), locations)

    @patch('ccx.views.render_to_response', intercept_renderer)
    @patch('ccx.views.TODAY')
    def test_edit_schedule(self, today):
        """
        Get CCX schedule, modify it, save it.
        """
        today.return_value = datetime.datetime(2014, 11, 25, tzinfo=pytz.UTC)
        self.make_coach()
        ccx = self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})
        response = self.client.get(url)
        schedule = json.loads(response.mako_context['schedule'])  # pylint: disable=no-member

        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[0]['hidden'], False)
        # If a coach does not override dates, then dates will be imported from master course.
        self.assertEqual(
            schedule[0]['start'],
            self.chapters[0].start.strftime('%Y-%m-%d %H:%M')
        )
        self.assertEqual(
            schedule[0]['children'][0]['start'],
            self.sequentials[0].start.strftime('%Y-%m-%d %H:%M')
        )

        if self.sequentials[0].due:
            expected_due = self.sequentials[0].due.strftime('%Y-%m-%d %H:%M')
        else:
            expected_due = None
        self.assertEqual(schedule[0]['children'][0]['due'], expected_due)

        url = reverse(
            'save_ccx',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})

        def unhide(unit):
            """
            Recursively unhide a unit and all of its children in the CCX
            schedule.
            """
            unit['hidden'] = False
            for child in unit.get('children', ()):
                unhide(child)

        unhide(schedule[0])
        schedule[0]['start'] = u'2014-11-20 00:00'
        schedule[0]['children'][0]['due'] = u'2014-12-25 00:00'  # what a jerk!
        schedule[0]['children'][0]['children'][0]['start'] = u'2014-12-20 00:00'
        schedule[0]['children'][0]['children'][0]['due'] = u'2014-12-25 00:00'

        response = self.client.post(
            url, json.dumps(schedule), content_type='application/json'
        )

        schedule = json.loads(response.content)['schedule']
        self.assertEqual(schedule[0]['hidden'], False)
        self.assertEqual(schedule[0]['start'], u'2014-11-20 00:00')
        self.assertEqual(
            schedule[0]['children'][0]['due'], u'2014-12-25 00:00'
        )

        self.assertEqual(
            schedule[0]['children'][0]['children'][0]['due'], u'2014-12-25 00:00'
        )
        self.assertEqual(
            schedule[0]['children'][0]['children'][0]['start'], u'2014-12-20 00:00'
        )

        # Make sure start date set on course, follows start date of earliest
        # scheduled chapter
        ccx = CustomCourseForEdX.objects.get()
        course_start = get_override_for_ccx(ccx, self.course, 'start')
        self.assertEqual(str(course_start)[:-9], self.chapters[0].start.strftime('%Y-%m-%d %H:%M'))

        # Make sure grading policy adjusted
        policy = get_override_for_ccx(ccx, self.course, 'grading_policy',
                                      self.course.grading_policy)
        self.assertEqual(policy['GRADER'][0]['type'], 'Homework')
        self.assertEqual(policy['GRADER'][0]['min_count'], 8)
        self.assertEqual(policy['GRADER'][1]['type'], 'Lab')
        self.assertEqual(policy['GRADER'][1]['min_count'], 0)
        self.assertEqual(policy['GRADER'][2]['type'], 'Midterm Exam')
        self.assertEqual(policy['GRADER'][2]['min_count'], 0)
        self.assertEqual(policy['GRADER'][3]['type'], 'Final Exam')
        self.assertEqual(policy['GRADER'][3]['min_count'], 0)

    @patch('ccx.views.render_to_response', intercept_renderer)
    def test_save_without_min_count(self):
        """
        POST grading policy without min_count field.
        """
        self.make_coach()
        ccx = self.make_ccx()

        course_id = CCXLocator.from_course_locator(self.course.id, ccx.id)
        save_policy_url = reverse(
            'ccx_set_grading_policy', kwargs={'course_id': course_id})

        # This policy doesn't include a min_count field
        policy = {
            "GRADE_CUTOFFS": {
                "Pass": 0.5
            },
            "GRADER": [
                {
                    "weight": 0.15,
                    "type": "Homework",
                    "drop_count": 2,
                    "short_label": "HW"
                }
            ]
        }

        response = self.client.post(
            save_policy_url, {"policy": json.dumps(policy)}
        )
        self.assertEqual(response.status_code, 302)

        ccx = CustomCourseForEdX.objects.get()

        # Make sure grading policy adjusted
        policy = get_override_for_ccx(
            ccx, self.course, 'grading_policy', self.course.grading_policy
        )
        self.assertEqual(len(policy['GRADER']), 1)
        self.assertEqual(policy['GRADER'][0]['type'], 'Homework')
        self.assertNotIn('min_count', policy['GRADER'][0])

        save_ccx_url = reverse('save_ccx', kwargs={'course_id': course_id})
        coach_dashboard_url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': course_id}
        )
        response = self.client.get(coach_dashboard_url)
        schedule = json.loads(response.mako_context['schedule'])  # pylint: disable=no-member
        response = self.client.post(
            save_ccx_url, json.dumps(schedule), content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

    @ddt.data(
        ('ccx_invite', True, 1, 'student-ids', ('enrollment-button', 'Enroll')),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Enroll')),
        ('ccx_manage_student', True, 1, 'student-id', ('student-action', 'add')),
        ('ccx_manage_student', False, 0, 'student-id', ('student-action', 'add')),
    )
    @ddt.unpack
    def test_enroll_member_student(self, view_name, send_email, outbox_count, student_form_input_name, button_tuple):
        """
        Tests the enrollment of  a list of students who are members
        of the class.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        enrollment = CourseEnrollmentFactory(course_id=self.course.id)
        student = enrollment.user
        outbox = self.get_outbox()
        self.assertEqual(outbox, [])

        url = reverse(
            view_name,
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: u','.join([student.email, ]),  # pylint: disable=no-member
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # we were redirected to our current location
        self.assertEqual(len(response.redirect_chain), 1)
        self.assertIn(302, response.redirect_chain[0])
        self.assertEqual(len(outbox), outbox_count)
        if send_email:
            self.assertIn(student.email, outbox[0].recipients())  # pylint: disable=no-member
        # a CcxMembership exists for this student
        self.assertTrue(
            CourseEnrollment.objects.filter(course_id=self.course.id, user=student).exists()
        )

    def test_ccx_invite_enroll_up_to_limit(self):
        """
        Enrolls a list of students up to the enrollment limit.

        This test is specific to one of the enrollment views: the reason is because
        the view used in this test can perform bulk enrollments.
        """
        self.make_coach()
        # create ccx and limit the maximum amount of students that can be enrolled to 2
        ccx = self.make_ccx(max_students_allowed=2)
        ccx_course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        # create some users
        students = [
            UserFactory.create(is_staff=False) for _ in range(3)
        ]
        url = reverse(
            'ccx_invite',
            kwargs={'course_id': ccx_course_key}
        )
        data = {
            'enrollment-button': 'Enroll',
            'student-ids': u','.join([student.email for student in students]),
        }
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # a CcxMembership exists for the first two students but not the third
        self.assertTrue(
            CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[0]).exists()
        )
        self.assertTrue(
            CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[1]).exists()
        )
        self.assertFalse(
            CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[2]).exists()
        )

    def test_manage_student_enrollment_limit(self):
        """
        Enroll students up to the enrollment limit.

        This test is specific to one of the enrollment views: the reason is because
        the view used in this test cannot perform bulk enrollments.
        """
        students_limit = 1
        self.make_coach()
        ccx = self.make_ccx(max_students_allowed=students_limit)
        ccx_course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        students = [
            UserFactory.create(is_staff=False) for _ in range(2)
        ]
        url = reverse(
            'ccx_manage_student',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)}
        )
        # enroll the first student
        data = {
            'student-action': 'add',
            'student-id': u','.join([students[0].email, ]),
        }
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # a CcxMembership exists for this student
        self.assertTrue(
            CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[0]).exists()
        )
        # try to enroll the second student without success
        # enroll the first student
        data = {
            'student-action': 'add',
            'student-id': u','.join([students[1].email, ]),
        }
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # a CcxMembership does not exist for this student
        self.assertFalse(
            CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[1]).exists()
        )
        error_message = 'The course is full: the limit is {students_limit}'.format(
            students_limit=students_limit
        )
        self.assertContains(response, error_message, status_code=200)

    @ddt.data(
        ('ccx_invite', True, 1, 'student-ids', ('enrollment-button', 'Unenroll')),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Unenroll')),
        ('ccx_manage_student', True, 1, 'student-id', ('student-action', 'revoke')),
        ('ccx_manage_student', False, 0, 'student-id', ('student-action', 'revoke')),
    )
    @ddt.unpack
    def test_unenroll_member_student(self, view_name, send_email, outbox_count, student_form_input_name, button_tuple):
        """
        Tests the unenrollment of a list of students who are members of the class.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        enrollment = CourseEnrollmentFactory(course_id=course_key)
        student = enrollment.user
        outbox = self.get_outbox()
        self.assertEqual(outbox, [])

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: u','.join([student.email, ]),  # pylint: disable=no-member
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # we were redirected to our current location
        self.assertEqual(len(response.redirect_chain), 1)
        self.assertIn(302, response.redirect_chain[0])
        self.assertEqual(len(outbox), outbox_count)
        if send_email:
            self.assertIn(student.email, outbox[0].recipients())  # pylint: disable=no-member
        # a CcxMembership does not exists for this student
        self.assertFalse(
            CourseEnrollment.objects.filter(course_id=self.course.id, user=student).exists()
        )

    @ddt.data(
        ('ccx_invite', True, 1, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody@nowhere.com'),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody@nowhere.com'),
        ('ccx_invite', True, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody'),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody'),
        ('ccx_manage_student', True, 0, 'student-id', ('student-action', 'add'), 'dummy_student_id'),
        ('ccx_manage_student', False, 0, 'student-id', ('student-action', 'add'), 'dummy_student_id'),
        ('ccx_manage_student', True, 1, 'student-id', ('student-action', 'add'), 'xyz@gmail.com'),
        ('ccx_manage_student', False, 0, 'student-id', ('student-action', 'add'), 'xyz@gmail.com'),
    )
    @ddt.unpack
    def test_enroll_non_user_student(
            self, view_name, send_email, outbox_count, student_form_input_name, button_tuple, identifier):
        """
        Tests the enrollment of a list of students who are not users yet.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        outbox = self.get_outbox()
        self.assertEqual(outbox, [])

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: u','.join([identifier, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # we were redirected to our current location
        self.assertEqual(len(response.redirect_chain), 1)
        self.assertIn(302, response.redirect_chain[0])
        self.assertEqual(len(outbox), outbox_count)

        # some error messages are returned for one of the views only
        if view_name == 'ccx_manage_student' and not is_email(identifier):
            error_message = 'Could not find a user with name or email "{identifier}" '.format(
                identifier=identifier
            )
            self.assertContains(response, error_message, status_code=200)

        if is_email(identifier):
            if send_email:
                self.assertIn(identifier, outbox[0].recipients())
            self.assertTrue(
                CourseEnrollmentAllowed.objects.filter(course_id=course_key, email=identifier).exists()
            )
        else:
            self.assertFalse(
                CourseEnrollmentAllowed.objects.filter(course_id=course_key, email=identifier).exists()
            )

    @ddt.data(
        ('ccx_invite', True, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody@nowhere.com'),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody@nowhere.com'),
        ('ccx_invite', True, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody'),
        ('ccx_invite', False, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody'),
    )
    @ddt.unpack
    def test_unenroll_non_user_student(
            self, view_name, send_email, outbox_count, student_form_input_name, button_tuple, identifier):
        """
        Unenroll a list of students who are not users yet
        """
        self.make_coach()
        course = CourseFactory.create()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(course.id, ccx.id)
        outbox = self.get_outbox()
        CourseEnrollmentAllowed(course_id=course_key, email=identifier)
        self.assertEqual(outbox, [])

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: u','.join([identifier, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        self.assertEqual(response.status_code, 200)
        # we were redirected to our current location
        self.assertEqual(len(response.redirect_chain), 1)
        self.assertIn(302, response.redirect_chain[0])
        self.assertEqual(len(outbox), outbox_count)
        self.assertFalse(
            CourseEnrollmentAllowed.objects.filter(
                course_id=course_key, email=identifier
            ).exists()
        )


GET_CHILDREN = XModuleMixin.get_children


def patched_get_children(self, usage_key_filter=None):
    """Emulate system tools that mask courseware not visible to students"""
    def iter_children():
        """skip children not visible to students"""
        for child in GET_CHILDREN(self, usage_key_filter=usage_key_filter):
            child._field_data_cache = {}  # pylint: disable=protected-access
            if not child.visible_to_staff_only:
                yield child
    return list(iter_children())


@attr('shard_1')
@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'ccx.overrides.CustomCoursesForEdxOverrideProvider',))
@patch('xmodule.x_module.XModuleMixin.get_children', patched_get_children, spec=True)
class TestCCXGrades(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests for Custom Courses views.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Set up tests
        """
        super(TestCCXGrades, self).setUp()

        self._course = CourseFactory.create(enable_ccx=True)

        # Create a course outline
        self.start = datetime.datetime(
            2010, 5, 12, 2, 42, tzinfo=pytz.UTC
        )
        chapter = ItemFactory.create(
            start=self.start, parent=self._course, category='sequential'
        )
        self.sections = [
            ItemFactory.create(
                parent=chapter,
                category="sequential",
                metadata={'graded': True, 'format': 'Homework'})
            for _ in xrange(4)
        ]
        # making problems available at class level for possible future use in tests
        self.problems = [
            [
                ItemFactory.create(
                    parent=section,
                    category="problem",
                    data=StringResponseXMLFactory().build_xml(answer='foo'),
                    metadata={'rerandomize': 'always'}
                ) for _ in xrange(4)
            ] for section in self.sections
        ]

        # Create instructor account
        self.coach = coach = AdminFactory.create()
        self.client.login(username=coach.username, password="test")

        # Create CCX
        role = CourseCcxCoachRole(self._course.id)
        role.add_users(coach)
        ccx = CcxFactory(course_id=self._course.id, coach=self.coach)

        # override course grading policy and make last section invisible to students
        override_field_for_ccx(ccx, self._course, 'grading_policy', {
            'GRADER': [
                {'drop_count': 0,
                 'min_count': 2,
                 'short_label': 'HW',
                 'type': 'Homework',
                 'weight': 1}
            ],
            'GRADE_CUTOFFS': {'Pass': 0.75},
        })
        override_field_for_ccx(
            ccx, self.sections[-1], 'visible_to_staff_only', True
        )

        # create a ccx locator and retrieve the course structure using that key
        # which emulates how a student would get access.
        self.ccx_key = CCXLocator.from_course_locator(self._course.id, ccx.id)
        self.course = get_course_by_id(self.ccx_key, depth=None)
        setup_students_and_grades(self)
        self.client.login(username=coach.username, password="test")
        self.addCleanup(RequestCache.clear_request_cache)

    @patch('ccx.views.render_to_response', intercept_renderer)
    @patch('instructor.views.gradebook_api.MAX_STUDENTS_PER_PAGE_GRADE_BOOK', 1)
    def test_gradebook(self):
        self.course.enable_ccx = True
        RequestCache.clear_request_cache()

        url = reverse(
            'ccx_gradebook',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Max number of student per page is one.  Patched setting MAX_STUDENTS_PER_PAGE_GRADE_BOOK = 1
        self.assertEqual(len(response.mako_context['students']), 1)  # pylint: disable=no-member
        student_info = response.mako_context['students'][0]  # pylint: disable=no-member
        self.assertEqual(student_info['grade_summary']['percent'], 0.5)
        self.assertEqual(
            student_info['grade_summary']['grade_breakdown'][0]['percent'],
            0.5)
        self.assertEqual(
            len(student_info['grade_summary']['section_breakdown']), 4)

    def test_grades_csv(self):
        self.course.enable_ccx = True
        RequestCache.clear_request_cache()

        url = reverse(
            'ccx_grades_csv',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Are the grades downloaded as an attachment?
        self.assertEqual(
            response['content-disposition'],
            'attachment'
        )
        rows = response.content.strip().split('\r')
        headers = rows[0]

        records = dict()
        for i in range(1, len(rows)):
            data = dict(zip(headers.strip().split(','), rows[i].strip().split(',')))
            records[data['username']] = data

        student_data = records[self.student.username]  # pylint: disable=no-member

        self.assertNotIn('HW 04', student_data)
        self.assertEqual(student_data['HW 01'], '0.75')
        self.assertEqual(student_data['HW 02'], '0.5')
        self.assertEqual(student_data['HW 03'], '0.25')
        self.assertEqual(student_data['HW Avg'], '0.5')

    @patch('courseware.views.render_to_response', intercept_renderer)
    def test_student_progress(self):
        self.course.enable_ccx = True
        patch_context = patch('courseware.views.get_course_with_access')
        get_course = patch_context.start()
        get_course.return_value = self.course
        self.addCleanup(patch_context.stop)

        self.client.login(username=self.student.username, password="test")
        url = reverse(
            'progress',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        grades = response.mako_context['grade_summary']  # pylint: disable=no-member
        self.assertEqual(grades['percent'], 0.5)
        self.assertEqual(grades['grade_breakdown'][0]['percent'], 0.5)
        self.assertEqual(len(grades['section_breakdown']), 4)


@ddt.ddt
class CCXCoachTabTestCase(SharedModuleStoreTestCase):
    """
    Test case for CCX coach tab.
    """
    @classmethod
    def setUpClass(cls):
        super(CCXCoachTabTestCase, cls).setUpClass()
        cls.ccx_enabled_course = CourseFactory.create(enable_ccx=True)
        cls.ccx_disabled_course = CourseFactory.create(enable_ccx=False)

    def setUp(self):
        super(CCXCoachTabTestCase, self).setUp()
        self.user = UserFactory.create()
        for course in [self.ccx_enabled_course, self.ccx_disabled_course]:
            CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
            role = CourseCcxCoachRole(course.id)
            role.add_users(self.user)

    def check_ccx_tab(self, course):
        """Helper function for verifying the ccx tab."""
        request = RequestFactory().request()
        request.user = self.user
        all_tabs = get_course_tab_list(request, course)
        return any(tab.type == 'ccx_coach' for tab in all_tabs)

    @ddt.data(
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
        (True, None, False)
    )
    @ddt.unpack
    def test_coach_tab_for_ccx_advance_settings(self, ccx_feature_flag, enable_ccx, expected_result):
        """
        Test ccx coach tab state (visible or hidden) depending on the value of enable_ccx flag, ccx feature flag.
        """
        with self.settings(FEATURES={'CUSTOM_COURSES_EDX': ccx_feature_flag}):
            course = self.ccx_enabled_course if enable_ccx else self.ccx_disabled_course
            self.assertEquals(
                expected_result,
                self.check_ccx_tab(course)
            )


class TestStudentDashboardWithCCX(ModuleStoreTestCase):
    """
    Test to ensure that the student dashboard works for users enrolled in CCX
    courses.
    """

    def setUp(self):
        """
        Set up courses and enrollments.
        """
        super(TestStudentDashboardWithCCX, self).setUp()

        # Create a Draft Mongo and a Split Mongo course and enroll a student user in them.
        self.student_password = "foobar"
        self.student = UserFactory.create(username="test", password=self.student_password, is_staff=False)
        self.draft_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.mongo)
        self.split_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        CourseEnrollment.enroll(self.student, self.draft_course.id)
        CourseEnrollment.enroll(self.student, self.split_course.id)

        # Create a CCX coach.
        self.coach = AdminFactory.create()
        role = CourseCcxCoachRole(self.split_course.id)
        role.add_users(self.coach)

        # Create a CCX course and enroll the user in it.
        self.ccx = CcxFactory(course_id=self.split_course.id, coach=self.coach)
        last_week = datetime.datetime.now(UTC()) - datetime.timedelta(days=7)
        override_field_for_ccx(self.ccx, self.split_course, 'start', last_week)  # Required by self.ccx.has_started().
        course_key = CCXLocator.from_course_locator(self.split_course.id, self.ccx.id)
        CourseEnrollment.enroll(self.student, course_key)

    def test_load_student_dashboard(self):
        self.client.login(username=self.student.username, password=self.student_password)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(re.search('Test CCX', response.content))
