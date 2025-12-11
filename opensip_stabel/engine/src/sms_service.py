#!/usr/bin/env python
"""
SMS Service for sending notifications via LimoSMS API
Loads configuration from DID config or environment variables.
"""

import logging
import requests
import os
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class SMSService:
    """Service for sending SMS messages via LimoSMS API"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize SMS service with configuration.
        
        Args:
            config: Optional DID config dictionary. If provided, uses SMS settings from it.
                   Otherwise falls back to environment variables.
        """
        environment = os.getenv('ENVIRONMENT', 'production')
        
        if config and isinstance(config, dict):
            sms_config = config.get('sms', {})
            self.api_url = sms_config.get('api_url') or os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
            self.api_key = sms_config.get('api_key') or os.getenv("SMS_API_KEY")
            self.sender_number = sms_config.get('sender_number') or os.getenv("SMS_SENDER_NUMBER")
            self.sms_enabled = sms_config.get('enabled', True)  # Default to enabled if not specified
        else:
            self.api_url = os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
            self.api_key = os.getenv("SMS_API_KEY")
            self.sender_number = os.getenv("SMS_SENDER_NUMBER")
            self.sms_enabled = True  # Default to enabled
        
        # Validate SMS configuration
        if not self.api_key or self.api_key.strip() == "":
            if environment == 'production' and self.sms_enabled:
                # Only log as error if SMS is explicitly enabled but key is missing
                logger.warning("⚠️  SMS API key not configured in production (SMS will be disabled for this call). Set SMS_API_KEY env var or configure in DID config.")
            else:
                logger.debug("ℹ️  SMS disabled: No API key configured (this is OK if SMS is not needed)")
        else:
            logger.info("✅ SMS configured: API key present, URL: %s", self.api_url)
    
    def send_sms(self, receiver: str, message: str) -> bool:
        """
        Send SMS to a single receiver
        
        Args:
            receiver: Phone number (e.g., "09154211914")
            message: SMS message text
            
        Returns:
            bool: True if successful, False otherwise
        """
        # Check if SMS is enabled and configured
        if not self.api_key or not self.api_key.strip():
            logger.debug("ℹ️  SMS not sent: API key not configured (SMS disabled for this call)")
            return False
        
        if not receiver or not message:
            logger.warning("⚠️  SMS: Missing receiver or message")
            return False
        
        # Normalize phone number
        from phone_normalizer import normalize_phone_number
        normalized_receiver = normalize_phone_number(receiver)
        
        if not normalized_receiver:
            logging.error(f"❌ SMS: Invalid phone number format: {receiver}")
            return False
        
        # Ensure phone number starts with 0 (Iranian format)
        if not normalized_receiver.startswith('0') and not normalized_receiver.startswith('+98'):
            if len(normalized_receiver) == 10:
                normalized_receiver = '0' + normalized_receiver
            elif len(normalized_receiver) == 9:
                normalized_receiver = '09' + normalized_receiver
        
        try:
            # MobileNumber must be a list according to API format
            payload = {
                'Message': message,
                'SenderNumber': self.sender_number,
                'MobileNumber': [normalized_receiver]  # Always a list
            }
            
            headers = {"ApiKey": self.api_key}
            
            logging.info(f"📱 Attempting to send SMS to {normalized_receiver} (original: {receiver})")
            logging.info(f"📱 SMS API URL: {self.api_url}")
            logging.info(f"📱 SMS payload: {payload}")
            
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            
            # Log response details
            logging.info(f"📱 SMS API response status: {response.status_code}")
            logging.info(f"📱 SMS API response text: {response.text}")
            
            # Check if response is successful
            if response.status_code == 200:
                try:
                    response_json = response.json()
                    logging.info(f"📱 SMS API response JSON: {response_json}")
                except:
                    pass
            
            response.raise_for_status()
            
            logging.info(f"✅ SMS sent successfully to {normalized_receiver}")
            return True
            
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Failed to send SMS to {normalized_receiver}: {e}")
            if hasattr(e, 'response') and e.response:
                logging.error(f"SMS API response status: {e.response.status_code}")
                logging.error(f"SMS API response text: {e.response.text}")
            return False
        except Exception as e:
            logging.error(f"❌ Unexpected error sending SMS to {normalized_receiver}: {e}", exc_info=True)
            return False
    
    def send_sms_bulk(self, receivers: List[str], message: str) -> dict:
        """
        Send SMS to multiple receivers
        
        Args:
            receivers: List of phone numbers
            message: SMS message text
            
        Returns:
            dict: Results with success count and failed numbers
        """
        results = {"success": 0, "failed": []}
        
        for receiver in receivers:
            if self.send_sms(receiver, message):
                results["success"] += 1
            else:
                results["failed"].append(receiver)
        
        return results


# Global instance
sms_service = SMSService()

