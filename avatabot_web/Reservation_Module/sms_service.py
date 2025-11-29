"""
SMS Service for Django - sends SMS notifications via LimoSMS API
"""
import os
import logging
import requests

# SMS API Configuration
SMS_API_URL = os.getenv("SMS_API_URL", "https://api.limosms.com/api/sendsms")
SMS_API_KEY = os.getenv("SMS_API_KEY", "8dd73576-e25c-4624-aba2-b0ed72bfab89")
SMS_SENDER_NUMBER = os.getenv("SMS_SENDER_NUMBER", "10000000002027")


def send_sms(receiver: str, message: str) -> bool:
    """
    Send SMS to a receiver
    
    Args:
        receiver: Phone number (e.g., "09154211914")
        message: SMS message text
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not receiver or not message:
        logging.warning("SMS: Missing receiver or message")
        return False
    
    # Normalize phone number (inline implementation)
    def normalize_phone(phone):
        """Normalize phone number: convert Persian digits and remove spaces"""
        if not phone:
            return ""
        
        # Persian to English digit mapping
        persian_digits = '۰۱۲۳۴۵۶۷۸۹'
        english_digits = '0123456789'
        arabic_digits = '٠١٢٣٤٥٦٧٨٩'
        
        # Translation table
        translation_table = str.maketrans(
            persian_digits + arabic_digits,
            english_digits + english_digits
        )
        
        # Convert digits
        normalized = phone.translate(translation_table)
        
        # Remove all non-digit characters except leading +
        result = ''
        for i, char in enumerate(normalized):
            if char.isdigit():
                result += char
            elif char == '+' and i == 0:
                result += char
        
        return result
    
    normalized_receiver = normalize_phone(receiver)
    
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
            'SenderNumber': SMS_SENDER_NUMBER,
            'MobileNumber': [normalized_receiver]  # Always a list
        }
        
        headers = {"ApiKey": SMS_API_KEY}
        
        logging.info(f"📱 Attempting to send SMS to {normalized_receiver} (original: {receiver})")
        logging.info(f"📱 SMS API URL: {SMS_API_URL}")
        logging.info(f"📱 SMS payload: {payload}")
        
        response = requests.post(SMS_API_URL, json=payload, headers=headers, timeout=10)
        
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

