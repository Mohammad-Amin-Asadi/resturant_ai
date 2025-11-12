"""
Utility functions for jdatetime (Persian calendar) conversion
All datetime operations use Iran/Tehran timezone
"""
import jdatetime
from django.utils import timezone
from datetime import datetime
try:
    import pytz
    TEHRAN_TZ = pytz.timezone('Asia/Tehran')
except ImportError:
    # Fallback: use zoneinfo if pytz not available (Python 3.9+)
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo('Asia/Tehran')


def get_tehran_now():
    """Get current datetime in Tehran timezone"""
    return timezone.now().astimezone(TEHRAN_TZ)


def datetime_to_jdatetime(dt):
    """
    Convert datetime to jdatetime (Persian calendar)
    If dt is None, returns None
    If dt is timezone-naive, assumes it's in Tehran timezone
    """
    if dt is None:
        return None
    
    # Ensure timezone-aware
    if timezone.is_naive(dt):
        # Make timezone-aware using Django's timezone utilities
        dt = timezone.make_aware(dt)
    
    # Convert to Tehran timezone
    dt_tehran = dt.astimezone(TEHRAN_TZ)
    
    # Convert to jdatetime
    return jdatetime.datetime.fromgregorian(
        datetime=dt_tehran,
        locale='fa_IR'
    )


def jdatetime_to_datetime(jdt):
    """
    Convert jdatetime to datetime (Gregorian calendar) in Tehran timezone
    If jdt is None, returns None
    """
    if jdt is None:
        return None
    
    # Convert to Gregorian datetime
    dt = jdt.togregorian()
    
    # Make timezone-aware in Tehran timezone
    dt_tehran = TEHRAN_TZ.localize(dt)
    
    return dt_tehran


def get_jdatetime_now():
    """Get current jdatetime in Tehran timezone"""
    return datetime_to_jdatetime(get_tehran_now())


def format_jdatetime(jdt, format_str='%Y/%m/%d %H:%M:%S'):
    """
    Format jdatetime to string
    Default format: 1403/08/18 14:30:00
    """
    if jdt is None:
        return None
    return jdt.strftime(format_str)

