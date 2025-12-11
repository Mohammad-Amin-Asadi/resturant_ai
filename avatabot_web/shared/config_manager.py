"""
Central configuration manager for multi-tenant support.
Provides unified access to tenant configurations from both database and JSON files.
"""

import logging
from typing import Dict, Any, Optional
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Central manager for tenant configurations.
    Handles loading and merging configs from database and JSON files.
    """
    
    CACHE_TIMEOUT = 300
    CACHE_KEY_PREFIX = 'tenant_config:'
    
    @classmethod
    def get_tenant_by_did(cls, did: str) -> Optional[Any]:
        """
        Get tenant configuration by DID number.
        
        Args:
            did: DID number (normalized)
            
        Returns:
            TenantConfig instance or None
        """
        if not did:
            return None
        
        try:
            from shared.config_models import TenantConfig
        except ImportError:
            try:
                from shared.config_models import TenantConfig
            except ImportError:
                logger.warning("TenantConfig model not found")
                return None
        
        normalized_did = cls._normalize_did(did)
        
        try:
            tenant = TenantConfig.objects.filter(
                tenant_id=normalized_did,
                is_active=True
            ).first()
            
            if not tenant:
                tenant = TenantConfig.objects.filter(
                    tenant_id__icontains=normalized_did,
                    is_active=True
                ).first()
            
            return tenant
        except Exception as e:
            logger.error(f"Error loading tenant for DID {did}: {e}")
            return None
    
    @classmethod
    def get_tenant_by_id(cls, tenant_id: str) -> Optional[Any]:
        """
        Get tenant configuration by tenant_id.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            TenantConfig instance or None
        """
        cache_key = f"{cls.CACHE_KEY_PREFIX}{tenant_id}"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        try:
            from shared.config_models import TenantConfig
        except ImportError:
            try:
                from shared.config_models import TenantConfig
            except ImportError:
                logger.warning("TenantConfig model not found")
                return None
        
        try:
            tenant = TenantConfig.objects.filter(
                tenant_id=tenant_id,
                is_active=True
            ).first()
            
            if tenant:
                cache.set(cache_key, tenant, cls.CACHE_TIMEOUT)
            
            return tenant
        except Exception as e:
            logger.error(f"Error loading tenant {tenant_id}: {e}")
            return None
    
    @classmethod
    def get_config(cls, tenant_id: str, json_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get merged configuration for a tenant.
        Merges database config with JSON config (JSON takes precedence).
        
        Args:
            tenant_id: Tenant identifier or DID number
            json_config: Optional JSON config dictionary (from DID config file)
            
        Returns:
            Merged configuration dictionary
        """
        tenant = cls.get_tenant_by_id(tenant_id)
        
        if not tenant:
            tenant = cls.get_tenant_by_did(tenant_id)
        
        if tenant:
            return tenant.merge_with_json_config(json_config or {})
        
        return json_config or {}
    
    @classmethod
    def get_config_value(
        cls,
        tenant_id: str,
        key: str,
        default: Any = None,
        json_config: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Get a specific configuration value for a tenant.
        Checks both database and JSON config.
        
        Args:
            tenant_id: Tenant identifier or DID number
            key: Configuration key (supports dot notation)
            default: Default value if key not found
            json_config: Optional JSON config dictionary
            
        Returns:
            Configuration value or default
        """
        tenant = cls.get_tenant_by_id(tenant_id)
        
        if not tenant:
            tenant = cls.get_tenant_by_did(tenant_id)
        
        if tenant:
            db_value = tenant.get_config_value(key)
            if db_value is not None:
                return db_value
        
        if json_config:
            keys = key.split('.')
            value = json_config
            
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            
            return value if value is not None else default
        
        return default
    
    @classmethod
    def clear_cache(cls, tenant_id: str = None):
        """
        Clear configuration cache.
        
        Args:
            tenant_id: Specific tenant ID to clear, or None to clear all
        """
        if tenant_id:
            cache_key = f"{cls.CACHE_KEY_PREFIX}{tenant_id}"
            cache.delete(cache_key)
        else:
            try:
                cache.delete_pattern(f"{cls.CACHE_KEY_PREFIX}*")
            except AttributeError:
                pass
    
    @staticmethod
    def _normalize_did(did: str) -> str:
        """
        Normalize DID number for matching.
        
        Args:
            did: DID number
            
        Returns:
            Normalized DID number
        """
        if not did:
            return ""
        
        did = did.replace("sip:", "").replace("tel:", "")
        
        if "@" in did:
            did = did.split("@")[0]
        
        if did.startswith("+"):
            normalized = "+" + "".join(c for c in did[1:] if c.isdigit())
        else:
            normalized = "".join(c for c in did if c.isdigit())
        
        if normalized.startswith("98") and len(normalized) > 2:
            normalized = normalized[2:]
        
        return normalized
