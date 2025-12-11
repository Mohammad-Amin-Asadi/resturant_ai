"""
Django template tags for formatting Jalali (Persian) dates.
"""

from django import template
from shared.jdatetime_utils import datetime_to_jdatetime, format_jdatetime

register = template.Library()


@register.filter
def jalali_date(value):
    """Format datetime as Jalali date (YYYY/MM/DD)"""
    if not value:
        return ""
    jdt = datetime_to_jdatetime(value)
    return format_jdatetime(jdt, '%Y/%m/%d') if jdt else ""


@register.filter
def jalali_date_short(value):
    """Format datetime as short Jalali date (YY/MM/DD)"""
    if not value:
        return ""
    jdt = datetime_to_jdatetime(value)
    return format_jdatetime(jdt, '%y/%m/%d') if jdt else ""


@register.filter
def jalali_datetime(value):
    """Format datetime as Jalali datetime (YYYY/MM/DD HH:MM:SS)"""
    if not value:
        return ""
    jdt = datetime_to_jdatetime(value)
    return format_jdatetime(jdt) if jdt else ""
