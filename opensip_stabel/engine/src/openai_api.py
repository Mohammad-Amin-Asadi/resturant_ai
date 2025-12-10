#!/usr/bin/env python
"""
Unified OpenAI Realtime + Soniox RT bridge
- Loads ALL configuration from DID-specific JSON files in config/did/
- Supports multiple services (taxi, restaurant, etc.) simultaneously
- No hardcoded prompts - everything loaded from JSON configs
- Function definitions loaded from config files
"""

import sys
import json
import time
import base64
import logging
import asyncio
import contextlib
import os
import re
import audioop
import requests
import urllib.parse
from queue import Empty
from datetime import datetime, timedelta
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from ai import AIEngine
from codec import get_codecs, CODECS, UnsupportedCodec
from config import Config
from storage import WalletMeetingDB
from api_sender import API
from phone_normalizer import normalize_phone_number
from did_config import load_did_config
from sms_service import sms_service

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False
    logging.warning("num2words not installed - numbers will not be converted to Persian words")

BACKEND_SERVER_URL = os.getenv("BACKEND_SERVER_URL", "http://localhost:8000")

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(message)s"
)

OPENAI_API_MODEL = "gpt-realtime-2025-08-28"
OPENAI_URL_FORMAT = "wss://api.openai.com/v1/realtime?model={}"


