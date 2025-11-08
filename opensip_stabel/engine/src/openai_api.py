#!/usr/bin/env python
"""
OpenAI Realtime + Soniox RT (Persian) bridge
- Streams inbound RTP (G.711 μ-law/A-law) -> Soniox for STT
- Sends finalized Persian text to OpenAI Realtime
- Streams OpenAI TTS audio back (G.711) into RTP queue
- Step-by-step FLOW logs so you can see the full path
- Fallback: if Soniox unavailable, auto-enable OpenAI Whisper and forward audio
"""

import sys
import json
import time
import base64
import logging
import asyncio
import contextlib
from queue import Empty
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from ai import AIEngine
from codec import get_codecs, CODECS, UnsupportedCodec
from config import Config
from storage import WalletMeetingDB
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
import re
from api_sender import API
from phone_normalizer import normalize_phone_number
import os
import audioop
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# دریافت آدرس سرور از environment variable
BACKEND_SERVER_URL = os.getenv("BACKEND_SERVER_URL", "http://localhost:8000")
api = API(BACKEND_SERVER_URL)

# ---- Ensure logs appear in the engine container ----
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(message)s"
)

OPENAI_API_MODEL = "gpt-realtime-2025-08-28"
OPENAI_URL_FORMAT = "wss://api.openai.com/v1/realtime?model={}"


