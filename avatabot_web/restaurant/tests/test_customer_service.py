"""
Unit tests for CustomerService.
"""

from django.test import TestCase
from restaurant.services.customer_service import CustomerService

try:
    from restaurant.models import Customer
except ImportError:
    from Reservation_Module.models import Customer


class CustomerServiceTestCase(TestCase):
    """Test cases for CustomerService"""
    
    def test_get_or_create_customer_new(self):
        """Test creating a new customer"""
        customer, created = CustomerService.get_or_create_customer(
            phone_number='09123456789',
            name='علی احمدی',
            address='تهران'
        )
        
        self.assertTrue(created)
        self.assertEqual(customer.phone_number, '09123456789')
        self.assertEqual(customer.name, 'علی احمدی')
        self.assertEqual(customer.address, 'تهران')
    
    def test_get_or_create_customer_existing(self):
        """Test getting existing customer"""
        # Create customer first
        existing = Customer.objects.create(
            phone_number='09123456789',
            name='علی احمدی',
            address='تهران'
        )
        
        customer, created = CustomerService.get_or_create_customer(
            phone_number='09123456789',
            name='علی احمدی جدید',
            address='اصفهان'
        )
        
        self.assertFalse(created)
        self.assertEqual(customer.id, existing.id)
        self.assertEqual(customer.name, 'علی احمدی جدید')
        self.assertEqual(customer.address, 'اصفهان')
    
    def test_get_customer_by_phone_found(self):
        """Test getting customer by phone number"""
        customer = Customer.objects.create(
            phone_number='09123456789',
            name='علی احمدی',
            address='تهران'
        )
        
        result = CustomerService.get_customer_by_phone('09123456789')
        
        self.assertIsNotNone(result)
        self.assertEqual(result.id, customer.id)
    
    def test_get_customer_by_phone_not_found(self):
        """Test getting non-existent customer"""
        result = CustomerService.get_customer_by_phone('09123456789')
        
        self.assertIsNone(result)
    
    def test_delete_customer_success(self):
        """Test deleting a customer"""
        customer = Customer.objects.create(
            phone_number='09123456789',
            name='علی احمدی',
            address='تهران'
        )
        customer_id = customer.id
        
        result = CustomerService.delete_customer(customer_id)
        
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertFalse(Customer.objects.filter(id=customer_id).exists())
    
    def test_delete_customer_not_found(self):
        """Test deleting non-existent customer"""
        result = CustomerService.delete_customer(99999)
        
        self.assertIn('success', result)
        self.assertFalse(result['success'])
        self.assertIn('error', result)
