"""Template filters for FFDB. Load them in a template with {% load repo_tags %}."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def formula(text):
    """C22H17NO8 -> C<sub>22</sub>H<sub>17</sub>NO<sub>8</sub>.

    Only a number straight after an element symbol is a count, so a charge such as
    Ca+2 is left as it is.
    """
    # Escape first, then mark safe: the <sub> tags are the only HTML that gets through.
    return mark_safe(re.sub(r"(?<=[A-Za-z])(\d+)", r"<sub>\1</sub>", escape(text)))