class OpenAI(AIEngine):
    """Unified OpenAI Realtime client - loads all config from DID JSON files."""

    def __init__(self, call, cfg):
        # === media & IO ===
        self.codec = self.choose_codec(call.sdp)
        self.queue = call.rtp
        self.call = call
        self.ws = None
        self.session = None

        # === Load DID configuration ===
        # Try both original DID (what user actually called) and current DID (IVR selection)
        did_number = getattr(call, 'did_number', None)
        original_did_number = getattr(call, 'original_did_number', None)
        self.did_number = did_number
        
        # Debug logging
        logging.info("🔍 OpenAI API: Loading DID config - did_number: %s, original_did_number: %s", 
                    did_number, original_did_number)
        
        # Try loading config: first original DID (what user called), then current DID (IVR), then default
        self.did_config = None
        tried_dids = []
        
        # Priority 1: Original DID (what the user actually called, e.g., 511882)
        if original_did_number and original_did_number != did_number:
            tried_dids.append(f"{original_did_number} (original)")
            original_config = load_did_config(original_did_number)
            if original_config:  # Accept config even without description
                description = original_config.get('description', 'Unknown')
                logging.info("✅ DID config loaded from original DID %s: %s (IVR routed to: %s)", 
                           original_did_number, description, did_number)
                self.did_config = original_config
        
        # Priority 2: Current DID (IVR selection, e.g., 1) - only if original didn't work
        if not self.did_config:
            if did_number:
                tried_dids.append(f"{did_number} (current)")
                current_config = load_did_config(did_number)
                if current_config:  # Accept config even without description
                    description = current_config.get('description', 'Unknown')
                    logging.info("✅ DID config loaded for current DID %s: %s", 
                               did_number, description)
                    self.did_config = current_config
        
        # Priority 3: Fallback to default
        if not self.did_config:
            logging.warning("⚠️  No DID config found (tried: %s), using default", 
                          ", ".join(tried_dids) if tried_dids else "none")
            self.did_config = load_did_config("default") or {}

        # === Merge base config with DID config ===
        base_cfg = Config.get("openai", cfg)
        merged_cfg_dict = dict(base_cfg)
        if self.did_config:
            if 'openai' in self.did_config:
                merged_cfg_dict.update(self.did_config['openai'])
            # Also check top-level keys
            for key in ['model', 'voice', 'temperature', 'welcome_message', 'intro']:
                if key in self.did_config:
                    merged_cfg_dict[key] = self.did_config[key]
        
        class MergedConfigSection:
            def __init__(self, base_section, did_overrides):
                self._base = base_section
                self._overrides = did_overrides
                
            def get(self, option, env=None, fallback=None):
                if isinstance(option, list):
                    for opt in option:
                        if opt in self._overrides:
                            return self._overrides[opt]
                    return self._base.get(option, env, fallback)
                else:
                    if option in self._overrides:
                        return self._overrides[option]
                    return self._base.get(option, env, fallback)
            
            def getboolean(self, option, env=None, fallback=None):
                val = self.get(option, env, None)
                if val is None:
                    return fallback
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    if val.isnumeric():
                        return int(val) != 0
                    if val.lower() in ["yes", "true", "on"]:
                        return True
                    if val.lower() in ["no", "false", "off"]:
                        return False
                return fallback
        
        self.cfg = MergedConfigSection(base_cfg, merged_cfg_dict)
        
        # === Backend API setup ===
        backend_url = BACKEND_SERVER_URL
        if self.did_config and 'backend_url' in self.did_config:
            backend_url = self.did_config['backend_url']
        self.api = API(backend_url)
        
        # === Database setup ===
        db_path = self.cfg.get("db_path", "OPENAI_DB_PATH", "./src/data/app.db")
        self.db = WalletMeetingDB(db_path)

        # === OpenAI settings from config ===
        self.model = self.cfg.get("model", "OPENAI_API_MODEL", OPENAI_API_MODEL)
        self.timezone = self.cfg.get("timezone", "OPENAI_TZ", "Asia/Tehran")
        self.url = self.cfg.get("url", "OPENAI_URL", OPENAI_URL_FORMAT.format(self.model))
        self.key = self.cfg.get(["key", "openai_key"], "OPENAI_API_KEY")
        self.voice = self.cfg.get(["voice", "openai_voice"], "OPENAI_VOICE", "alloy")
        
        # === Welcome message from config ===
        self.intro = self._get_welcome_message_from_config()
        
        # === Transfer settings ===
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        # === State variables (service-agnostic) ===
        self.temp_data = {}  # Generic temp storage for any service
        self.customer_name_from_history = None
        self.recent_order_ids = set()
        self.last_order_time = None

        # === Codec mapping ===
        if self.codec.name == "mulaw":
            self.codec_name = "g711_ulaw"
        elif self.codec.name == "alaw":
            self.codec_name = "g711_alaw"
        elif self.codec.name == "opus":
            self.codec_name = "opus"
        else:
            self.codec_name = "g711_ulaw"

        # === Soniox config (merged with DID config) ===
        base_soniox_cfg = Config.get("soniox", cfg)
        soniox_overrides = {}
        if self.did_config and 'soniox' in self.did_config:
            soniox_overrides = self.did_config['soniox']
        
        class MergedSonioxConfig:
            def __init__(self, base, overrides):
                self._base = base
                self._overrides = overrides
            def get(self, option, env=None, fallback=None):
                if option in self._overrides:
                    return self._overrides[option]
                return self._base.get(option, env, fallback)
            def getboolean(self, option, env=None, fallback=None):
                val = self.get(option, env, None)
                if val is None:
                    return fallback
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    if val.isnumeric():
                        return int(val) != 0
                    if val.lower() in ["yes", "true", "on"]:
                        return True
                    if val.lower() in ["no", "false", "off"]:
                        return False
                return fallback
        
        self.soniox_cfg = MergedSonioxConfig(base_soniox_cfg, soniox_overrides)
        self.soniox_enabled = bool(self.soniox_cfg.get("enabled", "SONIOX_ENABLED", True))
        self.soniox_key = self.soniox_cfg.get("key", "SONIOX_API_KEY")
        self.soniox_url = self.soniox_cfg.get("url", "SONIOX_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
        self.soniox_model = self.soniox_cfg.get("model", "SONIOX_MODEL", "stt-rt-preview")
        self.soniox_lang_hints = self.soniox_cfg.get("language_hints", "SONIOX_LANGUAGE_HINTS", ["fa"])
        self.soniox_enable_diar = bool(self.soniox_cfg.get("enable_speaker_diarization", "SONIOX_ENABLE_DIARIZATION", False))
        self.soniox_enable_lid = bool(self.soniox_cfg.get("enable_language_identification", "SONIOX_ENABLE_LID", False))
        self.soniox_enable_epd = bool(self.soniox_cfg.get("enable_endpoint_detection", "SONIOX_ENABLE_ENDPOINT", True))
        self.soniox_keepalive_sec = int(self.soniox_cfg.get("keepalive_sec", "SONIOX_KEEPALIVE_SEC", 15))
        self.soniox_upsample = bool(self.soniox_cfg.get("upsample_audio", "SONIOX_UPSAMPLE_AUDIO", True))
        
        # === Soniox context phrases from config ===
        default_context_phrases = []
        if self.did_config:
            custom_context = self.did_config.get('custom_context', {})
            menu_items = custom_context.get('menu_items', [])
            if menu_items:
                self.soniox_context_phrases = list(set(menu_items + default_context_phrases))
            else:
                self.soniox_context_phrases = default_context_phrases
        else:
            self.soniox_context_phrases = default_context_phrases
        
        # === Soniox state ===
        self._soniox_audio_buffer = b''
        self.soniox_ws = None
        self.soniox_task = None
        self.soniox_keepalive_task = None
        self._soniox_accum = []
        self._soniox_flush_timer = None
        self.soniox_silence_duration_ms = int(self.soniox_cfg.get("silence_duration_ms", "SONIOX_SILENCE_DURATION_MS", 500))
        self._order_confirmed = False
        self.forward_audio_to_openai = bool(self.soniox_cfg.get("forward_audio_to_openai", "FORWARD_AUDIO_TO_OPENAI", False))
        self._fallback_whisper_enabled = False

    # ---------------------- Config loading helpers ----------------------
    def _get_welcome_message_from_config(self):
        """Load welcome message from DID config."""
        if self.did_config:
            # Try multiple possible keys
            welcome = (self.did_config.get('welcome_message') or 
                      self.did_config.get('intro') or
                      (self.did_config.get('openai', {}).get('welcome_message') if isinstance(self.did_config.get('openai'), dict) else None) or
                      (self.did_config.get('openai', {}).get('intro') if isinstance(self.did_config.get('openai'), dict) else None))
            if welcome:
                return welcome
        # Fallback to config file
        return self.cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", "")

    # ---------------------- date/time helpers ----------------------
    def _to_ascii_digits(self, s: str) -> str:
        if not isinstance(s, str):
            return s
        return s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))

    def _now_tz(self):
        try:
            tz = ZoneInfo(self.timezone) if ZoneInfo else None
        except Exception:
            tz = None
        return datetime.now(tz) if tz else datetime.now()

    def _extract_time(self, text: str):
        if not text:
            return None
        t = self._to_ascii_digits(text.lower())
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

    def _parse_natural_date(self, text: str, now: datetime):
        if not text:
            return None
        t = self._to_ascii_digits(text.lower())
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

    def _normalize_date(self, s: str):
        if not s: return None
        s = self._to_ascii_digits(s.strip())
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        if not m: return None
        y, mth, d = map(int, m.groups())
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _normalize_time(self, s: str):
        if not s: return None
        s = self._to_ascii_digits(s.strip())
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
        if not m: return None
        hh, mm = map(int, m.groups())
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
        return None

    def _convert_numbers_to_persian_words(self, text: str) -> str:
        """
        Convert numbers in text to Persian words for better TTS pronunciation.
        Handles phone numbers, prices, and other numbers appropriately.
        """
        if not HAS_NUM2WORDS or not text:
            return text
        
        # Normalize Persian/Arabic digits to ASCII
        normalized_text = self._to_ascii_digits(text)
        
        # Pattern for phone numbers (Iranian format: 09xxxxxxxxx or 021xxxxxxxx)
        phone_pattern = r'\b(0\d{2,3}\d{8,9})\b'
        
        # Pattern for prices/currency (numbers followed by تومان, ریال, etc.)
        price_pattern = r'(\d{1,3}(?:[,\s]\d{3})*)\s*(?:تومان|ریال|دلار|یورو|USD|EUR|IRR)?'
        
        # Pattern for standalone numbers (not part of phone/price)
        number_pattern = r'\b(\d+)\b'
        
        def replace_phone(match):
            """Replace phone numbers digit by digit for clarity."""
            phone = match.group(1)
            digits = [num2words(int(d), lang='fa') for d in phone]
            return ' '.join(digits)
        
        def replace_price(match):
            """Replace prices with currency format."""
            num_str = match.group(1).replace(',', '').replace(' ', '')
            try:
                num = int(num_str)
                # Use currency format for prices
                persian = num2words(num, lang='fa', to='currency')
                # Remove "ریال" if already present in text, or add appropriate currency
                currency = match.group(2) if match.lastindex >= 2 and match.group(2) else ''
                if currency:
                    return f"{persian} {currency}"
                return persian
            except (ValueError, OverflowError):
                return match.group(0)
        
        def replace_number(match):
            """Replace standalone numbers."""
            num_str = match.group(1)
            try:
                num = int(num_str)
                # For small numbers (< 1000), convert to words
                # For larger numbers, keep as is or convert based on context
                if num < 1000:
                    return num2words(num, lang='fa')
                else:
                    # For larger numbers, convert but might be too long
                    # Only convert if reasonable (e.g., < 1 million)
                    if num < 1000000:
                        return num2words(num, lang='fa')
                    else:
                        # For very large numbers, keep as digits
                        return match.group(0)
            except (ValueError, OverflowError):
                return match.group(0)
        
        # Apply replacements in order: phone numbers first, then prices, then other numbers
        # But we need to avoid double replacement, so we'll mark already replaced parts
        result = normalized_text
        
        # Replace phone numbers
        result = re.sub(phone_pattern, replace_phone, result)
        
        # Replace prices
        result = re.sub(price_pattern, replace_price, result)
        
        # Replace remaining standalone numbers (but skip if they're part of already replaced patterns)
        # We'll be more careful here - only replace numbers that aren't in phone/price contexts
        def replace_standalone_number(match):
            # Check if this number is part of a phone number or price (shouldn't happen, but safety check)
            start, end = match.span()
            # If surrounded by digits or special chars, might be part of something else
            if start > 0 and end < len(result):
                before = result[start-1] if start > 0 else ''
                after = result[end] if end < len(result) else ''
                # Skip if part of email, URL, or other non-speech contexts
                if '@' in result[max(0, start-5):end+5] or '://' in result[max(0, start-10):end+10]:
                    return match.group(0)
            return replace_number(match)
        
        result = re.sub(number_pattern, replace_standalone_number, result)
        
        return result

    def _convert_numbers_in_output(self, output):
        """
        Recursively convert numbers in output dictionary/list to Persian words.
        This processes all string values in the output structure.
        """
        if not HAS_NUM2WORDS:
            return output
        
        if isinstance(output, dict):
            return {key: self._convert_numbers_in_output(value) for key, value in output.items()}
        elif isinstance(output, list):
            return [self._convert_numbers_in_output(item) for item in output]
        elif isinstance(output, str):
            return self._convert_numbers_to_persian_words(output)
        else:
            return output

    def _fetch_weather(self, city: str):
        """Fetch weather information for a city from one-api.ir and format in Persian."""
        if not city:
            return {"error": "نام شهر مشخص نشده است."}
        
        try:
            # Weather API configuration
            weather_token = "529569:691436185e3d0"
            weather_url = "https://one-api.ir/weather/"
            
            # URL encode the city name
            city_encoded = urllib.parse.quote(city)
            api_url = f"{weather_url}?token={weather_token}&action=current&city={city_encoded}"
            
            # Start timing
            api_start_time = time.time()
            logging.info(f"⏱️  Weather API: Starting API call for city: {city} at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            logging.info(f"🌐 Weather API: URL: {api_url}")
            
            # Make HTTP request (using requests since we're in a thread)
            response = requests.get(api_url, timeout=10)
            
            # Calculate API call duration
            api_end_time = time.time()
            api_duration = (api_end_time - api_start_time) * 1000  # Convert to milliseconds
            logging.info(f"✅ Weather API: Response received at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | API call duration: {api_duration:.2f}ms")
            
            response.raise_for_status()
            data = response.json()
            
            # Check API response status
            if data.get("status") != 200:
                error_msg = data.get("error", "خطا در دریافت اطلاعات آب و هوا")
                logging.error(f"Weather API error: {error_msg}")
                return {"error": f"خطا در دریافت اطلاعات آب و هوا: {error_msg}"}
            
            result = data.get("result", {})
            if not result:
                return {"error": "اطلاعات آب و هوا یافت نشد."}
            
            # Extract weather information
            weather_info = result.get("weather", [{}])[0]
            main_info = result.get("main", {})
            wind_info = result.get("wind", {})
            
            # Format Persian response
            description = weather_info.get("description", "نامشخص")
            temp = main_info.get("temp", 0)
            feels_like = main_info.get("feels_like", 0)
            humidity = main_info.get("humidity", 0)
            wind_speed = wind_info.get("speed", 0)
            
            # Create a natural Persian description
            weather_text = (
                f"وضعیت آب و هوای {city}:\n"
                f"شرایط: {description}\n"
                f"دما: {temp:.1f} درجه سانتی‌گراد\n"
                f"احساس دما: {feels_like:.1f} درجه سانتی‌گراد\n"
                f"رطوبت: {humidity} درصد\n"
                f"سرعت باد: {wind_speed:.1f} متر بر ثانیه"
            )
            
            logging.info(f"📊 Weather API: Successfully fetched weather for {city} | Total processing time: {(time.time() - api_start_time) * 1000:.2f}ms")
            return {
                "city": city,
                "description": description,
                "temperature": temp,
                "feels_like": feels_like,
                "humidity": humidity,
                "wind_speed": wind_speed,
                "weather_text": weather_text
            }
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Weather API request error: {e}")
            return {"error": f"خطا در ارتباط با سرویس آب و هوا: {str(e)}"}
        except json.JSONDecodeError as e:
            logging.error(f"Weather API JSON decode error: {e}")
            return {"error": "خطا در پردازش اطلاعات دریافتی از سرویس آب و هوا."}
        except Exception as e:
            logging.error(f"Weather API unexpected error: {e}", exc_info=True)
            return {"error": f"خطای غیرمنتظره در دریافت اطلاعات آب و هوا: {str(e)}"}

    def _interpret_meeting_datetime(self, args: dict):
        now = self._now_tz()
        raw_date = args.get("date")
        raw_time = args.get("time")
        raw_when = args.get("when")
        date_str = self._normalize_date(raw_date) if raw_date else None
        time_str = self._normalize_time(raw_time) if raw_time else None
        if not date_str:
            date_str = self._parse_natural_date(raw_when or raw_date or "", now)
        if not time_str:
            time_str = self._extract_time((raw_time or "") + " " + (raw_when or ""))
        if not time_str:
            time_str = "15:00"
        return date_str, time_str

    # ---------------------- codec helpers ----------------------
    def choose_codec(self, sdp):
        """Returns the preferred codec from a list - prefers Opus (48kHz) for better quality"""
        codecs = get_codecs(sdp)
        priority = ["opus", "pcma", "pcmu"]
        cmap = {c.name.lower(): c for c in codecs}
        for codec_name in priority:
            if codec_name in cmap:
                codec = CODECS[codec_name](cmap[codec_name])
                if codec_name == "opus" and codec.sample_rate == 48000:
                    logging.info("FLOW codec: Selected Opus at 48kHz (high quality)")
                    return codec
                elif codec_name == "opus":
                    logging.info("FLOW codec: Selected Opus at %dHz", codec.sample_rate)
                    return codec
                else:
                    logging.info("FLOW codec: Selected %s at %dHz", codec_name, codec.sample_rate)
                    return codec
        raise UnsupportedCodec("No supported codec found")

    def get_audio_format(self):
        """Returns the corresponding audio format string for OpenAI Realtime API."""
        if self.codec_name == "opus":
            return "g711_ulaw"
        return self.codec_name

    def _soniox_audio_format(self):
        """Map RTP codec to Soniox raw input config."""
        if self.codec.name == "opus" and self.codec.sample_rate == 48000:
            return ("pcm_s16le", 48000, 1)
        if self.soniox_upsample:
            return ("pcm_s16le", 16000, 1)
        if self.codec_name == "g711_ulaw":
            return ("mulaw", 8000, 1)
        if self.codec_name == "g711_alaw":
            return ("alaw", 8000, 1)
        return ("pcm_s16le", 16000, 1)
    
    def _convert_g711_to_pcm16(self, audio_data, is_ulaw=True):
        """Convert G.711 (μ-law or A-law) to 16-bit PCM."""
        try:
            if is_ulaw:
                pcm = audioop.ulaw2lin(audio_data, 2)
            else:
                pcm = audioop.alaw2lin(audio_data, 2)
            return pcm
        except Exception as e:
            logging.error("FLOW audio: G.711 conversion error: %s", e)
            return audio_data
    
    def _upsample_audio(self, pcm_data, from_rate=8000, to_rate=16000):
        """Upsample PCM audio from one sample rate to another."""
        if from_rate == to_rate:
            return pcm_data
        
        if not HAS_NUMPY:
            num_samples = len(pcm_data) // 2
            ratio = to_rate / from_rate
            new_num_samples = int(num_samples * ratio)
            
            samples = []
            for i in range(num_samples):
                idx = i * 2
                sample = int.from_bytes(pcm_data[idx:idx+2], byteorder='little', signed=True)
                samples.append(sample)
            
            new_samples = []
            for i in range(new_num_samples):
                pos = i / ratio
                idx = int(pos)
                frac = pos - idx
                
                if idx >= num_samples - 1:
                    new_samples.append(samples[-1])
                else:
                    sample = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
                    new_samples.append(sample)
            
            result = b''.join(s.to_bytes(2, byteorder='little', signed=True) for s in new_samples)
            return result
        else:
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            num_samples = len(samples)
            ratio = to_rate / from_rate
            new_num_samples = int(num_samples * ratio)
            
            indices = np.linspace(0, num_samples - 1, new_num_samples)
            new_samples = np.interp(indices, np.arange(num_samples), samples)
            new_samples = new_samples.astype(np.int16)
            return new_samples.tobytes()
    
    def _process_audio_for_soniox(self, audio_data):
        """Process audio for Soniox: convert G.711 to PCM and upsample if needed."""
        if not self.soniox_upsample:
            return audio_data
        
        if self.codec.name == "opus":
            return audio_data
        
        is_ulaw = (self.codec_name == "g711_ulaw")
        pcm_8k = self._convert_g711_to_pcm16(audio_data, is_ulaw)
        pcm_16k = self._upsample_audio(pcm_8k, from_rate=8000, to_rate=16000)
        return pcm_16k

    # ---------------------- Function definitions from config ----------------------
    def _get_function_definitions(self):
        """Load function definitions from DID config, with fallback to defaults."""
        # Default functions (always available)
        default_functions = [
            {"type": "function", "name": "terminate_call",
             "description": "ONLY call this function when the USER explicitly says they want to end the call. "
                            "Examples: 'خداحافظ', 'بای', 'تماس رو قطع کن', 'تماس رو پایان بده'. "
                            "DO NOT call this if: user is silent, user says '.', user pauses, or you just finished talking. "
                            "ONLY call when user EXPLICITLY requests to end the call. "
                            "Always say a friendly goodbye first, then call this function.",
             "parameters": {"type": "object", "properties": {}, "required": []}},
            {"type": "function", "name": "transfer_call",
             "description": "call the function if a request was received to transfer a call with an operator, a person",
             "parameters": {"type": "object", "properties": {}, "required": []}},
        ]
        
        # Load custom functions from DID config
        if self.did_config and 'functions' in self.did_config:
            custom_functions = self.did_config['functions']
            if isinstance(custom_functions, list):
                # If it's a list, replace all defaults
                return custom_functions
            elif isinstance(custom_functions, dict):
                # If it's a dict, merge with defaults (custom overrides defaults)
                function_map = {f['name']: f for f in default_functions}
                for func in custom_functions.values():
                    if isinstance(func, dict) and 'name' in func:
                        function_map[func['name']] = func
                return list(function_map.values())
        
        # Return defaults if no custom functions in config
        return default_functions

    # ---------------------- Instructions and welcome message builders ----------------------
    def _get_scenario_config(self, scenario_type):
        """Get scenario configuration from DID config."""
        if not self.did_config:
            return {}
        scenarios = self.did_config.get('scenarios', {})
        return scenarios.get(scenario_type, {})

    def _build_instructions_from_config(self, has_undelivered_order=False, orders=None):
        """Build instructions from DID config, with scenario support."""
        # Get base instructions from config
        base_instructions_template = ""
        if self.did_config and 'instructions_base' in self.did_config:
            base_instructions_template = self.did_config['instructions_base']
        else:
            # Minimal fallback
            base_instructions_template = "شما یک دستیار هوشمند هستید. فقط فارسی صحبت کنید. لحن: گرم، پرانرژی، مودب، حرفه‌ای."
        
        # Add customer name instruction if available
        name_instruction = ""
        if self.customer_name_from_history:
            name_instruction = f"مهم: نام مشتری ({self.customer_name_from_history}) از تاریخچه در دسترس است. نیازی به پرسیدن نام نیست. "
        else:
            name_instruction = "اگر نام مشتری موجود نیست، نام را بپرسید. "
        
        # Format base instructions
        base_instructions = base_instructions_template.replace("{name_instruction}", name_instruction)
        
        # Get scenario-specific instructions
        scenario_type = 'has_orders' if has_undelivered_order else 'new_customer'
        scenario_config = self._get_scenario_config(scenario_type)
        
        scenario_instructions = ""
        if scenario_config:
            if has_undelivered_order and orders:
                # Has orders scenario
                if len(orders) == 1:
                    template = scenario_config.get('single_order_template', "")
                    if template:
                        order = orders[0]
                        scenario_instructions = template.replace("{status_display}", str(order.get('status_display', '')))
                else:
                    template = scenario_config.get('multiple_orders_template', "")
                    if template:
                        scenario_instructions = template.replace("{orders_count}", str(len(orders)))
            else:
                # New customer scenario
                template = scenario_config.get('new_order_template', "")
                if template:
                    scenario_instructions = template.replace("{name_instruction}", name_instruction)
        
        # Combine base and scenario instructions
        if scenario_instructions:
            return base_instructions + " " + scenario_instructions
        return base_instructions

    def _build_welcome_message_from_config(self, has_undelivered_order=False, orders=None):
        """Build welcome message from DID config."""
        # Get service name from config
        service_name = (self.did_config.get('restaurant_name') if self.did_config else None) or \
                      (self.did_config.get('service_name') if self.did_config else None) or \
                      'خدمات ما'
        
        # Try to get scenario config
        scenario_type = 'has_orders' if has_undelivered_order else 'new_customer'
        scenario_config = self._get_scenario_config(scenario_type)
        welcome_templates = scenario_config.get('welcome_templates', {}) if scenario_config else {}
        
        # Build base greeting with fallbacks
        if self.customer_name_from_history:
            base_greeting_template = welcome_templates.get('with_customer_name', 
                "درودبرشما {customer_name} عزیز، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(
                    customer_name=self.customer_name_from_history,
                    service_name=service_name
                )
            except Exception:
                base_greeting = f"درودبرشما {self.customer_name_from_history} عزیز، با {service_name} تماس گرفته‌اید"
        else:
            base_greeting_template = welcome_templates.get('without_customer_name',
                "درودبرشما، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(service_name=service_name)
            except Exception:
                base_greeting = f"درودبرشما، با {service_name} تماس گرفته‌اید"
        
        # Add scenario-specific content (only for restaurant with orders)
        if has_undelivered_order and orders:
            # Format order details (for restaurant)
            order_details = []
            for order in orders:
                status_display = order.get('status_display', '')
                items_text = self._format_items_list_persian(order.get('items', []))
                if items_text:
                    order_details.append(f"سفارش شما {items_text} {status_display} است")
                else:
                    order_details.append(f"سفارش شما {status_display} است")
            
            orders_text = "، ".join(order_details)
            closing = welcome_templates.get('closing_with_orders', " از صبر شما متشکریم.")
            return f"{base_greeting}، {orders_text}.{closing}"
        else:
            # New customer or no orders
            new_customer_msg = welcome_templates.get('new_customer_question', 
                " لطفا درخواست خود را بفرمایید.")
            return f"{base_greeting}.{new_customer_msg}"

    def _format_items_list_persian(self, items):
        """Format items list in Persian (for restaurant orders)."""
        if not items:
            return ""
        
        persian_numbers = {
            1: "یک", 2: "دو", 3: "سه", 4: "چهار", 5: "پنج",
            6: "شش", 7: "هفت", 8: "هشت", 9: "نه", 10: "ده"
        }
        
        formatted_items = []
        for item in items:
            quantity = item.get('quantity', 1)
            item_name = (item.get('menu_item_name') or 
                        (item.get('menu_item', {}).get('name') if isinstance(item.get('menu_item'), dict) else None) or
                        item.get('name', ''))
            
            if not item_name:
                continue
            
            if quantity == 1:
                formatted_items.append(f"یک {item_name}")
            elif quantity <= 10:
                formatted_items.append(f"{persian_numbers.get(quantity, str(quantity))} {item_name}")
            else:
                formatted_items.append(f"{quantity} {item_name}")
        
        if not formatted_items:
            return ""
        elif len(formatted_items) == 1:
            return formatted_items[0]
        elif len(formatted_items) == 2:
            return f"{formatted_items[0]} و {formatted_items[1]}"
        else:
            all_except_last = "، ".join(formatted_items[:-1])
            return f"{all_except_last} و {formatted_items[-1]}"

    # ---------------------- Order checking helpers (for restaurant) ----------------------
    async def _check_undelivered_order(self, phone_number):
        """Check if caller has undelivered orders (restaurant service)."""
        if not phone_number:
            return False, []
        
        try:
            normalized_phone = normalize_phone_number(phone_number)
            
            try:
                customer_info = await self.api.get_customer_info(normalized_phone)
                if customer_info.get("success") and customer_info.get("customer"):
                    self.customer_name_from_history = customer_info["customer"].get("name")
            except Exception:
                pass
            
            result = await self.api.track_order(normalized_phone)
            if not result or not result.get("success"):
                return False, []
            
            orders = result.get("orders", [])
            if not orders:
                return False, []
            
            undelivered = [o for o in orders if o.get("status") not in ["delivered", "cancelled"]]
            
            if undelivered:
                if not self.customer_name_from_history:
                    self.customer_name_from_history = undelivered[0].get('customer_name')
                logging.info("Found %d undelivered order(s)", len(undelivered))
                return True, undelivered
            else:
                if not self.customer_name_from_history and orders:
                    self.customer_name_from_history = orders[0].get('customer_name')
                return False, []
                
        except Exception as e:
            logging.error("Exception checking orders: %s", e, exc_info=True)
            return False, []

    # ---------------------- session start ----------------------
    async def start(self):
        """Starts OpenAI connection, loads config, connects Soniox, runs main loop."""
        logging.info("FLOW start: connecting OpenAI WS → %s (DID: %s)", self.url, self.did_number)
        openai_headers = {"Authorization": f"Bearer {self.key}", "OpenAI-Beta": "realtime=v1"}
        self.ws = await connect(self.url, additional_headers=openai_headers)
        logging.info("FLOW start: OpenAI WS connected")

        # Expect initial hello from server
        try:
            json.loads(await self.ws.recv())
            logging.info("FLOW start: OpenAI hello received")
        except ConnectionClosedOK:
            logging.info("FLOW start: OpenAI WS closed during hello")
            return
        except ConnectionClosedError as e:
            logging.error("FLOW start: OpenAI hello error: %s", e)
            return

        # Check for orders (restaurant service) - only if API supports it
        caller_phone = self.call.from_number
        has_undelivered = False
        orders = None
        try:
            # Only check orders if we have track_order function (restaurant service)
            functions = self._get_function_definitions()
            has_track_order = any(f.get('name') == 'track_order' for f in functions)
            if has_track_order and caller_phone:
                has_undelivered, orders = await self._check_undelivered_order(caller_phone)
        except Exception as e:
            logging.warning("Could not check orders: %s", e)
        
        # Send menu via SMS when caller calls (for restaurant service)
        if caller_phone:
            functions = self._get_function_definitions()
            has_track_order = any(f.get('name') == 'track_order' for f in functions)
            if has_track_order:
                asyncio.create_task(self._send_menu_sms(caller_phone))

        # Build instructions and welcome message from config
        customized_instructions = self._build_instructions_from_config(has_undelivered, orders)
        welcome_message = self._build_welcome_message_from_config(has_undelivered, orders)
        
        # Use welcome message from config or fallback to intro
        if not welcome_message:
            welcome_message = self.intro

        # Include welcome message in instructions if provided
        if welcome_message:
            if customized_instructions:
                customized_instructions = f"{customized_instructions}\n\nWhen the call starts, greet the user with: {welcome_message}"
            else:
                customized_instructions = f"When the call starts, greet the user with: {welcome_message}"

        # Build session with config-loaded functions and instructions
        self.session = {
            "modalities": ["text", "audio"],
            "turn_detection": {
                "type": self.cfg.get("turn_detection_type", "OPENAI_TURN_DETECT_TYPE", "server_vad"),
                "silence_duration_ms": int(self.cfg.get("turn_detection_silence_ms", "OPENAI_TURN_DETECT_SILENCE_MS", 500)),
                "threshold": float(self.cfg.get("turn_detection_threshold", "OPENAI_TURN_DETECT_THRESHOLD", 0.6)),
                "prefix_padding_ms": int(self.cfg.get("turn_detection_prefix_ms", "OPENAI_TURN_DETECT_PREFIX_MS", 300)),
            },
            "input_audio_format": self.get_audio_format(),
            "output_audio_format": self.get_audio_format(),
            "voice": self.voice,
            "temperature": float(self.cfg.get("temperature", "OPENAI_TEMPERATURE", 0.8)),
            "max_response_output_tokens": self.cfg.get("max_tokens", "OPENAI_MAX_TOKENS", "inf"),
            "tools": self._get_function_definitions(),  # Load from config
            "tool_choice": "auto",
            "instructions": customized_instructions  # Load from config with welcome message
        }

        # Send session update
        await self.ws.send(json.dumps({"type": "session.update", "session": self.session}))
        logging.info("FLOW start: OpenAI session.update sent with %d functions", len(self.session["tools"]))

        # Trigger initial response to speak the welcome message
        if welcome_message:
            await self.ws.send(json.dumps({
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }))
            logging.info("FLOW start: welcome message trigger sent")

        # Connect Soniox
        soniox_key_ok = bool(self.soniox_key and self.soniox_key != "SONIOX_API_KEY")
        if self.soniox_enabled and soniox_key_ok:
            logging.info("FLOW STT: SONIOX enabled | model=%s | url=%s", self.soniox_model, self.soniox_url)
            ok = await self._soniox_connect()
            if ok:
                self.soniox_task = asyncio.create_task(self._soniox_recv_loop(), name="soniox-recv")
                self.soniox_keepalive_task = asyncio.create_task(self._soniox_keepalive_loop(), name="soniox-keepalive")
            else:
                logging.warning("FLOW STT: Soniox connect failed; enabling Whisper fallback on OpenAI")
                await self._enable_whisper_fallback()
        else:
            if not soniox_key_ok:
                logging.error("FLOW STT: SONIOX_API_KEY not set; STT fallback will be used")
            else:
                logging.info("FLOW STT: SONIOX disabled by config; using fallback")
            await self._enable_whisper_fallback()

        # Start consuming OpenAI events
        await self.handle_command()

    async def _enable_whisper_fallback(self):
        """Enable Whisper fallback on OpenAI."""
        await self.ws.send(json.dumps({
            "type": "session.update",
            "session": {"input_audio_transcription": {"model": "whisper-1"}}
        }))
        self._fallback_whisper_enabled = True
        self.forward_audio_to_openai = True
        logging.info("FLOW STT: Whisper fallback enabled")

    # ---------------------- OpenAI event loop ----------------------
    async def handle_command(self):
        """Handles OpenAI events; plays TTS audio; responds to tools dynamically."""
        leftovers = b""
        logging.info("FLOW TTS: handle_command loop started")
        async for smsg in self.ws:
            msg = json.loads(smsg)
            t = msg["type"]

            if t == "response.audio.delta":
                # Check if this is the first audio delta (start of speaking) after weather call
                if not hasattr(self, '_weather_audio_started'):
                    self._weather_audio_started = False
                if not self._weather_audio_started and hasattr(self, '_last_weather_call_time'):
                    time_since_weather = (time.time() - self._last_weather_call_time) * 1000
                    logging.info(f"🔊 Weather TTS: OpenAI started speaking about weather at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Time since weather API call: {time_since_weather:.2f}ms")
                    self._weather_audio_started = True
                
                media = base64.b64decode(msg["delta"])
                packets, leftovers = await self.run_in_thread(self.codec.parse, media, leftovers)
                for packet in packets:
                    self.queue.put_nowait(packet)

            elif t == "response.audio.done":
                logging.info("FLOW TTS: response.audio.done")
                if len(leftovers) > 0:
                    packet = await self.run_in_thread(self.codec.parse, None, leftovers)
                    self.queue.put_nowait(packet)
                    leftovers = b""

            elif t == "conversation.item.created":
                if msg["item"].get("status") == "completed":
                    self.drain_queue()

            elif t == "conversation.item.input_audio_transcription.completed":
                transcript = msg.get("transcript", "").rstrip()
                logging.info("OpenAI (whisper) transcript: %s", transcript)
                if self._fallback_whisper_enabled:
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))
                    logging.info("FLOW TTS: response.create issued (fallback Whisper turn)")

            elif t == "response.audio_transcript.done":
                transcript = msg.get("transcript", "")
                logging.info("OpenAI said: %s", transcript)
                
                # Check if this is a weather-related response
                if hasattr(self, '_last_weather_call_time') and any(word in transcript.lower() for word in ['آب و هوا', 'دما', 'درجه', 'رطوبت', 'باد', 'weather', 'temperature']):
                    time_since_weather = (time.time() - self._last_weather_call_time) * 1000
                    logging.info(f"💬 Weather TTS: OpenAI finished speaking about weather at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Total time from API call to speech end: {time_since_weather:.2f}ms")
                    # Reset flag
                    if hasattr(self, '_weather_audio_started'):
                        self._weather_audio_started = False
                    if hasattr(self, '_last_weather_call_time'):
                        delattr(self, '_last_weather_call_time')

            elif t == "response.function_call_arguments.done":
                call_id = msg.get("call_id")
                name = msg.get("name")
                logging.info("FLOW tool: %s called", name)
                try:
                    args = json.loads(msg.get("arguments") or "{}")
                except Exception:
                    args = {}

                # Handle function calls dynamically based on name
                await self._handle_function_call(name, call_id, args)

            elif t == "error":
                error_msg = msg.get("error", {})
                error_type = error_msg.get("type", "unknown")
                error_message = error_msg.get("message", str(msg))
                error_code = error_msg.get("code", "unknown")
                logging.error("OpenAI error [%s/%s]: %s", error_type, error_code, error_message)
                # Check for payment/credit errors
                if error_code in ["insufficient_quota", "billing_not_active", "invalid_api_key"]:
                    logging.error("⚠️ CRITICAL: OpenAI API issue - Code: %s, Message: %s", error_code, error_message)

            elif t == "response.done":
                # Check if response failed and log full error details
                response_obj = msg.get("response", {})
                status = response_obj.get("status", "unknown")
                status_details = response_obj.get("status_details", {})
                
                if status == "failed":
                    error_type = status_details.get("type", "unknown")
                    error_message = status_details.get("message", "No error message")
                    error_code = status_details.get("code", "unknown")
                    logging.error("⚠️ OpenAI response FAILED - Type: %s, Code: %s, Message: %s", 
                                error_type, error_code, error_message)
                    logging.error("Full response.done event: %s", json.dumps(msg, ensure_ascii=False))
                    
                    # Check for specific error types
                    if error_code in ["insufficient_quota", "billing_not_active", "invalid_api_key"]:
                        logging.error("🚨 CRITICAL: OpenAI billing/credit issue detected!")
                    elif "rate_limit" in error_message.lower() or error_code == "rate_limit_exceeded":
                        logging.error("⚠️ Rate limit exceeded - wait before retrying")
                else:
                    logging.info("OpenAI response completed with status: %s", status)
            
            else:
                logging.debug("OpenAI event: %s", t)
                # Log important events at INFO level
                if t in ["response.created", "response.audio_transcript.done", "conversation.item.created"]:
                    logging.info("OpenAI event: %s - %s", t, json.dumps(msg)[:200])

    async def _send_function_output(self, call_id, output):
        """
        Send function output to OpenAI with number conversion to Persian words.
        This ensures all numbers in the output are converted for better TTS pronunciation.
        """
        # Convert numbers in output to Persian words
        converted_output = self._convert_numbers_in_output(output)
        
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id,
                     "output": json.dumps(converted_output, ensure_ascii=False)}
        }))
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]}
        }))

    async def _handle_function_call(self, name, call_id, args):
        """Handle function calls dynamically - supports both taxi and restaurant."""
        if name == "terminate_call":
            logging.info("FLOW tool: terminate_call requested")
            self.terminate_call()

        elif name == "transfer_call":
            if self.transfer_to:
                logging.info("FLOW tool: Transferring call via REFER")
                self.call.ua_session_update(method="REFER", headers={
                    "Refer-To": f"<{self.transfer_to}>",
                    "Referred-By": f"<{self.transfer_by}>"
                })
            else:
                logging.warning("FLOW tool: transfer_call requested but transfer_to not configured")

        elif name == "get_wallet_balance":
            def _lookup():
                return self.db.get_wallet_balance(
                    customer_id=args.get("customer_id"),
                    phone=args.get("phone_number")
                )
            result = await self.run_in_thread(_lookup)
            await self._send_function_output(call_id, result)

        elif name == "schedule_meeting":
            date_str, time_str = self._interpret_meeting_datetime(args)
            def _schedule():
                return self.db.schedule_meeting(
                    date=date_str, time=time_str,
                    customer_id=args.get("customer_id"),
                    duration_minutes=args.get("duration_minutes") or 30,
                    subject=args.get("subject")
                )
            result = await self.run_in_thread(_schedule)
            await self._send_function_output(call_id, result)

        # === Taxi service functions ===
        elif name == "get_origin_destination_userame":
            await self._handle_taxi_booking(call_id, args)
        elif name == "get_weather":
            # Only allow weather for taxi service
            if self.did_config and self.did_config.get('service_id') == 'taxi_vip':
                await self._handle_get_weather(call_id, args)
            else:
                logging.warning("FLOW tool: get_weather called but not a taxi service")
                output = {"error": "این قابلیت فقط برای سرویس تاکسی در دسترس است."}
                await self._send_function_output(call_id, output)

        # === Restaurant service functions ===
        elif name == "track_order":
            await self._handle_track_order(call_id, args)
        elif name == "get_menu_specials":
            await self._handle_get_menu_specials(call_id)
        elif name == "search_menu_item":
            await self._handle_search_menu_item(call_id, args)
        elif name == "create_order":
            await self._handle_create_order(call_id, args)

        # === Direct FAQ service functions ===
        elif name == "answer_faq":
            await self._handle_answer_faq(call_id, args)

        # === Personal Assistant service functions ===
        elif name == "get_contact_info":
            await self._handle_get_contact_info(call_id, args)
        elif name == "get_resume_info":
            await self._handle_get_resume_info(call_id, args)
        elif name == "send_resume_pdf":
            await self._handle_send_resume_pdf(call_id, args)
        elif name == "send_website_info":
            await self._handle_send_website_info(call_id, args)

        else:
            logging.debug("FLOW tool: unhandled function name: %s", name)

    # ---------------------- Taxi service handlers ----------------------
    async def _handle_taxi_booking(self, call_id, args):
        """Handle taxi booking function call."""
        unique_time = time.time()
        origin = args.get("origin")
        destination = args.get("destination")
        user_name = args.get("user_name")
        logging.info("FLOW tool: Taxi booking - user=%s origin=%s dest=%s", user_name, origin, destination)

        # Store in temp_data
        if user_name is not None:
            self.temp_data[unique_time] = self.temp_data.get(unique_time, {})
            self.temp_data[unique_time]["user_name"] = user_name
        if origin is not None:
            self.temp_data[unique_time] = self.temp_data.get(unique_time, {})
            self.temp_data[unique_time]["origin"] = origin
        if destination is not None:
            self.temp_data[unique_time] = self.temp_data.get(unique_time, {})
            self.temp_data[unique_time]["destination"] = destination

        # Send to backend API (taxi reservation endpoint) - run in thread to avoid blocking
        api_result = False
        try:
            backend_url = (self.did_config.get('backend_url') if self.did_config else None) or BACKEND_SERVER_URL
            reservation_url = f"{backend_url.rstrip('/')}/add-reservation/"
            
            def _send_taxi_reservation():
                try:
                    # Get public key
                    response = requests.get(reservation_url, timeout=10)
                    response.raise_for_status()
                    public_key = response.json()["public_key"]
                    
                    # Prepare data
                    data = {
                        "user_fullname": user_name,
                        "origin": origin,
                        "destination": destination
                    }
                    
                    # Encrypt data (using API's encoder method)
                    encrypted_data = API.encoder(public_key, data)
                    
                    # Send reservation
                    response = requests.post(reservation_url, json=encrypted_data, timeout=10)
                    response.raise_for_status()
                    return True
                except Exception as e:
                    logging.error("Error sending taxi reservation: %s", e)
                    return False
            
            api_result = await self.run_in_thread(_send_taxi_reservation)
            logging.info(f"Taxi reservation API result: {api_result}")
        except Exception as e:
            logging.error("Exception in taxi API call: %s", e)
            api_result = False

        # Check if all required info is available
        temp_entry = self.temp_data.get(unique_time, {})
        if (temp_entry.get("user_name") and temp_entry.get("origin") and temp_entry.get("destination")):
            output = {
                "origin": origin, 
                "destination": destination, 
                "user_name": user_name
            }
            await self._send_function_output(call_id, output)
        else:
            missing = []
            if not temp_entry.get("user_name"):
                missing.append("نام")
            if not temp_entry.get("origin"):
                missing.append("مبدا")
            if not temp_entry.get("destination"):
                missing.append("مقصد")
            output = {
                "error": f"لطفاً {' و '.join(missing)} را مجدداً بفرمایید."
            }
            await self._send_function_output(call_id, output)

    async def _handle_get_weather(self, call_id, args):
        """Handle get_weather function call for taxi service."""
        city = args.get("city")
        handler_start_time = time.time()
        # Store start time for tracking when OpenAI starts speaking
        self._last_weather_call_time = handler_start_time
        self._weather_audio_started = False
        
        logging.info(f"🌤️  Weather Handler: Starting weather request for city: {city} at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        
        def _get_weather():
            return self._fetch_weather(city)
        
        result = await self.run_in_thread(_get_weather)
        
        # Log when function output is sent
        output_send_time = time.time()
        logging.info(f"📤 Weather Handler: Sending function output to OpenAI at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Handler processing time: {(output_send_time - handler_start_time) * 1000:.2f}ms")
        
        await self._send_function_output(call_id, result)
        
        # Log when response.create is sent (triggers OpenAI to speak)
        response_create_time = time.time()
        logging.info(f"🎤 Weather Handler: Requesting OpenAI to generate response (response.create) at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Time since handler start: {(response_create_time - handler_start_time) * 1000:.2f}ms")
        
        logging.info(f"⏱️  Weather Handler: Total handler time: {(time.time() - handler_start_time) * 1000:.2f}ms")

    # ---------------------- Restaurant service handlers ----------------------
    async def _handle_track_order(self, call_id, args):
        """Handle track_order function call."""
        phone_number = args.get("phone_number") or self.call.from_number
        if not phone_number:
            output = {"success": False, "message": "شماره تلفن در دسترس نیست."}
            await self._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        try:
            result = await self.api.track_order(normalized_phone)
            if result and result.get("success"):
                orders = result.get("orders", [])
                if orders:
                    latest = orders[0]
                    output = {
                        "success": True,
                        "message": f"سفارش شما {latest['status_display']} است.",
                        "order": latest
                    }
                else:
                    output = {
                        "success": True,
                        "message": "شما سفارشی ثبت نکرده‌اید خوشحال می‌شوم اطلاعات سفارش جدید را بدونم",
                        "orders": []
                    }
            else:
                output = {"success": False, "message": "خطا در پیگیری سفارش"}
        except Exception as e:
            logging.error("Exception tracking order: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        
        await self._send_function_output(call_id, output)

    # ---------------------- Direct FAQ handlers ----------------------
    async def _handle_answer_faq(self, call_id, args):
        """Handle answer_faq function call for Direct FAQ service."""
        user_question = (args.get("user_question") or "").strip()
        logging.info("FLOW tool: answer_faq - user_question=%s", user_question)

        # Load FAQ entries from DID config
        faq_entries = []
        if self.did_config:
            custom_context = self.did_config.get("custom_context", {})
            faq_entries = custom_context.get("faq_entries", []) or []

        not_found_answer = (
            "برای این سوال، پاسخ دقیقی در فهرست سؤالات متداول من ثبت نشده است. "
            "لطفاً سوال را کمی تغییر دهید یا سوال دیگری بپرسید."
        )

        best_answer = not_found_answer
        best_question = None
        best_score = 0.0

        def _normalize(text: str):
            if not isinstance(text, str):
                return []
            # Convert Persian digits to ASCII, remove punctuation, split on whitespace
            t = self._to_ascii_digits(text)
            t = t.replace("؟", " ").replace("?", " ")
            t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", t)
            tokens = [tok for tok in t.split() if tok]
            return tokens

        if user_question and faq_entries:
            q_tokens = set(_normalize(user_question))
            if q_tokens:
                for entry in faq_entries:
                    fq = entry.get("question") or ""
                    fa = entry.get("answer") or ""
                    if not fq or not fa:
                        continue
                    f_tokens = set(_normalize(fq))
                    if not f_tokens:
                        continue
                    inter = len(q_tokens & f_tokens)
                    union = len(q_tokens | f_tokens) or 1
                    jaccard = inter / union

                    # Small bonus if one text is substring of the other
                    bonus = 0.0
                    if fq in user_question or user_question in fq:
                        bonus = 0.15
                    score = jaccard + bonus

                    if score > best_score:
                        best_score = score
                        best_answer = fa
                        best_question = fq

        # Require a minimum similarity threshold
        threshold = 0.25
        if best_score < threshold:
            best_answer = not_found_answer
            best_question = None

        output = {
            "answer": best_answer,
            "matched_question": best_question,
            "similarity_score": best_score,
        }

        await self._send_function_output(call_id, output)

    async def _handle_get_menu_specials(self, call_id):
        """Handle get_menu_specials function call."""
        try:
            result = await self.api.get_menu_specials()
            if result and result.get("success"):
                output = {"success": True, "specials": result.get("items", [])}
            else:
                output = {"success": False, "message": "خطا در دریافت پیشنهادات"}
        except Exception as e:
            logging.error("Exception getting specials: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        
        await self._send_function_output(call_id, output)

    async def _handle_search_menu_item(self, call_id, args):
        """Handle search_menu_item function call."""
        item_name = args.get("item_name")
        category = args.get("category")
        try:
            result = await self.api.search_menu_item(item_name, category)
            if result and result.get("success"):
                output = {"success": True, "items": result.get("items", [])}
            else:
                output = {"success": False, "message": "غذایی با این نام یافت نشد"}
        except Exception as e:
            logging.error("Exception searching menu: %s", e)
            output = {"success": False, "message": "خطا در جستجو"}
        
        await self._send_function_output(call_id, output)

    async def _handle_create_order(self, call_id, args):
        """Handle create_order function call."""
        current_time = time.time()
        if self.last_order_time and (current_time - self.last_order_time) < 10:
            output = {
                "success": False, 
                "message": "سفارش قبلی شما در حال پردازش است. لطفا چند لحظه صبر کنید."
            }
            await self._send_function_output(call_id, output)
            return
        
        customer_name = args.get("customer_name")
        phone_number = self.call.from_number or args.get("phone_number")
        if not phone_number:
            output = {"success": False, "message": "شماره تلفن در دسترس نیست. لطفا دوباره تماس بگیرید."}
            await self._send_function_output(call_id, output)
            return
        
        address = args.get("address")
        items = args.get("items", [])
        notes = args.get("notes")
        
        validation_errors = []
        if not customer_name or not customer_name.strip():
            validation_errors.append("نام مشتری")
        if not address or not address.strip():
            validation_errors.append("آدرس")
        if not items:
            validation_errors.append("لیست غذاها (هیچ غذایی ثبت نشده)")
        else:
            for idx, item in enumerate(items):
                item_name = item.get('item_name', '').strip()
                quantity = item.get('quantity', 0)
                if not item_name:
                    validation_errors.append(f"نام غذا در آیتم {idx + 1}")
                if not quantity or quantity <= 0:
                    validation_errors.append(f"تعداد در آیتم {idx + 1} (باید عدد مثبت باشد، مقدار فعلی: {quantity})")
        
        if validation_errors:
            error_message = f"خطا: اطلاعات ناقص است. لطفا موارد زیر را تکمیل کنید: {', '.join(validation_errors)}"
            output = {
                "success": False,
                "message": error_message,
                "missing_fields": validation_errors
            }
            await self._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        logging.info("Creating order: Customer=%s, Items=%d", customer_name, len(items))
        
        try:
            result = await self.api.create_order(
                customer_name=customer_name,
                phone_number=normalized_phone,
                address=address,
                items=items,
                notes=notes
            )
            
            if result and result.get("success"):
                order = result.get("order", {})
                order_id = order.get('id')
                self.last_order_time = time.time()
                self.recent_order_ids.add(order_id)
                self._order_confirmed = True
                
                # Send SMS receipt to customer
                asyncio.create_task(self._send_order_receipt_sms(order, normalized_phone))
                
                output = {
                    "success": True,
                    "message": f"سفارش شما با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                    "order_id": order.get("id"),
                    "total_price": order.get("total_price")
                }
                logging.info("Order created: ID=%s, Total=%s", order_id, order.get('total_price'))
            else:
                output = {"success": False, "message": result.get("message", "خطا در ثبت سفارش")}
                logging.error("Order failed: %s", result.get("message"))
        except Exception as e:
            logging.error("Exception creating order: %s", e, exc_info=True)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        
        await self._send_function_output(call_id, output)
    
    async def _send_order_receipt_sms(self, order: dict, phone_number: str):
        """Send order receipt via SMS"""
        try:
            # Format order receipt message
            order_id = order.get('id')
            total_price = order.get('total_price', 0)
            status = order.get('status', 'pending')
            status_display = dict([
                ('pending', 'در انتظار تایید رستوران'),
                ('confirmed', 'تایید توسط رستوران'),
                ('preparing', 'در حال آماده سازی'),
                ('on_delivery', 'تحویل داده شده به پیک'),
                ('delivered', 'تحویل داده شده به مشتری'),
                ('cancelled', 'لغو شده')
            ]).get(status, status)
            
            # Format items
            items = order.get('items', [])
            items_text = []
            for item in items:
                item_name = item.get('menu_item_name') or (item.get('menu_item', {}).get('name') if isinstance(item.get('menu_item'), dict) else '') or ''
                quantity = item.get('quantity', 1)
                unit_price = item.get('unit_price', 0)
                if item_name:
                    items_text.append(f"{quantity}× {item_name} ({unit_price:,} تومان)")
            
            receipt = f"📋 فاکتور سفارش #{order_id}\n\n"
            if items_text:
                receipt += "موارد سفارش:\n" + "\n".join(items_text) + "\n\n"
            receipt += f"💰 جمع کل: {total_price:,} تومان\n"
            receipt += f"📊 وضعیت: {status_display}"
            
            # Send SMS
            sms_service.send_sms(phone_number, receipt)
            logging.info(f"📱 Order receipt SMS sent to {phone_number} for order #{order_id}")
            
        except Exception as e:
            logging.error(f"❌ Failed to send order receipt SMS: {e}", exc_info=True)
    
    async def _send_menu_sms(self, phone_number: str):
        """Send top menu items via SMS when caller calls"""
        try:
            if not phone_number:
                logging.warning("Cannot send menu SMS: no phone number")
                return
            
            # Normalize phone number
            normalized_phone = normalize_phone_number(phone_number)
            if not normalized_phone:
                logging.warning(f"Cannot send menu SMS: invalid phone number format: {phone_number}")
                return
            
            # Get top 10 menu items (5 special foods + 5 drinks)
            menu_result = await self.api.get_top_menu_items(limit=10, include_drinks=True)
            
            if not menu_result.get("success") or not menu_result.get("items"):
                logging.warning("Could not retrieve menu items for SMS")
                return
            
            items = menu_result.get("items", [])
            
            # Format menu message
            menu_text = "🍽️ منوی رستوران بزرگمهر\n\n"
            menu_text += "پیشنهادات ویژه:\n"
            
            # Separate foods and drinks
            foods = [item for item in items if item.get('category') != 'نوشیدنی']
            drinks = [item for item in items if item.get('category') == 'نوشیدنی']
            
            # Add top foods (up to 5)
            for i, item in enumerate(foods[:5], 1):
                name = item.get('name', '')
                price = item.get('final_price', 0)
                menu_text += f"{i}. {name} - {price:,} تومان\n"
            
            if drinks:
                menu_text += "\nنوشیدنی‌ها:\n"
                for i, item in enumerate(drinks[:5], 1):
                    name = item.get('name', '')
                    price = item.get('final_price', 0)
                    menu_text += f"{i}. {name} - {price:,} تومان\n"
            
            # Send SMS with normalized phone
            sms_service.send_sms(normalized_phone, menu_text)
            logging.info(f"📱 Menu SMS sent to {normalized_phone} (original: {phone_number})")
            
        except Exception as e:
            logging.error(f"❌ Failed to send menu SMS: {e}", exc_info=True)

    # ---------------------- Direct FAQ helpers & handlers ----------------------
    def _normalize_faq_text(self, text: str):
        """Simple normalizer for Persian text used as a fallback matcher."""
        if not isinstance(text, str):
            return []
        t = self._to_ascii_digits(text)
        t = t.replace("؟", " ").replace("?", " ")
        t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", t)
        tokens = [tok for tok in t.split() if tok]
        return tokens

    def _match_faq_locally(self, user_question, faq_entries, not_found_answer):
        """Fallback Jaccard-based matcher (used when OpenAI HTTP call fails)."""
        best_answer = not_found_answer
        best_question = None
        best_score = 0.0

        if not user_question or not faq_entries:
            return best_answer, best_question, best_score

        q_tokens = set(self._normalize_faq_text(user_question))
        if not q_tokens:
            return best_answer, best_question, best_score

        for entry in faq_entries:
            fq = entry.get("question") or ""
            fa = entry.get("answer") or ""
            if not fq or not fa:
                continue
            f_tokens = set(self._normalize_faq_text(fq))
            if not f_tokens:
                continue
            inter = len(q_tokens & f_tokens)
            union = len(q_tokens | f_tokens) or 1
            jaccard = inter / union

            bonus = 0.0
            if fq in user_question or user_question in fq:
                bonus = 0.15
            score = jaccard + bonus

            if score > best_score:
                best_score = score
                best_answer = fa
                best_question = fq

        return best_answer, best_question, best_score

    def _match_faq_with_openai(self, user_question, faq_entries, not_found_answer):
        """
        Use OpenAI Chat Completion API to semantically match user_question
        with the closest FAQ question. Returns (answer, matched_question, score).
        """
        try:
            if not user_question or not faq_entries:
                return not_found_answer, None, 0.0

            # Prepare numbered questions
            questions_text = []
            for idx, entry in enumerate(faq_entries):
                q = (entry.get("question") or "").strip()
                if not q:
                    continue
                questions_text.append(f"{idx}: {q}")

            if not questions_text:
                return not_found_answer, None, 0.0

            prompt_user = (
                "سوال کاربر:\n"
                f"{user_question}\n\n"
                "لیست سوالات متداول (FAQ):\n"
                + "\n".join(questions_text)
                + "\n\n"
                "کار تو این است که فقط تشخیص بدهی کدام سوال در این لیست از نظر معنا "
                "بیشترین شباهت را با سوال کاربر دارد.\n"
                "اگر هیچکدام از سوال‌ها مناسب نبود، عدد -1 را برگردان.\n\n"
                "خروجی نهایی تو باید فقط و فقط یک عدد صحیح باشد:\n"
                "- اگر یک سوال مناسب پیدا کردی: شماره آن سوال (ایندکس) به صورت عدد صحیح ۰-بنیان.\n"
                "- اگر سوال مناسبی پیدا نکردی: عدد -1.\n"
                "هیچ متن دیگری غیر از همین عدد ننویس."
            )

            api_key = self.key or os.getenv("OPENAI_API_KEY")
            if not api_key:
                logging.error("FAQ matcher: OPENAI_API_KEY not set, falling back to local matcher")
                return self._match_faq_locally(user_question, faq_entries, not_found_answer)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a semantic FAQ matcher. You only answer with a single integer index."
                    },
                    {"role": "user", "content": prompt_user},
                ],
                "temperature": 0.0,
            }

            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            logging.info("FAQ matcher (OpenAI) raw content: %s", content)

            # Extract integer index
            m = re.search(r"-?\d+", content)
            if not m:
                logging.warning("FAQ matcher: no integer index in response, falling back to local")
                return self._match_faq_locally(user_question, faq_entries, not_found_answer)

            idx = int(m.group(0))
            if idx < 0 or idx >= len(faq_entries):
                logging.info("FAQ matcher: index %s out of range, treating as no match", idx)
                return not_found_answer, None, 0.0

            matched_entry = faq_entries[idx]
            answer = matched_entry.get("answer") or not_found_answer
            question = matched_entry.get("question") or None

            # We don't have a real numeric similarity from the model; set a dummy high score
            return answer, question, 0.9

        except Exception as e:
            logging.error("FAQ matcher (OpenAI) error: %s", e, exc_info=True)
            return self._match_faq_locally(user_question, faq_entries, not_found_answer)

    async def _handle_answer_faq(self, call_id, args):
        """Handle answer_faq function call for Direct FAQ service."""
        user_question = (args.get("user_question") or "").strip()
        logging.info("FLOW tool: answer_faq - user_question=%s", user_question)

        # Load FAQ entries from DID config
        faq_entries = []
        if self.did_config:
            custom_context = self.did_config.get("custom_context", {})
            faq_entries = custom_context.get("faq_entries", []) or []

        not_found_answer = (
            "برای این سوال، پاسخ دقیقی در فهرست سؤالات متداول من ثبت نشده است. "
            "لطفاً سوال را کمی تغییر دهید یا سوال دیگری بپرسید."
        )

        # First try semantic matching via OpenAI; fall back to local if needed
        best_answer, best_question, best_score = self._match_faq_with_openai(
            user_question, faq_entries, not_found_answer
        )

        output = {
            "answer": best_answer,
            "matched_question": best_question,
            "similarity_score": best_score,
        }

        await self._send_function_output(call_id, output)

    # ---------------------- Personal Assistant service handlers ----------------------
    async def _handle_get_contact_info(self, call_id, args):
        """Handle get_contact_info function call for Mahdi Meshkani's assistant. IMPORTANT: Phone number is NEVER provided - only email."""
        contact_type = args.get("contact_type", "direct")
        topic = args.get("topic")
        
        logging.info(f"FLOW tool: Get contact info - type={contact_type}, topic={topic}")
        
        if contact_type == "direct":
            # Only email for direct contact
            output = {
                "email": "Mahdi.meshkani@gmail.com",
                "message": "حتماً. بهترین مسیر ارتباط مستقیم با مهدی ایمیلشه: 📧 Mahdi.meshkani@gmail.com اگه یکی‌دو خط بگی موضوعت چیه، کمک می‌کنه مکالمه‌تون سریع‌تر و دقیق‌تر پیش بره."
            }
        else:  # professional
            # Professional inquiries - still only email (phone is NEVER provided)
            output = {
                "email": "Mahdi.meshkani@gmail.com",
                "message": "این موضوع دقیقاً در حوزه کاریشه. می‌تونی مستقیم براش بنویسی: 📧 Mahdi.meshkani@gmail.com ایمیلات رو با دقت بررسی می‌کنه."
            }
        
        await self._send_function_output(call_id, output)

    async def _handle_get_resume_info(self, call_id, args):
        """Handle get_resume_info function call for Mahdi Meshkani's assistant."""
        section = args.get("section", "full")
        
        logging.info(f"FLOW tool: Get resume info - section={section}")
        
        # Get resume data from DID config
        custom_context = self.did_config.get('custom_context', {}) if self.did_config else {}
        resume_data = custom_context.get('resume_summary', {})
        mahdi_info = custom_context.get('mahdi_info', {})
        
        output = {}
        
        if section == "full" or not section:
            # Full resume summary
            output = {
                "name": mahdi_info.get("name", "مهدی مشکانی"),
                "title": mahdi_info.get("title", "کارآفرین خلاق و مدیر هنری"),
                "experience": resume_data.get("experience", ""),
                "achievements": resume_data.get("achievements", []),
                "education": resume_data.get("education", []),
                "memberships": resume_data.get("memberships", []),
                "skills": resume_data.get("skills", []),
                "message": "بذار یه تصویر واقعی از مهدی بهت بدم—نه فقط عنوان شغلی، بلکه مسیری که خودش با دست‌های خودش ساخته..."
            }
        elif section == "experience":
            output = {
                "section": "experience",
                "content": resume_data.get("experience", ""),
                "message": "مهدی بیش از ۲۰ سال تجربه داره از طراحی گرافیک تا مدیریت نشر، از تبلیغات تا سلامت روان دیجیتال."
            }
        elif section == "education":
            output = {
                "section": "education",
                "content": resume_data.get("education", []),
                "message": "تحصیلات مهدی شامل DBA مدیریت کسب‌وکار، کارشناسی ارشد روانشناسی، و کارشناسی مدیریت تبلیغات تجاری است."
            }
        elif section == "achievements":
            output = {
                "section": "achievements",
                "content": resume_data.get("achievements", []),
                "message": "برخی از دستاوردهای مهدی شامل خلق اولین کتاب رنگ‌آمیزی بزرگسالان در ایران و راه‌اندازی اولین مجله تبلیغاتی با واقعیت افزوده است."
            }
        elif section == "skills":
            output = {
                "section": "skills",
                "content": resume_data.get("skills", []),
                "message": "مهارت‌های مهدی شامل پلتفرمهای طراحی گرافیک، برندینگ، بازاریابی دیجیتال، و زبان انگلیسی حرفه‌ای است."
            }
        
        await self._send_function_output(call_id, output)

    async def _handle_send_resume_pdf(self, call_id, args):
        """Handle send_resume_pdf function call - automatically sends resume PDF link via SMS to caller's number."""
        # Always use caller's phone number - no need to ask
        phone_number = self.call.from_number
        
        logging.info(f"FLOW tool: Send resume PDF - automatically sending to caller phone: {phone_number}")
        
        if not phone_number:
            output = {
                "success": False,
                "error": "شماره تماس در دسترس نیست. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
            await self._send_function_output(call_id, output)
            return
        
        # Normalize phone number
        normalized_phone = normalize_phone_number(phone_number)
        
        if not normalized_phone:
            output = {
                "success": False,
                "error": "شماره تماس معتبر نیست. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
            await self._send_function_output(call_id, output)
            return
        
        # Send resume PDF link via SMS
        resume_link = "https://mahdi-meshkani.com/resume.pdf"  # Placeholder - replace with actual link
        sms_message = f"رزومه کامل مهدی مِشکانی در وبسایت mahdi-meshkani موجود است یا می‌تونید از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
        
        def _send_sms():
            return sms_service.send_sms(normalized_phone, sms_message)
        
        try:
            sms_result = await self.run_in_thread(_send_sms)
            if sms_result:
                output = {
                    "success": True,
                    "method": "sms",
                    "phone": normalized_phone,
                    "message": f"لینک دانلود رزومه به شماره شما ارسال شد."
                }
                logging.info(f"📱 Resume PDF link sent via SMS to {normalized_phone}")
            else:
                output = {
                    "success": False,
                    "error": "متأسفانه ارسال پیامک با مشکل مواجه شد. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
                }
        except Exception as e:
            logging.error(f"❌ Failed to send resume PDF SMS: {e}", exc_info=True)
            output = {
                "success": False,
                "error": "متأسفانه ارسال پیامک با مشکل مواجه شد. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
        
        await self._send_function_output(call_id, output)

    async def _handle_send_website_info(self, call_id, args):
        """Handle send_website_info function call - automatically sends website link via SMS to caller's number."""
        # Always use caller's phone number - no need to ask
        phone_number = self.call.from_number
        
        logging.info(f"FLOW tool: Send website info - automatically sending to caller phone: {phone_number}")
        
        if not phone_number:
            output = {
                "success": False,
                "error": "شماره تماس در دسترس نیست. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
            await self._send_function_output(call_id, output)
            return
        
        # Normalize phone number
        normalized_phone = normalize_phone_number(phone_number)
        
        if not normalized_phone:
            output = {
                "success": False,
                "error": "شماره تماس معتبر نیست. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
            await self._send_function_output(call_id, output)
            return
        
        # Get website from config
        website = "www.meshkani.pro"
        if self.did_config:
            custom_context = self.did_config.get('custom_context', {})
            mahdi_info = custom_context.get('mahdi_info', {})
            website = mahdi_info.get('website', 'www.meshkani.pro')
        
        # Send website link via SMS
        sms_message = f"🌐 اطلاعات و نمونه‌کارهای مهدی مشکانی:\n{website}\n\nبرای تماس مستقیم:\n📧 Mahdi.meshkani@gmail.com"
        
        def _send_sms():
            return sms_service.send_sms(normalized_phone, sms_message)
        
        try:
            sms_result = await self.run_in_thread(_send_sms)
            if sms_result:
                output = {
                    "success": True,
                    "method": "sms",
                    "phone": normalized_phone,
                    "website": website,
                    "message": f"لینک سایت به شماره شما ارسال شد."
                }
                logging.info(f"📱 Website info sent via SMS to {normalized_phone}")
            else:
                output = {
                    "success": False,
                    "error": "متأسفانه ارسال پیامک با مشکل مواجه شد. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
                }
        except Exception as e:
            logging.error(f"❌ Failed to send website info SMS: {e}", exc_info=True)
            output = {
                "success": False,
                "error": "متأسفانه ارسال پیامک با مشکل مواجه شد. لطفا از طریق ایمیل Mahdi.meshkani@gmail.com درخواست بدید."
            }
        
        await self._send_function_output(call_id, output)

    # ---------------------- lifecycle helpers ----------------------
    def terminate_call(self):
        """Marks call as terminated."""
        self.call.terminated = True
        logging.info("FLOW call: terminate_call set -> will close sockets")

    async def run_in_thread(self, func, *args):
        """Runs a blocking function in a thread"""
        return await asyncio.to_thread(func, *args)

    def drain_queue(self):
        """Drains the playback queue to avoid buffer bloat"""
        count = 0
        try:
            while self.queue.get_nowait():
                count += 1
        except Empty:
            if count > 0:
                logging.info("dropping %d packets", count)

    # ---------------------- Soniox wiring ----------------------
    async def _soniox_connect(self) -> bool:
        """Connect to Soniox STT service."""
        key = self.soniox_key if self.soniox_key and self.soniox_key != "SONIOX_API_KEY" else None
        if not key:
            return False
        try:
            self.soniox_ws = await connect(self.soniox_url)
            fmt, sr, ch = self._soniox_audio_format()
            init = {
                "api_key": key,
                "model": self.soniox_model,
                "audio_format": fmt,
                "sample_rate": sr,
                "num_channels": ch,
                "language_hints": self.soniox_lang_hints,
                "enable_speaker_diarization": self.soniox_enable_diar,
                "enable_language_identification": self.soniox_enable_lid,
                "enable_endpoint_detection": self.soniox_enable_epd,
                "language": "fa"
            }
            if hasattr(self, 'soniox_context_phrases') and self.soniox_context_phrases:
                try:
                    init["context_phrases"] = self.soniox_context_phrases
                except Exception:
                    pass
            
            await self.soniox_ws.send(json.dumps(init))
            
            try:
                confirmation = await asyncio.wait_for(self.soniox_ws.recv(), timeout=5.0)
                if isinstance(confirmation, (bytes, bytearray)):
                    return False
                conf_msg = json.loads(confirmation)
                if conf_msg.get("error_code"):
                    logging.error("Soniox init error: %s", conf_msg.get("error_message"))
                    return False
                return True
            except asyncio.TimeoutError:
                return True
            except Exception:
                return True
        except Exception as e:
            logging.error("Soniox connect failed: %s", e, exc_info=True)
            self.soniox_ws = None
            return False

    async def _soniox_keepalive_loop(self):
        """Keep Soniox alive across silences."""
        try:
            while self.soniox_ws and not self.call.terminated:
                await asyncio.sleep(self.soniox_keepalive_sec)
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "keepalive"}))
        except asyncio.CancelledError:
            pass

    async def _soniox_recv_loop(self):
        """Receive loop for Soniox STT."""
        if not self.soniox_ws:
            return
        try:
            async for raw in self.soniox_ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError as e:
                    logging.error("Failed to parse JSON: %s", e)
                    continue

                if msg.get("error_code"):
                    logging.error("Soniox error: %s", msg.get("error_message"))
                    continue

                if msg.get("finished"):
                    if self._soniox_flush_timer:
                        self._soniox_flush_timer.cancel()
                        self._soniox_flush_timer = None
                    await self._flush_soniox_segment()
                    break

                tokens = msg.get("tokens") or []
                if not tokens:
                    continue

                finals = [t.get("text", "") for t in tokens if t.get("is_final")]
                has_nonfinal = any(not t.get("is_final") for t in tokens)
                
                if finals:
                    final_text = "".join(finals)
                    logging.info("STT (final): %s", final_text)
                    # Filter out control tokens like <end>, <fin>, etc.
                    if final_text and final_text not in ["<end>", "<fin>", "<start>"]:
                        self._soniox_accum.append(final_text)
                        if self._soniox_flush_timer:
                            self._soniox_flush_timer.cancel()
                            self._soniox_flush_timer = None
                        # REAL-TIME: Flush immediately when final token received (no delay)
                        # This ensures bot responds immediately when user finishes speaking
                        if not has_nonfinal:
                            logging.info("FLOW STT: Final token received - flushing immediately (REAL-TIME)")
                            await self._flush_soniox_segment()
                    else:
                        logging.debug("FLOW STT: Ignoring control token: %s", final_text)

                if any(t.get("text") == "<fin>" for t in tokens):
                    logging.info("FLOW STT: <fin> token received, flushing immediately")
                    if self._soniox_flush_timer:
                        self._soniox_flush_timer.cancel()
                        self._soniox_flush_timer = None
                    await self._flush_soniox_segment()

        except Exception as e:
            logging.error("Soniox recv loop error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                if self.soniox_ws:
                    await self.soniox_ws.close()
            self.soniox_ws = None

    async def _delayed_flush_soniox_segment(self):
        """Delayed flush for Soniox segments."""
        try:
            delay = self.soniox_silence_duration_ms / 1000.0
            logging.info("FLOW STT: Delayed flush scheduled, waiting %.2f seconds", delay)
            await asyncio.sleep(delay)
            if self._soniox_flush_timer and not self._soniox_flush_timer.cancelled():
                logging.info("FLOW STT: Delayed flush timer expired, flushing segment")
                await self._flush_soniox_segment()
                self._soniox_flush_timer = None
            else:
                logging.debug("FLOW STT: Delayed flush cancelled or already flushed")
        except asyncio.CancelledError:
            logging.debug("FLOW STT: Delayed flush cancelled")
            pass
    
    def _correct_common_misrecognitions(self, text: str) -> str:
        """Correct common STT misrecognitions."""
        if not text:
            return text
        
        original_text = text
        corrected = text
        
        corrections = [
            (r'\bپرس\s*کوبیده\b', 'کباب کوبیده'),
            (r'(?<!کباب\s)\bکوبیده\b', 'کباب کوبیده'),
            (r'\bیه\s*پرس\s*چهل\s*و\s*شش\s*گیگ\b', 'یه پرس چلو ششلیک'),
            (r'\bیک\s*پرس\s*چهل\s*و\s*شش\s*گیگ\b', 'یک پرس چلو ششلیک'),
            (r'\bیه\s*پرس\s*۴۶\s*گیگ\b', 'یه پرس ششلیک'),
            (r'\bیک\s*پرس\s*۴۶\s*گیگ\b', 'یک پرس ششلیک'),
            (r'\bیه\s*پرس\s*۶۱\b', 'یه پرس ششلیک'),
            (r'\bیک\s*پرس\s*۶۱\b', 'یک پرس ششلیک'),
            (r'\bچهل\s*و\s*شش\s*گیگ\b', 'چلو ششلیک'),
            (r'\bچهار\s*صد\s*و\s*شصت\s*و\s*یک\b', 'چلو ششلیک'),
            (r'\b۴۶۱\b', 'چلو ششلیک'),
            (r'\b۴۶\s*گیگ\b', 'ششلیک'),
            (r'\bشصت\s*و\s*یک\b', 'ششلیک'),
            (r'\b۶۱\b', 'ششلیک'),
        ]
        
        for pattern, replacement in corrections:
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
        
        if corrected != original_text:
            logging.info("STT correction: '%s' -> '%s'", original_text, corrected)
        
        return corrected
    
    async def _flush_soniox_segment(self):
        """Forward finalized transcript to OpenAI."""
        if not self._soniox_accum:
            return
        text = "".join(self._soniox_accum).strip()
        self._soniox_accum.clear()
        if not text:
            return
        
        corrected_text = self._correct_common_misrecognitions(text)
        logging.info("FLOW STT: Final transcript: '%s' (length: %d)", corrected_text, len(corrected_text))
        await self._send_user_text_to_openai(corrected_text)
    
    async def _send_user_text_to_openai(self, text: str):
        """Send user text to OpenAI."""
        # Clean the text - remove <end> marker if present
        cleaned_text = text.replace("<end>", "").strip()
        if not cleaned_text:
            logging.warning("FLOW TTS: Empty transcript after cleaning, skipping")
            return
            
        logging.info("FLOW TTS: Sending transcript to OpenAI: '%s'", cleaned_text)
        try:
            # Send user message
            user_msg = {
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": cleaned_text}]}
            }
            await self.ws.send(json.dumps(user_msg))
            logging.info("FLOW TTS: conversation.item.create sent for user message")
            
            # Trigger response
            response_msg = {
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }
            await self.ws.send(json.dumps(response_msg))
            logging.info("FLOW TTS: response.create sent - waiting for OpenAI response")
        except Exception as e:
            logging.error("FLOW TTS: Error forwarding transcript to OpenAI: %s", e, exc_info=True)

    # ---------------------- audio ingress ----------------------
    async def send(self, audio):
        """Primary audio path: RTP bytes -> Soniox; (opt) also to OpenAI."""
        if self.call.terminated:
            return

        processed_audio = self._process_audio_for_soniox(audio)
        
        try:
            if self.soniox_ws:
                await self.soniox_ws.send(processed_audio)
            elif self._fallback_whisper_enabled and self.ws:
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
        except ConnectionClosedError:
            self.soniox_ws = None
            logging.error("Soniox connection lost")
        except Exception as e:
            if "closed" in str(e).lower() or "ConnectionClosed" in str(type(e)):
                self.soniox_ws = None
                logging.error("Soniox connection error")

        if self.forward_audio_to_openai and self.ws:
            try:
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
            except Exception:
                pass

    # ---------------------- shutdown ----------------------
    async def close(self):
        """Close Soniox first, then OpenAI."""
        logging.info("FLOW close: closing sockets (Soniox → OpenAI)")

        # Cancel background tasks
        for t in (self.soniox_keepalive_task, self.soniox_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        # Close Soniox first
        try:
            if self.soniox_ws:
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "finalize"}))
                await self.soniox_ws.close()
                logging.info("FLOW close: Soniox WS closed")
        finally:
            self.soniox_ws = None

        # Then close OpenAI
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
            logging.info("FLOW close: OpenAI WS closed")
