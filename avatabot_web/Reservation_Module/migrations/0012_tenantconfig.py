# Generated manually for Step 1: Configuration System

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Reservation_Module', '0011_reservationmodel_status_taxistatuslog'),
    ]

    operations = [
        migrations.CreateModel(
            name='TenantConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.CharField(db_index=True, help_text='شناسه یکتا برای tenant (مثلاً DID number یا business ID)', max_length=100, unique=True, verbose_name='شناسه Tenant')),
                ('tenant_name', models.CharField(help_text='نام نمایشی tenant', max_length=300, verbose_name='نام Tenant')),
                ('tenant_type', models.CharField(choices=[('restaurant', 'رستوران'), ('taxi', 'تاکسی'), ('other', 'سایر')], default='restaurant', max_length=20, verbose_name='نوع Tenant')),
                ('is_active', models.BooleanField(default=True, verbose_name='فعال است')),
                ('backend_url', models.URLField(blank=True, help_text='URL سرور backend برای این tenant', null=True, verbose_name='آدرس Backend')),
                ('config_json', models.JSONField(blank=True, default=dict, help_text='تنظیمات اضافی به صورت JSON (AI settings, prompts, etc.)', verbose_name='تنظیمات JSON')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ایجاد')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='آخرین به‌روزرسانی')),
            ],
            options={
                'verbose_name': 'تنظیمات Tenant',
                'verbose_name_plural': 'تنظیمات Tenants',
                'db_table': 'tenant_config',
                'ordering': ['tenant_name'],
            },
        ),
        migrations.AddIndex(
            model_name='tenantconfig',
            index=models.Index(fields=['tenant_id'], name='tenant_conf_tenant__idx'),
        ),
        migrations.AddIndex(
            model_name='tenantconfig',
            index=models.Index(fields=['tenant_type', 'is_active'], name='tenant_conf_tenant__idx2'),
        ),
    ]
