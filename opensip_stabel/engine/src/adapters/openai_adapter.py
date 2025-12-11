"""
OpenAI Adapter interface and implementations.
"""

import logging
import json
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncIterator
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

logger = logging.getLogger(__name__)


class OpenAIAdapter(ABC):
    """Abstract base class for OpenAI adapters"""
    
    @abstractmethod
    async def connect(self, url: str, api_key: str, headers: Optional[Dict[str, str]] = None) -> Any:
        """Connect to OpenAI API"""
        pass
    
    @abstractmethod
    async def send_message(self, ws: Any, message: Dict[str, Any]) -> None:
        """Send message to OpenAI"""
        pass
    
    @abstractmethod
    async def receive_messages(self, ws: Any) -> AsyncIterator[Dict[str, Any]]:
        """Receive messages from OpenAI"""
        pass
    
    @abstractmethod
    async def close(self, ws: Any) -> None:
        """Close connection"""
        pass


class OpenAIRealtimeAdapter(OpenAIAdapter):
    """OpenAI Realtime API adapter implementation"""
    
    async def connect(self, url: str, api_key: str, headers: Optional[Dict[str, str]] = None) -> Any:
        """Connect to OpenAI Realtime WebSocket"""
        if headers is None:
            headers = {"Authorization": f"Bearer {api_key}", "OpenAI-Beta": "realtime=v1"}
        else:
            headers.setdefault("Authorization", f"Bearer {api_key}")
            headers.setdefault("OpenAI-Beta", "realtime=v1")
        
        logger.info(f"Connecting to OpenAI Realtime: {url}")
        ws = await connect(url, additional_headers=headers)
        
        # Expect initial hello
        try:
            hello = await ws.recv()
            if isinstance(hello, str):
                hello_data = json.loads(hello)
                logger.info(f"OpenAI hello received: {hello_data.get('type', 'unknown')}")
        except (ConnectionClosedOK, ConnectionClosedError) as e:
            logger.error(f"OpenAI connection closed during hello: {e}")
            await ws.close()
            raise
        
        return ws
    
    async def send_message(self, ws: Any, message: Dict[str, Any]) -> None:
        """Send JSON message to OpenAI"""
        try:
            await ws.send(json.dumps(message))
            logger.debug(f"Sent message to OpenAI: {message.get('type', 'unknown')}")
        except Exception as e:
            logger.error(f"Error sending message to OpenAI: {e}")
            raise
    
    async def receive_messages(self, ws: Any) -> AsyncIterator[Dict[str, Any]]:
        """Receive messages from OpenAI"""
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    message = json.loads(raw)
                    yield message
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse OpenAI message: {e}")
        except (ConnectionClosedOK, ConnectionClosedError) as e:
            logger.info(f"OpenAI connection closed: {e}")
        except Exception as e:
            logger.error(f"Error receiving messages from OpenAI: {e}")
            raise
    
    async def close(self, ws: Any) -> None:
        """Close OpenAI connection"""
        try:
            if ws:
                await ws.close()
                logger.info("OpenAI connection closed")
        except Exception as e:
            logger.error(f"Error closing OpenAI connection: {e}")
