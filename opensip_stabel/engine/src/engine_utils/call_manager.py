"""
Call manager for tracking active calls.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CallManager:
    """Manages active calls in the system"""
    
    def __init__(self):
        """Initialize call manager"""
        self._calls: Dict[str, any] = {}
    
    def add_call(self, key: str, call: any) -> None:
        """Add a call to the manager"""
        if key in self._calls:
            logger.warning("Call %s already exists, replacing", key)
        self._calls[key] = call
        logger.debug("Added call %s (total: %d)", key, len(self._calls))
    
    def get_call(self, key: str) -> Optional[any]:
        """Get a call by key"""
        return self._calls.get(key)
    
    def remove_call(self, key: str) -> Optional[any]:
        """Remove a call from the manager"""
        call = self._calls.pop(key, None)
        if call:
            logger.debug("Removed call %s (remaining: %d)", key, len(self._calls))
        return call
    
    def has_call(self, key: str) -> bool:
        """Check if a call exists"""
        return key in self._calls
    
    def get_all_calls(self) -> Dict[str, any]:
        """Get all active calls"""
        return self._calls.copy()
    
    def count(self) -> int:
        """Get number of active calls"""
        return len(self._calls)
    
    async def close_all(self) -> None:
        """Close all active calls"""
        logger.info("Closing %d active calls", len(self._calls))
        for key, call in list(self._calls.items()):
            if hasattr(call, 'terminated') and call.terminated:
                continue
            try:
                if hasattr(call, 'close'):
                    await call.close()
            except Exception as e:
                logger.error("Error closing call %s: %s", key, e)
        self._calls.clear()
