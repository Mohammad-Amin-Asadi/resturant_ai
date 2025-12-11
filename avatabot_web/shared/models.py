"""
Shared models used by both restaurant and taxi domains.
"""

from django.db import models
from .jdatetime_utils import get_tehran_now

# Import TenantConfig from shared.config_models (moved from Reservation_Module)
try:
    from shared.config_models import TenantConfig
except ImportError:
    TenantConfig = None


class InscriptionModel(models.Model):
    """Encryption keys model - shared between domains"""
    private_key = models.TextField(verbose_name='کلید خصوصی')
    public_key = models.TextField(verbose_name='کلید عمومی')
    use_count = models.PositiveIntegerField(verbose_name='تعداد استفاده')

    class Meta:
        verbose_name = 'کلید رمزنگاری'
        verbose_name_plural = 'کلیدهای رمزنگاری'
        ordering = ['id']
        db_table = 'inscription_model'

    def __str__(self):
        return f"Key {self.id} - Uses: {self.use_count}"
