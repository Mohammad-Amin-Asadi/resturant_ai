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
from queue import Empty
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from ai import AIEngine
from codec import get_codecs, CODECS, UnsupportedCodec
from config import Config
from storage import WalletMeetingDB
from api_sender import API
from phone_normalizer import normalize_phone_number
from sms_service import SMSService
from datetime import datetime

# Import modular components
from openai_engine.config_loader import ConfigLoader
from openai_engine.prompts_builder import PromptsBuilder
from openai_engine.audio_processor import AudioProcessor
from openai_engine.function_handlers import FunctionHandlers
from openai_engine.soniox_handler import SonioxHandler
from openai_engine.utils import DateTimeUtils, NumberConverter

# Backend server URL - should be set via environment variable
# Default to localhost only for development
# Backend server URL - defaults to Docker service name for containerized deployments
BACKEND_SERVER_URL = os.getenv("BACKEND_SERVER_URL", "http://backend-restaurant:8000")
environment = os.getenv("ENVIRONMENT", "production")
if BACKEND_SERVER_URL == "http://localhost:8000" and environment == "production":
    import warnings
    warnings.warn(
        "BACKEND_SERVER_URL is using default localhost value in production! "
        "This will not work in Docker. Set BACKEND_SERVER_URL to Docker service name (e.g., http://backend-restaurant:8000).",
        UserWarning
    )

# Configure logging level from environment variable (if not already configured)
import os
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, LOG_LEVEL, logging.INFO)
if not isinstance(log_level, int):
    log_level = logging.INFO

