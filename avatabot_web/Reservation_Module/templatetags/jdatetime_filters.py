"""
Django template filters for jdatetime (Persian calendar)
"""
from django import template
from Reservation_Module.jdatetime_utils import datetime_to_jdatetime, format_jdatetime

register = template.Library()


@register.filter
def jalali_date(dt, format_str='%Y/%m/%d %H:%M:%S'):
    """
    Convert datetime to Persian calendar (Jalali) string
    Usage: {{ order.order_date|jalali_date }}
    Usage with format: {{ order.order_date|jalali_date:"%Y/%m/%d" }}
    """
    if dt is None:
        return ''
    jdt = datetime_to_jdatetime(dt)
    if jdt is None:
        return ''
    return format_jdatetime(jdt, format_str)


@register.filter
def jalali_date_short(dt):
    """Short format: 1403/08/18"""
    return jalali_date(dt, '%Y/%m/%d')


@register.filter
def jalali_datetime(dt):
    """Full datetime format: Year:Month:Day - Hour:Minute:Second (e.g., 1404:08:21 - 01:45:24)"""
    if dt is None:
        return ''
    jdt = datetime_to_jdatetime(dt)
    if jdt is None:
        return ''
    # Explicitly construct in the correct order: Year:Month:Day - Hour:Minute:Second
    # Use Unicode Left-to-Right Mark (U+200E) to prevent RTL reversal
    date_part = jdt.strftime('%Y:%m:%d')
    time_part = jdt.strftime('%H:%M:%S')
    # Add LRM at the start to force LTR direction
    return f'\u200E{date_part} - {time_part}'

