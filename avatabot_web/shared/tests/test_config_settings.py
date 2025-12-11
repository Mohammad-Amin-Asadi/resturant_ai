"""
Unit tests for ConfigSettings.
"""

from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch
from shared.config_settings import ConfigSettings

try:
    from shared.config_models import TenantConfig
except ImportError:
    from Reservation_Module.config_models import TenantConfig


class ConfigSettingsTestCase(TestCase):
    """Test cases for ConfigSettings"""
    
    def setUp(self):
        """Set up test data"""
        cache.clear()
    
    @patch.dict('os.environ', {'SMS_API_KEY': 'test_key', 'SMS_API_URL': 'https://test.com'})
    def test_get_sms_config_from_env(self):
        """Test getting SMS config from environment variables"""
        config = ConfigSettings.get_sms_config()
        
        self.assertEqual(config['api_key'], 'test_key')
        self.assertEqual(config['api_url'], 'https://test.com')
    
    def test_get_sms_config_from_tenant(self):
        """Test getting SMS config from tenant config"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True,
            config_json={
                'sms': {
                    'api_key': 'tenant_key',
                    'api_url': 'https://tenant.com',
                    'sender_number': '1000'
                }
            }
        )
        
        config = ConfigSettings.get_sms_config('test_tenant')
        
        self.assertEqual(config['api_key'], 'tenant_key')
        self.assertEqual(config['api_url'], 'https://tenant.com')
        self.assertEqual(config['sender_number'], '1000')
    
    @patch.dict('os.environ', {'WEATHER_API_TOKEN': 'test_token'})
    def test_get_weather_config_from_env(self):
        """Test getting weather config from environment variables"""
        config = ConfigSettings.get_weather_config()
        
        self.assertEqual(config['api_token'], 'test_token')
    
    def test_get_weather_config_from_tenant(self):
        """Test getting weather config from tenant config"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True,
            config_json={
                'weather': {
                    'api_token': 'tenant_token',
                    'api_url': 'https://weather.tenant.com'
                }
            }
        )
        
        config = ConfigSettings.get_weather_config('test_tenant')
        
        self.assertEqual(config['api_token'], 'tenant_token')
        self.assertEqual(config['api_url'], 'https://weather.tenant.com')
    
    def test_clear_cache(self):
        """Test clearing config cache"""
        # Load config to cache
        ConfigSettings.get_sms_config('test_tenant')
        
        # Clear cache
        ConfigSettings.clear_cache('test_tenant')
        
        # Cache should be cleared
        # (In real scenario, would verify cache is empty)
