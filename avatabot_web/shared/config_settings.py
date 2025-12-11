"""
Configuration settings loader for Django backend.
Loads tenant-specific settings from database or environment variables.
"""

import os
import logging
from typing import Optional, Dict, Any, List
from django.core.cache import cache
from .config_manager import ConfigManager

logger = logging.getLogger(__name__)


class ConfigSettings:
    """
    Centralized configuration settings loader.
    Prioritizes tenant config, then environment variables, then defaults.
    """
    
    CACHE_TIMEOUT = 300
    
    @staticmethod
    def get_sms_config(tenant_id: str = None) -> Dict[str, Any]:
        """
        Get SMS configuration for a tenant.
        
        Args:
            tenant_id: Optional tenant ID for tenant-specific config
            
        Returns:
            Dictionary with SMS settings
        """
        cache_key = f"sms_config:{tenant_id or 'default'}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        config = {
            'api_url': os.getenv('SMS_API_URL', 'https://api.limosms.com/api/sendsms'),
            'api_key': os.getenv('SMS_API_KEY'),
            'sender_number': os.getenv('SMS_SENDER_NUMBER'),
        }
        
        if tenant_id:
            tenant_config = ConfigManager.get_config(tenant_id)
            sms_config = tenant_config.get('sms', {})
            if sms_config:
                config.update({
                    'api_url': sms_config.get('api_url', config['api_url']),
                    'api_key': sms_config.get('api_key', config['api_key']),
                    'sender_number': sms_config.get('sender_number', config['sender_number']),
                })
        
        if not config['api_key']:
            logger.warning("SMS API key not configured (neither in tenant config nor environment)")
        
        cache.set(cache_key, config, ConfigSettings.CACHE_TIMEOUT)
        return config
    
    @staticmethod
    def get_weather_config(tenant_id: str = None) -> Dict[str, Any]:
        """
        Get weather API configuration for a tenant.
        
        Args:
            tenant_id: Optional tenant ID for tenant-specific config
            
        Returns:
            Dictionary with weather API settings
        """
        cache_key = f"weather_config:{tenant_id or 'default'}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        config = {
            'api_url': os.getenv('WEATHER_API_URL', 'https://one-api.ir/weather/'),
            'api_token': os.getenv('WEATHER_API_TOKEN'),
        }
        
        if tenant_id:
            tenant_config = ConfigManager.get_config(tenant_id)
            weather_config = tenant_config.get('weather', {})
            if weather_config:
                config.update({
                    'api_url': weather_config.get('api_url', config['api_url']),
                    'api_token': weather_config.get('api_token', config['api_token']),
                })
        
        cache.set(cache_key, config, ConfigSettings.CACHE_TIMEOUT)
        return config
    
    @staticmethod
    def get_encryption_config(tenant_id: str = None) -> Dict[str, Any]:
        """
        Get encryption configuration for a tenant.
        
        Args:
            tenant_id: Optional tenant ID for tenant-specific config
            
        Returns:
            Dictionary with encryption settings
        """
        config = {
            'key_storage': os.getenv('ENCRYPTION_KEY_STORAGE', 'database'),
            'use_secure_storage': os.getenv('ENCRYPTION_USE_SECURE_STORAGE', 'False').lower() == 'true',
        }
        
        if tenant_id:
            tenant_config = ConfigManager.get_config(tenant_id)
            encryption_config = tenant_config.get('encryption', {})
            if encryption_config:
                config.update(encryption_config)
        
        return config
    
    @staticmethod
    def clear_cache(tenant_id: str = None):
        """Clear configuration cache."""
        if tenant_id:
            cache.delete(f"sms_config:{tenant_id}")
            cache.delete(f"weather_config:{tenant_id}")
        else:
            try:
                cache.delete_pattern("sms_config:*")
                cache.delete_pattern("weather_config:*")
            except AttributeError:
                pass
