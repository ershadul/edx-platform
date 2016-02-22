"""
Mixins to facilitate testing OAuth connections to Django-OAuth-Toolkit
"""

# pylint: disable=protected-access

from mock import patch

from oauth2_provider import models
from provider.oauth2.models import Client
from lms.djangoapps.oauth_dispatch import views


class DOTTestMixin(object):
    """
    Mixin to rewire existing tests to use Django-OAuth-Toolkit (DOT) backend

    Overwrites self.client, self.oauth2_client
    """

    client_id = 'dot_test_client_id'
    access_token = 'dot_test_access_token'

    def setUp(self):
        super(DOTTestMixin, self).setUp()
        patcher = patch.object(
            views.AccessTokenExchangeView,
            'select_backend',
            return_value=views.DOT_BACKEND
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create_client(self):
        """
        Create a DOT application object to use as the Oauth2 client
        """
        return models.Application.objects.create(
            client_id=self.client_id,
            user=self.user,
            client_type=models.Application.CLIENT_PUBLIC,
        )

    @property
    def _expected_keys(self):
        """
        DOT returns the same keys as DOP, plus a refresh token.
        """
        return super(DOTTestMixin, self)._expected_keys | {'refresh_token'}

    def _get_expected_scopes(self, expected_scopes):
        """
        Return the list of expected scopes in serialized (space-separated) form.
        """
        if not expected_scopes:
            expected_scopes = ['default']
        return ' '.join(expected_scopes)

    def _get_token_scope_names(self, token):
        """
        Return the scopes associated with the given token.
        """
        return list(token.scopes)

    def _get_access_token(self, token):
        """
        Return the (DOT) access token for the given token string.
        """
        return models.AccessToken.objects.get(token=token)

    def _get_token_client(self, token):
        return token.application
