from django.contrib import admin
from django.utils.html import format_html

# Try to import from new locations, fallback to old for backward compatibility
try:
    from restaurant.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings
    from shared.models import InscriptionModel
    from shared.config_models import TenantConfig
except ImportError:
    from Reservation_Module.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings, InscriptionModel
    try:
        from shared.config_models import TenantConfig
    except ImportError:
        from Reservation_Module.config_models import TenantConfig


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('subtotal',)


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'final_price', 'discount_percent', 'is_available', 'is_special')
    list_filter = ('category', 'is_available', 'is_special')
    search_fields = ('name', 'category')
    list_editable = ('is_available', 'is_special')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'phone_number', 'order_date', 'status', 'total_price')
    list_filter = ('status', 'order_date')
    search_fields = ('customer_name', 'phone_number')
    readonly_fields = ('order_date', 'total_price')
    inlines = [OrderItemInline]
    
    def get_queryset(self, request):
        """Optimize query with select_related and prefetch_related"""
        qs = super().get_queryset(request)
        return qs.select_related('customer').prefetch_related('items__menu_item')
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Prefetch items to avoid N+1 in calculate_total
        obj = Order.objects.prefetch_related('items').get(id=obj.id)
        obj.calculate_total()
        obj.save()


@admin.register(RestaurantSettings)
class RestaurantSettingsAdmin(admin.ModelAdmin):
    list_display = ('restaurant_name', 'phone_number', 'is_active')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone_number', 'updated_at')
    list_filter = ('updated_at',)
    search_fields = ('name', 'phone_number')
    readonly_fields = ('created_at', 'updated_at')
    
    def get_queryset(self, request):
        """Optimize query with prefetch_related for orders"""
        qs = super().get_queryset(request)
        return qs.prefetch_related('orders')


@admin.register(InscriptionModel)
class InscriptionModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'use_count')
    readonly_fields = ('private_key', 'public_key')


@admin.register(TenantConfig)
class TenantConfigAdmin(admin.ModelAdmin):
    list_display = ('tenant_name', 'tenant_id', 'tenant_type', 'is_active', 'backend_url_display', 'updated_at')
    list_filter = ('tenant_type', 'is_active', 'created_at')
    search_fields = ('tenant_name', 'tenant_id', 'tenant_type')
    readonly_fields = ('created_at', 'updated_at', 'config_json_preview')
    fieldsets = (
        ('اطلاعات پایه', {
            'fields': ('tenant_id', 'tenant_name', 'tenant_type', 'is_active')
        }),
        ('تنظیمات', {
            'fields': ('backend_url', 'config_json')
        }),
        ('اطلاعات سیستمی', {
            'fields': ('created_at', 'updated_at', 'config_json_preview'),
            'classes': ('collapse',)
        }),
    )
    
    def backend_url_display(self, obj):
        if obj.backend_url:
            return format_html('<a href="{}" target="_blank">{}</a>', obj.backend_url, obj.backend_url)
        return '-'
    backend_url_display.short_description = 'Backend URL'
    
    def config_json_preview(self, obj):
        if obj.config_json:
            import json
            return format_html('<pre>{}</pre>', json.dumps(obj.config_json, indent=2, ensure_ascii=False))
        return '-'
    config_json_preview.short_description = 'پیش‌نمایش JSON'
    
    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)
        try:
            from shared.config_manager import ConfigManager
        except ImportError:
            # Fallback for backward compatibility during migration
            try:
                from Reservation_Module.config_manager import ConfigManager
            except ImportError:
                logging.error("ConfigManager not found in shared or Reservation_Module")
                raise
        ConfigManager.clear_cache(obj.tenant_id)
