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
    """Full datetime format: 1403/08/18 14:30:00"""
    return jalali_date(dt, '%Y/%m/%d %H:%M:%S')

