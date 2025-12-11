"""
Menu service for managing menu items.
"""

import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class MenuService:
    """Service for menu-related operations"""
    
    @staticmethod
    def get_available_items(
        category: Optional[str] = None,
        special_only: bool = False
    ) -> List[Any]:
        """
        Get available menu items with optional filters.
        
        Args:
            category: Optional category filter
            special_only: If True, only return special items
            
        Returns:
            List of menu items
        """
        try:
            from restaurant.models import MenuItem
        except ImportError:
            from Reservation_Module.models import MenuItem
        
        queryset = MenuItem.objects.filter(is_available=True)
        
        if category:
            queryset = queryset.filter(category=category)
        
        if special_only:
            queryset = queryset.filter(is_special=True)
        
        return queryset.order_by('category', 'name')
    
    @staticmethod
    def get_item_by_id(item_id: int) -> Optional[Any]:
        """
        Get menu item by ID.
        
        Args:
            item_id: Menu item ID
            
        Returns:
            MenuItem instance or None
        """
        try:
            from restaurant.models import MenuItem
        except ImportError:
            from Reservation_Module.models import MenuItem
        
        try:
            return MenuItem.objects.get(id=item_id)
        except MenuItem.DoesNotExist:
            return None
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for get_item_by_id: {e}", exc_info=True)
            return None
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error getting menu item: {e}", exc_info=True)
            return None
