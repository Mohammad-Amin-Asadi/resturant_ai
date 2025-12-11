"""
Configuration models for multi-tenant support.
Stores tenant-specific settings in the database.
"""

from django.db import models
from django.core.exceptions import ValidationError
import json


class TenantConfig(models.Model):
    """
    Multi-tenant configuration model.
    Stores tenant-specific settings that can be overridden via JSON configs.
    """
    
    TENANT_TYPE_CHOICES = [
        ('restaurant', 'رستوران'),
        ('taxi', 'تاکسی'),
        ('other', 'سایر'),
    ]
    
    tenant_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name='شناسه Tenant',
        help_text='شناسه یکتا برای tenant (مثلاً DID number یا business ID)'
    )
    
    tenant_name = models.CharField(
        max_length=300,
        verbose_name='نام Tenant',
        help_text='نام نمایشی tenant'
    )
    
    tenant_type = models.CharField(
        max_length=20,
        choices=TENANT_TYPE_CHOICES,
        default='restaurant',
        verbose_name='نوع Tenant'
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name='فعال است'
    )
    
    backend_url = models.URLField(
        blank=True,
        null=True,
        verbose_name='آدرس Backend',
        help_text='URL سرور backend برای این tenant'
    )
    
    config_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='تنظیمات JSON',
        help_text='تنظیمات اضافی به صورت JSON (AI settings, prompts, etc.)'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='تاریخ ایجاد'
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='آخرین به‌روزرسانی'
    )
    
    class Meta:
        verbose_name = 'تنظیمات Tenant'
        verbose_name_plural = 'تنظیمات Tenants'
        ordering = ['tenant_name']
        db_table = 'tenant_config'
        indexes = [
            models.Index(fields=['tenant_id']),
            models.Index(fields=['tenant_type', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.tenant_name} ({self.tenant_id})"
    
    def clean(self):
        """Validate JSON config structure"""
        if self.config_json:
            try:
                json.dumps(self.config_json)
            except (TypeError, ValueError) as e:
                raise ValidationError(f"Invalid JSON config: {e}")
    
    def get_config_value(self, key: str, default=None):
        """
        Get a configuration value from config_json.
        Supports dot notation for nested keys.
        
        Args:
            key: Configuration key (e.g., "openai.voice" or "sms.sender")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        if not self.config_json:
            return default
        
        keys = key.split('.')
        value = self.config_json
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value if value is not None else default
    
    def set_config_value(self, key: str, value):
        """
        Set a configuration value in config_json.
        Supports dot notation for nested keys.
        
        Args:
            key: Configuration key (e.g., "openai.voice")
            value: Value to set
        """
        if not self.config_json:
            self.config_json = {}
        
        keys = key.split('.')
        config = self.config_json
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def merge_with_json_config(self, json_config: dict) -> dict:
        """
        Merge this tenant's config with a JSON config file.
        JSON config takes precedence over database config.
        
        Args:
            json_config: Configuration dictionary from JSON file
            
        Returns:
            Merged configuration dictionary
        """
        merged = {}
        
        if self.config_json:
            merged.update(self.config_json)
        
        if json_config:
            merged.update(json_config)
        
        if self.backend_url:
            merged['backend_url'] = self.backend_url
        
        merged['tenant_id'] = self.tenant_id
        merged['tenant_name'] = self.tenant_name
        merged['tenant_type'] = self.tenant_type
        
        return merged
