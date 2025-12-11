#!/usr/bin/env python
"""
Iranian Phone Number Validator
تشخیص و validation شماره‌های موبایل ایران
"""

import re
import logging
from typing import List, Optional, Dict, Any
from engine_config import EngineConfig

logger = logging.getLogger(__name__)

# Default prefixes (fallback if not in config)
DEFAULT_IRANIAN_MOBILE_PREFIXES = [
    '0910', '0911', '0912', '0913', '0914', '0915', '0916', '0917', '0918', '0919',
    '0990', '0991', '0992', '0993', '0994',
    '0901', '0902', '0903', '0905', '0930', '0933', '0935', '0936', '0937', '0938', '0939',
    '0920', '0921', '0922',
    '0931', '0932', '0934',
    '0941', '0998',
]

def is_iranian_mobile(phone_number, did_config: Optional[Dict[str, Any]] = None):
    """
    چک می‌کند آیا شماره، شماره موبایل ایران است یا نه
    
    Args:
        phone_number: شماره تلفن
        did_config: Optional DID config dictionary for tenant-specific prefixes
        
    Returns:
        bool: True اگر شماره موبایل ایران باشد
    """
    if not phone_number:
        return False
    
    phone_config = EngineConfig.get_phone_validator_config(did_config)
    prefixes = phone_config.get('mobile_prefixes', DEFAULT_IRANIAN_MOBILE_PREFIXES)
        
    # حذف فاصله‌ها و کاراکترهای اضافی
    phone = str(phone_number).strip().replace(' ', '').replace('-', '').replace('+98', '0')
    
    # چک کردن طول (باید 11 رقم باشد)
    if len(phone) != 11:
        return False
    
    # چک کردن که با 09 شروع شود
    if not phone.startswith('09'):
        return False
    
    # چک کردن prefix
    prefix = phone[:4]
    if prefix in prefixes:
        logger.info(f"✅ Valid Iranian mobile: {phone} (prefix: {prefix})")
        return True
    else:
        logger.warning(f"❌ Invalid Iranian mobile prefix: {phone} (prefix: {prefix})")
        return False

def extract_config_number_from_from_header(from_header, did_config: Optional[Dict[str, Any]] = None):
    """
    استخراج شماره از الگوی "[prefix][شماره]-None" در From header برای لود کانفیگ
    و حذف prefix از ابتدای شماره
    
    Args:
        from_header: SIP From header
        did_config: Optional DID config dictionary for tenant-specific prefix
        
    Returns:
        str or None: شماره استخراج شده بدون prefix یا None اگر الگو پیدا نشد
    """
    if not from_header:
        return None
    
    phone_config = EngineConfig.get_phone_validator_config(did_config)
    config_prefix = phone_config.get('config_number_prefix', '15923')
    
    # جستجوی الگوی "[prefix][شماره]-None" در قسمت display name
    pattern = rf'"({config_prefix}[0-9]+)-None"'
    match = re.search(pattern, from_header)
    if match:
        full_number = match.group(1)
        # حذف prefix از ابتدای شماره
        if full_number.startswith(config_prefix):
            config_number = full_number[len(config_prefix):]
            logger.info(f"✅ Found config number in From header: {full_number} -> {config_number} (removed {config_prefix})")
            return config_number
        else:
            logger.info(f"✅ Found config number in From header: {full_number}")
            return full_number
    
    return None

def clean_from_header_after_config_extraction(from_header, did_config: Optional[Dict[str, Any]] = None):
    """
    پاک کردن بخش "[prefix][شماره]-None" از From header و نگه داشتن فقط شماره واقعی
    
    Args:
        from_header: SIP From header
        did_config: Optional DID config dictionary for tenant-specific prefix
        
    Returns:
        str: From header پاک شده
    """
    if not from_header:
        return from_header
    
    phone_config = EngineConfig.get_phone_validator_config(did_config)
    config_prefix = phone_config.get('config_number_prefix', '15923')
    
    # حذف الگوی "[prefix][شماره]-None" از display name
    pattern = rf'"{config_prefix}[0-9]+-None"\s*'
    cleaned = re.sub(pattern, '', from_header)
    
    if cleaned != from_header:
        logger.info(f"🧹 Cleaned From header: removed pattern, result: {cleaned}")
    
    return cleaned

def validate_caller_number(from_header, did_config: Optional[Dict[str, Any]] = None):
    """
    استخراج و validation شماره از SIP From header
    
    Args:
        from_header: SIP From header
        did_config: Optional DID config dictionary for tenant-specific settings
        
    Returns:
        tuple: (is_valid, phone_number)
    """
    if not from_header:
        return (False, None)
    
    phone_config = EngineConfig.get_phone_validator_config(did_config)
    config_prefix = phone_config.get('config_number_prefix', '15923')
    
    # استخراج شماره از From header
    pattern = rf'sip:((?:{config_prefix}[0-9+]*|[0-9+]+))@'
    match = re.search(pattern, from_header)
    if not match:
        return (False, None)
    
    phone = match.group(1)
    
    # اگر شماره با config prefix شروع می‌شود، به طور خودکار قبول کن
    if phone.startswith(config_prefix):
        logger.info(f"✅ Valid number (starts with {config_prefix}): {phone}")
        phone = phone[len(config_prefix):]
        return (True, phone)
    
    is_valid = is_iranian_mobile(phone, did_config)
    
    return (is_valid, phone)
