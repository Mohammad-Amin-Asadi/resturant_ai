"""
Meeting scheduling utilities for OpenAI engine.
"""

import logging
from datetime import datetime
from .utils import DateTimeUtils

logger = logging.getLogger(__name__)


class MeetingUtils:
    """Utilities for interpreting meeting datetime from function arguments"""
    
    @staticmethod
    def interpret_meeting_datetime(args: dict, timezone: str = "Asia/Tehran"):
        """
        Interpret meeting date and time from function arguments.
        
        Args:
            args: Function arguments dict with 'date' and 'time' keys
            timezone: Timezone string (default: Asia/Tehran)
            
        Returns:
            Tuple of (date_str, time_str) in YYYY-MM-DD and HH:MM format
        """
        now = DateTimeUtils.now_tz(timezone)
        
        date_str = args.get("date")
        time_str = args.get("time")
        
        # Parse natural date if provided
        if date_str:
            parsed_date = DateTimeUtils.parse_natural_date(date_str, now)
            if parsed_date:
                date_str = parsed_date
            else:
                date_str = DateTimeUtils.normalize_date(date_str)
        
        # Extract time if provided
        if time_str:
            extracted_time = DateTimeUtils.extract_time(time_str)
            if extracted_time:
                time_str = extracted_time
            else:
                time_str = DateTimeUtils.normalize_time(time_str)
        
        # Defaults
        if not date_str:
            date_str = now.strftime("%Y-%m-%d")
        if not time_str:
            time_str = "09:00"  # Default to 9 AM
        
        return date_str, time_str
