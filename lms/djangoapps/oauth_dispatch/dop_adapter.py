"""
Adapter to isolate django-oauth-toolkit dependencies
"""

from mock import patch

from provider.oauth2 import models
from provider import constants, scope

class DOPAdapter(object):
    """
    Standard interface for working with django-oauth-toolkit
    """

    backend = object()

    def patch_view_backend(self, view):
        return patch.object(view, 'select_backend', return_value=self.backend)

    def create_public_client(self, user, client_id=None):
        return models.Client.objects.create(
            user=user,
            client_id=client_id,
            redirect_uri='/',
            client_type=constants.PUBLIC
        )

    def create_token(self, request, user, scopes, client):
        pass

    def get_client_for_token(self, token):
        return token.client

    def get_access_token(self, token_string):
        return models.AccessToken.objects.get(access_token=token_string)

    def get_token_response_keys(self):
        return {'access_token', 'token_type', 'expires_in', 'scope'}

    def normalize_scopes(self, scopes):
        return ' '.join(scopes)

    def get_token_scope_names(self, token):
        return scope.to_names(token.scope)
