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
    """Unified OpenAI Realtime client - loads all config from DID JSON files."""

    def __init__(self, call, cfg):
        # === media & IO ===
        self.codec = self.choose_codec(call.sdp)
        self.queue = call.rtp
        self.call = call
        self.ws = None
        self.session = None

        # === Load DID configuration ===
        did_number = getattr(call, 'did_number', None)
        self.did_number = did_number
        
        if did_number:
            self.did_config = load_did_config(did_number)
            if self.did_config:
                logging.info("✅ DID config loaded for %s: %s", 
                           did_number, self.did_config.get('description', 'Unknown'))
            else:
                logging.warning("⚠️  No DID config for %s, using default", did_number)
                self.did_config = load_did_config("default")
        else:
            logging.warning("⚠️  No DID number available, using default config")
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
                "سلام و درودبرشما {customer_name} عزیز، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(
                    customer_name=self.customer_name_from_history,
                    service_name=service_name
                )
            except Exception:
                base_greeting = f"سلام و درودبرشما {self.customer_name_from_history} عزیز، با {service_name} تماس گرفته‌اید"
        else:
            base_greeting_template = welcome_templates.get('without_customer_name',
                "سلام و درودبرشما، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(service_name=service_name)
            except Exception:
                base_greeting = f"سلام و درودبرشما، با {service_name} تماس گرفته‌اید"
        
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

        # Build instructions and welcome message from config
        customized_instructions = self._build_instructions_from_config(has_undelivered, orders)
        welcome_message = self._build_welcome_message_from_config(has_undelivered, orders)
        
        # Use welcome message from config or fallback to intro
        if not welcome_message:
            welcome_message = self.intro

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
            "instructions": customized_instructions  # Load from config
        }

        # Send session update
        await self.ws.send(json.dumps({"type": "session.update", "session": self.session}))
        logging.info("FLOW start: OpenAI session.update sent with %d functions", len(self.session["tools"]))

        # Send welcome message
        if welcome_message:
            intro_payload = {
                "modalities": ["text", "audio"],
                "instructions": "Please greet the user with the following: " + welcome_message
            }
            await self.ws.send(json.dumps({"type": "response.create", "response": intro_payload}))
            logging.info("FLOW start: welcome message sent")

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
                logging.info("OpenAI said: %s", msg["transcript"])

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
                logging.error("OpenAI error: %s", msg)

            else:
                logging.debug("OpenAI event: %s", t)

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
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id,
                         "output": json.dumps(result, ensure_ascii=False)}
            }))
            await self.ws.send(json.dumps({
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }))

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
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id,
                         "output": json.dumps(result, ensure_ascii=False)}
            }))
            await self.ws.send(json.dumps({
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }))

        # === Taxi service functions ===
        elif name == "get_origin_destination_userame":
            await self._handle_taxi_booking(call_id, args)

        # === Restaurant service functions ===
        elif name == "track_order":
            await self._handle_track_order(call_id, args)
        elif name == "get_menu_specials":
            await self._handle_get_menu_specials(call_id)
        elif name == "search_menu_item":
            await self._handle_search_menu_item(call_id, args)
        elif name == "create_order":
            await self._handle_create_order(call_id, args)

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
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output",
                         "call_id": call_id,
                         "output": json.dumps(output, ensure_ascii=False)}
            }))
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
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output",
                         "call_id": call_id,
                         "output": json.dumps(output, ensure_ascii=False)}
            }))
        
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]}
        }))

    # ---------------------- Restaurant service handlers ----------------------
    async def _handle_track_order(self, call_id, args):
        """Handle track_order function call."""
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
        
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id,
                     "output": json.dumps(output, ensure_ascii=False)}
        }))
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]}
        }))

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
        
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id,
                     "output": json.dumps(output, ensure_ascii=False)}
        }))
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]}
        }))

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
        
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id,
                     "output": json.dumps(output, ensure_ascii=False)}
        }))
        await self.ws.send(json.dumps({
            "type": "response.create",
            "response": {"modalities": ["text", "audio"]}
        }))

    async def _handle_create_order(self, call_id, args):
        """Handle create_order function call."""
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
            return
        
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
            await self.ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id,
                         "output": json.dumps(output, ensure_ascii=False)}
            }))
            await self.ws.send(json.dumps({
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }))
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
        """Delayed flush for Soniox segments."""
        try:
            await asyncio.sleep(self.soniox_silence_duration_ms / 1000.0)
            if self._soniox_flush_timer and not self._soniox_flush_timer.cancelled():
                await self._flush_soniox_segment()
                self._soniox_flush_timer = None
        except asyncio.CancelledError:
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
        logging.info("STT transcript: %s", corrected_text)
        await self._send_user_text_to_openai(corrected_text)
    
    async def _send_user_text_to_openai(self, text: str):
        """Send user text to OpenAI."""
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
