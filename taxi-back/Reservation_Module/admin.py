from django.contrib import admin
from Reservation_Module.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings, InscriptionModel


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
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
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


@admin.register(InscriptionModel)
class InscriptionModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'use_count')
    readonly_fields = ('private_key', 'public_key')
