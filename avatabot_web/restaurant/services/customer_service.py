"""
Customer service for managing customer operations.
"""

import logging
from typing import Optional, Dict, Any
from django.db import transaction

logger = logging.getLogger(__name__)


class CustomerService:
    """Service for customer-related operations"""
    
    @staticmethod
    def get_or_create_customer(
        phone_number: str,
        name: Optional[str] = None,
        address: Optional[str] = None
    ) -> tuple[Any, bool]:
        """
        Get or create a customer by phone number.
        
        Args:
            phone_number: Customer phone number
            name: Customer name (optional)
            address: Customer address (optional)
            
        Returns:
            Tuple of (customer, created)
        """
        try:
            from restaurant.models import Customer
        except ImportError:
            from Reservation_Module.models import Customer
        
        if not phone_number:
            raise ValueError("Phone number is required")
        
        try:
            customer, created = Customer.objects.get_or_create(
                phone_number=phone_number,
                defaults={
                    'name': name or '',
                    'address': address or ''
                }
            )
            
            if not created:
                updated = False
                if name and customer.name != name:
                    customer.name = name
                    updated = True
                if address and customer.address != address:
                    customer.address = address
                    updated = True
                
                if updated:
                    customer.save()
            
            return customer, created
        except Exception as e:
            logger.error(f"Error creating/updating customer: {e}", exc_info=True)
            raise
    
    @staticmethod
    def get_customer_by_phone(phone_number: str) -> Optional[Any]:
        """
        Get customer by phone number.
        
        Args:
            phone_number: Customer phone number
            
        Returns:
            Customer instance or None
        """
        try:
            from restaurant.models import Customer
        except ImportError:
            from Reservation_Module.models import Customer
        
        try:
            return Customer.objects.get(phone_number=phone_number)
        except Customer.DoesNotExist:
            return None
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for get_customer_by_phone: {e}", exc_info=True)
            return None
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error getting customer: {e}", exc_info=True)
            return None
    
    @staticmethod
    def delete_customer(customer_id: int) -> Dict[str, Any]:
        """
        Delete a customer.
        
        Args:
            customer_id: Customer ID
            
        Returns:
            Dictionary with deletion result
        """
        try:
            from restaurant.models import Customer
        except ImportError:
            from Reservation_Module.models import Customer
        
        try:
            customer = Customer.objects.get(id=customer_id)
            customer_name = customer.name
            customer_phone = customer.phone_number
            customer.delete()
            
            return {
                'success': True,
                'message': f'مشتری {customer_name} ({customer_phone}) با موفقیت حذف شد'
            }
        except Customer.DoesNotExist:
            return {
                'success': False,
                'error': 'مشتری یافت نشد'
            }
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for delete_customer: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'شناسه مشتری نامعتبر است'
            }
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error deleting customer: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'خطا در حذف مشتری: {str(e)}'
            }
