from rest_framework import serializers
from Reservation_Module.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings
from Reservation_Module.jdatetime_utils import datetime_to_jdatetime, format_jdatetime


class MenuItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuItem
        fields = '__all__'


class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_name = serializers.CharField(source='menu_item.name', read_only=True)
    menu_item_category = serializers.CharField(source='menu_item.category', read_only=True)
    
    class Meta:
        model = OrderItem
        fields = ['id', 'menu_item', 'menu_item_name', 'menu_item_category', 
                  'quantity', 'unit_price', 'subtotal']
        read_only_fields = ['subtotal']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=False, required=False)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    order_date_jalali = serializers.SerializerMethodField()
    
    class Meta:
        model = Order
        fields = ['id', 'customer_name', 'phone_number', 'address', 'order_date', 'order_date_jalali',
                  'status', 'status_display', 'total_price', 'notes', 'items']
        read_only_fields = ['order_date', 'total_price', 'order_date_jalali']
    
    def get_order_date_jalali(self, obj):
        """Return order_date as Persian calendar string"""
        jdt = datetime_to_jdatetime(obj.order_date)
        return format_jdatetime(jdt) if jdt else None
    
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        order = Order.objects.create(**validated_data)
        
        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)
        
        # Calculate and save total
        order.calculate_total()
        order.save()
        
        return order
    
    def update(self, instance, validated_data):
        # Handle items update if provided
        items_data = validated_data.pop('items', None)
        
        # Update order fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        if items_data is not None:
            # Clear existing items and create new ones
            instance.items.all().delete()
            for item_data in items_data:
                OrderItem.objects.create(order=instance, **item_data)
            
            # Recalculate total
            instance.calculate_total()
        
        instance.save()
        return instance


class RestaurantSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestaurantSettings
        fields = '__all__'
