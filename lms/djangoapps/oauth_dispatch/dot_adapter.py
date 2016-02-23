"""
Adapter to isolate django-oauth-toolkit dependencies
"""

from mock import patch

from oauth2_provider import models

class DOTAdapter(object):
    """
    Standard interface for working with django-oauth-toolkit
    """

    backend = object()

    def patch_view_backend(self, view):
        return patch.object(view, 'select_backend', return_value=self.backend)

    def create_public_client(self, user, client_id=None):
        return models.Application.objects.create(
            user=user,
            client_id=client_id,
            redirect_uris='/',
            authorization_grant_type=models.Application.GRANT_PASSWORD,
        )

    def create_token(self, request, user, scopes, client):
        pass

    def get_client_for_token(self, token):
        return token.application

    def get_access_token(self, token_string):
        return models.AccessToken.objects.get(token=token_string)

    def get_token_response_keys(self):
        return {'access_token', 'token_type', 'expires_in', 'scope', 'refresh_token'}

    def normalize_scopes(self, scopes):
        if not scopes:
            scopes = ['default']
        return ' '.join(scopes)

    def get_token_scope_names(self, token):
        return list(token.scopes)
