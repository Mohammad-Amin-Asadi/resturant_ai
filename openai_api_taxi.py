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
import os
import urllib.parse
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    import requests
    HAS_AIOHTTP = False

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

        # Assistant behavior (Persian)
        self.instructions = (
            "تو یک اپراتور تاکسی به نام تاکسی وی آی پی هستی و همیشه مودب و محترمانه و حرفه‌ای "
            "از کاربر می‌خواهی فقط نام خودش را بگوید؛ بعد از دریافت نام، فقط مبدا را می‌پرسی، سپس فقط مقصد را."
        )
        # Fixed: use correct parameter order (option, env, fallback)
        self.intro = self.cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", " درود بر شما، باتاکسیِ وی آی پی تماس گرفته اید، لطفا جهت رِزِرو تاکسی نام خود را بفرمایید.")
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        # state for tools
        self.temp_dict_users_taxi_info = {}

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
            
            logging.info(f"Weather API: Fetching weather for city: {city} (URL: {api_url})")
            
            # Make HTTP request (using requests since we're in a thread)
            response = requests.get(api_url, timeout=10)
            
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
            
            logging.info(f"Weather API: Successfully fetched weather for {city}")
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
                    "name": "get_wallet_balance",
                    "description": "Lookup and return the user's wallet balance by customer_id.",
                    "parameters": {"type": "object",
                                   "properties": {"customer_id": {"type": "string", "description": "Customer/account identifier."}},
                                   "additionalProperties": False}
                },
                {
                    "type": "function",
                    "name": "schedule_meeting",
                    "description": "Create a meeting if no other meeting exists at the same date and time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Date in YYYY-MM-DD."},
                            "time": {"type": "string", "description": "Time in HH:MM (24h)."},
                            "when": {"type": "string", "description": "Natural language like 'tomorrow 3pm' or 'فردا ساعت ۱۵'."},
                            "customer_id": {"type": "string", "description": "Customer/account identifier.", "nullable": True},
                            "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 480, "default": 30},
                            "subject": {"type": "string", "description": "Optional subject/title.", "maxLength": 200},
                        },
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "get_origin_destination_userame",
                    "description": "Extract origin, destination, and user name from the conversation and get sure to save it in databse. even if one of these inputs were available execute the function and save it in database.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "origin": {"type": "string", "description": "The starting point of the trip."},
                            "destination": {"type": "string", "description": "The endpoint of the trip."},
                            "user_name": {"type": "string", "description": "The name of the user."},
                        },
                        "required": ["origin", "destination", "user_name"],
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get current weather information for a city. Use this when the user asks about weather conditions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "The name of the city to get weather for (e.g., 'مشهد', 'تهران', 'اصفهان')."},
                        },
                        "required": ["city"],
                        "additionalProperties": False,
                    },
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
                logging.info("OpenAI said: %s", msg["transcript"])

            elif t == "response.function_call_arguments.done":
                global call_id
                unique_time = time.time()
                call_id = msg.get("call_id")
                name = msg.get("name")
                logging.info("FLOW tool: %s called", name)
                try:
                    args = json.loads(msg.get("arguments") or "{}")
                except Exception:
                    args = {}

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

                elif name == "get_origin_destination_userame":
                    origin = args.get("origin")
                    destination = args.get("destination")
                    user_name = args.get("user_name")
                    logging.info("FLOW tool: Extracted - user=%s origin=%s dest=%s", user_name, origin, destination)

                    if user_name is not None:
                        self.temp_dict_users_taxi_info.update({unique_time: {"user_name": user_name}})
                        logging.info("FLOW tool: user_name received, resetting temp dict")

                    if origin is not None:
                        self.temp_dict_users_taxi_info.update({unique_time: {"origin": origin}})
                        logging.info("FLOW tool: origin received, resetting temp dict")
                    
                    if destination is not None:
                        self.temp_dict_users_taxi_info.update({unique_time: {"destination": destination}})
                        logging.info("FLOW tool: destination received, resetting temp dict")

                    logging.info(f"Temporary dict state: {self.temp_dict_users_taxi_info}")
                    api_result = api(fullname=user_name, origin=origin, destination=destination)

                    logging.info(f"API result: {api_result}")
                    if api_result:
                        logging.info("Data successfully sent to server")
                    else:
                        logging.warning("Failed to send data to server")

                    if (self.temp_dict_users_taxi_info.get(unique_time, {}).get("user_name") and
                        self.temp_dict_users_taxi_info.get(unique_time, {}).get("origin") and
                        self.temp_dict_users_taxi_info.get(unique_time, {}).get("destination")):

                        logging.info("All required information received, sending to OpenAI and resetting temp dict")

                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output",
                                     "call_id": call_id,
                                     "unique_time": unique_time,
                                     "output": json.dumps({
                                     "origin": origin, "destination": destination, "user_name": user_name
                                     }, ensure_ascii=False)}
                        }))
                        logging.info(f"TEXT FINAL: , {user_name}, {origin}, {destination} ")
                    
                            
                    else:
                        missing = []
                        if not self.temp_dict_users_taxi_info.get(unique_time, {}).get("user_name"):
                            missing.append("نام")
                        if not self.temp_dict_users_taxi_info.get(unique_time, {}).get("origin"):
                            missing.append("مبدا")
                        if not self.temp_dict_users_taxi_info.get(unique_time, {}).get("destination"):
                            missing.append("مقصد")
                        await self.ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output",
                                     "unique_time": unique_time,
                                     "call_id": call_id,
                                     "output": json.dumps({
                                         "error": f"لطفاً {' و '.join(missing)} را مجدداً بفرمایید."
                                     }, ensure_ascii=False)}
                        }))
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))

                elif name == "get_weather":
                    city = args.get("city")
                    logging.info("FLOW tool: get_weather called for city: %s", city)
                    
                    def _get_weather():
                        return self._fetch_weather(city)
                    
                    result = await self.run_in_thread(_get_weather)
                    await self.ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {"type": "function_call_output", "call_id": call_id,
                                 "output": json.dumps(result, ensure_ascii=False)}
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
                has_nonfinal = any(not t.get("is_final") for t in tokens)
                if finals:
                    self._soniox_accum.append("".join(finals))

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
        logging.info("FLOW STT: Transcript (fa): %s", text)
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
