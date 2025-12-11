#!/usr/bin/env python
"""
Engine configuration loader.
Loads engine-specific settings from DID config or environment variables.
"""

import os
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class EngineConfig:
    """Engine configuration manager"""
    
    @staticmethod
    def get_ip_whitelist(did_config: Dict[str, Any] = None) -> List[str]:
        """
        Get IP whitelist from config or environment.
        
        Args:
            did_config: Optional DID config dictionary
            
        Returns:
            List of whitelisted IP addresses
        """
        whitelist = []
        
        if did_config:
            security_config = did_config.get('security', {})
            whitelist = security_config.get('ip_whitelist', [])
        
        if not whitelist:
            env_whitelist = os.getenv('IP_WHITELIST', '')
            if env_whitelist:
                whitelist = [ip.strip() for ip in env_whitelist.split(',') if ip.strip()]
        
        if not whitelist:
            whitelist = [
                "127.0.0.1",
                "::1",
            ]
            logger.warning("Using default IP whitelist. Configure IP_WHITELIST env var or in DID config.")
        
        return whitelist
    
    @staticmethod
    def get_phone_validator_config(did_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Get phone validator configuration.
        
        Args:
            did_config: Optional DID config dictionary
            
        Returns:
            Dictionary with phone validator settings
        """
        config = {
            'mobile_prefixes': [],
            'config_number_prefix': '15923',
        }
        
        if did_config:
            phone_config = did_config.get('phone_validator', {})
            if phone_config:
                config.update({
                    'mobile_prefixes': phone_config.get('mobile_prefixes', []),
                    'config_number_prefix': phone_config.get('config_number_prefix', '15923'),
                })
        
        if not config['mobile_prefixes']:
            env_prefixes = os.getenv('IRANIAN_MOBILE_PREFIXES', '')
            if env_prefixes:
                config['mobile_prefixes'] = [p.strip() for p in env_prefixes.split(',') if p.strip()]
        
        if not config['mobile_prefixes']:
            config['mobile_prefixes'] = [
                '0910', '0911', '0912', '0913', '0914', '0915', '0916', '0917', '0918', '0919',
                '0990', '0991', '0992', '0993', '0994',
                '0901', '0902', '0903', '0905', '0930', '0933', '0935', '0936', '0937', '0938', '0939',
                '0920', '0921', '0922',
                '0931', '0932', '0934',
                '0941', '0998',
            ]
            logger.debug("Using default Iranian mobile prefixes")
        
        return config
    
    @staticmethod
    def get_weather_config(did_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Get weather API configuration.
        
        Args:
            did_config: Optional DID config dictionary
            
        Returns:
            Dictionary with weather API settings
        """
        config = {
            'api_url': os.getenv('WEATHER_API_URL', 'https://one-api.ir/weather/'),
            'api_token': os.getenv('WEATHER_API_TOKEN'),
        }
        
        if did_config:
            weather_config = did_config.get('weather', {})
            if weather_config:
                config.update({
                    'api_url': weather_config.get('api_url', config['api_url']),
                    'api_token': weather_config.get('api_token', config['api_token']),
                })
        
        return config
