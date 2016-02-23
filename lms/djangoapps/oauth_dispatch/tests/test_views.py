"""
Tests for Blocks Views
"""

import json

import ddt
from django.test import TestCase, RequestFactory
from mock import patch
from oauth2_provider.models import Application
from provider import constants
from provider.oauth2.models import Client
from student.tests.factories import UserFactory

from ..dop_adapter import DOPAdapter
from ..dot_adapter import DOTAdapter
from .. import views


@ddt.ddt
class TestAccessTokenView(TestCase):
    """
    Test class for AccessTokenView
    """

    dop_adapter = DOPAdapter()
    dot_adapter = DOTAdapter()

    def setUp(self):
        super(TestAccessTokenView, self).setUp()
        self.user = UserFactory()
        self.dot_app = self.dot_adapter.create_public_client(user=self.user, client_id='dot-app-client-id')
        self.dop_client = self.dop_adapter.create_public_client(user=self.user, client_id='dop-app-client-id')

    def test_dot_application_gets_client_id(self):
        self.assertGreater(len(self.dot_app.client_id), 0)

    def test_dop_client_gets_client_id(self):
        self.assertGreater(len(self.dop_client.client_id), 0)

    @ddt.data(
        (DOTAdapter(), 'dot_app'),
        (DOPAdapter(), 'dop_client'),
    )
    @ddt.unpack
    def test_access_token_fields(self, adapter, client_attr):
        client = getattr(self, client_attr)
        token_view = views.AccessTokenView.as_view()
        reqfac = RequestFactory()
        request = reqfac.post('/', {
            'client_id': client.client_id,
            'grant_type': 'password',
            'username': self.user.username,
            'password': 'test',
        })
        with patch.object(views.AccessTokenView, 'select_backend', return_value=adapter.backend):
            response = token_view(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('access_token', data)
        self.assertIn('expires_in', data)
        self.assertIn('scope', data)
        self.assertIn('token_type', data)

    def test_dot_access_token_provides_refresh_token(self):
        token_view = views.AccessTokenView.as_view()
        reqfac = RequestFactory()
        request = reqfac.post('/', {
            'client_id': self.dot_app.client_id,
            'grant_type': 'password',
            'username': self.user.username,
            'password': 'test',
        })
        with DOTAdapter().patch_view_backend(views.AccessTokenView):
            response = token_view(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('refresh_token', data)

    def test_dop_access_token(self):
        token_view = views.AccessTokenView.as_view()
        reqfac = RequestFactory()
        request = reqfac.post('/', {
            'client_id': self.dop_client.client_id,
            'grant_type': 'password',
            'username': self.user.username,
            'password': 'test',
        })
        with self.dop_adapter.patch_view_backend(views.AccessTokenView):
            response = token_view(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertNotIn('refresh_token', data)


@ddt.ddt
class TestAuthorizationView(TestCase):
    """
    Test class for AccessTokenView
    """

    dop_adapter = DOPAdapter()
    dot_adapter = DOTAdapter()
    def setUp(self):
        super(TestAuthorizationView, self).setUp()
        self.user = UserFactory()
        self.dot_app = Application.objects.create(
            name='Test Application',
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            user=self.user,  # pylint: disable=no-member
            redirect_uris='/',
        )
        self.dop_client = Client.objects.create(
            user=self.user,  # pylint: disable=no-member
            redirect_uri='http://example.edx/redirect',
            client_type=constants.CONFIDENTIAL,
        )

    @ddt.data(
        (dop_adapter, 'dop_client'),
        (dot_adapter, 'dot_app'),
    )
    @ddt.unpack
    def test_authorization_view(self, adapter, client_attr):
        client = getattr(self, client_attr)
        self.client.login(username=self.user.username, password='test')
        with adapter.patch_view_backend(views.AuthorizationView):
            response = self.client.post('/oauth2/authorize/', {
                'client_id': client.client_id,
                'response_type': 'code',
                'state': 'random_state_string',
                'redirect_uri': 'http://example.edx/redirect',
            }, follow=True)
        self.assertEqual(response.status_code, 200)

        # check form is in context and form params are valid
        context = response.context  # pylint: disable=no-member
        self.assertIn('form', context)

    def test_dot_authorization_view(self):
        self.client.login(username=self.user.username, password='test')
        with self.dot_adapter.patch_view_backend(views.AuthorizationView):
            response = self.client.post('/oauth2/authorize/', {
                'client_id': self.dot_app.client_id,
                'response_type': 'code',
                'state': 'random_state_string',
                'redirect_uri': 'http://example.edx/redirect',
            }, follow=True)
        self.assertEqual(response.status_code, 200)

        # check form is in context and form params are valid
        context = response.context  # pylint: disable=no-member

        self.assertIn('form', context)
        form = context['form']
        self.assertEqual(form['redirect_uri'].value(), 'http://example.edx/redirect')
        self.assertEqual(form['state'].value(), 'random_state_string')
        self.assertEqual(form['client_id'].value(), self.dot_app.client_id)
        self.assertFalse(form['allow'].value())

    def test_dop_authorization_view(self):
        self.client.login(username=self.user.username, password='test')
        with self.dop_adapter.patch_view_backend(views.AuthorizationView):
            response = self.client.post('/oauth2/authorize/', {
                'client_id': self.dop_client.client_id,
                'response_type': 'code',
                'state': 'random_state_string',
                'redirect_uri': 'http://example.edx/redirect',
            }, follow=True)
        self.assertEqual(response.status_code, 200)

        # check form is in context and form params are valid
        context = response.context  # pylint: disable=no-member

        self.assertIn('form', context)
        form = context['form']
        self.assertIsNone(form['authorize'].value())

        self.assertIn('oauth_data', context)
        oauth_data = context['oauth_data']
        print oauth_data
        self.assertEqual(oauth_data['redirect_uri'], 'http://example.edx/redirect')
        self.assertEqual(oauth_data['state'], 'random_state_string')
        #self.assertEqual(oauth_data['client_id'], self.dot_app.client_id)
