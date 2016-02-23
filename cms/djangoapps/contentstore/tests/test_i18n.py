from unittest import skip

from django.contrib.auth.models import User

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from contentstore.tests.utils import AjaxEnabledTestClient
from xmodule.modulestore.django import ModuleI18nService
from django.utils import translation
import mock


class TestModuleI18nService(ModuleStoreTestCase):
    """ Test ModuleI18nService """

    xblock_name = 'dummy_block'

    def setUp(self):
        """ Setting up tests """
        super(TestModuleI18nService, self).setUp()
        self.xblock_mock = mock.Mock()
        self.xblock_mock.unmixed_class.__name__ = self.xblock_name
        self.i18n_service = ModuleI18nService(self.xblock_mock)

    def test_service_translation_works(self):
        """
        Test service use the django translation
        """
        language = 'dummy language'

        def wrap_with_test(func):
            """
            A decorator function that just adds 'TEST ' to the front of all strings
            """
            def new_func(*args, **kwargs):
                """ custom function """
                output = func(*args, **kwargs)
                return "TEST " + output
            return new_func

        old_lang = translation.get_language()

        # Activate french, so that if the fr files haven't been loaded, they will be loaded now.
        translation.activate("fr")
        french_translation = translation.trans_real._active.value  # pylint: disable=protected-access

        # wrap the ugettext and ungettext functions so that 'TEST ' will prefix each translation
        french_translation.ugettext = wrap_with_test(french_translation.ugettext)
        french_translation.ungettext = wrap_with_test(french_translation.ungettext)
        self.assertEqual(self.i18n_service.ugettext(language), 'TEST dummy language')

        # Turn back on our old translations
        translation.activate(old_lang)
        del old_lang
        self.assertEqual(self.i18n_service.ugettext(language), 'dummy language')

    @mock.patch('django.utils.translation.ugettext', mock.Mock(return_value='TEST LANGUAGE'))
    def test_service_not_translate_text(self):
        """
        Test: Not translate the text if no block is associated or string is empty
        """
        self.assertEqual(ModuleI18nService(block=None).ugettext('dummy language'), 'dummy language')
        self.assertEqual(self.i18n_service.ugettext(''), '')


class InternationalizationTest(ModuleStoreTestCase):
    """
    Tests to validate Internationalization.
    """

    def setUp(self):
        """
        These tests need a user in the DB so that the django Test Client
        can log them in.
        They inherit from the ModuleStoreTestCase class so that the mongodb collection
        will be cleared out before each test case execution and deleted
        afterwards.
        """
        super(InternationalizationTest, self).setUp(create_user=False)

        self.uname = 'testuser'
        self.email = 'test+courses@edx.org'
        self.password = 'foo'

        # Create the use so we can log them in.
        self.user = User.objects.create_user(self.uname, self.email, self.password)

        # Note that we do not actually need to do anything
        # for registration if we directly mark them active.
        self.user.is_active = True
        # Staff has access to view all courses
        self.user.is_staff = True
        self.user.save()

        self.course_data = {
            'org': 'MITx',
            'number': '999',
            'display_name': 'Robot Super Course',
        }

    def test_course_plain_english(self):
        """Test viewing the index page with no courses"""
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.uname, password=self.password)

        resp = self.client.get_html('/home/')
        self.assertContains(resp,
                            '<h1 class="page-header">Studio Home</h1>',
                            status_code=200,
                            html=True)

    def test_course_explicit_english(self):
        """Test viewing the index page with no courses"""
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.uname, password=self.password)

        resp = self.client.get_html(
            '/home/',
            {},
            HTTP_ACCEPT_LANGUAGE='en',
        )

        self.assertContains(resp,
                            '<h1 class="page-header">Studio Home</h1>',
                            status_code=200,
                            html=True)

    # ****
    # NOTE:
    # ****
    #
    # This test will break when we replace this fake 'test' language
    # with actual Esperanto. This test will need to be updated with
    # actual Esperanto at that time.
    # Test temporarily disable since it depends on creation of dummy strings
    @skip
    def test_course_with_accents(self):
        """Test viewing the index page with no courses"""
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.uname, password=self.password)

        resp = self.client.get_html(
            '/home/',
            {},
            HTTP_ACCEPT_LANGUAGE='eo'
        )

        TEST_STRING = (
            u'<h1 class="title-1">'
            u'My \xc7\xf6\xfcrs\xe9s L#'
            u'</h1>'
        )

        self.assertContains(resp,
                            TEST_STRING,
                            status_code=200,
                            html=True)
