"""
Taxi domain models.
"""

from django.db import models
from shared.jdatetime_utils import get_tehran_now, datetime_to_jdatetime


class ReservationModel(models.Model):
    """Taxi reservation model"""
    
    STATUS_CHOICES = [
        ('to_source', 'در مسیر مبدا'),
        ('at_source', 'در مبدا'),
        ('to_destination', 'در مسیر مقصد'),
        ('at_destination', 'به مقصد رسید'),
    ]
    
    user_fullname = models.CharField(max_length=300, verbose_name='User Fullname')
    origin = models.CharField(max_length=300, verbose_name='مبدا')
    destination = models.CharField(max_length=300, verbose_name='مقصد')
    date_time = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ و زمان')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='to_source',
        verbose_name='وضعیت تاکسی',
        db_index=True
    )

    class Meta:
        verbose_name = 'Reservation'
        verbose_name_plural = 'Reservations'
        ordering = ['date_time']
        db_table = 'reservation_model'

    def __str__(self):
        return f"{self.origin} - {self.destination} - {self.date_time}"


class TaxiStatusLog(models.Model):
    """Log model for tracking taxi status changes"""
    reservation = models.ForeignKey(
        ReservationModel,
        on_delete=models.CASCADE,
        related_name='status_logs',
        verbose_name='رزرو'
    )
    old_status = models.CharField(
        max_length=20,
        choices=ReservationModel.STATUS_CHOICES,
        null=True,
        blank=True,
        verbose_name='وضعیت قبلی'
    )
    new_status = models.CharField(
        max_length=20,
        choices=ReservationModel.STATUS_CHOICES,
        verbose_name='وضعیت جدید'
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name='زمان تغییر')
    changed_by = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name='تغییر دهنده'
    )

    class Meta:
        verbose_name = 'لاگ وضعیت تاکسی'
        verbose_name_plural = 'لاگ‌های وضعیت تاکسی'
        ordering = ['-changed_at']
        db_table = 'taxi_status_log'

    def __str__(self):
        return f"{self.reservation.id} - {self.old_status} → {self.new_status} - {self.changed_at}"


class TelephoneTaxiModel(models.Model):
    """Taxi service settings"""
    organization = models.CharField(max_length=300, verbose_name='Organization')
    server_url = models.URLField(verbose_name='Server URL')
    description = models.TextField(null=True, blank=True, verbose_name='Description')
    is_active = models.BooleanField(default=True, verbose_name='Is Active')

    class Meta:
        verbose_name = 'Telephone Taxi'
        verbose_name_plural = 'Telephone Taxi'
        ordering = ['organization']
        db_table = 'telephone_taxi_model'

    def __str__(self):
        return f"{self.organization} - {self.server_url}"
