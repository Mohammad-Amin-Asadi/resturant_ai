"""
Soniox STT handler for OpenAI engine - handles Soniox WebSocket connection and transcript processing.
"""

import json
import re
import logging
import asyncio
import contextlib
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from .audio_processor import AudioProcessor

logger = logging.getLogger(__name__)


class SonioxHandler:
    """Handles Soniox STT connection and transcript processing"""
    
    def __init__(self, openai_instance):
        """
        Initialize Soniox handler.
        
        Args:
            openai_instance: OpenAI instance (for access to call, ws, etc.)
        """
        self.openai = openai_instance
        self.soniox_ws = None
        self.soniox_task = None
        self.soniox_keepalive_task = None
        self._soniox_accum = []
        self._soniox_flush_timer = None
    
    async def connect(self) -> bool:
        """Connect to Soniox STT service"""
        key = self.openai.soniox_key if self.openai.soniox_key and self.openai.soniox_key != "SONIOX_API_KEY" else None
        if not key:
            logger.warning("⚠️  Soniox API key not configured, cannot connect")
            return False
        try:
            logger.info("🔌 Connecting to Soniox STT: %s", self.openai.soniox_url)
            self.soniox_ws = await connect(self.openai.soniox_url)
            logger.info("✅ Soniox WebSocket connected successfully")
            fmt, sr, ch = AudioProcessor.get_soniox_audio_format()
            init = {
                "api_key": key,
                "model": self.openai.soniox_model,
                "audio_format": fmt,
                "sample_rate": sr,
                "num_channels": ch,
                "language_hints": self.openai.soniox_lang_hints,
                "enable_speaker_diarization": self.openai.soniox_enable_diar,
                "enable_language_identification": self.openai.soniox_enable_lid,
                "enable_endpoint_detection": self.openai.soniox_enable_epd,
                "language": "fa"
            }
            if hasattr(self.openai, 'soniox_context_phrases') and self.openai.soniox_context_phrases:
                try:
                    init["context_phrases"] = self.openai.soniox_context_phrases
                except (KeyError, TypeError):
                    # Context phrases not supported or invalid format - skip silently
                    pass
            
            await self.soniox_ws.send(json.dumps(init))
            
            try:
                confirmation = await asyncio.wait_for(self.soniox_ws.recv(), timeout=5.0)
                if isinstance(confirmation, (bytes, bytearray)):
                    return False
                conf_msg = json.loads(confirmation)
                if conf_msg.get("error_code"):
                    logger.error("Soniox init error: %s", conf_msg.get("error_message"))
                    return False
                return True
            except asyncio.TimeoutError:
                # Timeout is acceptable - connection may still be valid
                return True
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Soniox confirmation decode error (assuming success): %s", e)
                return True
            except Exception as e:
                # Other errors during confirmation - assume connection is OK
                logger.warning("Soniox confirmation error (assuming success): %s", e)
                return True
        except Exception as e:
            logger.error("Soniox connect failed: %s", e, exc_info=True)
            self.soniox_ws = None
            return False
    
    async def start_loops(self):
        """Start receive and keepalive loops"""
        if self.soniox_ws:
            self.soniox_task = asyncio.create_task(self._recv_loop(), name="soniox-recv")
            self.soniox_keepalive_task = asyncio.create_task(self._keepalive_loop(), name="soniox-keepalive")
    
    async def _keepalive_loop(self):
        """Keep Soniox alive across silences"""
        try:
            while self.soniox_ws and not self.openai.call.terminated:
                await asyncio.sleep(self.openai.soniox_keepalive_sec)
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "keepalive"}))
        except asyncio.CancelledError:
            pass
    
    async def _recv_loop(self):
        """Receive loop for Soniox STT"""
        if not self.soniox_ws:
            logger.warning("⚠️  Soniox WebSocket not available, cannot start receive loop")
            return
        logger.info("🎧 Soniox receive loop started, waiting for transcripts...")
        try:
            async for raw in self.soniox_ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse JSON: %s", e)
                    continue
                
                if msg.get("error_code"):
                    logger.error("Soniox error: %s", msg.get("error_message"))
                    continue
                
                if msg.get("finished"):
                    if self._soniox_flush_timer:
                        self._soniox_flush_timer.cancel()
                        self._soniox_flush_timer = None
                    await self._flush_segment()
                    break
                
                tokens = msg.get("tokens") or []
                if not tokens:
                    continue
                
                finals = [t.get("text", "") for t in tokens if t.get("is_final")]
                has_nonfinal = any(not t.get("is_final") for t in tokens)
                
                if finals:
                    final_text = "".join(finals)
                    logger.info("🎤 Soniox transcript (final): '%s'", final_text)
                    if final_text and final_text not in ["<end>", "<fin>", "<start>"]:
                        self._soniox_accum.append(final_text)
                        logger.debug("FLOW STT: Added to accumulator (total segments: %d)", len(self._soniox_accum))
                        if self._soniox_flush_timer:
                            self._soniox_flush_timer.cancel()
                        if not has_nonfinal:
                            logger.info("FLOW STT: Scheduling delayed flush (no non-final tokens)")
                            self._soniox_flush_timer = asyncio.create_task(
                                self._delayed_flush()
                            )
                    else:
                        logger.debug("FLOW STT: Ignoring control token: %s", final_text)
                
                # Log non-final tokens for debugging
                non_finals = [t.get("text", "") for t in tokens if not t.get("is_final")]
                if non_finals:
                    logger.debug("🎤 Soniox transcript (partial): '%s'", "".join(non_finals))
                
                if any(t.get("text") == "<fin>" for t in tokens):
                    logger.info("FLOW STT: <fin> token received, flushing immediately")
                    if self._soniox_flush_timer:
                        self._soniox_flush_timer.cancel()
                        self._soniox_flush_timer = None
                    await self._flush_segment()
        
        except (ConnectionClosedError, ConnectionClosedOK) as e:
            logger.info("Soniox connection closed: %s", e)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Soniox message decode error: %s", e)
        except Exception as e:
            logger.error("Unexpected error in Soniox recv loop: %s", e, exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                if self.soniox_ws:
                    await self.soniox_ws.close()
            self.soniox_ws = None
    
    async def _delayed_flush(self):
        """Delayed flush for Soniox segments"""
        try:
            delay = self.openai.soniox_silence_duration_ms / 1000.0
            logger.info("FLOW STT: Delayed flush scheduled, waiting %.2f seconds", delay)
            await asyncio.sleep(delay)
            if self._soniox_flush_timer and not self._soniox_flush_timer.cancelled():
                logger.info("FLOW STT: Delayed flush timer expired, flushing segment")
                await self._flush_segment()
                self._soniox_flush_timer = None
            else:
                logger.debug("FLOW STT: Delayed flush cancelled or already flushed")
        except asyncio.CancelledError:
            logger.debug("FLOW STT: Delayed flush cancelled")
    
    def _correct_common_misrecognitions(self, text: str) -> str:
        """Correct common STT misrecognitions"""
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
            logger.info("STT correction: '%s' -> '%s'", original_text, corrected)
        
        return corrected
    
    async def _flush_segment(self):
        """Forward finalized transcript to OpenAI"""
        if not self._soniox_accum:
            logger.debug("FLOW STT: No accumulated text to flush")
            return
        text = "".join(self._soniox_accum).strip()
        self._soniox_accum.clear()
        if not text:
            logger.debug("FLOW STT: Accumulated text is empty after strip")
            return
        
        corrected_text = self._correct_common_misrecognitions(text)
        logger.info("🎤 Soniox transcript: '%s' (length: %d, is_final=True)", corrected_text, len(corrected_text))
        await self._send_user_text_to_openai(corrected_text)
    
    async def _send_user_text_to_openai(self, text: str):
        """Send user text to OpenAI"""
        cleaned_text = text.replace("<end>", "").strip()
        if not cleaned_text:
            logger.warning("⚠️  FLOW STT: Empty transcript after cleaning, skipping")
            return
        
        logger.info("📤 FLOW STT: Forwarding transcript to OpenAI as user message: '%s'", cleaned_text)
        try:
            user_msg = {
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": cleaned_text}]}
            }
            await self.openai.ws.send(json.dumps(user_msg))
            logger.info("✅ FLOW STT: conversation.item.create sent for user message: '%s'", cleaned_text)
            
            response_msg = {
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]}
            }
            await self.openai.ws.send(json.dumps(response_msg))
            logger.info("✅ FLOW STT: response.create sent - waiting for OpenAI response to: '%s'", cleaned_text)
        except (ConnectionClosedError, ConnectionClosedOK) as e:
            logger.warning("⚠️  OpenAI connection closed while sending transcript: %s", e)
        except (json.JSONEncodeError, UnicodeEncodeError) as e:
            logger.error("❌ Error encoding transcript for OpenAI: %s", e)
        except Exception as e:
            logger.error("❌ Unexpected error forwarding transcript to OpenAI: %s", e, exc_info=True)
    
    async def send_audio(self, audio_data):
        """Send audio to Soniox"""
        if not self.soniox_ws:
            logger.debug("⚠️  Soniox WebSocket not connected, cannot send audio")
            return False
        
        try:
            processed_audio = AudioProcessor.process_audio_for_soniox(
                audio_data, 
                self.openai.codec, 
                self.openai.soniox_upsample
            )
            # Track audio chunks sent for logging (DEBUG level only - too verbose for INFO)
            if not hasattr(self, '_audio_chunk_count'):
                self._audio_chunk_count = 0
            self._audio_chunk_count += 1
            
            logger.debug("🎤 Soniox: sending audio chunk #%d, size=%d bytes (processed from %d bytes)", 
                      self._audio_chunk_count, len(processed_audio), len(audio_data))
            await self.soniox_ws.send(processed_audio)
            return True
        except (ConnectionClosedError, ConnectionClosedOK) as e:
            self.soniox_ws = None
            logger.info("ℹ️  Soniox connection closed (normal): %s", e)
            return False
        except (ValueError, TypeError) as e:
            logger.error("❌ Invalid audio data for Soniox: %s", e)
            return False
        except Exception as e:
            # Check if it's a connection-related error
            if "closed" in str(e).lower() or "ConnectionClosed" in str(type(e).__name__):
                self.soniox_ws = None
                logger.warning("⚠️  Soniox connection error: %s", e)
            else:
                logger.error("❌ Unexpected error sending audio to Soniox: %s", e, exc_info=True)
            return False
    
    async def close(self):
        """Close Soniox connection and cancel tasks"""
        # Cancel background tasks
        for t in (self.soniox_keepalive_task, self.soniox_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        
        # Close Soniox WebSocket
        try:
            if self.soniox_ws:
                with contextlib.suppress(Exception):
                    await self.soniox_ws.send(json.dumps({"type": "finalize"}))
                await self.soniox_ws.close()
                logger.info("FLOW close: Soniox WS closed")
        finally:
            self.soniox_ws = None
