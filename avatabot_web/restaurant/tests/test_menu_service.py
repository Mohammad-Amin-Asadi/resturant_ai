"""
Unit tests for MenuService.
"""

from django.test import TestCase
from restaurant.services.menu_service import MenuService

try:
    from restaurant.models import MenuItem
except ImportError:
    from Reservation_Module.models import MenuItem


class MenuServiceTestCase(TestCase):
    """Test cases for MenuService"""
    
    def setUp(self):
        """Set up test data"""
        self.menu_item1 = MenuItem.objects.create(
            name="کباب کوبیده",
            category="غذای ایرانی",
            original_price=150000,
            final_price=120000,
            is_available=True,
            is_special=False
        )
        self.menu_item2 = MenuItem.objects.create(
            name="نوشابه قوطی کوکا",
            category="نوشیدنی",
            original_price=15000,
            final_price=12000,
            is_available=True,
            is_special=True
        )
        self.menu_item3 = MenuItem.objects.create(
            name="غذای غیرفعال",
            category="غذای ایرانی",
            original_price=100000,
            final_price=80000,
            is_available=False,
            is_special=False
        )
    
    def test_get_available_items_all(self):
        """Test getting all available items"""
        items = MenuService.get_available_items()
        
        self.assertEqual(len(items), 2)  # Only available items
        self.assertIn(self.menu_item1, items)
        self.assertIn(self.menu_item2, items)
        self.assertNotIn(self.menu_item3, items)
    
    def test_get_available_items_by_category(self):
        """Test getting items by category"""
        items = MenuService.get_available_items(category='غذای ایرانی')
        
        self.assertEqual(len(items), 1)
        self.assertIn(self.menu_item1, items)
        self.assertNotIn(self.menu_item2, items)
    
    def test_get_available_items_special_only(self):
        """Test getting special items only"""
        items = MenuService.get_available_items(special_only=True)
        
        self.assertEqual(len(items), 1)
        self.assertIn(self.menu_item2, items)
        self.assertNotIn(self.menu_item1, items)
    
    def test_get_item_by_id_found(self):
        """Test getting item by ID"""
        item = MenuService.get_item_by_id(self.menu_item1.id)
        
        self.assertIsNotNone(item)
        self.assertEqual(item.id, self.menu_item1.id)
    
    def test_get_item_by_id_not_found(self):
        """Test getting non-existent item"""
        item = MenuService.get_item_by_id(99999)
        
        self.assertIsNone(item)
