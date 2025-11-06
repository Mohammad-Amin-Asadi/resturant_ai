#!/usr/bin/env python
"""
Phone number normalization utility
Converts Persian digits to English and removes spaces/dashes
"""

def normalize_phone_number(phone):
    """
    Normalize phone number:
    - Convert Persian/Arabic digits to English
    - Remove spaces, dashes, parentheses
    - Keep only digits and leading +
    
    Examples:
        "۰۹۱۵ ۴۲۱ ۱۹۱۴" -> "09154211914"
        "0915 421 1914" -> "09154211914"
        "0915-421-1914" -> "09154211914"
        "+98 915 421 1914" -> "+989154211914"
    """
    if not phone:
        return ""
    
    # Persian to English digit mapping
    persian_digits = '۰۱۲۳۴۵۶۷۸۹'
    english_digits = '0123456789'
    
    # Arabic to English digit mapping
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


def format_phone_display(phone):
    """
    Format phone number for display
    Example: "09154211914" -> "0915 421 1914"
    """
    normalized = normalize_phone_number(phone)
    
    if not normalized:
        return phone
    
    # Format Iranian mobile numbers (11 digits starting with 09)
    if len(normalized) == 11 and normalized.startswith('09'):
        return f"{normalized[:4]} {normalized[4:7]} {normalized[7:]}"
    
    # Format international (with +98)
    if normalized.startswith('+98') and len(normalized) == 13:
        return f"+98 {normalized[3:6]} {normalized[6:9]} {normalized[9:]}"
    
    # Return as-is if format unknown
    return normalized