logging.basicConfig(
    level=log_level,
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

        # === Load DID configuration using ConfigLoader ===
        base_cfg = Config.get("openai", cfg)
        self.did_config, self.did_number, backend_url = ConfigLoader.load_did_config_for_call(call, cfg)
        
        # === Merge base config with DID config ===
        self.cfg = ConfigLoader.merge_openai_config(base_cfg, self.did_config)
        
        # === Backend API setup ===
        if not backend_url:
            backend_url = BACKEND_SERVER_URL
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
        self.intro = ConfigLoader.get_welcome_message(self.did_config, self.cfg)
        
        # === Initialize handlers ===
        self.function_handlers = FunctionHandlers(self)
        self.soniox_handler = SonioxHandler(self)
        
        # === SMS Service (with DID config) ===
        self.sms_service = SMSService(self.did_config)
        
        # === Transfer settings ===
        self.transfer_to = self.cfg.get("transfer_to", "OPENAI_TRANSFER_TO", None)
        self.transfer_by = self.cfg.get("transfer_by", "OPENAI_TRANSFER_BY", self.call.to)

        # === State variables (service-agnostic) ===
        self.temp_data = {}  # Generic temp storage for any service
        self.customer_name_from_history = None
        self.recent_order_ids = set()
        self.last_order_time = None

        # === Codec mapping using AudioProcessor ===
        self.codec_name = AudioProcessor.get_codec_name(self.codec)

        # === Soniox config (merged with DID config) ===
        base_soniox_cfg = Config.get("soniox", cfg)
        self.soniox_cfg = ConfigLoader.merge_soniox_config(base_soniox_cfg, self.did_config)
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
        
        # === Soniox state (delegated to SonioxHandler) ===
        self.soniox_silence_duration_ms = int(self.soniox_cfg.get("silence_duration_ms", "SONIOX_SILENCE_DURATION_MS", 500))
        self._order_confirmed = False
        self.forward_audio_to_openai = bool(self.soniox_cfg.get("forward_audio_to_openai", "FORWARD_AUDIO_TO_OPENAI", False))
        self._fallback_whisper_enabled = False

    # ---------------------- Utility methods (delegated to modules) ----------------------
    def _to_ascii_digits(self, s: str) -> str:
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.to_ascii_digits(s)

    def _now_tz(self):
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.now_tz(self.timezone)

    def _extract_time(self, text: str):
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.extract_time(text)

    def _parse_natural_date(self, text: str, now):
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.parse_natural_date(text, now)

    def _normalize_date(self, s: str):
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.normalize_date(s)

    def _normalize_time(self, s: str):
        """Delegate to DateTimeUtils"""
        return DateTimeUtils.normalize_time(s)

    def _convert_numbers_to_persian_words(self, text: str) -> str:
        """Delegate to NumberConverter"""
        return NumberConverter.convert_to_persian_words(text)

    def _convert_numbers_in_output(self, output):
        """Delegate to NumberConverter"""
        return NumberConverter.convert_in_output(output)

    def _interpret_meeting_datetime(self, args: dict):
        """Delegate to MeetingUtils"""
        from openai_engine.meeting_utils import MeetingUtils
        return MeetingUtils.interpret_meeting_datetime(args, self.timezone)

    # ---------------------- codec helpers (delegated to AudioProcessor) ----------------------
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
        return AudioProcessor.get_audio_format(self.codec_name)

    # ---------------------- Function definitions and prompts (delegated to PromptsBuilder) ----------------------
    def _get_function_definitions(self):
        """Load function definitions from DID config, with fallback to defaults."""
        prompts_builder = PromptsBuilder(self.did_config, self.customer_name_from_history)
        return prompts_builder.get_function_definitions()

    def _get_scenario_config(self, scenario_type):
        """Get scenario configuration from DID config."""
        prompts_builder = PromptsBuilder(self.did_config, self.customer_name_from_history)
        return prompts_builder.get_scenario_config(scenario_type)

    def _build_instructions_from_config(self, has_undelivered_order=False, orders=None):
        """Build instructions from DID config, with scenario support."""
        prompts_builder = PromptsBuilder(self.did_config, self.customer_name_from_history)
        return prompts_builder.build_instructions(has_undelivered_order, orders)

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
            logging.info("🔍 Checking customer info for: %s (normalized: %s)", phone_number, normalized_phone)
            
            # Try to get customer info - but don't fail if it returns 400
            try:
                customer_info = await self.api.get_customer_info(normalized_phone)
                if customer_info.get("success") and customer_info.get("customer"):
                    self.customer_name_from_history = customer_info["customer"].get("name")
                    logging.info("✅ Customer info retrieved: name=%s", self.customer_name_from_history)
                else:
                    logging.debug("ℹ️  Customer info not found or not successful (continuing)")
            except Exception as e:
                # Backend 400/500 errors should not break the call
                logging.warning("⚠️  Could not get customer info (continuing): %s", e)
            
            # Try to track orders - but don't fail if it returns 400
            try:
                result = await self.api.track_order(normalized_phone)
                if not result or not result.get("success"):
                    logging.debug("ℹ️  No orders found or track_order failed (continuing)")
                    return False, []
                
                orders = result.get("orders", [])
                if not orders:
                    logging.debug("ℹ️  No orders found for customer (continuing)")
                    return False, []
                
                undelivered = [o for o in orders if o.get("status") not in ["delivered", "cancelled"]]
                
                if undelivered:
                    if not self.customer_name_from_history:
                        self.customer_name_from_history = undelivered[0].get('customer_name')
                    logging.info("✅ Found %d undelivered order(s)", len(undelivered))
                    return True, undelivered
                else:
                    if not self.customer_name_from_history and orders:
                        self.customer_name_from_history = orders[0].get('customer_name')
                    logging.debug("ℹ️  All orders are delivered or cancelled")
                    return False, []
            except Exception as e:
                # Backend 400/500 errors should not break the call
                logging.warning("⚠️  Could not track orders (continuing): %s", e)
                return False, []
                
        except Exception as e:
            logging.warning("⚠️  Exception checking orders (continuing with call): %s", e)
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
        # IMPORTANT: This must NOT block greeting - wrap in try/except and continue on failure
        caller_phone = self.call.from_number
        has_undelivered = False
        orders = None
        try:
            # Only check orders if we have track_order function (restaurant service)
            functions = self._get_function_definitions()
            has_track_order = any(f.get('name') == 'track_order' for f in functions)
            if has_track_order and caller_phone:
                logging.info("🔍 Checking for undelivered orders for phone: %s", caller_phone)
                has_undelivered, orders = await self._check_undelivered_order(caller_phone)
                logging.info("📦 Order check result: has_undelivered=%s, orders_count=%d", 
                           has_undelivered, len(orders) if orders else 0)
        except Exception as e:
            logging.warning("⚠️  Could not check orders (continuing with call): %s", e)
            # Continue with default values - don't block the call
            has_undelivered = False
            orders = None
        
        # Send menu via SMS when caller calls (for restaurant service)
        # IMPORTANT: This must NOT block greeting - run as background task
        if caller_phone:
            functions = self._get_function_definitions()
            has_track_order = any(f.get('name') == 'track_order' for f in functions)
            if has_track_order:
                logging.info("📱 Scheduling menu SMS task for phone: %s", caller_phone)
                asyncio.create_task(self._send_menu_sms(caller_phone))

        # Build instructions and welcome message from config
        # IMPORTANT: This must always succeed - use fallbacks if needed
        try:
            customized_instructions = self._build_instructions_from_config(has_undelivered, orders)
            welcome_message = self._build_welcome_message_from_config(has_undelivered, orders)
        except Exception as e:
            logging.warning("⚠️  Error building welcome message from config (using fallback): %s", e)
            customized_instructions = ""
            welcome_message = None
        
        # Use welcome message from config or fallback to intro
        if not welcome_message:
            welcome_message = self.intro
        
        # Ensure we have a welcome message - use hardcoded fallback if needed
        if not welcome_message or not welcome_message.strip():
            welcome_message = "درود بر شما، با ما تماس گرفته‌اید. لطفاً درخواست خود را بفرمایید."
            logging.warning("⚠️  No welcome message found, using hardcoded fallback")

        logging.info("💬 Welcome message prepared: '%s'", welcome_message)

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
        # CRITICAL: This must always execute - greeting is essential
        if welcome_message:
            try:
                await self.ws.send(json.dumps({
                    "type": "response.create",
                    "response": {"modalities": ["text", "audio"]}
                }))
                logging.info("✅ FLOW start: welcome message trigger sent (message: '%s')", welcome_message[:50])
            except Exception as e:
                logging.error("❌ CRITICAL: Failed to send welcome message trigger: %s", e, exc_info=True)
                # Try again with a simpler message
                try:
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text", "audio"]}
                    }))
                    logging.info("✅ FLOW start: welcome message trigger sent (retry)")
                except Exception as e2:
                    logging.error("❌ CRITICAL: Failed to send welcome message trigger (retry): %s", e2)
        else:
            logging.error("❌ CRITICAL: No welcome message available - call will be silent!")
            # Force a greeting anyway
            try:
                await self.ws.send(json.dumps({
                    "type": "response.create",
                    "response": {"modalities": ["text", "audio"]}
                }))
                logging.info("✅ FLOW start: forced welcome message trigger sent (no message available)")
            except Exception as e:
                logging.error("❌ CRITICAL: Failed to send forced welcome message trigger: %s", e)

        # Connect Soniox using SonioxHandler
        soniox_key_ok = bool(self.soniox_key and self.soniox_key != "SONIOX_API_KEY")
        if self.soniox_enabled and soniox_key_ok:
            logging.info("FLOW STT: SONIOX enabled | model=%s | url=%s", self.soniox_model, self.soniox_url)
            ok = await self.soniox_handler.connect()
            if ok:
                logging.info("✅ Soniox connected successfully, starting receive loops")
                await self.soniox_handler.start_loops()
                logging.info("🎧 Soniox STT ready - listening for audio transcripts")
            else:
                logging.warning("⚠️  FLOW STT: Soniox connect failed; enabling Whisper fallback on OpenAI")
                await self._enable_whisper_fallback()
        else:
            if not soniox_key_ok:
                logging.warning("⚠️  FLOW STT: SONIOX_API_KEY not set; STT fallback will be used")
            else:
                logging.info("ℹ️  FLOW STT: SONIOX disabled by config; using fallback")
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
                packet_count = 0
                for packet in packets:
                    self.queue.put_nowait(packet)
                    packet_count += 1
                if packet_count > 0:
                    # Log audio queuing at DEBUG level (sampled at INFO every 100th)
                    if not hasattr(self, '_tts_packet_count'):
                        self._tts_packet_count = 0
                    self._tts_packet_count += packet_count
                    # Log queued audio packets at DEBUG level only (too verbose for INFO)
                    logging.debug("📤 Queued %d audio packets from OpenAI (total queued: %d, queue size: ~%d)", 
                                packet_count, self._tts_packet_count, self.queue.qsize())

            elif t == "response.audio.done":
                logging.info("FLOW TTS: response.audio.done")
                if len(leftovers) > 0:
                    packet = await self.run_in_thread(self.codec.parse, None, leftovers)
                    self.queue.put_nowait(packet)
                    logging.info("📤 Final audio packet queued: %d bytes", len(packet))
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
                logging.info("💬 OpenAI said: '%s'", transcript)
                
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
                    error_details = status_details.get("error", {})
                    error_message = error_details.get("message", status_details.get("message", "No error message"))
                    error_code = error_details.get("code", status_details.get("code", "unknown"))
                    error_details_type = error_details.get("type", "")
                    
                    logging.error("⚠️ OpenAI response FAILED - Type: %s, Error Type: %s, Code: %s, Message: %s", 
                                error_type, error_details_type, error_code, error_message)
                    
                    # Track retry attempts per response ID to avoid retrying the same response multiple times
                    response_id = response_obj.get("id", "unknown")
                    retry_key = f"_retry_count_{response_id}"
                    if not hasattr(self, retry_key):
                        setattr(self, retry_key, 0)
                    retry_count = getattr(self, retry_key)
                    
                    # Retry logic for server errors (transient errors)
                    # Check if it's a server_error or if message contains "server" or "error processing"
                    is_server_error = (
                        error_details_type == "server_error" or 
                        "server had an error" in error_message.lower() or
                        "error while processing" in error_message.lower()
                    )
                    
                    if is_server_error:
                        if retry_count < 2:  # Max 2 retries
                            setattr(self, retry_key, retry_count + 1)
                            wait_time = 0.5 * (retry_count + 1)  # 0.5s, 1s
                            logging.warning("🔄 Retrying OpenAI response (attempt %d/2) after %.1fs... (response_id: %s)", 
                                          retry_count + 1, wait_time, response_id[:20])
                            await asyncio.sleep(wait_time)
                            
                            # Retry by creating a new response with the same welcome message context
                            try:
                                # Re-send the welcome message or last user input to maintain context
                                await self.ws.send(json.dumps({
                                    "type": "response.create",
                                    "response": {"modalities": ["text", "audio"]}
                                }))
                                logging.info("✅ Retry: New response.create sent (attempt %d/2)", retry_count + 1)
                                continue  # Continue to next event, don't log as final failure yet
                            except Exception as e:
                                logging.error("❌ Failed to send retry response.create: %s", e)
                                setattr(self, retry_key, 2)  # Mark as failed to prevent infinite retries
                        else:
                            logging.error("❌ Max retries reached for OpenAI response (response_id: %s). Giving up.", response_id[:20])
                            # Send a simple fallback message
                            try:
                                fallback_msg = "متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید."
                                await self.ws.send(json.dumps({
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "input_text", "text": fallback_msg}]
                                    }
                                }))
                                await self.ws.send(json.dumps({
                                    "type": "response.create",
                                    "response": {"modalities": ["text", "audio"]}
                                }))
                                logging.info("✅ Fallback message sent after max retries")
                            except Exception as e:
                                logging.error("❌ Failed to send fallback message: %s", e)
                            # Clean up retry tracking for this response
                            if hasattr(self, retry_key):
                                delattr(self, retry_key)
                    # Check for specific error types
                    elif error_code in ["insufficient_quota", "billing_not_active", "invalid_api_key"]:
                        logging.error("🚨 CRITICAL: OpenAI billing/credit issue detected!")
                        # Don't retry for billing issues - clean up retry tracking
                        if hasattr(self, retry_key):
                            delattr(self, retry_key)
                    elif "rate_limit" in error_message.lower() or error_code == "rate_limit_exceeded":
                        logging.error("⚠️ Rate limit exceeded - wait before retrying")
                        # Wait longer for rate limits
                        await asyncio.sleep(2.0)
                        try:
                            await self.ws.send(json.dumps({
                                "type": "response.create",
                                "response": {"modalities": ["text", "audio"]}
                            }))
                            logging.info("✅ Retry after rate limit: New response.create sent")
                        except Exception as e:
                            logging.error("❌ Failed to retry after rate limit: %s", e)
                        # Clean up retry tracking
                        if hasattr(self, retry_key):
                            delattr(self, retry_key)
                    else:
                        # Other errors - clean up retry tracking
                        if hasattr(self, retry_key):
                            delattr(self, retry_key)
                else:
                    # Success - clean up any retry tracking for this response
                    response_id = response_obj.get("id", "unknown")
                    retry_key = f"_retry_count_{response_id}"
                    if hasattr(self, retry_key):
                        delattr(self, retry_key)
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
        """Handle function calls dynamically - delegates to FunctionHandlers."""
        await self.function_handlers.handle_function_call(name, call_id, args)

    # ---------------------- Helper methods (kept for internal use) ----------------------
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
                logging.debug("Could not retrieve menu items for SMS (menu may be empty or API error)")
                return
            
            items = menu_result.get("items", [])
            
            # Get restaurant name from config
            restaurant_name = "رستوران"
            if self.did_config:
                restaurant_name = (self.did_config.get('restaurant_name') or 
                                 self.did_config.get('service_name') or 
                                 'رستوران')
            
            # Format menu message
            menu_text = f"🍽️ منوی {restaurant_name}\n\n"
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
            
            # Send SMS with normalized phone - don't fail if SMS fails
            try:
                sms_sent = self.sms_service.send_sms(normalized_phone, menu_text)
                if sms_sent:
                    logging.info(f"✅ Menu SMS sent to {normalized_phone} (original: {phone_number})")
                else:
                    logging.warning(f"⚠️  Menu SMS failed to send to {normalized_phone} (continuing)")
            except Exception as e:
                logging.warning(f"⚠️  Exception sending menu SMS (continuing): {e}")
            
        except Exception as e:
            logging.error(f"❌ Failed to send menu SMS: {e}", exc_info=True)


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


    # ---------------------- audio ingress ----------------------
    async def send(self, audio):
        """Primary audio path: RTP bytes -> Soniox; (opt) also to OpenAI."""
        if self.call.terminated:
            logging.debug("⏹️  Call terminated, ignoring audio")
            return

        # Track audio received for logging (DEBUG level, sampled at INFO every 100th)
        if not hasattr(self, '_audio_received_count'):
            self._audio_received_count = 0
        self._audio_received_count += 1

        # Log audio received at DEBUG level only (too verbose for INFO)
        logging.debug("🎤 Audio received from RTP: %d bytes (chunk #%d)", len(audio), self._audio_received_count)

        # Send to Soniox via SonioxHandler
        if self.soniox_handler.soniox_ws:
            success = await self.soniox_handler.send_audio(audio)
            if success:
                # Log at DEBUG level only (too verbose for INFO)
                logging.debug("✅ Audio sent to Soniox: %d bytes (chunk #%d)", len(audio), self._audio_received_count)
            else:
                logging.warning("⚠️  Failed to send audio to Soniox (connection may be closed)")
        elif self._fallback_whisper_enabled and self.ws:
            logging.debug("📤 Sending audio to OpenAI (Whisper fallback): %d bytes", len(audio))
            try:
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
            except Exception as e:
                logging.warning("⚠️  Failed to send audio to OpenAI (Whisper fallback): %s", e)

        # Optionally forward to OpenAI
        if self.forward_audio_to_openai and self.ws:
            try:
                logging.debug("📤 Forwarding audio to OpenAI: %d bytes", len(audio))
                await self.ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("utf-8")
                }))
            except Exception as e:
                logging.debug("⚠️  Failed to forward audio to OpenAI: %s", e)

    # ---------------------- shutdown ----------------------
    async def close(self):
        """Close Soniox first, then OpenAI."""
        logging.info("FLOW close: closing sockets (Soniox → OpenAI)")

        # Close Soniox via SonioxHandler
        await self.soniox_handler.close()

        # Then close OpenAI
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
            logging.info("FLOW close: OpenAI WS closed")
