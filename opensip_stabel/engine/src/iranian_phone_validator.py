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

def extract_config_number_from_from_header(from_header):
    """
    استخراج شماره از الگوی "15923[شماره]-None" در From header برای لود کانفیگ
    و حذف 15923 از ابتدای شماره
    
    Args:
        from_header (str): SIP From header
        مثال: "15923511882-None" <sip:09154211914@188.0.240.163>;tag=as777408cf
        
    Returns:
        str or None: شماره استخراج شده بدون 15923 (مثلاً "511882") یا None اگر الگو پیدا نشد
    """
    if not from_header:
        return None
    
    # جستجوی الگوی "15923[شماره]-None" در قسمت display name
    # الگو: "15923[0-9]+-None"
    match = re.search(r'"(15923[0-9]+)-None"', from_header)
    if match:
        full_number = match.group(1)
        # حذف 15923 از ابتدای شماره
        if full_number.startswith('15923'):
            config_number = full_number[5:]  # حذف 5 کاراکتر اول (15923)
            logging.info(f"✅ Found config number in From header: {full_number} -> {config_number} (removed 15923)")
            return config_number
        else:
            logging.info(f"✅ Found config number in From header: {full_number}")
            return full_number
    
    return None

def clean_from_header_after_config_extraction(from_header):
    """
    پاک کردن بخش "15923[شماره]-None" از From header و نگه داشتن فقط شماره واقعی
    
    Args:
        from_header (str): SIP From header
        مثال: "15923511882-None" <sip:09154211914@188.0.240.163>;tag=as777408cf
        
    Returns:
        str: From header پاک شده
        مثال: <sip:09154211914@188.0.240.163>;tag=as777408cf
    """
    if not from_header:
        return from_header
    
    # حذف الگوی "15923[شماره]-None" از display name
    cleaned = re.sub(r'"15923[0-9]+-None"\s*', '', from_header)
    
    if cleaned != from_header:
        logging.info(f"🧹 Cleaned From header: removed pattern, result: {cleaned}")
    
    return cleaned

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
    # مثال: "John" <sip:09123456789@domain.com> یا sip:15923...@domain.com
    match = re.search(r'sip:((?:15923[0-9+]*|[0-9+]+))@', from_header)
    if not match:
        return (False, None)
    
    phone = match.group(1)
    
    # اگر شماره با 15923 شروع می‌شود، به طور خودکار قبول کن
    if phone.startswith('15923'):
        # شماره کامل را برگردان (بدون حذف 15923)
        logging.info(f"✅ Valid number (starts with 15923): {phone}")
        phone = phone[5:]
        return (True, phone)
    
    is_valid = is_iranian_mobile(phone)
    
    return (is_valid, phone)
