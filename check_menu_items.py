#!/usr/bin/env python
"""
Script to check if menu items exist in the database.
Can be run from host or inside Django container.
"""

import os
import sys
import django

# Add Django project to path
sys.path.insert(0, '/home/avatabot_web')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Server.settings')

try:
    django.setup()
except Exception as e:
    print(f"Error setting up Django: {e}")
    print("\nTrying to run from container...")
    print("Run this command inside the Django container:")
    print("  docker exec -it avatabot-backend-restaurant python /home/check_menu_items.py")
    sys.exit(1)

from django.db import connection
from restaurant.models import MenuItem
from Reservation_Module.models import MenuItem as OldMenuItem

def check_menu_items():
    """Check if menu items exist in database"""
    print("=" * 60)
    print("Checking Menu Items in Database")
    print("=" * 60)
    
    # Check database connection
    try:
        connection.ensure_connection()
        print("✅ Database connection: OK")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False
    
    # Try new model first (restaurant app)
    try:
        total_count = MenuItem.objects.count()
        available_count = MenuItem.objects.filter(is_available=True).count()
        special_count = MenuItem.objects.filter(is_special=True, is_available=True).count()
        food_count = MenuItem.objects.filter(category="غذای ایرانی", is_available=True).count()
        drink_count = MenuItem.objects.filter(category="نوشیدنی", is_available=True).count()
        
        print(f"\n📊 Menu Items Statistics (restaurant.MenuItem):")
        print(f"   Total items: {total_count}")
        print(f"   Available items: {available_count}")
        print(f"   Special items (available): {special_count}")
        print(f"   Foods (غذای ایرانی, available): {food_count}")
        print(f"   Drinks (نوشیدنی, available): {drink_count}")
        
        if total_count == 0:
            print("\n⚠️  WARNING: No menu items found in database!")
            print("   You need to populate the menu using:")
            print("   docker exec -it avatabot-backend-restaurant python manage.py populate_menu")
            return False
        
        if available_count == 0:
            print("\n⚠️  WARNING: No available menu items found!")
            print("   All items are marked as unavailable (is_available=False)")
            return False
        
        # Show sample items
        print(f"\n📋 Sample Menu Items (first 5):")
        sample_items = MenuItem.objects.filter(is_available=True)[:5]
        for i, item in enumerate(sample_items, 1):
            special_mark = "⭐" if item.is_special else "  "
            print(f"   {i}. {special_mark} {item.name} - {item.final_price:,} تومان ({item.category})")
        
        # Check categories
        categories = MenuItem.objects.filter(is_available=True).values_list('category', flat=True).distinct()
        print(f"\n📂 Available Categories: {', '.join(categories) or 'None'}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error querying restaurant.MenuItem: {e}")
        # Try old model as fallback
        try:
            total_count = OldMenuItem.objects.count()
            available_count = OldMenuItem.objects.filter(is_available=True).count()
            print(f"\n📊 Menu Items Statistics (Reservation_Module.MenuItem):")
            print(f"   Total items: {total_count}")
            print(f"   Available items: {available_count}")
            
            if total_count == 0:
                print("\n⚠️  WARNING: No menu items found in database!")
                return False
            
            return True
        except Exception as e2:
            print(f"❌ Error querying Reservation_Module.MenuItem: {e2}")
            return False

if __name__ == "__main__":
    success = check_menu_items()
    print("\n" + "=" * 60)
    if success:
        print("✅ Menu items check completed successfully")
    else:
        print("❌ Menu items check found issues - see warnings above")
    print("=" * 60)
    sys.exit(0 if success else 1)
