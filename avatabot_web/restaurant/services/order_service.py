"""
Order service for managing restaurant orders.
"""

import logging
import threading
from typing import Dict, Any, Optional, List
from django.db import transaction

logger = logging.getLogger(__name__)


class OrderService:
    """Service for order-related operations"""
    
    @staticmethod
    def create_order_from_decrypted_data(
        decrypted_data: Dict[str, Any],
        customer: Optional[Any] = None
    ) -> tuple[Any, Dict[str, Any]]:
        """
        Create an order from decrypted data.
        
        Args:
            decrypted_data: Decrypted order data dictionary
            customer: Optional customer instance
            
        Returns:
            Tuple of (order, result_dict)
        """
        try:
            from restaurant.models import Order, OrderItem, MenuItem
            from restaurant.serializers import OrderSerializer
        except ImportError:
            from Reservation_Module.models import Order, OrderItem, MenuItem
            from Reservation_Module.serializers import OrderSerializer
        
        items = decrypted_data.get('items', [])
        if not items:
            return None, {'error': 'سفارش باید حداقل یک آیتم داشته باشد. لیست غذاها خالی است.'}
        
        # Validate all items and fetch menu items in one query (avoid N+1)
        menu_item_ids = []
        for idx, item in enumerate(items):
            menu_item_id = item.get('menu_item')
            quantity = item.get('quantity', 0)
            if not menu_item_id:
                return None, {'error': f'آیتم {idx + 1} نامعتبر است: شناسه غذا مشخص نشده'}
            if quantity <= 0:
                return None, {'error': f'آیتم {idx + 1} نامعتبر است: تعداد باید بیشتر از صفر باشد'}
            menu_item_ids.append(menu_item_id)
        
        # Fetch all menu items in one query
        existing_menu_items = set(
            MenuItem.objects.filter(id__in=menu_item_ids).values_list('id', flat=True)
        )
        
        # Validate all menu items exist
        missing_ids = set(menu_item_ids) - existing_menu_items
        if missing_ids:
            return None, {'error': f'غذاهای با شناسه‌های {", ".join(map(str, missing_ids))} یافت نشدند'}
        
        customer_name = decrypted_data.get('customer_name', '').strip()
        address = decrypted_data.get('address', '').strip()
        
        if not customer_name:
            return None, {'error': 'نام مشتری الزامی است'}
        if not address:
            return None, {'error': 'آدرس تحویل الزامی است'}
        
        serializer = OrderSerializer(data=decrypted_data)
        if not serializer.is_valid():
            return None, {'error': serializer.errors}
        
        with transaction.atomic():
            order = serializer.save()
            
            if customer:
                order.customer = customer
                order.save()
        
        return order, {'success': True, 'order': serializer.data}
    
    @staticmethod
    def update_order_status(order_id: int, new_status: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Update order status and send SMS notification.
        
        Args:
            order_id: Order ID
            new_status: New status value
            tenant_id: Optional tenant ID for SMS config
            
        Returns:
            Dictionary with update result
        """
        try:
            from restaurant.models import Order
        except ImportError:
            from Reservation_Module.models import Order
        
        try:
            order = Order.objects.select_related('customer').prefetch_related(
                'items__menu_item'
            ).get(id=order_id)
        except Order.DoesNotExist:
            return {'error': 'سفارش یافت نشد'}
        
        valid_statuses = dict(Order.STATUS_CHOICES).keys()
        if new_status not in valid_statuses:
            return {'error': 'وضعیت نامعتبر است'}
        
        old_status = order.status
        order.status = new_status
        order.save()
        
        if old_status != new_status and order.phone_number:
            OrderService._send_status_change_sms(order, old_status, new_status, tenant_id)
        
        return {'success': True, 'order': order}
    
    @staticmethod
    def _send_status_change_sms(order: Any, old_status: str, new_status: str, tenant_id: Optional[str] = None):
        """Send SMS notification for status change (async)"""
        try:
            from Reservation_Module.sms_service import send_sms
        except ImportError:
            logger.warning("SMS service not available")
            return
        
        try:
            status_display = dict(Order.STATUS_CHOICES).get(new_status, new_status)
            old_status_display = dict(Order.STATUS_CHOICES).get(old_status, old_status)
            
            # Prefetch items and menu_item to avoid N+1
            order = Order.objects.prefetch_related('items__menu_item').get(id=order.id)
            items_text = []
            for item in order.items.all():
                items_text.append(f"{item.quantity}× {item.menu_item.name}")
            
            message = f"📋 به‌روزرسانی سفارش #{order.id}\n\n"
            if items_text:
                message += "موارد سفارش:\n" + "\n".join(items_text[:5])
                if len(items_text) > 5:
                    message += f"\nو {len(items_text) - 5} مورد دیگر..."
                message += "\n\n"
            message += f"وضعیت سفارش شما از «{old_status_display}» به «{status_display}» تغییر کرد."
            
            threading.Thread(
                target=send_sms,
                args=(order.phone_number, message, tenant_id),
                daemon=True
            ).start()
            
            logger.info(f"📱 Status change SMS queued for order #{order.id} to {order.phone_number}")
        except Exception as e:
            logger.error(f"❌ Failed to send status change SMS: {e}", exc_info=True)
    
    @staticmethod
    def delete_order(order_id: int) -> Dict[str, Any]:
        """
        Delete an order.
        
        Args:
            order_id: Order ID
            
        Returns:
            Dictionary with deletion result
        """
        try:
            from restaurant.models import Order
        except ImportError:
            from Reservation_Module.models import Order
        
        try:
            order = Order.objects.get(id=order_id)
            order_id_deleted = order.id
            order.delete()
            
            return {
                'success': True,
                'message': f'سفارش #{order_id_deleted} با موفقیت حذف شد',
                'order_id': order_id_deleted
            }
        except Order.DoesNotExist:
            return {'error': 'سفارش یافت نشد'}
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for delete_order: {e}", exc_info=True)
            return {'error': 'شناسه سفارش نامعتبر است'}
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error deleting order: {e}", exc_info=True)
            return {'error': f'خطا در حذف سفارش: {str(e)}'}
    
    @staticmethod
    def get_orders_by_phone(phone_number: str) -> List[Any]:
        """
        Get orders by phone number.
        
        Args:
            phone_number: Customer phone number
            
        Returns:
            List of orders
        """
        try:
            from restaurant.models import Order
        except ImportError:
            from Reservation_Module.models import Order
        
        return Order.objects.filter(
            phone_number=phone_number
        ).select_related('customer').prefetch_related('items__menu_item').order_by('-order_date')
