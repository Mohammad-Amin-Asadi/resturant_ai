"""
Unit tests for OrderService.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from restaurant.services.order_service import OrderService
from restaurant.services.customer_service import CustomerService

try:
    from restaurant.models import Order, OrderItem, MenuItem, Customer
    from restaurant.serializers import OrderSerializer
except ImportError:
    from Reservation_Module.models import Order, OrderItem, MenuItem, Customer
    from Reservation_Module.serializers import OrderSerializer


class OrderServiceTestCase(TestCase):
    """Test cases for OrderService"""
    
    def setUp(self):
        """Set up test data"""
        # Create test menu items
        self.menu_item1 = MenuItem.objects.create(
            name="کباب کوبیده",
            category="غذای ایرانی",
            original_price=150000,
            final_price=120000,
            is_available=True
        )
        self.menu_item2 = MenuItem.objects.create(
            name="نوشابه قوطی کوکا",
            category="نوشیدنی",
            original_price=15000,
            final_price=12000,
            is_available=True
        )
        
        # Create test customer
        self.customer = Customer.objects.create(
            name="علی احمدی",
            phone_number="09123456789",
            address="تهران، خیابان ولیعصر"
        )
    
    def test_create_order_success(self):
        """Test successful order creation"""
        decrypted_data = {
            'customer_name': 'علی احمدی',
            'phone_number': '09123456789',
            'address': 'تهران، خیابان ولیعصر',
            'items': [
                {
                    'menu_item': self.menu_item1.id,
                    'quantity': 2,
                    'unit_price': 120000,
                    'subtotal': 240000
                },
                {
                    'menu_item': self.menu_item2.id,
                    'quantity': 1,
                    'unit_price': 12000,
                    'subtotal': 12000
                }
            ]
        }
        
        order, result = OrderService.create_order_from_decrypted_data(decrypted_data, self.customer)
        
        self.assertIsNotNone(order)
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.items.count(), 2)
    
    def test_create_order_no_items(self):
        """Test order creation with no items"""
        decrypted_data = {
            'customer_name': 'علی احمدی',
            'phone_number': '09123456789',
            'address': 'تهران، خیابان ولیعصر',
            'items': []
        }
        
        order, result = OrderService.create_order_from_decrypted_data(decrypted_data)
        
        self.assertIsNone(order)
        self.assertIn('error', result)
        self.assertIn('لیست غذاها خالی', result['error'])
    
    def test_create_order_invalid_menu_item(self):
        """Test order creation with invalid menu item ID"""
        decrypted_data = {
            'customer_name': 'علی احمدی',
            'phone_number': '09123456789',
            'address': 'تهران، خیابان ولیعصر',
            'items': [
                {
                    'menu_item': 99999,  # Non-existent ID
                    'quantity': 1,
                    'unit_price': 120000,
                    'subtotal': 120000
                }
            ]
        }
        
        order, result = OrderService.create_order_from_decrypted_data(decrypted_data)
        
        self.assertIsNone(order)
        self.assertIn('error', result)
        self.assertIn('یافت نشدند', result['error'])
    
    def test_create_order_missing_customer_name(self):
        """Test order creation without customer name"""
        decrypted_data = {
            'customer_name': '',
            'phone_number': '09123456789',
            'address': 'تهران، خیابان ولیعصر',
            'items': [
                {
                    'menu_item': self.menu_item1.id,
                    'quantity': 1,
                    'unit_price': 120000,
                    'subtotal': 120000
                }
            ]
        }
        
        order, result = OrderService.create_order_from_decrypted_data(decrypted_data)
        
        self.assertIsNone(order)
        self.assertIn('error', result)
        self.assertIn('نام مشتری', result['error'])
    
    def test_update_order_status(self):
        """Test updating order status"""
        # Create an order first
        order = Order.objects.create(
            customer_name='علی احمدی',
            phone_number='09123456789',
            address='تهران',
            status='pending'
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item1,
            quantity=1,
            unit_price=120000,
            subtotal=120000
        )
        
        with patch('restaurant.services.order_service.send_sms') as mock_sms:
            result = OrderService.update_order_status(order.id, 'confirmed')
            
            self.assertIn('success', result)
            self.assertTrue(result['success'])
            
            order.refresh_from_db()
            self.assertEqual(order.status, 'confirmed')
    
    def test_delete_order(self):
        """Test deleting an order"""
        order = Order.objects.create(
            customer_name='علی احمدی',
            phone_number='09123456789',
            address='تهران',
            status='pending'
        )
        order_id = order.id
        
        result = OrderService.delete_order(order.id)
        
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertFalse(Order.objects.filter(id=order_id).exists())
    
    def test_get_orders_by_phone(self):
        """Test getting orders by phone number"""
        order1 = Order.objects.create(
            customer_name='علی احمدی',
            phone_number='09123456789',
            address='تهران',
            status='pending'
        )
        order2 = Order.objects.create(
            customer_name='علی احمدی',
            phone_number='09123456789',
            address='تهران',
            status='delivered'
        )
        
        orders = OrderService.get_orders_by_phone('09123456789')
        
        self.assertEqual(len(orders), 2)
        self.assertIn(order1, orders)
        self.assertIn(order2, orders)
