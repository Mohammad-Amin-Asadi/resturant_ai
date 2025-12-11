"""
Soniox STT Adapter interface and implementations.
"""

import logging
import json
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncIterator
from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)


class SonioxAdapter(ABC):
    """Abstract base class for Soniox STT adapters"""
    
    @abstractmethod
    async def connect(self, url: str, api_key: str, config: Dict[str, Any]) -> Any:
        """Connect to Soniox STT service"""
        pass
    
    @abstractmethod
    async def send_audio(self, ws: Any, audio_data: bytes) -> None:
        """Send audio data to Soniox"""
        pass
    
    @abstractmethod
    async def receive_transcripts(self, ws: Any) -> AsyncIterator[Dict[str, Any]]:
        """Receive transcripts from Soniox"""
        pass
    
    @abstractmethod
    async def send_keepalive(self, ws: Any) -> None:
        """Send keepalive message"""
        pass
    
    @abstractmethod
    async def finalize(self, ws: Any) -> None:
        """Finalize transcription"""
        pass
    
    @abstractmethod
    async def close(self, ws: Any) -> None:
        """Close connection"""
        pass


class SonioxSTTAdapter(SonioxAdapter):
    """Soniox STT API adapter implementation"""
    
    async def connect(self, url: str, api_key: str, config: Dict[str, Any]) -> Any:
        """Connect to Soniox STT WebSocket"""
        if not api_key or api_key == "SONIOX_API_KEY":
            raise ValueError("Soniox API key not configured")
        
        logger.info(f"Connecting to Soniox STT: {url}")
        ws = await connect(url)
        
        init_config = {
            "api_key": api_key,
            "model": config.get("model", "stt-rt-preview"),
            "audio_format": config.get("audio_format", "pcm_s16le"),
            "sample_rate": config.get("sample_rate", 16000),
            "num_channels": config.get("num_channels", 1),
            "language_hints": config.get("language_hints", ["fa"]),
            "enable_speaker_diarization": config.get("enable_speaker_diarization", False),
            "enable_language_identification": config.get("enable_language_identification", False),
            "enable_endpoint_detection": config.get("enable_endpoint_detection", True),
            "language": config.get("language", "fa"),
        }
        
        if config.get("context_phrases"):
            init_config["context_phrases"] = config["context_phrases"]
        
        await ws.send(json.dumps(init_config))
        
        # Wait for confirmation
        try:
            confirmation = await ws.recv()
            if isinstance(confirmation, (bytes, bytearray)):
                logger.error("Soniox: Received binary data instead of JSON")
                await ws.close()
                return None
            
            conf_msg = json.loads(confirmation)
            if conf_msg.get("error_code"):
                logger.error(f"Soniox init error: {conf_msg.get('error_message')}")
                await ws.close()
                return None
            
            logger.info("Soniox STT connected successfully")
            return ws
        except Exception as e:
            logger.error(f"Soniox connection error: {e}")
            await ws.close()
            return None
    
    async def send_audio(self, ws: Any, audio_data: bytes) -> None:
        """Send audio data to Soniox"""
        try:
            await ws.send(audio_data)
        except Exception as e:
            logger.error(f"Error sending audio to Soniox: {e}")
            raise
    
    async def receive_transcripts(self, ws: Any) -> AsyncIterator[Dict[str, Any]]:
        """Receive transcripts from Soniox"""
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    message = json.loads(raw)
                    if message.get("error_code"):
                        logger.error(f"Soniox error: {message.get('error_message')}")
                        continue
                    yield message
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse Soniox message: {e}")
        except Exception as e:
            logger.error(f"Error receiving transcripts from Soniox: {e}")
            raise
    
    async def send_keepalive(self, ws: Any) -> None:
        """Send keepalive message"""
        try:
            await ws.send(json.dumps({"type": "keepalive"}))
        except Exception as e:
            logger.error(f"Error sending keepalive to Soniox: {e}")
    
    async def finalize(self, ws: Any) -> None:
        """Finalize transcription"""
        try:
            await ws.send(json.dumps({"type": "finalize"}))
        except Exception as e:
            logger.error(f"Error finalizing Soniox: {e}")
    
    async def close(self, ws: Any) -> None:
        """Close Soniox connection"""
        try:
            if ws:
                await ws.close()
                logger.info("Soniox connection closed")
        except Exception as e:
            logger.error(f"Error closing Soniox connection: {e}")
