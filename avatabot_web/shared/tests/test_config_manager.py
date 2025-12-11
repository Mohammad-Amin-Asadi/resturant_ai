"""
Unit tests for ConfigManager.
"""

from django.test import TestCase
from django.core.cache import cache
from shared.config_manager import ConfigManager

try:
    from shared.config_models import TenantConfig
except ImportError:
    from Reservation_Module.config_models import TenantConfig


class ConfigManagerTestCase(TestCase):
    """Test cases for ConfigManager"""
    
    def setUp(self):
        """Set up test data"""
        cache.clear()
    
    def test_normalize_did(self):
        """Test DID normalization"""
        # Test various formats
        self.assertEqual(ConfigManager._normalize_did("sip:511882@example.com"), "511882")
        self.assertEqual(ConfigManager._normalize_did("tel:+98511882"), "511882")
        self.assertEqual(ConfigManager._normalize_did("511882"), "511882")
        self.assertEqual(ConfigManager._normalize_did("98511882"), "511882")
        self.assertEqual(ConfigManager._normalize_did(""), "")
    
    def test_get_tenant_by_id_found(self):
        """Test getting tenant by ID"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True,
            config_json={'test_key': 'test_value'}
        )
        
        result = ConfigManager.get_tenant_by_id('test_tenant')
        
        self.assertIsNotNone(result)
        self.assertEqual(result.id, tenant.id)
    
    def test_get_tenant_by_id_not_found(self):
        """Test getting non-existent tenant"""
        result = ConfigManager.get_tenant_by_id('non_existent')
        
        self.assertIsNone(result)
    
    def test_get_tenant_by_did_found(self):
        """Test getting tenant by DID"""
        tenant = TenantConfig.objects.create(
            tenant_id='511882',
            tenant_name='Test DID',
            tenant_type='restaurant',
            is_active=True
        )
        
        result = ConfigManager.get_tenant_by_did('511882')
        
        self.assertIsNotNone(result)
        self.assertEqual(result.id, tenant.id)
    
    def test_get_config_with_tenant(self):
        """Test getting merged config with tenant"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True,
            config_json={'db_key': 'db_value', 'merged_key': 'db_value'}
        )
        
        json_config = {'json_key': 'json_value', 'merged_key': 'json_value'}
        
        config = ConfigManager.get_config('test_tenant', json_config)
        
        # JSON should take precedence
        self.assertEqual(config['merged_key'], 'json_value')
        self.assertEqual(config['db_key'], 'db_value')
        self.assertEqual(config['json_key'], 'json_value')
    
    def test_get_config_without_tenant(self):
        """Test getting config without tenant (JSON only)"""
        json_config = {'key': 'value'}
        
        config = ConfigManager.get_config('non_existent', json_config)
        
        self.assertEqual(config, json_config)
    
    def test_get_config_value(self):
        """Test getting specific config value"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True,
            config_json={'nested': {'key': 'value'}}
        )
        
        value = ConfigManager.get_config_value('test_tenant', 'nested.key')
        
        self.assertEqual(value, 'value')
    
    def test_clear_cache(self):
        """Test clearing config cache"""
        tenant = TenantConfig.objects.create(
            tenant_id='test_tenant',
            tenant_name='Test Tenant',
            tenant_type='restaurant',
            is_active=True
        )
        
        # Load to cache
        ConfigManager.get_tenant_by_id('test_tenant')
        
        # Clear cache
        ConfigManager.clear_cache('test_tenant')
        
        # Cache should be cleared (will reload from DB)
        result = ConfigManager.get_tenant_by_id('test_tenant')
        self.assertIsNotNone(result)
