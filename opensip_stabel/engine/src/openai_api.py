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

        # Assistant behavior (Persian) - Restaurant ordering
        self.instructions = (
            "با لحنی گرم و پر انرژی صحبت کن "
            "فقط و فقط فارسی صحبت کن ، به هیچ زبان دیگه ای بجز فارسی صحبت نکن."
            " تو یک دستیار هوشمند رستوران بزرگمهر هستی. همیشه حرفه‌ای و مودب و بااحترام و پر انرژی و شاد حرف میزنی . "
            "همیشه با لحن مودب و با احترام و پر انرژی حرف بزن"
            "وظیفه تو دریافت سفارش غذا و اعلام وضعیت سفارشات قبلی است. "
            "سناریو: ابتدا سلام کن و بپرس کاربر میخواهد سفارش جدید ثبت کند یا پیگیری سفارش قبلی داشته باشد. "
            "اگر پیگیری سفارش: شماره تلفن او را بگیر و با استفاده از تابع track_order وضعیت سفارش را اعلام کن. "
            
            "اگر ثبت سفارش جدید: "
            "1) نام مشتری را بپرس "
            "2) بپرس چیزی مدنظرش هست یا منو پیامک بشود؟ اگر خواست پیشنهادات ویژه رستوران را با get_menu_specials بگیر و بگو "
            "3) سفارش غذای اصلی را بگیر، اگر عین آن غذا موجود نبود شبیه‌ترین را با search_menu_item بیاب و پیشنهاد بده "
            "4) آدرس تحویل را بگیر  و مطمعن بشو که شماره تلفن رو گرفتی"
            "5) همه موارد سفارش را تایید کن و با create_order ثبت کن. "
            "همیشه طبیعی و دوستانه صحبت کن."
            " به هیچ وجه اشاره ای به جنسیت شخص نکن  (مثل خطاب کردن و گفتن آقا یا خانم)"
            "کاربر از تو چیزی خارج از سفارش نمیپرسه ، پس اگر موقع انتخاب غذاها چیزی شنیدی که انگار مرتبط با غذا نیست بررسی کن ببین شبیه ترین چیز به یکی از اسم های غذا چی بود بعد یکی از غذاها رو در نظر بگیر و ازش بپرس که آیا منظورش این بود یا نه . مثلا اگر کاربر کفت کووید میخواستم ، بگو کوبیده  فرمودین ؟ فقط اگر چیزی گفت که اسم غذا نبود مستقیما."
            "با مشتری حرفه ای و با لحن احترام سخن بگو و با تو خطاب نکن ، همیشه از کلمه ی شما استفاده کن"
        )
        # Fixed: use correct parameter order (option, env, fallback)
        self.intro = self.cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", ". سلام و درود بر شما،با رستوران بزرگمهر تماس گرفته اید . درخدمتم. ")
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        # state for tools
        self.temp_order_data = {}  # Temporary storage for order being placed

        # === codec mapping ===
        if self.codec.name == "mulaw":
            self.codec_name = "g711_ulaw"
        elif self.codec.name == "alaw":
            self.codec_name = "g711_alaw"
        else:
            self.codec_name = "g711_ulaw"

        # === Soniox config & state ===
        self.soniox_cfg = Config.get("soniox", cfg)
        self.soniox_enabled = bool(self.soniox_cfg.get("enabled", "SONIOX_ENABLED", True))
        # دریافت کلید از config یا environment variable
        self.soniox_key = self.soniox_cfg.get("key", "SONIOX_API_KEY")
        self.soniox_url = self.soniox_cfg.get("url", "SONIOX_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
        self.soniox_model = self.soniox_cfg.get("model", "SONIOX_MODEL", "stt-rt-preview")
        self.soniox_lang_hints = self.soniox_cfg.get("language_hints", "SONIOX_LANGUAGE_HINTS", ["fa"])
        self.soniox_enable_diar = bool(self.soniox_cfg.get("enable_speaker_diarization", "SONIOX_ENABLE_DIARIZATION", True))
        self.soniox_enable_lid = bool(self.soniox_cfg.get("enable_language_identification", "SONIOX_ENABLE_LID", True))
        self.soniox_enable_epd = bool(self.soniox_cfg.get("enable_endpoint_detection", "SONIOX_ENABLE_ENDPOINT", True))
        self.soniox_keepalive_sec = int(self.soniox_cfg.get("keepalive_sec", "SONIOX_KEEPALIVE_SEC", 15))

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
        """Returns the preferred codec from a list"""
        codecs = get_codecs(sdp)
        priority = ["pcma", "pcmu"]
        cmap = {c.name.lower(): c for c in codecs}
        for codec in priority:
            if codec in cmap:
                return CODECS[codec](cmap[codec])
        raise UnsupportedCodec("No supported codec found")

    def get_audio_format(self):
        """Returns the corresponding audio format string used by your existing code"""
        return self.codec_name

    def _soniox_audio_format(self):
        """Map RTP codec to Soniox raw input config."""
        if self.codec_name == "g711_ulaw":
            return ("mulaw", 8000, 1)
        if self.codec_name == "g711_alaw":
            return ("alaw", 8000, 1)
        return ("pcm_s16le", 16000, 1)

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

        # Build session
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
                 "description": "Call me when any of the session's parties want to terminate the call. "
                                "Always say goodbye before hanging up. Send the audio first, then call this function.",
                 "parameters": {"type": "object", "properties": {}, "required": []}},
                {"type": "function", "name": "transfer_call",
                 "description": "call the function if a request was received to transfer a call with an operator, a person",
                 "parameters": {"type": "object", "properties": {}, "required": []}},
                {
                    "type": "function",
                    "name": "track_order",
                    "description": "پیگیری سفارش قبلی بر اساس شماره تلفن مشتری. وضعیت سفارش را برمی‌گرداند.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "phone_number": {"type": "string", "description": "شماره تلفن مشتری برای پیگیری سفارش"},
                        },
                        "required": ["phone_number"],
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
                    "description": "ثبت سفارش نهایی در سیستم. باید همه اطلاعات مشتری و لیست غذاها کامل باشد.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string", "description": "نام مشتری"},
                            "phone_number": {"type": "string", "description": "شماره تلفن مشتری"},
                            "address": {"type": "string", "description": "آدرس تحویل سفارش"},
                            "items": {
                                "type": "array",
                                "description": "لیست آیتم‌های سفارش شامل نام غذا و تعداد",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "item_name": {"type": "string", "description": "نام دقیق غذا از منو"},
                                        "quantity": {"type": "integer", "description": "تعداد", "minimum": 1, "default": 1}
                                    },
                                    "required": ["item_name"],
                                }
                            },
                            "notes": {"type": "string", "description": "یادداشت یا توضیحات اضافی (اختیاری)", "nullable": True},
                        },
                        "required": ["customer_name", "phone_number", "address", "items"],
                        "additionalProperties": False
                    }
                },
            ],
            "tool_choice": "auto",
        }
        if self.instructions:
            self.session["instructions"] = self.instructions

        # Send session update and optional intro
        await self.ws.send(json.dumps({"type": "session.update", "session": self.session}))
        logging.info("FLOW start: OpenAI session.update sent")

        if self.intro:
            intro_payload = {
                "modalities": ["text", "audio"],  # CRITICAL: Force audio output!
                "instructions": "Please greet the user with the following: " + self.intro
            }
            await self.ws.send(json.dumps({"type": "response.create", "response": intro_payload}))
            logging.info("FLOW start: intro response.create sent (with audio modality)")

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
                    await self.terminate_call()

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
                    # Track order by phone number
                    phone_number = args.get("phone_number")
                    normalized_phone = normalize_phone_number(phone_number)
                    logging.info("🔍 TRACKING ORDER")
                    logging.info("  Original: %s", phone_number)
                    logging.info("  Normalized: %s", normalized_phone)
                    
                    try:
                        result = await api.track_order(phone_number)
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
                    # Create restaurant order
                    customer_name = args.get("customer_name")
                    phone_number = args.get("phone_number") or self.call.from_number or "unknown"
                    address = args.get("address")
                    items = args.get("items", [])
                    notes = args.get("notes")
                    
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
                            output = {
                                "success": True,
                                "message": f"سفارش شماره {order.get('id')} با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                                "order_id": order.get("id"),
                                "total_price": order.get("total_price")
                            }
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

        # Send to Soniox (raw mulaw/alaw/pcm)
        try:
            if self.soniox_ws:
                await self.soniox_ws.send(audio)
                logging.debug("FLOW media: sent %d bytes → Soniox", len(audio))
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
        except Exception as e:
            logging.error("FLOW media: error sending audio to Soniox: %s", e)

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
