#!/usr/bin/env python
"""
SMS Service for sending notifications via LimoSMS API
"""

import logging
import requests
import os
from typing import List, Optional

# SMS API Configuration
SMS_API_URL = os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
SMS_API_KEY = os.getenv("SMS_API_KEY", "8dd73576-e25c-4624-aba2-b0ed72bfab89")
SMS_SENDER_NUMBER = os.getenv("SMS_SENDER_NUMBER", "10000000002027")


class SMSService:
    """Service for sending SMS messages via LimoSMS API"""
    
    def __init__(self):
        self.api_url = SMS_API_URL
        self.api_key = SMS_API_KEY
        self.sender_number = SMS_SENDER_NUMBER
    
    def send_sms(self, receiver: str, message: str) -> bool:
        """
        Send SMS to a single receiver
        
        Args:
            receiver: Phone number (e.g., "09154211914")
            message: SMS message text
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not receiver or not message:
            logging.warning("SMS: Missing receiver or message")
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

