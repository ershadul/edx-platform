"""
Mixins to facilitate testing OAuth connections to Django-OAuth-Toolkit
"""

# pylint: disable=protected-access

from lms.djangoapps.oauth_dispatch import dop_adapter, dot_adapter, views


class _BaseAdapterMixin(object):
    """
    Common functionality for OAuth Adapter mixins
    """

    def setUp(self):
        super(_BaseAdapterMixin, self).setUp()
        patcher = self.oauth2_adapter.patch_view_backend(views.AccessTokenExchangeView)
        patcher.start()
        self.addCleanup(patcher.stop)


class DOTAdapterMixin(_BaseAdapterMixin):
    """
    Mixin to rewire existing tests to use django-oauth-toolkit (DOT) backend

    Overwrites self.client_id, self.access_token, self.oauth2_adapter
    """

    client_id = 'dot_test_client_id'
    access_token = 'dot_test_access_token'
    oauth2_adapter = dot_adapter.DOTAdapter()


class DOPAdapterMixin(_BaseAdapterMixin):
    client_id = 'dot_test_client_id'
    access_token = 'dot_test_access_token'
    oauth2_adapter = dop_adapter.DOTAdapter()
