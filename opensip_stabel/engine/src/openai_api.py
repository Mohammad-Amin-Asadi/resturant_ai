#!/usr/bin/env python
"""OpenAI Realtime + Soniox RT bridge for Persian STT."""

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

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

BACKEND_SERVER_URL = os.getenv("BACKEND_SERVER_URL", "http://localhost:8000")

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(message)s"
)

OPENAI_API_MODEL = "gpt-realtime-2025-08-28"
OPENAI_URL_FORMAT = "wss://api.openai.com/v1/realtime?model={}"


class OpenAI(AIEngine):
    """OpenAI Realtime client using Soniox for STT."""

    def __init__(self, call, cfg):
        self.codec = self.choose_codec(call.sdp)
        self.queue = call.rtp
        self.call = call
        self.ws = None
        self.session = None

        did_number = getattr(call, 'did_number', None)
        did_config = {}
        
        if did_number:
            did_config = load_did_config(did_number)
            if did_config:
                logging.info("DID config loaded: %s", did_config.get('restaurant_name', 'Unknown'))
            else:
                logging.warning("No DID config for %s, using default", did_number)
        else:
            logging.warning("No DID number available, using default config")

        base_cfg = Config.get("openai", cfg)
        merged_cfg_dict = dict(base_cfg)
        if did_config:
            if 'openai' in did_config:
                merged_cfg_dict.update(did_config['openai'])
            for key in ['model', 'voice', 'temperature', 'welcome_message', 'intro']:
                if key in did_config:
                    merged_cfg_dict[key] = did_config[key]
        
        class MergedConfigSection:
            def __init__(self, base_section, did_overrides):
                self._base = base_section
                self._overrides = did_overrides
                
            def get(self, option, env=None, fallback=None):
                # Check DID overrides first
                if isinstance(option, list):
                    for opt in option:
                        if opt in self._overrides:
                            return self._overrides[opt]
                    # Try base config
                    return self._base.get(option, env, fallback)
                else:
                    if option in self._overrides:
                        return self._overrides[option]
                    # Try base config
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
        self.did_config = did_config
        self.did_number = did_number
        
        backend_url = BACKEND_SERVER_URL
        if did_config and 'backend_url' in did_config:
            backend_url = did_config['backend_url']
        self.api = API(backend_url)
        db_path = self.cfg.get("db_path", "OPENAI_DB_PATH", "./src/data/app.db")
        self.db = WalletMeetingDB(db_path)

        self.model = self.cfg.get("model", "OPENAI_API_MODEL", OPENAI_API_MODEL)
        self.timezone = self.cfg.get("timezone", "OPENAI_TZ", "Asia/Tehran")
        self.url = self.cfg.get("url", "OPENAI_URL", OPENAI_URL_FORMAT.format(self.model))
        self.key = self.cfg.get(["key", "openai_key"], "OPENAI_API_KEY")
        self.voice = self.cfg.get(["voice", "openai_voice"], "OPENAI_VOICE", "alloy")
        self.intro = self.cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", ". سلام و درودبرشما،با رستوران بزرگمهر تماس گرفته اید . لطفا سفارشتون رو بفرمایید تا ثبت کنم. ")
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        self.temp_order_data = {}
        self.user_mentioned_items = []
        self.customer_name_from_history = None
        self.recent_order_ids = set()
        self.last_order_time = None

        if self.codec.name == "mulaw":
            self.codec_name = "g711_ulaw"
        elif self.codec.name == "alaw":
            self.codec_name = "g711_alaw"
        elif self.codec.name == "opus":
            self.codec_name = "opus"
        else:
            self.codec_name = "g711_ulaw"

        base_soniox_cfg = Config.get("soniox", cfg)
        soniox_overrides = {}
        if did_config and 'soniox' in did_config:
            soniox_overrides = did_config['soniox']
        
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
        
        default_context_phrases = [
            "کباب", "پرس کوبیده","کوبیده", "جوجه", "مرغ", "ته چین", "ته‌چین", "نوشابه", "کوکا", "فانتا", "خانواده",
            "دوغ", "عالیس", "قوطی", "شیشه", "بطری", "قیمه", "خورش", "چلو", "برگ", "سلطانی",
            "شیشلیک", "ششلیک", "چلو ششلیک", "چلو شیشلیک", "کباب شیشلیک", "کباب ششلیک", 
            "پرس ششلیک", "پرس شیشلیک", "یه پرس ششلیک", "یک پرس ششلیک",
            "ترش", "گیلانی", "تبریزی", "اردبیلی", "مصری", "بره", "میگو", "ماهی",
            "پیتزا", "همبرگر", "چیزبرگر", "سیب زمینی", "پاستا", "سالاد", "سزار", "ماست",
            "نیمرو", "املت", "تخم مرغ", "سوسیس", "هات داگ", "کره", "پنیر", "مربا",
            "یک", "دو", "سه", "چهار", "پنج", "کوچک", "بزرگ", "خانواده", "مخصوص",
            "بدون", "گوجه", "خیارشور", "پیاز", "برشته", "خوب", "پرس"
        ]
        
        if self.did_config:
            custom_context = self.did_config.get('custom_context', {})
            menu_items = custom_context.get('menu_items', [])
            if menu_items:
                self.soniox_context_phrases = list(set(menu_items + default_context_phrases))
            else:
                self.soniox_context_phrases = default_context_phrases
        else:
            self.soniox_context_phrases = default_context_phrases
        
        self._soniox_audio_buffer = b''
        self.soniox_ws = None
        self.soniox_task = None
        self.soniox_keepalive_task = None
        self._soniox_accum = []
        self._soniox_flush_timer = None
        self.soniox_silence_duration_ms = int(self.soniox_cfg.get("silence_duration_ms", "SONIOX_SILENCE_DURATION_MS", 500))
        self._order_confirmed = False
        self.forward_audio_to_openai = bool(self.soniox_cfg.get("forward_audio_to_openai", "FORWARD_AUDIO_TO_OPENAI", False))
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
        # Prefer Opus first (48kHz high quality), then G.711
        priority = ["opus", "pcma", "pcmu"]
        cmap = {c.name.lower(): c for c in codecs}
        for codec_name in priority:
            if codec_name in cmap:
                codec = CODECS[codec_name](cmap[codec_name])
                # For Opus, prefer 48kHz sample rate
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
        """Returns the corresponding audio format string for OpenAI Realtime API.
        OpenAI only supports G.711, so we always return G.711 format even if we use Opus for Soniox."""
        # OpenAI Realtime API only supports G.711 (g711_ulaw or g711_alaw)
        # Even if we use Opus for better Soniox quality, OpenAI needs G.711
        if self.codec_name == "opus":
            # If Opus is selected, we'll need to convert to G.711 for OpenAI
            # Default to ulaw for compatibility
            return "g711_ulaw"
        return self.codec_name

    def _soniox_audio_format(self):
        """Map RTP codec to Soniox raw input config. Prefers PCM at 16kHz for better quality."""
        # If we have Opus at 48kHz, use it directly
        if self.codec.name == "opus" and self.codec.sample_rate == 48000:
            return ("pcm_s16le", 48000, 1)
        # For G.711, we'll convert to PCM and upsample to 16kHz
        # Soniox will receive PCM at 16kHz instead of G.711 at 8kHz
        if self.soniox_upsample:
            return ("pcm_s16le", 16000, 1)
        # Fallback: use original format
        if self.codec_name == "g711_ulaw":
            return ("mulaw", 8000, 1)
        if self.codec_name == "g711_alaw":
            return ("alaw", 8000, 1)
        return ("pcm_s16le", 16000, 1)
    
    def _convert_g711_to_pcm16(self, audio_data, is_ulaw=True):
        """Convert G.711 (μ-law or A-law) to 16-bit PCM."""
        try:
            if is_ulaw:
                # Convert μ-law to linear PCM
                pcm = audioop.ulaw2lin(audio_data, 2)  # 2 bytes per sample (16-bit)
            else:
                # Convert A-law to linear PCM
                pcm = audioop.alaw2lin(audio_data, 2)  # 2 bytes per sample (16-bit)
            return pcm
        except Exception as e:
            logging.error("FLOW audio: G.711 conversion error: %s", e)
            return audio_data
    
    def _upsample_audio(self, pcm_data, from_rate=8000, to_rate=16000):
        """Upsample PCM audio from one sample rate to another using linear interpolation."""
        if from_rate == to_rate:
            return pcm_data
        
        if not HAS_NUMPY:
            # Simple linear interpolation without numpy
            # Convert bytes to samples (16-bit = 2 bytes per sample)
            num_samples = len(pcm_data) // 2
            ratio = to_rate / from_rate
            new_num_samples = int(num_samples * ratio)
            
            # Convert to list of samples
            samples = []
            for i in range(num_samples):
                idx = i * 2
                sample = int.from_bytes(pcm_data[idx:idx+2], byteorder='little', signed=True)
                samples.append(sample)
            
            # Linear interpolation
            new_samples = []
            for i in range(new_num_samples):
                pos = i / ratio
                idx = int(pos)
                frac = pos - idx
                
                if idx >= num_samples - 1:
                    new_samples.append(samples[-1])
                else:
                    # Linear interpolation
                    sample = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
                    new_samples.append(sample)
            
            # Convert back to bytes
            result = b''.join(s.to_bytes(2, byteorder='little', signed=True) for s in new_samples)
            return result
        else:
            # Use numpy for better quality resampling
            # Convert bytes to numpy array
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            # Linear interpolation
            num_samples = len(samples)
            ratio = to_rate / from_rate
            new_num_samples = int(num_samples * ratio)
            
            # Create indices for interpolation
            indices = np.linspace(0, num_samples - 1, new_num_samples)
            # Linear interpolation
            new_samples = np.interp(indices, np.arange(num_samples), samples)
            # Convert back to int16 and then to bytes
            new_samples = new_samples.astype(np.int16)
            return new_samples.tobytes()
    
    def _process_audio_for_soniox(self, audio_data):
        """Process audio for Soniox: convert G.711 to PCM and upsample if needed."""
        if not self.soniox_upsample:
            return audio_data
        
        # If we're using Opus, audio is already high quality
        if self.codec.name == "opus":
            # Opus audio might need conversion depending on format
            # For now, assume it's already in good format
            return audio_data
        
        # Convert G.711 to PCM
        is_ulaw = (self.codec_name == "g711_ulaw")
        pcm_8k = self._convert_g711_to_pcm16(audio_data, is_ulaw)
        
        # Upsample from 8kHz to 16kHz
        pcm_16k = self._upsample_audio(pcm_8k, from_rate=8000, to_rate=16000)
        
        return pcm_16k

    # ---------------------- order checking helpers ----------------------
    async def _check_undelivered_order(self, phone_number):
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

    def _format_items_list_persian(self, items):
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

    def _get_scenario_config(self, scenario_type):
        if not self.did_config:
            return {}
        scenarios = self.did_config.get('scenarios', {})
        return scenarios.get(scenario_type, {})
    
    def _get_function_definitions(self):
        default_functions = [
            {"type": "function", "name": "terminate_call",
             "description": "ONLY call this function when the USER explicitly says they want to end the call. "
                            "Examples: 'خداحافظ', 'بای', 'تماس رو قطع کن', 'تماس رو پایان بده', 'خداحافظی', 'خداحافظی می‌کنم'. "
                            "DO NOT call this if: user is silent, user says '.', user pauses, or you just finished talking. "
                            "ONLY call when user EXPLICITLY requests to end the call. "
                            "Always say a friendly goodbye first, then call this function.",
             "parameters": {"type": "object", "properties": {}, "required": []}},
            {"type": "function", "name": "transfer_call",
             "description": "call the function if a request was received to transfer a call with an operator, a person",
             "parameters": {"type": "object", "properties": {}, "required": []}},
            {
                "type": "function",
                "name": "track_order",
                "description": "پیگیری سفارش بر اساس شماره تلفن. شماره تلفن خودکار است.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone_number": {"type": "string", "description": "شماره تلفن مشتری برای پیگیری سفارش (اختیاری - اگر ارائه نشود از شماره تماس‌گیرنده استفاده می‌شود)"},
                    },
                    "required": [],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "get_menu_specials",
                "description": "دریافت پیشنهادات ویژه رستوران.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "search_menu_item",
                "description": "جستجوی غذا در منو. اگر نام دقیق موجود نبود، نزدیک‌ترین را پیدا می‌کند.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_name": {"type": "string", "description": "نام غذا یا کلمه کلیدی برای جستجو"},
                        "category": {"type": "string", "description": "دسته‌بندی غذا (اختیاری): غذای ایرانی، نوشیدنی، فست فود، سینی ها، صبحانه، پیش غذا", "nullable": True},
                    },
                    "required": ["item_name"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "create_order",
                "description": "ثبت سفارش نهایی. فقط یکبار در آخر تماس و بعد از مرور و تایید کاربر. قبل از صدا زدن: customer_name و address موجود، items خالی نیست، همه غذاها و تعدادها درست، notes ثبت شده. شماره تلفن خودکار است.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string", "description": "نام مشتری (الزامی - نباید خالی باشد)"},
                        "phone_number": {"type": "string", "description": "شماره تلفن مشتری (اختیاری - به صورت خودکار از تماس گرفته می‌شود)"},
                        "address": {"type": "string", "description": "آدرس تحویل سفارش (الزامی - نباید خالی باشد)"},
                        "items": {
                            "type": "array",
                            "description": "لیست آیتم‌های سفارش شامل نام غذا و تعداد (الزامی - نباید خالی باشد، باید حداقل یک غذا داشته باشد). خیلی مهم: 1) همه غذاهایی که کاربر گفت باید در این لیست باشند، 2) تعداد (quantity) هر غذا باید دقیقا همان باشد که کاربر گفت (اگر گفت 'دو' یا 'دو تا' باید 2 باشد، اگر گفت 'سه' یا 'سه تا' باید 3 باشد). 3) برای کباب کوبیده: اگر کاربر گفت 'یک کوبیده' یا 'یک کباب کوبیده' یا 'یک پرس کوبیده'، quantity=1 ثبت کن. اگر گفت 'دو کوبیده' یا 'دو کباب کوبیده' یا 'دو پرس کوبیده'، quantity=2 ثبت کن. هیچ وقت از کاربر نپرس چند سیخ می‌خواهد.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item_name": {"type": "string", "description": "نام دقیق غذا از منو"},
                                    "quantity": {"type": "integer", "description": "تعداد دقیق غذا - باید دقیقا همان باشد که کاربر گفت (اگر گفت 'یک ته‌چین مرغ' باید quantity=1 باشد، اگر گفت 'دو کباب' باید quantity=2 باشد، اگر گفت 'سه' یا 'سه تا' باید 3 باشد). خیلی مهم: اگر کاربر تعداد را در همان جمله اول گفت (مثلا 'یک ته‌چین مرغ میخام' یا 'یک کوبیده' یا 'یک کباب کوبیده')، از همان عدد استفاده کن و دیگر از او تعداد نپرس. برای کباب کوبیده: اگر گفت 'یک کوبیده' = quantity=1، 'دو کوبیده' = quantity=2. هیچ وقت درباره سیخ نپرس. اگر کاربر تعداد نگفت، مقدار پیش‌فرض 1 است.", "minimum": 1, "default": 1}
                                },
                                "required": ["item_name", "quantity"],
                            }
                        },
                        "notes": {"type": "string", "description": "اگر کاربر درخواست خاصی درباره سفارش داد، حتما در این فیلد ثبت کن.", "nullable": True},
                    },
                    "required": ["customer_name", "address", "items"],
                    "additionalProperties": False
                }
            },
        ]
        
        if self.did_config and 'functions' in self.did_config:
            custom_functions = self.did_config['functions']
            if isinstance(custom_functions, list):
                return custom_functions
            elif isinstance(custom_functions, dict):
                function_map = {f['name']: f for f in default_functions}
                for func in custom_functions.values():
                    if isinstance(func, dict) and 'name' in func:
                        function_map[func['name']] = func
                return list(function_map.values())
        
        return default_functions
    
    def _build_welcome_message(self, has_undelivered_order, orders=None):
        restaurant_name = self.did_config.get('restaurant_name', 'رستوران بزرگمهر') if self.did_config else 'رستوران بزرگمهر'
        welcome_config = self._get_scenario_config('has_orders' if has_undelivered_order else 'new_customer')
        welcome_templates = welcome_config.get('welcome_templates', {})
        
        if self.customer_name_from_history:
            base_greeting_template = welcome_templates.get('with_customer_name', 
                "سلام و درودبرشما {customer_name} عزیز، با {restaurant_name} تماس گرفته‌اید")
            base_greeting = base_greeting_template.format(
                customer_name=self.customer_name_from_history,
                restaurant_name=restaurant_name
            )
        else:
            base_greeting_template = welcome_templates.get('without_customer_name',
                "سلام و درودبرشما، با {restaurant_name} تماس گرفته‌اید")
            base_greeting = base_greeting_template.format(restaurant_name=restaurant_name)
        
        if has_undelivered_order and orders:
            order_details_list = []
            for order in orders:
                status_display = order.get('status_display', '')
                address = order.get('address', '')
                items = order.get('items', [])
                order_status = order.get('status', '')
                items_text = self._format_items_list_persian(items)
                
                if order_status == 'preparing':
                    status_text = f"{status_display} توسط رستوران است"
                else:
                    status_text = f"{status_display} است"
                
                if items_text:
                    if address:
                        order_detail = f"سفارش شما {items_text}، به مقصد {address} ثبت شده بود {status_text}"
                    else:
                        order_detail = f"سفارش شما {items_text} ثبت شده بود {status_text}"
                else:
                    if address:
                        order_detail = f"سفارش شما به مقصد {address} ثبت شده بود {status_text}"
                    else:
                        order_detail = f"سفارش شما ثبت شده بود {status_text}"
                
                order_details_list.append(order_detail)
            
            if len(order_details_list) == 1:
                orders_text = order_details_list[0]
            else:
                orders_text = "، ".join(order_details_list[:-1]) + f" و همچنین {order_details_list[-1]}"
            
            closing_message = welcome_templates.get('closing_with_orders',
                " از صبر و شکیبایی شما متشکریم. اگر امر دیگری هست در خدمت شما هستم.")
            return f"{base_greeting}، {orders_text}.{closing_message}"
        else:
            new_customer_message = welcome_templates.get('new_customer_question', " لطفا سفارشتون رو بفرمایید تا ثبت کنم.")
            return f"{base_greeting}.{new_customer_message}"

    def _build_customized_instructions(self, has_undelivered_order, orders=None):
        if self.did_config:
            base_instructions_template = self.did_config.get('instructions_base',
                "شما دستیار هوشمند رستوران هستید. "
                "فقط فارسی صحبت کنید. لحن: گرم، پرانرژی، مودب، حرفه‌ای. "
                "از 'شما' استفاده کنید، نه 'تو'. به جنسیت اشاره نکنید (آقا/خانم). "
                "{name_instruction}"
                "شماره تلفن خودکار است، نپرسید. "
                "اگر کلمه‌ای شبیه نام غذا بود، آن را پیشنهاد دهید (مثلا: 'کووید' → 'کوبیده فرمودید؟'). "
                "مهم: 'ششلیک' یا 'شیشلیک' یک نوع غذا است (کباب ششلیک)، نه عدد 61 (شصت و یک). "
                "همچنین 'چلو ششلیک' یا 'چلو شیشلیک' یک غذا است، نه عدد 461. "
                "اگر کاربر گفت 'ششلیک' یا 'شیشلیک'، منظورشان غذا است نه عدد. "
                "اگر کاربر گفت '۴۶ گیگ'، 'چهل و شش گیگ'، 'شصت و یک'، یا '۶۱' در متن سفارش غذا، "
                "احتمالا منظورشان 'ششلیک' یا 'چلو ششلیک' است. همیشه 'ششلیک' را به عنوان غذا در نظر بگیرید نه عدد. "
                "خیلی مهم - استخراج تعداد: وقتی کاربر می‌گوید 'یک ته‌چین مرغ میخام' یا 'دو کباب میخوام'، تعداد را از همان جمله استخراج کن. "
                "اگر کاربر تعداد را در همان جمله گفت، دیگر از او تعداد نپرس. "
                "خیلی مهم - ممنوعیت تکرار: به هیچ عنوان و در هیچ مرحله‌ای چیزی که کاربر گفت را تکرار نکن. "
                "هیچ وقت نگو 'پس شما یک کباب کوبیده می‌خوای' یا 'پس سفارش شما اینه' یا 'پس شما گفتید' یا هر جمله مشابهی که چیزی که کاربر گفت را دوباره می‌گوید. "
                "بعد از هر پاسخ کاربر (غذا، آدرس، نام، و غیره)، فقط به مرحله بعدی برو و سوال بعدی را بپرس. هیچ تکرار، تاکید، یا تاییدی نکن. "
                "فقط در آخر (قبل از ثبت) یکبار کل سفارش را مرور کن و بعد از تایید کاربر فقط ثبت را انجام بده. "
                "خیلی مهم - کباب کوبیده: وقتی کاربر کباب کوبیده سفارش داد (مثلا 'یک کباب کوبیده'، 'دو کباب کوبیده'، 'یک کوبیده'، 'دو کوبیده'، 'یک پرس کوبیده'، 'دو پرس کوبیده')، "
                "هیچ وقت از او نپرس 'چند تا کباب کوبیده می‌خوای' یا 'چند سیخ کباب کوبیده می‌خوای'. "
                "فقط همان تعداد کباب کوبیده‌ای که گفته را مستقیماً ثبت کن (اگر گفت 'یک کوبیده' = یک کباب کوبیده، اگر گفت 'دو کوبیده' = دو کباب کوبیده). "
                "مطلقاً از پرسیدن درباره تعداد یا سیخ خودداری کن و بلافاصله به مرحله بعدی (آدرس) برو. "
                "مهم: هیچ وقت شماره سفارش (order ID) را به کاربر نگو. فقط وضعیت و جزئیات سفارش را بگو. "
                "تماس را فقط با صراحت کاربر قطع کنید (خداحافظ، بای، قطع کن). سکوت به معنای پایان نیست.")
        else:
            base_instructions_template = (
                "شما دستیار هوشمند رستوران بزرگمهر هستید. "
                "فقط فارسی صحبت کنید. لحن: گرم، پرانرژی، مودب، حرفه‌ای. "
                "از 'شما' استفاده کنید، نه 'تو'. به جنسیت اشاره نکنید (آقا/خانم). "
                "{name_instruction}"
                "شماره تلفن خودکار است، نپرسید. "
                "اگر کلمه‌ای شبیه نام غذا بود، آن را پیشنهاد دهید (مثلا: 'کووید' → 'کوبیده فرمودید؟'). "
                "خیلی مهم - استخراج تعداد: وقتی کاربر می‌گوید 'یک ته‌چین مرغ میخام' یا 'دو کباب میخوام'، تعداد را از همان جمله استخراج کن. "
                "اگر کاربر تعداد را در همان جمله گفت، دیگر از او تعداد نپرس. "
                "خیلی مهم - ممنوعیت تکرار: به هیچ عنوان و در هیچ مرحله‌ای چیزی که کاربر گفت را تکرار نکن. "
                "هیچ وقت نگو 'پس شما یک کباب کوبیده می‌خوای' یا 'پس سفارش شما اینه' یا 'پس شما گفتید' یا هر جمله مشابهی که چیزی که کاربر گفت را دوباره می‌گوید. "
                "بعد از هر پاسخ کاربر (غذا، آدرس، نام، و غیره)، فقط به مرحله بعدی برو و سوال بعدی را بپرس. هیچ تکرار، تاکید، یا تاییدی نکن. "
                "فقط در آخر (قبل از ثبت) یکبار کل سفارش را مرور کن و بعد از تایید کاربر فقط ثبت را انجام بده. "
                "خیلی مهم - کباب کوبیده: وقتی کاربر کباب کوبیده سفارش داد (مثلا 'یک کباب کوبیده'، 'دو کباب کوبیده'، 'یک کوبیده'، 'دو کوبیده'، 'یک پرس کوبیده'، 'دو پرس کوبیده')، "
                "هیچ وقت از او نپرس 'چند تا کباب کوبیده می‌خوای' یا 'چند سیخ کباب کوبیده می‌خوای'. "
                "فقط همان تعداد کباب کوبیده‌ای که گفته را مستقیماً ثبت کن (اگر گفت 'یک کوبیده' = یک کباب کوبیده، اگر گفت 'دو کوبیده' = دو کباب کوبیده). "
                "مطلقاً از پرسیدن درباره تعداد کوبیده یا کباب کوبیده اگر خودش اعلام کرده بود خودداری کن و بلافاصله به مرحله بعدی (آدرس) برو. "
                "مهم: هیچ وقت شماره سفارش (order ID) را به کاربر نگو. فقط وضعیت و جزئیات سفارش را بگو. "
                "تماس را فقط با صراحت کاربر قطع کنید (خداحافظ، بای، قطع کن). سکوت به معنای پایان نیست."
            )
        
        # Add customer name instruction if available
        name_instruction = ""
        if self.customer_name_from_history:
            name_instruction = f"مهم: نام مشتری ({self.customer_name_from_history}) از سفارشات قبلی در دسترس است. نیازی به پرسیدن نام نیست و از نام موجود استفاده کن. "
        else:
            name_instruction = "اگر مشتری قبلا سفارش نداده، نام مشتری را بپرس. "
        
        # Format base instructions with name instruction
        base_instructions = base_instructions_template.format(name_instruction=name_instruction)
        
        # Get scenario configuration
        scenario_config = self._get_scenario_config('has_orders' if has_undelivered_order else 'new_customer')
        
        if has_undelivered_order and orders and len(orders) > 0:
            # Scenario 1: Caller has undelivered order(s)
            orders_count = len(orders)
            
            # Get scenario instructions template from config
            if orders_count == 1:
                order = orders[0]
                order_status = order.get('status', '')
                order_id = order.get('id', '')
                status_display = order.get('status_display', '')
                
                # Try to get template from config
                template = scenario_config.get('single_order_template',
                    "مشتری سفارش ({status_display}) دارد. "
                    "1) وضعیت را تایید کنید و بپرسید سوالی دارند. "
                    "2) برای سفارش جدید به سناریوی ثبت بروید. "
                    "3) برای بررسی مجدد از track_order استفاده کنید. "
                    "4) درباره زمان/جزئیات پاسخ دهید. "
                    "مهم: هیچ وقت شماره سفارش را به کاربر نگو.")
                
                # Use safe replacement to avoid KeyError with other curly braces
                scenario_instructions = template.replace("{status_display}", str(status_display))
            else:
                # Multiple orders
                order_ids = [str(o.get('id', '')) for o in orders]
                
                # Try to get template from config
                template = scenario_config.get('multiple_orders_template',
                    "مشتری {orders_count} سفارش تحویل نشده دارد. "
                    "1) وضعیت را تایید کنید و بپرسید سوالی دارند. "
                    "2) برای سفارش جدید به سناریوی ثبت بروید. "
                    "3) برای بررسی مجدد از track_order استفاده کنید. "
                    "4) درباره زمان/جزئیات پاسخ دهید. "
                    "مهم: هیچ وقت شماره سفارش را به کاربر نگو.")
                
                # Use safe replacement to avoid KeyError with other curly braces
                scenario_instructions = template.replace("{orders_count}", str(orders_count))
            
            # Add status-specific guidance for latest order
            latest_order = orders[0]
            order_status = latest_order.get('status', '')
            
            # Get status-specific messages from config
            status_messages = scenario_config.get('status_messages', {})
            
            if order_status in ['pending', 'confirmed']:
                status_msg = status_messages.get('pending',
                    "نکته: سفارش در حال تایید یا تایید شده است. به مشتری اطمینان بده که سفارش در حال آماده شدن است. ")
                scenario_instructions += status_msg
            elif order_status == 'preparing':
                status_msg = status_messages.get('preparing',
                    "نکته: سفارش در حال آماده سازی است. به مشتری بگو که به زودی آماده می‌شود. ")
                scenario_instructions += status_msg
            elif order_status == 'on_delivery':
                status_msg = status_messages.get('on_delivery',
                    "نکته: سفارش به پیک تحویل داده شده و در راه است. به مشتری بگو که به زودی به دستش می‌رسد. ")
                scenario_instructions += status_msg
            
        else:
            # Scenario 2: Caller has no undelivered orders (new customer or all orders delivered)
            # Get new customer scenario template from config
            template = scenario_config.get('new_order_template',
                "وظیفه: دریافت سفارش جدید. "
                "{name_instruction}"
                "مراحل: 1) پیشنهادات ویژه (get_menu_specials) اگر درخواست شد. "
                "2) غذاها را بگیرید؛ اگر موجود نبود با search_menu_item شبیه‌ترین را بیابید. "
                "3) آدرس را بگیرید. "
                "4) همه غذاها و تعدادها را ثبت کنید: 'یک کباب و دو دوغ' → [{item_name: 'کباب', quantity: 1}, {item_name: 'دوغ', quantity: 2}]. "
                "5) فقط یکبار در آخر تماس (قبل از ثبت) مرور و تایید بگیرید. "
                "6) وقتی همه اطلاعات کامل است و کاربر آماده ثبت است، فقط یکبار کل سفارش را مرور کنید: 'پس سفارش شما: دو کباب، سه دوغ - درست است؟' "
                "7) بعد از تایید کاربر ('بله'/'درسته'/'باشه'/'ثبت کن')، فقط create_order را صدا بزنید و هیچ چیز دیگری نگو. "
                "8) قبل از create_order: items خالی نیست، customer_name و address موجود، همه غذاها و تعدادها درست، notes ثبت شده. "
                "9) فقط یکبار create_order را صدا بزنید. "
                "خیلی مهم - ممنوعیت تکرار: به هیچ عنوان و در هیچ مرحله‌ای چیزی که کاربر گفت را تکرار نکن. "
                "هیچ وقت نگو 'پس شما یک کباب کوبیده می‌خوای' یا 'پس سفارش شما اینه' یا 'پس شما گفتید' یا هر جمله مشابهی که چیزی که کاربر گفت را دوباره می‌گوید. "
                "بعد از هر پاسخ کاربر (غذا، آدرس، نام، و غیره)، فقط به مرحله بعدی برو و سوال بعدی را بپرس. هیچ تکرار، تاکید، یا تاییدی نکن. "
                "فقط در آخر (قبل از ثبت) یکبار کل سفارش را مرور کن و بعد از تایید کاربر فقط ثبت را انجام بده. "
                "خیلی مهم - استخراج تعداد: وقتی کاربر می‌گوید 'یک ته‌چین مرغ میخام' یا 'دو کباب میخوام'، تعداد را از همان جمله استخراج کن (یک=1، دو=2، سه=3 و غیره). "
                "اگر کاربر تعداد را در همان جمله گفت، دیگر از او تعداد نپرس و همان عدد را استفاده کن. "
                "خیلی مهم - کباب کوبیده: وقتی کاربر کباب کوبیده سفارش داد (مثلا 'یک کباب کوبیده'، 'دو کباب کوبیده'، 'یک کوبیده'، 'دو کوبیده'، 'یک پرس کوبیده'، 'دو پرس کوبیده')، "
                "هیچ وقت از او نپرس 'چند تا کباب کوبیده می‌خوای' یا 'چند سیخ کباب کوبیده می‌خوای'. "
                "فقط همان تعداد کباب کوبیده‌ای که گفته را مستقیماً ثبت کن (اگر گفت 'یک کوبیده' = یک کباب کوبیده quantity=1، اگر گفت 'دو کوبیده' = دو کباب کوبیده quantity=2). "
                "مطلقاً از پرسیدن درباره تعداد یا سیخ خودداری کن و بلافاصله به مرحله بعدی (آدرس) برو. "
                "مهم: هیچ وقت شماره سفارش (order ID) را به کاربر نگو. فقط وضعیت و جزئیات سفارش را بگو.")
            
            name_instruction = ""
            if self.customer_name_from_history:
                name_instruction = f"نام ({self.customer_name_from_history}) موجود است. "
            else:
                name_instruction = "نام را بپرسید. "
            
            # Use safe formatting that only replaces {name_instruction}
            # Escape other curly braces in the template to avoid KeyError
            # First, escape all other { } except {name_instruction}
            import re
            # Replace {name_instruction} with a placeholder
            temp_placeholder = "___NAME_INSTRUCTION_PLACEHOLDER___"
            escaped_template = template.replace("{name_instruction}", temp_placeholder)
            # Escape all remaining curly braces
            escaped_template = escaped_template.replace("{", "{{").replace("}", "}}")
            # Restore {name_instruction}
            escaped_template = escaped_template.replace(temp_placeholder, "{name_instruction}")
            # Now format safely
            scenario_instructions = escaped_template.format(name_instruction=name_instruction)
        
        return base_instructions + " " + scenario_instructions

    # ---------------------- session start ----------------------
    async def start(self):
        logging.info("NEW CALL - connecting OpenAI WS")
        openai_headers = {"Authorization": f"Bearer {self.key}", "OpenAI-Beta": "realtime=v1"}
        self.ws = await connect(self.url, additional_headers=openai_headers)

        try:
            json.loads(await self.ws.recv())
        except ConnectionClosedOK:
            return
        except ConnectionClosedError as e:
            logging.error("OpenAI hello error: %s", e)
            return

        caller_phone = self.call.from_number
        has_undelivered, orders = await self._check_undelivered_order(caller_phone)
        customized_instructions = self._build_customized_instructions(has_undelivered, orders)

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
            "tools": self._get_function_definitions(),
            "tool_choice": "auto",
            "instructions": customized_instructions
        }

        await self.ws.send(json.dumps({"type": "session.update", "session": self.session}))
        
        welcome_message = self._build_welcome_message(has_undelivered, orders)
        if welcome_message:
            intro_payload = {
                "modalities": ["text", "audio"],
                "instructions": "Please greet the user with the following: " + welcome_message
            }
            await self.ws.send(json.dumps({"type": "response.create", "response": intro_payload}))

        soniox_key_ok = bool(self.soniox_key and self.soniox_key != "SONIOX_API_KEY")
        if self.soniox_enabled and soniox_key_ok:
            ok = await self._soniox_connect()
            if ok:
                self.soniox_task = asyncio.create_task(self._soniox_recv_loop(), name="soniox-recv")
                self.soniox_keepalive_task = asyncio.create_task(self._soniox_keepalive_loop(), name="soniox-keepalive")
            else:
                logging.error("Soniox connect failed")
        else:
            logging.error("Soniox not available")

        await self.handle_command()

    # ---------------------- OpenAI event loop ----------------------
    async def handle_command(self):
        leftovers = b""
        async for smsg in self.ws:
            msg = json.loads(smsg)
            t = msg["type"]

            if t == "response.audio.delta":
                media = base64.b64decode(msg["delta"])
                packets, leftovers = await self.run_in_thread(self.codec.parse, media, leftovers)
                for packet in packets:
                    self.queue.put_nowait(packet)

            elif t == "response.audio.done":
                if len(leftovers) > 0:
                    packet = await self.run_in_thread(self.codec.parse, None, leftovers)
                    self.queue.put_nowait(packet)
                    leftovers = b""

            elif t == "conversation.item.created":
                if msg["item"].get("status") == "completed":
                    self.drain_queue()

            elif t == "response.function_call_arguments.done":
                global call_id
                call_id = msg.get("call_id")
                name = msg.get("name")
                try:
                    args = json.loads(msg.get("arguments") or "{}")
                except Exception:
                    args = {}
                
                logging.info("FUNCTION CALL: %s", name)

                if name == "terminate_call":
                    self.terminate_call()

                elif name == "transfer_call":
                    if self.transfer_to:
                        self.call.ua_session_update(method="REFER", headers={
                            "Refer-To": f"<{self.transfer_to}>",
                            "Referred-By": f"<{self.transfer_by}>"
                        })

                elif name == "track_order":
                    phone_number = args.get("phone_number") or self.call.from_number
                    if not phone_number:
                        output = {"success": False, "message": "شماره تلفن در دسترس نیست."}
                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id,
                                     "output": json.dumps(output, ensure_ascii=False)}
                        }))
                        await self.ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["text", "audio"]}
                        }))
                        continue
                    
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
                    
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": call_id,
                                 "output": json.dumps(output, ensure_ascii=False)}
                    }))
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))

                elif name == "get_menu_specials":
                    try:
                        result = await self.api.get_menu_specials()
                        if result and result.get("success"):
                            output = {"success": True, "specials": result.get("items", [])}
                        else:
                            output = {"success": False, "message": "خطا در دریافت پیشنهادات"}
                    except Exception as e:
                        logging.error("Exception getting specials: %s", e)
                        output = {"success": False, "message": "خطا در اتصال به سرور"}
                    
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": call_id,
                                 "output": json.dumps(output, ensure_ascii=False)}
                    }))
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))

                elif name == "search_menu_item":
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
                    
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": call_id,
                                 "output": json.dumps(output, ensure_ascii=False)}
                    }))
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))

                elif name == "create_order":
                    current_time = time.time()
                    if self.last_order_time and (current_time - self.last_order_time) < 10:
                        output = {
                            "success": False, 
                            "message": "سفارش قبلی شما در حال پردازش است. لطفا چند لحظه صبر کنید."
                        }
                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id,
                                     "output": json.dumps(output, ensure_ascii=False)}
                        }))
                        await self.ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["text", "audio"]}
                        }))
                        continue
                    
                    customer_name = args.get("customer_name")
                    phone_number = self.call.from_number or args.get("phone_number")
                    if not phone_number:
                        output = {"success": False, "message": "شماره تلفن در دسترس نیست. لطفا دوباره تماس بگیرید."}
                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id,
                                     "output": json.dumps(output, ensure_ascii=False)}
                        }))
                        await self.ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["text", "audio"]}
                        }))
                        continue
                    
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
                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id,
                                     "output": json.dumps(output, ensure_ascii=False)}
                        }))
                        await self.ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["text", "audio"]}
                        }))
                        continue
                    
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
                    
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": call_id,
                                 "output": json.dumps(output, ensure_ascii=False)}
                    }))
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))

                else:
                    logging.debug("FLOW tool: unhandled function name: %s", name)

            elif t == "error":
                logging.error("OpenAI error: %s", msg)

            else:
                # Log ALL events with full details for debugging
                # logging.info("OpenAI event: %s | data: %s", t, json.dumps(msg, ensure_ascii=False)[:500])
                pass

    # ---------------------- lifecycle helpers ----------------------
    def terminate_call(self):
        self.call.terminated = True
        logging.info("CALL TERMINATED")

    async def run_in_thread(self, func, *args):
        return await asyncio.to_thread(func, *args)

    def drain_queue(self):
        count = 0
        try:
            while self.queue.get_nowait():
                count += 1
        except Empty:
            if count > 0:
                logging.info("dropping %d packets", count)

    # ---------------------- Soniox wiring ----------------------
    async def _soniox_connect(self) -> bool:
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
        try:
            while self.soniox_ws and not self.call.terminated:
                await asyncio.sleep(self.soniox_keepalive_sec)
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "keepalive"}))
        except asyncio.CancelledError:
            pass

    async def _soniox_recv_loop(self):
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
                nonfinals = [t.get("text", "") for t in tokens if not t.get("is_final")]
                has_nonfinal = any(not t.get("is_final") for t in tokens)
                
                if nonfinals and self._soniox_flush_timer:
                    self._soniox_flush_timer.cancel()
                    self._soniox_flush_timer = None
                
                if finals:
                    final_text = "".join(finals)
                    logging.info("STT (final): %s", final_text)
                    self._soniox_accum.append(final_text)
                    if self._soniox_flush_timer:
                        self._soniox_flush_timer.cancel()
                    if not has_nonfinal:
                        self._soniox_flush_timer = asyncio.create_task(
                            self._delayed_flush_soniox_segment()
                        )

                if any(t.get("text") == "<fin>" for t in tokens):
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
        try:
            await asyncio.sleep(self.soniox_silence_duration_ms / 1000.0)
            if self._soniox_flush_timer and not self._soniox_flush_timer.cancelled():
                await self._flush_soniox_segment()
                self._soniox_flush_timer = None
        except asyncio.CancelledError:
            pass
    
    def _correct_common_misrecognitions(self, text: str) -> str:
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
        if not self._soniox_accum:
            return
        text = "".join(self._soniox_accum).strip()
        self._soniox_accum.clear()
        if not text:
            return
        
        corrected_text = self._correct_common_misrecognitions(text)
        logging.info("STT transcript: %s", corrected_text)
        await self._send_user_text_to_openai(corrected_text)
    
    async def _send_user_text_to_openai(self, text: str):
        try:
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            }))
            await self.ws.send(json.dumps({
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }))
        except Exception as e:
            logging.error("Error forwarding transcript: %s", e)

    async def send(self, audio):
        if self.call.terminated:
            return

        processed_audio = self._process_audio_for_soniox(audio)
        
        try:
            if self.soniox_ws:
                await self.soniox_ws.send(processed_audio)
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

    async def close(self):
        for t in (self.soniox_keepalive_task, self.soniox_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        try:
            if self.soniox_ws:
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "finalize"}))
                await self.soniox_ws.close()
        finally:
            self.soniox_ws = None

        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
