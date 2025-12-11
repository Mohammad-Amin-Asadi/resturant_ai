"""
Utility functions for OpenAI engine: date/time parsing, number conversion, weather fetching.
"""

import re
import time
import logging
import urllib.parse
import requests
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False

logger = logging.getLogger(__name__)


class DateTimeUtils:
    """Date and time parsing utilities"""
    
    @staticmethod
    def to_ascii_digits(s: str) -> str:
        """Convert Persian/Arabic digits to ASCII"""
        if not isinstance(s, str):
            return s
        return s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    
    @staticmethod
    def now_tz(timezone: str = "Asia/Tehran"):
        """Get current datetime in specified timezone"""
        try:
            tz = ZoneInfo(timezone) if ZoneInfo else None
        except Exception:
            tz = None
        return datetime.now(tz) if tz else datetime.now()
    
    @staticmethod
    def extract_time(text: str):
        """Extract time from Persian text"""
        if not text:
            return None
        t = DateTimeUtils.to_ascii_digits(text.lower())
        if "بامداد" in t: return "00:30"
        if "صبح" in t: return "09:00"
        if "ظهر" in t and "بعدازظهر" not in t: return "12:00"
        if "بعدازظهر" in t or "بعد از ظهر" in t: return "15:00"
        if "عصر" in t: return "17:00"
        if "شب" in t: return "20:00"
        m = re.search(r"(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm == "pm" and hh < 12: hh += 12
            if ampm == "am" and hh == 12: hh = 0
            if 0 <= hh <= 23 and 0 <= mm <= 59: return f"{hh:02d}:{mm:02d}"
        m2 = re.search(r"\b(\d{1,2})\s*(بعدازظهر|بعد از ظهر|عصر|شب)\b", t)
        if m2:
            hh = int(m2.group(1))
            if hh < 12: hh += 12
            return f"{hh:02d}:00"
        return None
    
    @staticmethod
    def parse_natural_date(text: str, now: datetime):
        """Parse natural language date in Persian"""
        if not text:
            return None
        t = DateTimeUtils.to_ascii_digits(text.lower())
        t = t.replace("پس‌فردا", "پسفردا").replace("بعدازظهر", "بعدازظهر")
        if "امروز" in t: return now.strftime("%Y-%m-%d")
        if "فردا" in t: return (now + timedelta(days=1)).strftime("%Y-%m-%d")
        if "پسفردا" in t: return (now + timedelta(days=2)).strftime("%Y-%m-%d")
        if "دیروز" in t: return (now - timedelta(days=1)).strftime("%Y-%m-%d")
        m_iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
        if m_iso:
            y, m, d = map(int, m_iso.groups())
            try:
                dt = datetime(y, m, d, now.hour, now.minute, now.second, tzinfo=now.tzinfo)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
        weekdays = {
            "شنبه": 5, "یکشنبه": 6, "يكشنبه": 6,
            "دوشنبه": 0, "سه شنبه": 1, "سه‌شنبه": 1, "سهشنبه": 1,
            "چهارشنبه": 2, "پنجشنبه": 3, "پنج‌شنبه": 3, "جمعه": 4
        }
        for name, target in weekdays.items():
            if name in t:
                today = now.weekday()
                delta = (target - today) % 7
                if delta == 0: delta = 7
                if any(kw in t for kw in ["بعدی", "هفته بعد", "هفته‌ی بعد", "هفته آتی"]):
                    if delta == 0: delta = 7
                    elif delta < 7: delta += 7
                return (now + timedelta(days=delta)).strftime("%Y-%m-%d")
        return None
    
    @staticmethod
    def normalize_date(s: str):
        """Normalize date string to YYYY-MM-DD format"""
        if not s: return None
        s = DateTimeUtils.to_ascii_digits(s.strip())
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        if not m: return None
        y, mth, d = map(int, m.groups())
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except ValueError:
            return None
    
    @staticmethod
    def normalize_time(s: str):
        """Normalize time string to HH:MM format"""
        if not s: return None
        s = DateTimeUtils.to_ascii_digits(s.strip())
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
        if not m: return None
        hh, mm = map(int, m.groups())
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
        return None


class NumberConverter:
    """Convert numbers to Persian words for better TTS"""
    
    @staticmethod
    def convert_to_persian_words(text: str) -> str:
        """Convert numbers in text to Persian words"""
        if not HAS_NUM2WORDS or not text:
            return text
        
        normalized_text = DateTimeUtils.to_ascii_digits(text)
        phone_pattern = r'\b(0\d{2,3}\d{8,9})\b'
        price_pattern = r'(\d{1,3}(?:[,\s]\d{3})*)\s*(?:تومان|ریال|دلار|یورو|USD|EUR|IRR)?'
        number_pattern = r'\b(\d+)\b'
        
        def replace_phone(match):
            phone = match.group(1)
            digits = [num2words(int(d), lang='fa') for d in phone]
            return ' '.join(digits)
        
        def replace_price(match):
            num_str = match.group(1).replace(',', '').replace(' ', '')
            try:
                num = int(num_str)
                persian = num2words(num, lang='fa', to='currency')
                currency = match.group(2) if match.lastindex >= 2 and match.group(2) else ''
                if currency:
                    return f"{persian} {currency}"
                return persian
            except (ValueError, OverflowError):
                return match.group(0)
        
        def replace_number(match):
            num_str = match.group(1)
            try:
                num = int(num_str)
                if num < 1000:
                    return num2words(num, lang='fa')
                elif num < 1000000:
                    return num2words(num, lang='fa')
                else:
                    return match.group(0)
            except (ValueError, OverflowError):
                return match.group(0)
        
        result = normalized_text
        result = re.sub(phone_pattern, replace_phone, result)
        result = re.sub(price_pattern, replace_price, result)
        result = re.sub(number_pattern, replace_number, result)
        return result
    
    @staticmethod
    def convert_in_output(output):
        """Recursively convert numbers in output dict/list to Persian words"""
        if not HAS_NUM2WORDS:
            return output
        if isinstance(output, dict):
            return {key: NumberConverter.convert_in_output(value) for key, value in output.items()}
        elif isinstance(output, list):
            return [NumberConverter.convert_in_output(item) for item in output]
        elif isinstance(output, str):
            return NumberConverter.convert_to_persian_words(output)
        else:
            return output


class WeatherService:
    """Weather fetching service"""
    
    @staticmethod
    def fetch_weather(city: str, did_config: dict = None) -> dict:
        """Fetch weather information for a city"""
        if not city:
            return {"error": "نام شهر مشخص نشده است."}
        
        try:
            from engine_config import EngineConfig
            weather_config = EngineConfig.get_weather_config(did_config)
            weather_token = weather_config.get('api_token')
            weather_url = weather_config.get('api_url', 'https://one-api.ir/weather/')
            
            if not weather_token:
                logger.warning("Weather API token not configured")
                return {"error": "سرویس آب و هوا پیکربندی نشده است."}
            
            city_encoded = urllib.parse.quote(city)
            api_url = f"{weather_url}?token={weather_token}&action=current&city={city_encoded}"
            
            api_start_time = time.time()
            logger.info(f"⏱️  Weather API: Starting API call for city: {city}")
            
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            api_end_time = time.time()
            logger.info(f"⏱️  Weather API: Completed in {(api_end_time - api_start_time) * 1000:.2f}ms")
            
            if data.get("status") == "OK" and "result" in data:
                result = data["result"]
                temp = result.get("temp", "نامشخص")
                condition = result.get("condition", "نامشخص")
                humidity = result.get("humidity", "نامشخص")
                wind_speed = result.get("wind_speed", "نامشخص")
                
                return {
                    "city": city,
                    "temperature": f"{temp} درجه سانتی‌گراد",
                    "condition": condition,
                    "humidity": f"{humidity} درصد",
                    "wind_speed": f"{wind_speed} کیلومتر بر ساعت"
                }
            else:
                return {"error": "اطلاعات آب و هوا در دسترس نیست."}
        except requests.exceptions.Timeout:
            logger.error("Weather API timeout")
            return {"error": "زمان اتصال به سرویس آب و هوا به پایان رسید."}
        except requests.exceptions.RequestException as e:
            logger.error(f"Weather API error: {e}")
            return {"error": "خطا در دریافت اطلاعات آب و هوا."}
        except Exception as e:
            logger.error(f"Weather fetch exception: {e}", exc_info=True)
            return {"error": "خطا در پردازش اطلاعات آب و هوا."}