class OpenAI(AIEngine):  # pylint: disable=too-many-instance-attributes
    """OpenAI Realtime client that uses Soniox for STT."""

    def __init__(self, call, cfg):
        # === media & IO ===
        self.codec = self.choose_codec(call.sdp)
        self.queue = call.rtp
        self.call = call
        self.ws = None
        self.session = None

        # === config ===
        self.cfg = Config.get("openai", cfg)
        db_path = self.cfg.get("db_path", "OPENAI_DB_PATH", "./src/data/app.db")
        self.db = WalletMeetingDB(db_path)

        self.model = self.cfg.get("model", "OPENAI_API_MODEL", OPENAI_API_MODEL)
        self.timezone = self.cfg.get("timezone", "OPENAI_TZ", "Asia/Tehran")
        self.url = self.cfg.get("url", "OPENAI_URL", OPENAI_URL_FORMAT.format(self.model))
        self.key = self.cfg.get(["key", "openai_key"], "OPENAI_API_KEY")
        self.voice = self.cfg.get(["voice", "openai_voice"], "OPENAI_VOICE", "alloy")

        # NOTE: Instructions are now DYNAMIC and built per call based on order status
        # See _build_customized_instructions() method which creates scenario-specific instructions
        # Static instructions removed - each call gets customized instructions in start() method
        # Fixed: use correct parameter order (option, env, fallback)
        self.intro = self.cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", ". سلام و درود بر شما،با رستوران بزرگمهر تماس گرفته اید . درخدمتم. ")
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        # state for tools
        self.temp_order_data = {}  # Temporary storage for order being placed
        self.user_mentioned_items = []  # Track items user mentioned during conversation for verification
        self.customer_name_from_history = None  # Customer name from previous orders
        self.recent_order_ids = set()  # Track recently created orders to prevent duplicates
        self.last_order_time = None  # Track when last order was created

        # === codec mapping ===
        if self.codec.name == "mulaw":
            self.codec_name = "g711_ulaw"
        elif self.codec.name == "alaw":
            self.codec_name = "g711_alaw"
        elif self.codec.name == "opus":
            self.codec_name = "opus"  # Opus codec for high quality
        else:
            self.codec_name = "g711_ulaw"

        # === Soniox config & state ===
        self.soniox_cfg = Config.get("soniox", cfg)
        self.soniox_enabled = bool(self.soniox_cfg.get("enabled", "SONIOX_ENABLED", True))
        # دریافت کلید از config یا environment variable
        self.soniox_key = self.soniox_cfg.get("key", "SONIOX_API_KEY")
        self.soniox_url = self.soniox_cfg.get("url", "SONIOX_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
        # Use better model for Persian recognition
        self.soniox_model = self.soniox_cfg.get("model", "SONIOX_MODEL", "stt-rt-preview")
        # Enhanced language hints for better Persian recognition
        self.soniox_lang_hints = self.soniox_cfg.get("language_hints", "SONIOX_LANGUAGE_HINTS", ["fa", "fa-IR"])
        # Disable diarization for better accuracy (single speaker)
        self.soniox_enable_diar = bool(self.soniox_cfg.get("enable_speaker_diarization", "SONIOX_ENABLE_DIARIZATION", False))
        # Enable LID for better language detection
        self.soniox_enable_lid = bool(self.soniox_cfg.get("enable_language_identification", "SONIOX_ENABLE_LID", True))
        # Enable endpoint detection for better sentence boundaries
        self.soniox_enable_epd = bool(self.soniox_cfg.get("enable_endpoint_detection", "SONIOX_ENABLE_ENDPOINT", True))
        self.soniox_keepalive_sec = int(self.soniox_cfg.get("keepalive_sec", "SONIOX_KEEPALIVE_SEC", 15))
        
        # Audio quality enhancement: convert G.711 to PCM and upsample for Soniox
        # Temporarily disabled by default to avoid WebSocket connection issues
        # Can be enabled via SONIOX_UPSAMPLE_AUDIO=true if needed
        self.soniox_upsample = bool(self.soniox_cfg.get("upsample_audio", "SONIOX_UPSAMPLE_AUDIO", False))
        self._soniox_audio_buffer = b''  # Buffer for audio conversion

        self.soniox_ws = None
        self.soniox_task = None
        self.soniox_keepalive_task = None
        self._soniox_accum = []

        # Optional: also forward mic audio to OpenAI (usually unnecessary)
        self.forward_audio_to_openai = bool(
            self.soniox_cfg.get("forward_audio_to_openai", "FORWARD_AUDIO_TO_OPENAI", False)
        )

        # Track whether we enabled fallback Whisper on OpenAI
        self._fallback_whisper_enabled = False

    # ---------------------- date/time helpers (unchanged) ----------------------
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
        """
        Check if caller has any undelivered orders.
        Returns: (has_undelivered, orders_list) tuple
        - orders_list: List of ALL undelivered orders (not just the latest)
        - Also extracts customer name from Customer table (not just orders) for use in welcome message
        """
        if not phone_number:
            logging.warning("⚠️  No phone number provided for order check")
            return False, []
        
        try:
            # Normalize phone number
            normalized_phone = normalize_phone_number(phone_number)
            logging.info("🔍 Checking orders for phone: %s (normalized: %s)", phone_number, normalized_phone)
            
            # First, try to get customer name from Customer table (persists even after orders are deleted)
            try:
                customer_info = await api.get_customer_info(normalized_phone)
                if customer_info.get("success") and customer_info.get("customer"):
                    self.customer_name_from_history = customer_info["customer"].get("name")
                    if self.customer_name_from_history:
                        logging.info("  👤 Customer name from Customer table: %s", self.customer_name_from_history)
            except Exception as e:
                logging.debug("  Could not get customer info from Customer table: %s", e)
            
            # Track orders
            result = await api.track_order(normalized_phone)
            
            if not result or not result.get("success"):
                logging.warning("⚠️  Failed to check orders: %s", result.get("message", "Unknown error"))
                return False, []
            
            orders = result.get("orders", [])
            if not orders:
                logging.info("📭 No orders found for phone: %s", normalized_phone)
                # Customer name already set from Customer table above
                return False, []
            
            # Filter out delivered and cancelled orders
            undelivered = [o for o in orders if o.get("status") not in ["delivered", "cancelled"]]
            
            if undelivered:
                # If we don't have customer name from Customer table, get it from order
                if not self.customer_name_from_history:
                    latest_order = undelivered[0]
                    self.customer_name_from_history = latest_order.get('customer_name')
                logging.info("✅ Found %d undelivered order(s):", len(undelivered))
                for order in undelivered:
                    logging.info("  - Order ID=%s, Status=%s", order.get('id'), order.get('status_display'))
                if self.customer_name_from_history:
                    logging.info("  👤 Customer name: %s", self.customer_name_from_history)
                return True, undelivered
            else:
                logging.info("✅ All orders are delivered or cancelled for phone: %s", normalized_phone)
                # If we don't have customer name from Customer table, get it from latest order
                if not self.customer_name_from_history and orders:
                    latest_order = orders[0]
                    self.customer_name_from_history = latest_order.get('customer_name')
                if self.customer_name_from_history:
                    logging.info("  👤 Customer name: %s", self.customer_name_from_history)
                return False, []
                
        except Exception as e:
            logging.error(f"❌ Exception checking orders: {e}", exc_info=True)
            return False, []

    def _format_items_list_persian(self, items):
        """
        Format order items list in Persian.
        Example: [{"menu_item_name": "کباب کوبیده", "quantity": 1}, {"menu_item_name": "دوغ سنتی", "quantity": 2}]
        Returns: "یک کباب کوبیده و دوغ سنتی کوچک"
        """
        if not items or len(items) == 0:
            return ""
        
        persian_numbers = {
            1: "یک", 2: "دو", 3: "سه", 4: "چهار", 5: "پنج",
            6: "شش", 7: "هفت", 8: "هشت", 9: "نه", 10: "ده"
        }
        
        formatted_items = []
        for item in items:
            quantity = item.get('quantity', 1)
            # Try different possible field names for item name
            item_name = (item.get('menu_item_name') or 
                        (item.get('menu_item', {}).get('name') if isinstance(item.get('menu_item'), dict) else None) or
                        item.get('name', ''))
            
            if not item_name:
                logging.warning(f"⚠️  Item name not found in order item: {item}")
                continue
            
            if quantity == 1:
                formatted_items.append(f"یک {item_name}")
            elif quantity <= 10:
                formatted_items.append(f"{persian_numbers.get(quantity, str(quantity))} {item_name}")
            else:
                formatted_items.append(f"{quantity} {item_name}")
        
        if len(formatted_items) == 0:
            return ""
        elif len(formatted_items) == 1:
            return formatted_items[0]
        elif len(formatted_items) == 2:
            return f"{formatted_items[0]} و {formatted_items[1]}"
        else:
            # For 3+ items: "یک X، دو Y و سه Z"
            all_except_last = "، ".join(formatted_items[:-1])
            return f"{all_except_last} و {formatted_items[-1]}"

    def _build_welcome_message(self, has_undelivered_order, orders=None):
        """
        Build welcome message based on order status.
        Always includes hello and restaurant name.
        When orders exist, includes full order details for ALL orders.
        Uses customer name from history if available (with 'عزیز' suffix).
        """
        # Use customer name from history if available
        if self.customer_name_from_history:
            base_greeting = f"سلام و درود بر شما {self.customer_name_from_history} عزیز، با رستوران بزرگمهر تماس گرفته‌اید"
        else:
            base_greeting = "سلام و درود بر شما، با رستوران بزرگمهر تماس گرفته‌اید"
        
        if has_undelivered_order and orders and len(orders) > 0:
            # Has undelivered orders - report ALL orders
            order_details_list = []
            
            for order in orders:
                order_id = order.get('id', '')
                status_display = order.get('status_display', '')
                address = order.get('address', '')
                items = order.get('items', [])
                order_status = order.get('status', '')
                
                logging.info(f"📋 Processing order ID={order_id}, items_count={len(items)}, address={bool(address)}")
                
                # Format items list in Persian using helper function
                items_text = self._format_items_list_persian(items)
                
                # Build status text based on order status
                if order_status == 'preparing':
                    status_text = f"{status_display} توسط رستوران است"
                else:
                    status_text = f"{status_display} است"
                
                # Build order detail for this order
                if items_text:
                    if address:
                        order_detail = f"سفارش شما به شماره ی {order_id} که {items_text}، به مقصد {address} ثبت شده بود {status_text}"
                    else:
                        order_detail = f"سفارش شما به شماره ی {order_id} که {items_text} ثبت شده بود {status_text}"
                else:
                    # Fallback if items are not available
                    if address:
                        order_detail = f"سفارش شما به شماره ی {order_id} به مقصد {address} ثبت شده بود {status_text}"
                    else:
                        order_detail = f"سفارش شما به شماره ی {order_id} ثبت شده بود {status_text}"
                
                order_details_list.append(order_detail)
            
            # Join all order details
            if len(order_details_list) == 1:
                orders_text = order_details_list[0]
            else:
                # For multiple orders, join with "همچنین" (also)
                orders_text = "، ".join(order_details_list[:-1]) + f" و همچنین {order_details_list[-1]}"
            
            # Join greeting and order details, then add closing
            full_message = f"{base_greeting}، {orders_text}."
            full_message += " از صبر و شکیبایی شما متشکریم. اگر امر دیگری هست در خدمت شما هستم."
            
            return full_message
        else:
            # No undelivered orders - ask if they want to order
            return f"{base_greeting}. آیا می‌خواهید سفارش جدیدی ثبت کنید؟"

    def _build_customized_instructions(self, has_undelivered_order, orders=None):
        """
        Build customized instructions based on call context.
        Different scenarios for different call situations.
        """
        # Add customer name instruction if available
        name_instruction = ""
        if self.customer_name_from_history:
            name_instruction = f"مهم: نام مشتری ({self.customer_name_from_history}) از سفارشات قبلی در دسترس است. نیازی به پرسیدن نام نیست و از نام موجود استفاده کن. "
        else:
            name_instruction = "اگر مشتری قبلا سفارش نداده، نام مشتری را بپرس. "
        
        base_instructions = (
            "با لحنی گرم و پر انرژی صحبت کن "
            "فقط و فقط فارسی صحبت کن ، به هیچ زبان دیگه ای بجز فارسی صحبت نکن."
            " تو یک دستیار هوشمند رستوران بزرگمهر هستی. همیشه حرفه‌ای و مودب و بااحترام و پر انرژی و شاد حرف میزنی . "
            "همیشه با لحن مودب و با احترام و پر انرژی حرف بزن"
            "مهم: شماره تلفن مشتری به صورت خودکار از تماس گرفته می‌شود و نیازی به پرسیدن آن نیست. "
            f"{name_instruction}"
            "همیشه طبیعی و دوستانه صحبت کن."
            " به هیچ وجه اشاره ای به جنسیت شخص نکن  (مثل خطاب کردن و گفتن آقا یا خانم)"
            "کاربر از تو چیزی خارج از سفارش نمیپرسه ، پس اگر موقع انتخاب غذاها چیزی شنیدی که انگار مرتبط با غذا نیست بررسی کن ببین شبیه ترین چیز به یکی از اسم های غذا چی بود بعد یکی از غذاها رو در نظر بگیر و ازش بپرس که آیا منظورش این بود یا نه . مثلا اگر کاربر کفت کووید میخواستم ، بگو کوبیده  فرمودین ؟ فقط اگر چیزی گفت که اسم غذا نبود مستقیما."
            "با مشتری حرفه ای و با لحن احترام سخن بگو و با تو خطاب نکن ، همیشه از کلمه ی شما استفاده کن"
            "خیلی مهم: هیچ وقت تماس را قطع نکن مگر اینکه کاربر صریحا و واضحا بگوید که می‌خواهد تماس را تمام کند (مثل خداحافظ، بای، تماس رو قطع کن، تماس رو پایان بده). "
            "اگر کاربر فقط سکوت کرد یا چیزی مثل '.' گفت، این به معنای پایان تماس نیست. منتظر بمان و بپرس آیا کار دیگری هست یا نه. "
            "هیچ وقت در وسط صحبت خودت تماس را قطع نکن. همیشه منتظر بمان تا کاربر بگوید که می‌خواهد تماس را تمام کند."
        )
        
        if has_undelivered_order and orders and len(orders) > 0:
            # Scenario 1: Caller has undelivered order(s)
            orders_count = len(orders)
            if orders_count == 1:
                order = orders[0]
                order_status = order.get('status', '')
                order_id = order.get('id', '')
                status_display = order.get('status_display', '')
                
                scenario_instructions = (
                    f"وضعیت سفارش: مشتری دارای سفارش شماره {order_id} با وضعیت {status_display} است که هنوز تحویل داده نشده. "
                    "وظیفه تو: "
                    "1) ابتدا وضعیت سفارش را که در پیام خوش‌آمدگویی گفته شده، تایید کن و بپرس آیا سوالی درباره سفارش دارند. "
                    "2) اگر می‌خواهند سفارش جدید ثبت کنند، به سناریوی ثبت سفارش جدید برو. "
                    "3) اگر می‌خواهند وضعیت سفارش را دوباره بررسی کنند، می‌توانی از تابع track_order استفاده کنی (شماره تلفن به صورت خودکار استفاده می‌شود). "
                    "4) اگر سوالی درباره زمان تحویل یا جزئیات سفارش دارند، با لحن دوستانه پاسخ بده. "
                )
            else:
                # Multiple orders
                order_ids = [str(o.get('id', '')) for o in orders]
                scenario_instructions = (
                    f"وضعیت سفارش: مشتری دارای {orders_count} سفارش تحویل نشده با شماره‌های {', '.join(order_ids)} است. "
                    "وضعیت همه سفارشات در پیام خوش‌آمدگویی گفته شده است. "
                    "وظیفه تو: "
                    "1) ابتدا وضعیت سفارشات را که در پیام خوش‌آمدگویی گفته شده، تایید کن و بپرس آیا سوالی درباره سفارشات دارند. "
                    "2) اگر می‌خواهند سفارش جدید ثبت کنند، به سناریوی ثبت سفارش جدید برو. "
                    "3) اگر می‌خواهند وضعیت سفارشات را دوباره بررسی کنند، می‌توانی از تابع track_order استفاده کنی (شماره تلفن به صورت خودکار استفاده می‌شود). "
                    "4) اگر سوالی درباره زمان تحویل یا جزئیات سفارشات دارند، با لحن دوستانه پاسخ بده. "
                )
            
            # Add status-specific guidance for latest order
            latest_order = orders[0]
            order_status = latest_order.get('status', '')
            if order_status in ['pending', 'confirmed']:
                scenario_instructions += (
                    "نکته: سفارش در حال تایید یا تایید شده است. به مشتری اطمینان بده که سفارش در حال آماده شدن است. "
                )
            elif order_status == 'preparing':
                scenario_instructions += (
                    "نکته: سفارش در حال آماده سازی است. به مشتری بگو که به زودی آماده می‌شود. "
                )
            elif order_status == 'on_delivery':
                scenario_instructions += (
                    "نکته: سفارش به پیک تحویل داده شده و در راه است. به مشتری بگو که به زودی به دستش می‌رسد. "
                )
            
        else:
            # Scenario 2: Caller has no undelivered orders (new customer or all orders delivered)
            scenario_instructions = (
                "پر انرژی و گرم حرف بزن"
                "وضعیت سفارش: مشتری سفارش تحویل نشده‌ای ندارد. "
                "وظیفه تو: دریافت سفارش جدید. "
                "سناریوی ثبت سفارش جدید: "
            )
            if self.customer_name_from_history:
                scenario_instructions += (
                    f"1) نام مشتری ({self.customer_name_from_history}) از سفارشات قبلی در دسترس است، نیازی به پرسیدن نیست. "
                )
            else:
                scenario_instructions += (
                    "1) نام مشتری را بپرس "
                )
            scenario_instructions += (
                "2) اگر کاربر درخواست کرد پیشنهادات ویژه رستوران را با get_menu_specials بگیر و بگو "
                "3) سفارش غذای اصلی را بگیر، اگر عین آن غذا موجود نبود شبیه‌ترین را با search_menu_item بیاب و پیشنهاد بده "
                "4) آدرس تحویل را بگیر (شماره تلفن به صورت خودکار از تماس گرفته می‌شود و نیازی به پرسیدن آن نیست)"
                "5) خیلی خیلی مهم: وقتی کاربر چند غذا را در یک جمله می‌گوید، حتما همه را با تعداد دقیق یادداشت کن و هیچ کدام را از قلم نینداز. "
                "   - اگر کاربر گفت 'یک کباب کوبیده و دو دوغ' باید ثبت کنی: [{item_name: 'کباب کوبیده', quantity: 1}, {item_name: 'دوغ', quantity: 2}] "
                "   - اگر کاربر گفت 'دو کباب و سه تا نوشابه' باید ثبت کنی: [{item_name: 'کباب', quantity: 2}, {item_name: 'نوشابه', quantity: 3}] "
                "   - اگر کاربر گفت 'سه تا کباب و دو دوغ' باید ثبت کنی: [{item_name: 'کباب', quantity: 3}, {item_name: 'دوغ', quantity: 2}] "
                "   - هیچ وقت نباید هیچ غذایی یا تعدادش را از قلم بیندازی. همه چیزهایی که کاربر گفت با تعداد دقیق باید در لیست items باشد. "
                "   - اگر کاربر تعداد نگفت، به صورت پیش‌فرض quantity: 1 بگذار، اما اگر گفت 'دو' یا 'سه تا' یا 'چهار' حتما همان تعداد را ثبت کن. "
                "6) قبل از ثبت سفارش، حتما همه غذاهایی که کاربر گفته را با تعداد دقیق برایش تکرار کن تا مطمئن شوی همه را درست فهمیده‌ای. "
                "   - لیست کامل با تعداد را بگو: 'پس سفارش شما: [مثلا: دو کباب کوبیده، سه دوغ، یک نوشابه] درست است؟' "
                "   - حتما تعداد هر غذا را هم بگو: 'دو تا کباب، سه تا دوغ' نه فقط 'کباب و دوغ' "
                "   - اگر کاربر تایید کرد، فقط در این صورت create_order را صدا بزن "
                "7) خیلی مهم: قبل از صدا زدن create_order، مطمئن شو که: "
                "   - لیست items خالی نیست (حتما حداقل یک غذا باید باشد) "
                "   - customer_name وجود دارد "
                "   - address وجود دارد "
                "   - همه غذاهایی که کاربر گفت در لیست items هستند "
                "   - تعداد هر غذا (quantity) دقیقا همان است که کاربر گفت (اگر گفت 'دو' باید quantity: 2 باشد، اگر گفت 'سه تا' باید quantity: 3 باشد) "
                "8) اگر لیست items خالی است یا customer_name یا address نداریم، هیچ وقت create_order را صدا نزن. "
                "   در عوض از کاربر بپرس که اطلاعات گم شده را بدهد. "
                "9) همه موارد سفارش را تایید کن و با create_order ثبت کن. "
                "10) خیلی مهم: فقط یک بار create_order را صدا بزن برای هر سفارش. هیچ وقت برای یک سفارش چند بار create_order را صدا نزن. "
                "11) بعد از ثبت سفارش، اگر پیام موفقیت آمیز بود، سفارش ثبت شده است و نیازی به ثبت دوباره نیست. "
            )
        
        return base_instructions + " " + scenario_instructions

    # ---------------------- session start ----------------------
    async def start(self):
        """Starts OpenAI connection, connects Soniox, runs main loop."""
        logging.info("\n" + "=" * 80)
        logging.info("🎬 NEW CALL - Restaurant Ordering System")
        logging.info("=" * 80)
        logging.info("FLOW start: connecting OpenAI WS → %s", self.url)
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

        # Check caller's phone number and orders BEFORE building session (for customized scenario)
        caller_phone = self.call.from_number
        logging.info("📞 Caller phone number: %s", caller_phone or "Not available")
        
        # Check for undelivered orders (returns list of ALL undelivered orders)
        has_undelivered, orders = await self._check_undelivered_order(caller_phone)
        logging.info("📦 Order status: has_undelivered=%s, orders_count=%d", 
                     has_undelivered, len(orders) if orders else 0)
        if orders:
            for order in orders:
                logging.info("   - Order ID: %s, Status: %s", order.get('id'), order.get('status_display'))
        
        # Build DYNAMIC customized instructions based on call context
        # This creates a unique scenario for EACH call based on order status
        customized_instructions = self._build_customized_instructions(has_undelivered, orders)
        logging.info("🎯 DYNAMIC SCENARIO: Customized instructions built for this specific call")
        if has_undelivered and orders:
            logging.info("   → Scenario: Customer with %d undelivered order(s)", len(orders))
        else:
            logging.info("   → Scenario: New customer or all orders delivered - focus on new order")
        if self.customer_name_from_history:
            logging.info("   → Customer name from history: %s", self.customer_name_from_history)
        logging.debug("   Instructions preview: %s", customized_instructions[:200] + "...")

        # Build session with customized instructions
        self.session = {
            "modalities": ["text", "audio"],  # REQUIRED: Enable audio output!
            "turn_detection": {
                "type": self.cfg.get("turn_detection_type", "OPENAI_TURN_DETECT_TYPE", "server_vad"),
                "silence_duration_ms": int(self.cfg.get("turn_detection_silence_ms", "OPENAI_TURN_DETECT_SILENCE_MS", 300)),
                "threshold": float(self.cfg.get("turn_detection_threshold", "OPENAI_TURN_DETECT_THRESHOLD", 0.6)),
                "prefix_padding_ms": int(self.cfg.get("turn_detection_prefix_ms", "OPENAI_TURN_DETECT_PREFIX_MS", 300)),
            },
            "input_audio_format": self.get_audio_format(),   # your existing structure
            "output_audio_format": self.get_audio_format(),  # plays back via your codec parser
            # We'll add Whisper below if Soniox is unavailable
            "voice": self.voice,
            "temperature": float(self.cfg.get("temperature", "OPENAI_TEMPERATURE", 0.8)),
            "max_response_output_tokens": self.cfg.get("max_tokens", "OPENAI_MAX_TOKENS", "inf"),
            "tools": [
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
                    "description": "پیگیری سفارش قبلی بر اساس شماره تلفن مشتری. وضعیت سفارش را برمی‌گرداند. اگر شماره تلفن ارائه نشود، شماره تماس‌گیرنده به صورت خودکار استفاده می‌شود.",
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
                    "description": "دریافت لیست پیشنهادات ویژه رستوران. غذاهای ویژه و محبوب از هر دسته‌بندی را برمی‌گرداند.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False
                    }
                },
                {
                    "type": "function",
                    "name": "search_menu_item",
                    "description": "جستجوی یک غذا در منو. اگر نام دقیق غذا موجود نباشد، نزدیک‌ترین و مشابه‌ترین غذا را پیدا می‌کند.",
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
                    "description": "ثبت سفارش نهایی در سیستم. خیلی مهم: قبل از صدا زدن این تابع، مطمئن شو که: 1) customer_name وجود دارد و خالی نیست، 2) address وجود دارد و خالی نیست، 3) items لیست خالی نیست و حداقل یک غذا دارد، 4) همه غذاهایی که کاربر گفت در لیست items هستند. شماره تلفن به صورت خودکار از تماس گرفته می‌شود.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string", "description": "نام مشتری (الزامی - نباید خالی باشد)"},
                            "phone_number": {"type": "string", "description": "شماره تلفن مشتری (اختیاری - به صورت خودکار از تماس گرفته می‌شود)"},
                            "address": {"type": "string", "description": "آدرس تحویل سفارش (الزامی - نباید خالی باشد)"},
                            "items": {
                                "type": "array",
                                "description": "لیست آیتم‌های سفارش شامل نام غذا و تعداد (الزامی - نباید خالی باشد، باید حداقل یک غذا داشته باشد). خیلی مهم: 1) همه غذاهایی که کاربر گفت باید در این لیست باشند، 2) تعداد (quantity) هر غذا باید دقیقا همان باشد که کاربر گفت (اگر گفت 'دو' یا 'دو تا' باید 2 باشد، اگر گفت 'سه' یا 'سه تا' باید 3 باشد).",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "item_name": {"type": "string", "description": "نام دقیق غذا از منو"},
                                        "quantity": {"type": "integer", "description": "تعداد دقیق غذا - باید دقیقا همان باشد که کاربر گفت (اگر گفت 'دو' یا 'دو تا' باید 2 باشد، اگر گفت 'سه' یا 'سه تا' باید 3 باشد). اگر کاربر تعداد نگفت، مقدار پیش‌فرض 1 است.", "minimum": 1, "default": 1}
                                    },
                                    "required": ["item_name", "quantity"],
                                }
                            },
                            "notes": {"type": "string", "description": "یادداشت یا توضیحات اضافی (اختیاری)", "nullable": True},
                        },
                        "required": ["customer_name", "address", "items"],
                        "additionalProperties": False
                    }
                },
            ],
            "tool_choice": "auto",
        }
        # Use customized instructions instead of static ones
        self.session["instructions"] = customized_instructions
        logging.info("✅ Customized instructions applied to session")

        # Send session update
        await self.ws.send(json.dumps({"type": "session.update", "session": self.session}))
        logging.info("FLOW start: OpenAI session.update sent (with customized scenario)")
        
        # Build dynamic welcome message based on order status
        welcome_message = self._build_welcome_message(has_undelivered, orders)
        logging.info("💬 Welcome message: %s", welcome_message)
        
        # Send welcome message
        if welcome_message:
            intro_payload = {
                "modalities": ["text", "audio"],  # CRITICAL: Force audio output!
                "instructions": "Please greet the user with the following: " + welcome_message
            }
            await self.ws.send(json.dumps({"type": "response.create", "response": intro_payload}))
            logging.info("FLOW start: dynamic welcome message sent (with audio modality)")

        # Connect Soniox (NOT gated on intro)
        soniox_key_ok = bool(self.soniox_key and self.soniox_key != "SONIOX_API_KEY")
        logging.info("\n🔊 STT Configuration:")
        logging.info("  Soniox Enabled: %s", self.soniox_enabled)
        logging.info("  Soniox Key Available: %s", soniox_key_ok)
        
        if self.soniox_enabled and soniox_key_ok:
            logging.info("FLOW STT: SONIOX enabled | model=%s | url=%s", self.soniox_model, self.soniox_url)
            ok = await self._soniox_connect()
            if ok:
                logging.info("✅ SONIOX CONNECTED - Persian STT Active")
                self.soniox_task = asyncio.create_task(self._soniox_recv_loop(), name="soniox-recv")
                self.soniox_keepalive_task = asyncio.create_task(self._soniox_keepalive_loop(), name="soniox-keepalive")
            else:
                logging.warning("FLOW STT: Soniox connect failed; enabling Whisper fallback on OpenAI")
                await self._enable_whisper_fallback()
        else:
            # Fallback: enable Whisper on OpenAI and forward audio so bot still speaks
            if not soniox_key_ok:
                logging.error("FLOW STT: SONIOX_API_KEY not set; STT fallback will be used")
            else:
                logging.info("FLOW STT: SONIOX disabled by config; using fallback")
            await self._enable_whisper_fallback()

        # Start consuming OpenAI events (audio out, tools, etc.)
        await self.handle_command()

    async def _enable_whisper_fallback(self):
        await self.ws.send(json.dumps({
            "type": "session.update",
            "session": {"input_audio_transcription": {"model": "whisper-1"}}
        }))
        self._fallback_whisper_enabled = True
        self.forward_audio_to_openai = True
        logging.info("FLOW STT: Whisper fallback enabled; audio will be forwarded to OpenAI")

    # ---------------------- OpenAI event loop ----------------------
    async def handle_command(self):  # pylint: disable=too-many-branches
        """Handles OpenAI events; plays TTS audio; responds to tools."""
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
                # IMPORTANT: when using Whisper fallback, *ask* for a response after each completed transcript
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
                logging.info("=" * 80)
                logging.info("AI RESPONSE (Audio): %s", transcript)
                logging.info("=" * 80)

            elif t == "response.function_call_arguments.done":
                global call_id
                unique_time = time.time()
                call_id = msg.get("call_id")
                name = msg.get("name")
                try:
                    args = json.loads(msg.get("arguments") or "{}")
                except Exception:
                    args = {}
                
                logging.info("=" * 80)
                logging.info("FUNCTION CALL: %s", name)
                logging.info("Arguments: %s", json.dumps(args, ensure_ascii=False, indent=2))
                logging.info("=" * 80)

                if name == "terminate_call":
                    logging.info("FLOW tool: terminate_call requested")
                    self.terminate_call()  # Not async, don't await

                elif name == "transfer_call":
                    if self.transfer_to:
                        logging.info("FLOW tool: Transferring call via REFER")
                        self.call.ua_session_update(method="REFER", headers={
                            "Refer-To": f"<{self.transfer_to}>",
                            "Referred-By": f"<{self.transfer_by}>"
                        })
                    else:
                        logging.warning("FLOW tool: transfer_call requested but transfer_to not configured")

                elif name == "track_order":
                    # Track order by phone number (use caller's phone automatically)
                    phone_number = args.get("phone_number") or self.call.from_number
                    if not phone_number:
                        output = {"success": False, "message": "شماره تلفن در دسترس نیست."}
                        logging.error("❌ No phone number available for tracking")
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
                    logging.info("🔍 TRACKING ORDER")
                    logging.info("  Original: %s", phone_number)
                    logging.info("  Normalized: %s", normalized_phone)
                    
                    try:
                        result = await api.track_order(normalized_phone)
                        if result and result.get("success"):
                            orders = result.get("orders", [])
                            if orders:
                                latest = orders[0]
                                output = {
                                    "success": True,
                                    "message": f"سفارش شماره {latest['id']} شما {latest['status_display']} است.",
                                    "order": latest
                                }
                                logging.info("✅ Order found: ID=%s, Status=%s", latest['id'], latest['status_display'])
                            else:
                                output = {"success": False, "message": "سفارشی با این شماره یافت نشد."}
                                logging.warning("⚠️  No orders found for phone: %s", phone_number)
                        else:
                            output = {"success": False, "message": "خطا در پیگیری سفارش"}
                            logging.error("❌ Error tracking order")
                    except Exception as e:
                        logging.error(f"❌ Exception tracking order: {e}")
                        output = {"success": False, "message": "خطا در اتصال به سرور"}
                    
                    logging.info("FUNCTION RESULT: %s", json.dumps(output, ensure_ascii=False))
                    
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
                    # Get special menu items
                    logging.info("⭐ GETTING MENU SPECIALS")
                    
                    try:
                        result = await api.get_menu_specials()
                        if result and result.get("success"):
                            items = result.get("items", [])
                            output = {
                                "success": True,
                                "specials": items
                            }
                            logging.info("✅ Found %d special items", len(items))
                            for item in items[:5]:  # Log first 5
                                logging.info("  - %s: %s تومان", item.get('name'), item.get('final_price'))
                        else:
                            output = {"success": False, "message": "خطا در دریافت پیشنهادات"}
                            logging.error("❌ Error getting specials")
                    except Exception as e:
                        logging.error(f"❌ Exception getting specials: {e}")
                        output = {"success": False, "message": "خطا در اتصال به سرور"}
                    
                    logging.info("FUNCTION RESULT: %d special items", len(output.get("specials", [])))
                    
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
                    # Search for menu item
                    item_name = args.get("item_name")
                    category = args.get("category")
                    logging.info("🔍 SEARCHING MENU: '%s' (category: %s)", item_name, category or "همه")
                    
                    try:
                        result = await api.search_menu_item(item_name, category)
                        if result and result.get("success"):
                            items = result.get("items", [])
                            output = {
                                "success": True,
                                "items": items
                            }
                            logging.info("✅ Found %d matching items:", len(items))
                            for item in items:
                                logging.info("  - %s (%s): %s تومان", 
                                           item.get('name'), item.get('category'), item.get('final_price'))
                        else:
                            output = {"success": False, "message": "غذایی با این نام یافت نشد"}
                            logging.warning("⚠️  No items found for: %s", item_name)
                    except Exception as e:
                        logging.error(f"❌ Exception searching menu: {e}")
                        output = {"success": False, "message": "خطا در جستجو"}
                    
                    logging.info("FUNCTION RESULT: %d items found", len(output.get("items", [])))
                    
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
                    # Prevent duplicate orders - check if we just created an order recently
                    current_time = time.time()
                    if self.last_order_time and (current_time - self.last_order_time) < 10:  # 10 seconds cooldown
                        logging.warning("⚠️  DUPLICATE ORDER PREVENTION: Order creation attempted too soon after last order (%.1f seconds ago)", 
                                      current_time - self.last_order_time)
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
                    
                    # Create restaurant order (use caller's phone automatically)
                    customer_name = args.get("customer_name")
                    # Always use caller's phone number automatically
                    phone_number = self.call.from_number or args.get("phone_number")
                    if not phone_number:
                        output = {"success": False, "message": "شماره تلفن در دسترس نیست. لطفا دوباره تماس بگیرید."}
                        logging.error("❌ No phone number available for order creation")
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
                    
                    # CRITICAL VALIDATION: Reject order if missing required fields
                    validation_errors = []
                    
                    # Check customer name
                    if not customer_name or not customer_name.strip():
                        validation_errors.append("نام مشتری")
                    
                    # Check address
                    if not address or not address.strip():
                        validation_errors.append("آدرس")
                    
                    # Check items - MUST NOT BE EMPTY
                    if not items or len(items) == 0:
                        validation_errors.append("لیست غذاها (هیچ غذایی ثبت نشده)")
                    else:
                        # Validate each item has required fields
                        for idx, item in enumerate(items):
                            item_name = item.get('item_name', '').strip()
                            quantity = item.get('quantity', 0)
                            if not item_name:
                                validation_errors.append(f"نام غذا در آیتم {idx + 1}")
                            if not quantity or quantity <= 0:
                                validation_errors.append(f"تعداد در آیتم {idx + 1} (باید عدد مثبت باشد، مقدار فعلی: {quantity})")
                            # Log item details for debugging
                            logging.info("  ✅ Validating item %d: '%s' × %d", idx + 1, item_name, quantity)
                    
                    # If validation fails, reject the order
                    if validation_errors:
                        error_message = f"خطا: اطلاعات ناقص است. لطفا موارد زیر را تکمیل کنید: {', '.join(validation_errors)}"
                        logging.error("❌ ORDER VALIDATION FAILED: %s", ', '.join(validation_errors))
                        logging.error("   Customer: %s", customer_name)
                        logging.error("   Address: %s", address)
                        logging.error("   Items count: %d", len(items) if items else 0)
                        
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
                    
                    # Normalize phone number
                    normalized_phone = normalize_phone_number(phone_number)
                    
                    logging.info("=" * 80)
                    logging.info("📝 CREATING ORDER")
                    logging.info("Customer: %s", customer_name)
                    logging.info("Phone (original): %s", phone_number)
                    logging.info("Phone (normalized): %s", normalized_phone)
                    logging.info("Address: %s", address)
                    logging.info("Items (%d):", len(items))
                    for item in items:
                        logging.info("  - %s × %d", item.get('item_name'), item.get('quantity', 1))
                    if notes:
                        logging.info("Notes: %s", notes)
                    logging.info("=" * 80)
                    
                    try:
                        result = await api.create_order(
                            customer_name=customer_name,
                            phone_number=normalized_phone,  # Use normalized phone
                            address=address,
                            items=items,
                            notes=notes
                        )
                        
                        if result and result.get("success"):
                            order = result.get("order", {})
                            order_id = order.get('id')
                            
                            # Track this order creation to prevent duplicates
                            self.last_order_time = time.time()
                            self.recent_order_ids.add(order_id)
                            logging.info("✅ Order ID %s tracked to prevent duplicates", order_id)
                            
                            # Verify order was created correctly - fetch it from database (for logging only)
                            logging.info("🔍 Verifying order creation - fetching order from database...")
                            try:
                                # Fetch the created order to verify all items were captured
                                verify_result = await api.track_order(normalized_phone)
                                if verify_result and verify_result.get("success"):
                                    all_orders = verify_result.get("orders", [])
                                    created_order = None
                                    for o in all_orders:
                                        if o.get('id') == order_id:
                                            created_order = o
                                            break
                                    
                                    if created_order:
                                        db_items = created_order.get('items', [])
                                        submitted_items = items
                                        
                                        # Compare submitted items with database items (for logging only)
                                        submitted_item_names = {item.get('item_name', '').lower().strip() for item in submitted_items}
                                        db_item_names = {item.get('menu_item_name', '').lower().strip() for item in db_items}
                                        
                                        missing_items = submitted_item_names - db_item_names
                                        
                                        if missing_items:
                                            logging.warning("⚠️  MISSING ITEMS DETECTED (logged for debugging): %s", missing_items)
                                            logging.warning("   Submitted: %s", submitted_item_names)
                                            logging.warning("   In DB: %s", db_item_names)
                                            # Note: We don't tell the bot to create another order - just log it
                                        
                                        logging.info("✅ Order verification passed - order created successfully")
                                        output = {
                                            "success": True,
                                            "message": f"سفارش شماره {order.get('id')} با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                                            "order_id": order.get("id"),
                                            "total_price": order.get("total_price")
                                        }
                                    else:
                                        logging.warning("⚠️  Could not find created order in database for verification")
                                        output = {
                                            "success": True,
                                            "message": f"سفارش شماره {order.get('id')} با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                                            "order_id": order.get("id"),
                                            "total_price": order.get("total_price")
                                        }
                            except Exception as verify_error:
                                logging.error(f"⚠️  Error verifying order: {verify_error}")
                                # Fallback output if verification fails
                                output = {
                                    "success": True,
                                    "message": f"سفارش شماره {order.get('id')} با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                                    "order_id": order.get("id"),
                                    "total_price": order.get("total_price")
                                }
                            
                            # Output is set in verification block above
                            logging.info("✅ ORDER CREATED SUCCESSFULLY!")
                            logging.info("Order ID: %s", order.get('id'))
                            logging.info("Total Price: %s تومان", f"{order.get('total_price'):,}")
                        else:
                            output = {"success": False, "message": result.get("message", "خطا در ثبت سفارش")}
                            logging.error("❌ ORDER FAILED: %s", result.get("message"))
                    except Exception as e:
                        logging.error(f"❌ Exception creating order: {e}", exc_info=True)
                        output = {"success": False, "message": "خطا در اتصال به سرور"}
                    
                    logging.info("FUNCTION RESULT: %s", json.dumps(output, ensure_ascii=False))
                    
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
                logging.info("OpenAI event: %s | data: %s", t, json.dumps(msg, ensure_ascii=False)[:500])

    # ---------------------- lifecycle helpers ----------------------
    def terminate_call(self):
        """Marks call as terminated (your framework should then call close())."""
        self.call.terminated = True
        logging.info("\n" + "=" * 80)
        logging.info("📞 CALL TERMINATED")
        logging.info("=" * 80)
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
        key = self.soniox_key if self.soniox_key and self.soniox_key != "SONIOX_API_KEY" else None
        if not key:
            logging.error("FLOW STT: SONIOX_API_KEY not set; STT disabled")
            return False
        try:
            logging.info("FLOW STT: connecting Soniox WS → %s", self.soniox_url)
            self.soniox_ws = await connect(self.soniox_url)
            logging.info("FLOW STT: Soniox WS connected")

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
            }
            await self.soniox_ws.send(json.dumps(init))
            logging.info("FLOW STT: Soniox init sent (fmt=%s sr=%s ch=%s hints=%s)", fmt, sr, ch, self.soniox_lang_hints)
            return True
        except Exception as e:
            logging.error("FLOW STT: Soniox connect/init failed: %s", e)
            self.soniox_ws = None
            return False

    async def _soniox_keepalive_loop(self):
        """Keep Soniox alive across silences; exits on termination."""
        try:
            while self.soniox_ws and not self.call.terminated:
                await asyncio.sleep(self.soniox_keepalive_sec)
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "keepalive"}))
                logging.debug("FLOW STT: keepalive sent")
        except asyncio.CancelledError:
            pass

    async def _soniox_recv_loop(self):
        if not self.soniox_ws:
            logging.info("FLOW STT: recv loop not started (no WS)")
            return
        logging.info("FLOW STT: recv loop started")
        try:
            async for raw in self.soniox_ws:
                if isinstance(raw, (bytes, bytearray)):
                    # Soniox messages are JSON text; ignore binary
                    continue

                msg = json.loads(raw)

                if msg.get("error_code"):
                    logging.error("FLOW STT: Soniox error %s: %s", msg.get("error_code"), msg.get("error_message"))
                    continue

                if msg.get("finished"):
                    logging.info("FLOW STT: finished marker")
                    await self._flush_soniox_segment()
                    break

                tokens = msg.get("tokens") or []
                if not tokens:
                    continue

                finals = [t.get("text", "") for t in tokens if t.get("is_final")]
                nonfinals = [t.get("text", "") for t in tokens if not t.get("is_final")]
                has_nonfinal = any(not t.get("is_final") for t in tokens)
                
                # Log partial transcripts (non-final)
                if nonfinals:
                    logging.info("🎤 STT (partial): %s", "".join(nonfinals))
                
                if finals:
                    final_text = "".join(finals)
                    logging.info("✅ STT (final): %s", final_text)
                    self._soniox_accum.append(final_text)

                if (finals and not has_nonfinal) or any(t.get("text") == "<fin>" for t in tokens):
                    await self._flush_soniox_segment()

        except Exception as e:
            logging.error("FLOW STT: recv loop error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                if self.soniox_ws:
                    await self.soniox_ws.close()
                    logging.info("FLOW STT: Soniox WS closed (recv loop exit)")
            self.soniox_ws = None

    async def _flush_soniox_segment(self):
        """Forward finalized Persian transcript to OpenAI to trigger TTS."""
        if not self._soniox_accum:
            return
        text = "".join(self._soniox_accum).strip()
        self._soniox_accum.clear()
        if not text:
            return
        logging.info("=" * 80)
        logging.info("SONIOX TRANSCRIPT (Persian): %s", text)
        logging.info("=" * 80)
        await self._send_user_text_to_openai(text)

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
            logging.info("FLOW TTS: forwarded transcript to OpenAI (response.create issued)")
        except Exception as e:
            logging.error("FLOW TTS: forwarding transcript failed: %s", e)

    # ---------------------- audio ingress ----------------------
    async def send(self, audio):
        """Primary audio path: RTP bytes -> Soniox; (opt) also to OpenAI."""
        if self.call.terminated:
            logging.debug("FLOW media: drop audio (call terminated)")
            return

        # Process audio for Soniox: convert G.711 to PCM and upsample for better quality
        processed_audio = self._process_audio_for_soniox(audio)
        
        # Send to Soniox (PCM at 16kHz for better recognition)
        try:
            if self.soniox_ws:
                # Check if WebSocket is still open before sending
                if self.soniox_ws.closed:
                    logging.warning("FLOW media: Soniox WS is closed, cannot send audio")
                    self.soniox_ws = None
                else:
                    await self.soniox_ws.send(processed_audio)
                    logging.debug("FLOW media: sent %d bytes → Soniox (processed from %d bytes)", 
                                 len(processed_audio), len(audio))
            elif self._fallback_whisper_enabled and self.ws:
                # if in fallback mode, audio must also go to OpenAI's input buffer
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
            else:
                logging.debug("FLOW media: Soniox WS not ready yet")
        except ConnectionClosedError as e:
            logging.error("FLOW media: Soniox WS closed while sending audio: %s", e)
            self.soniox_ws = None
            # Try to enable fallback if Soniox fails
            if not self._fallback_whisper_enabled:
                logging.warning("FLOW media: Soniox connection lost, enabling Whisper fallback")
                await self._enable_whisper_fallback()
        except Exception as e:
            error_str = str(e)
            logging.error("FLOW media: error sending audio to Soniox: %s", e)
            # If it's a WebSocket error (connection closed), mark connection as closed
            if "1000" in error_str or "closed" in error_str.lower() or "ConnectionClosed" in str(type(e)):
                logging.warning("FLOW media: Soniox WS connection error detected, marking as closed")
                self.soniox_ws = None
                if not self._fallback_whisper_enabled:
                    logging.warning("FLOW media: Enabling Whisper fallback due to Soniox connection error")
                    await self._enable_whisper_fallback()

        # (Optional) also forward to OpenAI input even when Soniox is on (generally not needed)
        if self.forward_audio_to_openai and self.ws:
            try:
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
            except Exception as e:
                logging.warning("FLOW media: forward-to-OpenAI failed (ignored): %s", e)

    # ---------------------- shutdown ----------------------
    async def close(self):
        """Close Soniox first (avoid concurrent limit), then OpenAI."""
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
