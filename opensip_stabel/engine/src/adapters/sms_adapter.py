"""
SMS Adapter interface and implementations.
"""

import logging
import requests
import os
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class SMSAdapter(ABC):
    """Abstract base class for SMS adapters"""
    
    @abstractmethod
    def send_sms(self, receiver: str, message: str) -> bool:
        """
        Send SMS to a receiver.
        
        Args:
            receiver: Phone number
            message: SMS message text
            
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def send_sms_bulk(self, receivers: List[str], message: str) -> Dict[str, Any]:
        """
        Send SMS to multiple receivers.
        
        Args:
            receivers: List of phone numbers
            message: SMS message text
            
        Returns:
            dict: Results with success count and failed numbers
        """
        pass


class LimoSMSAdapter(SMSAdapter):
    """LimoSMS API adapter implementation"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize LimoSMS adapter.
        
        Args:
            config: Optional config dict with api_url, api_key, sender_number
        """
        if config and isinstance(config, dict):
            sms_config = config.get('sms', {})
            self.api_url = sms_config.get('api_url') or os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
            self.api_key = sms_config.get('api_key') or os.getenv("SMS_API_KEY")
            self.sender_number = sms_config.get('sender_number') or os.getenv("SMS_SENDER_NUMBER")
        else:
            self.api_url = os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
            self.api_key = os.getenv("SMS_API_KEY")
            self.sender_number = os.getenv("SMS_SENDER_NUMBER")
        
        if not self.api_key:
            logger.warning("SMS API key not configured. Set SMS_API_KEY env var or configure in DID config.")
    
    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number"""
        from phone_normalizer import normalize_phone_number
        return normalize_phone_number(phone)
    
    def send_sms(self, receiver: str, message: str) -> bool:
        """Send SMS via LimoSMS API"""
        if not receiver or not message:
            logger.warning("SMS: Missing receiver or message")
            return False
        
        normalized_receiver = self._normalize_phone(receiver)
        if not normalized_receiver:
            logger.error(f"❌ SMS: Invalid phone number format: {receiver}")
            return False
        
        if not normalized_receiver.startswith('0') and not normalized_receiver.startswith('+98'):
            if len(normalized_receiver) == 10:
                normalized_receiver = '0' + normalized_receiver
            elif len(normalized_receiver) == 9:
                normalized_receiver = '09' + normalized_receiver
        
        if not self.api_key:
            logger.error("SMS: API key not configured")
            return False
        
        try:
            payload = {
                'Message': message,
                'SenderNumber': self.sender_number,
                'MobileNumber': [normalized_receiver]
            }
            headers = {"ApiKey": self.api_key}
            
            logger.info(f"📱 Sending SMS to {normalized_receiver}")
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            logger.info(f"✅ SMS sent successfully to {normalized_receiver}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to send SMS: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending SMS: {e}", exc_info=True)
            return False
    
    def send_sms_bulk(self, receivers: List[str], message: str) -> Dict[str, Any]:
        """Send SMS to multiple receivers"""
        results = {"success": 0, "failed": []}
        for receiver in receivers:
            if self.send_sms(receiver, message):
                results["success"] += 1
            else:
                results["failed"].append(receiver)
        return results
