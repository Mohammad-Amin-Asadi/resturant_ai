"""
Backend API Adapter interface and implementations.
"""

import logging
import requests
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from phone_normalizer import normalize_phone_number

logger = logging.getLogger(__name__)


class BackendAdapter(ABC):
    """Abstract base class for backend API adapters"""
    
    @abstractmethod
    async def track_order(self, phone_number: str) -> Dict[str, Any]:
        """Track order by phone number"""
        pass
    
    @abstractmethod
    async def get_customer_info(self, phone_number: str) -> Dict[str, Any]:
        """Get customer information by phone number"""
        pass
    
    @abstractmethod
    async def get_menu_specials(self) -> Dict[str, Any]:
        """Get special menu items"""
        pass
    
    @abstractmethod
    async def search_menu_item(self, item_name: str, category: Optional[str] = None) -> Dict[str, Any]:
        """Search for menu item by name"""
        pass
    
    @abstractmethod
    async def create_order(self, customer_name: str, phone_number: str, address: str, 
                          items: List[Dict[str, Any]], notes: Optional[str] = None) -> Dict[str, Any]:
        """Create a new order"""
        pass
    
    @abstractmethod
    async def get_top_menu_items(self, limit: int = 10, include_drinks: bool = True) -> Dict[str, Any]:
        """Get top menu items"""
        pass


class DjangoBackendAdapter(BackendAdapter):
    """Django backend API adapter implementation"""
    
    def __init__(self, base_url: str):
        """
        Initialize Django backend adapter.
        
        Args:
            base_url: Base URL of Django backend (e.g., "http://localhost:8000")
        """
        self.base_url = base_url.rstrip('/')
        self.orders_url = f"{self.base_url}/api/orders/"
        self.menu_url = f"{self.base_url}/api/menu/"
        self.track_url = f"{self.base_url}/api/orders/track/"
        self.customer_info_url = f"{self.base_url}/api/customers/info/"
    
    async def track_order(self, phone_number: str) -> Dict[str, Any]:
        """Track order by phone number"""
        try:
            normalized_phone = normalize_phone_number(phone_number)
            logger.info(f"📱 Tracking order for phone: '{phone_number}' -> '{normalized_phone}'")
            
            response = requests.get(
                self.track_url,
                params={"phone_number": normalized_phone},
                timeout=10
            )
            
            if response.status_code == 404:
                logger.info("📭 No orders found (404)")
                return {"success": True, "orders": []}
            
            response.raise_for_status()
            data = response.json()
            
            if isinstance(data, list) and len(data) == 0:
                return {"success": True, "orders": []}
            
            return {"success": True, "orders": data}
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response and e.response.status_code == 404:
                return {"success": True, "orders": []}
            logger.error(f"Error tracking order: {e}")
            return {"success": False, "message": str(e)}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error tracking order: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_customer_info(self, phone_number: str) -> Dict[str, Any]:
        """Get customer information by phone number"""
        try:
            normalized_phone = normalize_phone_number(phone_number)
            logger.info(f"📱 Getting customer info for phone: '{phone_number}' -> '{normalized_phone}'")
            
            response = requests.get(
                self.customer_info_url,
                params={"phone_number": normalized_phone},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting customer info: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_menu_specials(self) -> Dict[str, Any]:
        """Get special menu items"""
        try:
            response = requests.get(
                self.menu_url,
                params={"special": "true"},
                timeout=10
            )
            response.raise_for_status()
            items = response.json()
            return {"success": True, "items": items}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting menu specials: {e}")
            return {"success": False, "message": str(e)}
    
    async def search_menu_item(self, item_name: str, category: Optional[str] = None) -> Dict[str, Any]:
        """Search for menu item by name"""
        # This would use the complex search logic from api_sender.py
        # For now, simplified version
        try:
            params = {}
            if category:
                params["category"] = category
            
            response = requests.get(self.menu_url, params=params, timeout=10)
            response.raise_for_status()
            all_items = response.json()
            
            # Simple search (full implementation would use alias matching from api_sender.py)
            matches = [item for item in all_items if item_name.lower() in item.get('name', '').lower()]
            
            return {"success": True, "items": matches[:5]}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching menu: {e}")
            return {"success": False, "message": str(e)}
    
    async def create_order(self, customer_name: str, phone_number: str, address: str,
                          items: List[Dict[str, Any]], notes: Optional[str] = None) -> Dict[str, Any]:
        """Create a new order"""
        try:
            # Get public key
            response = requests.get(self.orders_url, timeout=10)
            response.raise_for_status()
            public_key = response.json()["public_key"]
            
            # Prepare data
            data = {
                "customer_name": customer_name,
                "phone_number": normalize_phone_number(phone_number),
                "address": address,
                "items": items,
            }
            if notes:
                data["notes"] = notes
            
            # Encrypt data
            encrypted_data = API.encoder(public_key, data)
            
            # Send order
            response = requests.post(self.orders_url, json=encrypted_data, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            return {"success": True, "order": result.get("order", {})}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating order: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_top_menu_items(self, limit: int = 10, include_drinks: bool = True) -> Dict[str, Any]:
        """Get top menu items"""
        try:
            # Get special items
            special_params = {"special": "true", "is_available": "true"}
            special_response = requests.get(self.menu_url, params=special_params, timeout=10)
            special_response.raise_for_status()
            special_items = special_response.json()
            
            # Get regular items
            food_params = {"category": "غذای ایرانی"}
            food_response = requests.get(self.menu_url, params=food_params, timeout=10)
            food_response.raise_for_status()
            food_items = food_response.json()
            
            # Get drinks if requested
            drink_items = []
            if include_drinks:
                drink_params = {"category": "نوشیدنی"}
                drink_response = requests.get(self.menu_url, params=drink_params, timeout=10)
                drink_response.raise_for_status()
                drink_items = drink_response.json()
            
            # Combine: special items first, then foods, then drinks
            all_items = []
            special_limit = min(len(special_items), limit // 2)
            all_items.extend(special_items[:special_limit])
            
            remaining = limit - len(all_items)
            if remaining > 0:
                all_items.extend(food_items[:remaining])
            
            if include_drinks:
                remaining = limit - len(all_items)
                if remaining > 0:
                    all_items.extend(drink_items[:remaining])
            
            return {"success": True, "items": all_items[:limit]}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting top menu items: {e}")
            return {"success": False, "message": str(e)}


# Import API for encoder method
try:
    from api_sender import API
except ImportError:
    logger.warning("api_sender.API not available for encryption")
