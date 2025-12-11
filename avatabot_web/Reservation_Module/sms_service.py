"""
SMS Service for Django - sends SMS notifications via LimoSMS API
"""
import os
import logging
import requests

# Import from shared (primary location)
try:
    from shared.config_settings import ConfigSettings
except ImportError:
    # Fallback for backward compatibility during migration
    try:
        from Reservation_Module.config_settings import ConfigSettings
    except ImportError:
        logger.error("ConfigSettings not found in shared or Reservation_Module")
        raise

logger = logging.getLogger(__name__)


def send_sms(receiver: str, message: str, tenant_id: str = None) -> bool:
    """
    Send SMS to a receiver
    
    Args:
        receiver: Phone number (e.g., "09154211914")
        message: SMS message text
        tenant_id: Optional tenant ID for tenant-specific SMS config
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not receiver or not message:
        logger.warning("SMS: Missing receiver or message")
        return False
    
    sms_config = ConfigSettings.get_sms_config(tenant_id)
    
    if not sms_config.get('api_key'):
        logger.error("SMS: API key not configured. Set SMS_API_KEY env var or configure in tenant config.")
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
        logger.error(f"❌ SMS: Invalid phone number format: {receiver}")
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
            'SenderNumber': sms_config.get('sender_number'),
            'MobileNumber': [normalized_receiver]  # Always a list
        }
        
        headers = {"ApiKey": sms_config.get('api_key')}
        
        logger.info(f"📱 Attempting to send SMS to {normalized_receiver} (original: {receiver})")
        logger.info(f"📱 SMS API URL: {sms_config.get('api_url')}")
        
        response = requests.post(
            sms_config.get('api_url'),
            json=payload,
            headers=headers,
            timeout=10
        )
        
        # Log response details
        logger.info(f"📱 SMS API response status: {response.status_code}")
        logger.info(f"📱 SMS API response text: {response.text}")
        
        # Check if response is successful
        if response.status_code == 200:
            try:
                response_json = response.json()
                logger.info(f"📱 SMS API response JSON: {response_json}")
            except:
                pass
        
        response.raise_for_status()
        
        logger.info(f"✅ SMS sent successfully to {normalized_receiver}")
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to send SMS to {normalized_receiver}: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"SMS API response status: {e.response.status_code}")
            logger.error(f"SMS API response text: {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error sending SMS to {normalized_receiver}: {e}", exc_info=True)
        return False

