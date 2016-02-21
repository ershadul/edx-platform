"""
Utilities for use in Mako markup.
"""

from django.utils.translation import ugettext
from django.utils.translation import ungettext
from django.utils.translation import ugettext_lazy
import markupsafe


# So that we can use escape() imported from here.
escape = markupsafe.escape  # pylint: disable=invalid-name


# allow ugettext to be imported with raw_ugettext name
# In Mako:
#     <% from openedx.core.djangolib.markup import raw_ugettext as _ %>
# Use this with JavaScript or in the case you want to delay escaping
raw_ugettext = ugettext  # pylint: disable=invalid-name


# allow ungettext to be imported with raw_ugettext name
# In Mako:
#     <% from openedx.core.djangolib.markup import raw_ungettext %>
# Use this with JavaScript or in the case you want to delay escaping
raw_ungettext = ungettext  # pylint: disable=invalid-name


# allow ugettext_lazy to be imported with raw_ugettext_lazy name
# In Mako:
#     <% from openedx.core.djangolib.markup import raw_ugettext_lazy as _ %>
# Use this with JavaScript or in the case you want to delay escaping
raw_ugettext_lazy = ugettext_lazy  # pylint: disable=invalid-name


def html_escaped_ugettext(text):
    """
    Translate a string, and escape it as plain text.

    Use like this in Mako::

        <% from openedx.core.djangolib.markup import raw_ugettext as _ %>
        <%page expression_filter="h"/>
        ...
        <p>${_("Hello, world!")}</p>

    Or with formatting::

        <% from openedx.core.djangolib.markup import HTML, html_escaped_ugettext as _h %>
        <%page expression_filter="h"/>
        ...
        ${_h("Write & send {start}email{end}").format(
            start=HTML("<a href='mailto:{}'>".format(user.email),
            end=HTML("</a>"),
           )}

    """
    return markupsafe.escape(django_ugettext(text))


def html_escaped_ungettext(text1, text2, num):
    """Translate a number-sensitive string, and escape it as plain text."""
    return markupsafe.escape(django_ungettext(text1, text2, num))


def HTML(html):                                 # pylint: disable=invalid-name
    """
    Mark a string as already HTML, so that it won't be escaped before output.

    Use this when formatting HTML into other strings::

        <% from openedx.core.djangolib.markup import html_escaped_ugettext as _h %>
        ${_("Write & send {start}email{end}").format(
            start=HTML("<a href='mailto:{}'>".format(user.email),
            end=HTML("</a>"),
           )}

    """
    return markupsafe.Markup(html)
