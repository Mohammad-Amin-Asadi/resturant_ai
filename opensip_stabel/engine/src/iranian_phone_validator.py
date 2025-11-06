#!/usr/bin/env python
"""
Iranian Phone Number Validator
تشخیص و validation شماره‌های موبایل ایران
"""

import re
import logging

IRANIAN_MOBILE_PREFIXES = [
    '0910', '0911', '0912', '0913', '0914', '0915', '0916', '0917', '0918', '0919',  # همراه اول
    '0990', '0991', '0992', '0993', '0994',  # همراه اول (4G)
    '0901', '0902', '0903', '0905', '0930', '0933', '0935', '0936', '0937', '0938', '0939',  # ایرانسل
    '0920', '0921', '0922',  # رایتل
    '0931', '0932', '0934',  # ایرانسل (4G)
    '0941', '0998',  # سایر
]

def is_iranian_mobile(phone_number):
    """
    چک می‌کند آیا شماره، شماره موبایل ایران است یا نه
    
    Args:
        phone_number (str): شماره تلفن
        
    Returns:
        bool: True اگر شماره موبایل ایران باشد
    """
    if not phone_number:
        return False
        
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
    if prefix in IRANIAN_MOBILE_PREFIXES:
        logging.info(f"✅ Valid Iranian mobile: {phone} (prefix: {prefix})")
        return True
    else:
        logging.warning(f"❌ Invalid Iranian mobile prefix: {phone} (prefix: {prefix})")
        return False

def validate_caller_number(from_header):
    """
    استخراج و validation شماره از SIP From header
    
    Args:
        from_header (str): SIP From header
        
    Returns:
        tuple: (is_valid, phone_number)
    """
    if not from_header:
        return (False, None)
    
    # استخراج شماره از From header
    # مثال: "John" <sip:09123456789@domain.com>
    match = re.search(r'sip:([0-9+]+)@', from_header)
    if not match:
        return (False, None)
    
    phone = match.group(1)
    is_valid = is_iranian_mobile(phone)
    
    return (is_valid, phone)
