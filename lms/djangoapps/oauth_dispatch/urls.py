"""
OAuth2 wrapper urls
"""

from django.conf.urls import patterns, url
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt

from . import views


urlpatterns = patterns(
    '',
    #url(r'^authorize/?$', login_required(views.AuthorizationView.as_view()), name='capture'),
    #url(r'^redirect/?$', login_required(views.Redirect.as_view()), name='redirect'),
    url(r'^access_token/?$', csrf_exempt(views.AccessTokenView.as_view()), name='access_token'),
    url(
        r'^exchange_access_token/(?P<backend>[^/]+)/$',
        views.AccessTokenExchangeView.as_view(),
        name='exchange_access_token'
    )
)

