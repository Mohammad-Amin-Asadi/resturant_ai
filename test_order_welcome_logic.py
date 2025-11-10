#!/usr/bin/env python
"""
Test file to verify the order checking logic for welcome message
This will be tested before implementing in production code
"""

# Simulate order data structure (based on OrderSerializer)
def test_order_checking_logic():
    """Test the logic for checking if caller has undelivered orders"""
    
    # Test Case 1: Caller has undelivered order
    orders_case1 = [
        {
            "id": 1,
            "status": "preparing",
            "status_display": "در حال آماده سازی",
            "customer_name": "علی احمدی",
            "total_price": 150000
        }
    ]
    
    # Test Case 2: Caller has only delivered orders
    orders_case2 = [
        {
            "id": 2,
            "status": "delivered",
            "status_display": "تحویل داده شده به مشتری",
            "customer_name": "علی احمدی",
            "total_price": 120000
        }
    ]
    
    # Test Case 3: Caller has no orders
    orders_case3 = []
    
    # Test Case 4: Caller has cancelled order (should be treated as no active order)
    orders_case4 = [
        {
            "id": 3,
            "status": "cancelled",
            "status_display": "لغو شده",
            "customer_name": "علی احمدی",
            "total_price": 100000
        }
    ]
    
    # Test Case 5: Caller has multiple orders, one undelivered
    orders_case5 = [
        {
            "id": 4,
            "status": "delivered",
            "status_display": "تحویل داده شده به مشتری",
            "customer_name": "علی احمدی",
            "total_price": 100000
        },
        {
            "id": 5,
            "status": "on_delivery",
            "status_display": "تحویل داده شده به پیک",
            "customer_name": "علی احمدی",
            "total_price": 200000
        }
    ]
    
    def has_undelivered_order(orders):
        """Check if there are any undelivered orders"""
        if not orders:
            return False, None
        
        # Filter out delivered and cancelled orders
        undelivered = [o for o in orders if o.get("status") not in ["delivered", "cancelled"]]
        
        if undelivered:
            # Return the latest undelivered order (first in list, as orders are sorted by date desc)
            return True, undelivered[0]
        return False, None
    
    def build_welcome_message(has_order, order=None):
        """Build welcome message based on order status"""
        base_greeting = " درودبرشما، با رستوران بزرگمهر تماس گرفته‌اید."
        
        if has_order and order:
            # Has undelivered order - report status
            status_msg = f"سفارش شماره {order['id']} شما {order['status_display']} است."
            return f"{base_greeting} {status_msg}"
        else:
            # No undelivered orders - ask if they want to order
            return f"{base_greeting} آیا می‌خواهید سفارش جدیدی ثبت کنید؟"
    
    # Run tests
    print("=" * 80)
    print("TEST 1: Caller has undelivered order (preparing)")
    print("=" * 80)
    has_order, order = has_undelivered_order(orders_case1)
    message = build_welcome_message(has_order, order)
    print(f"Has undelivered order: {has_order}")
    print(f"Order: {order}")
    print(f"Welcome message: {message}")
    print()
    
    print("=" * 80)
    print("TEST 2: Caller has only delivered orders")
    print("=" * 80)
    has_order, order = has_undelivered_order(orders_case2)
    message = build_welcome_message(has_order, order)
    print(f"Has undelivered order: {has_order}")
    print(f"Order: {order}")
    print(f"Welcome message: {message}")
    print()
    
    print("=" * 80)
    print("TEST 3: Caller has no orders")
    print("=" * 80)
    has_order, order = has_undelivered_order(orders_case3)
    message = build_welcome_message(has_order, order)
    print(f"Has undelivered order: {has_order}")
    print(f"Order: {order}")
    print(f"Welcome message: {message}")
    print()
    
    print("=" * 80)
    print("TEST 4: Caller has cancelled order")
    print("=" * 80)
    has_order, order = has_undelivered_order(orders_case4)
    message = build_welcome_message(has_order, order)
    print(f"Has undelivered order: {has_order}")
    print(f"Order: {order}")
    print(f"Welcome message: {message}")
    print()
    
    print("=" * 80)
    print("TEST 5: Caller has multiple orders, one undelivered")
    print("=" * 80)
    has_order, order = has_undelivered_order(orders_case5)
    message = build_welcome_message(has_order, order)
    print(f"Has undelivered order: {has_order}")
    print(f"Order: {order}")
    print(f"Welcome message: {message}")
    print()
    
    print("=" * 80)
    print("ALL TESTS COMPLETED")
    print("=" * 80)

if __name__ == "__main__":
    test_order_checking_logic()

