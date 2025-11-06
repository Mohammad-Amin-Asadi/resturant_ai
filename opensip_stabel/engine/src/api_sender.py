# # api_sender.py
# import base64
# import json
# import requests
# from Crypto.Cipher import PKCS1_OAEP
# from Crypto.PublicKey import RSA

# class API:
#     def __init__(self, server_url: str) -> None:
#         self.server_url = f"{server_url}/add-reservation/"

#     def __call__(self, fullname: str, origin: str, destination: str) -> bool:
#         try:
#             response = requests.get(self.server_url, timeout=10)
#             response.raise_for_status()
#             public_key = response.json()["public_key"]

#             data = {
#                 "user_fullname": fullname,
#                 "origin": origin,
#                 "destination": destination
#             }
#             data = self.encoder(public_key, data)

#             response = requests.post(self.server_url, data=data, timeout=10)
#             response.raise_for_status()
#             print(f"Sent data to server with status code: {response.status_code}")
#             return True
#         except requests.exceptions.RequestException as e:
#             if e.response:
#                 print(f"Error in sending data: {e}\n{e.response.text}")
#             else:
#                 print("Server down. Please try again.")
#             return False

#     @staticmethod
#     def encoder(public_key, data):
#         data_bytes = json.dumps(data).encode("utf-8")
#         recipient_key = RSA.import_key(public_key)
#         cipher_rsa = PKCS1_OAEP.new(recipient_key)
#         encrypted = cipher_rsa.encrypt(data_bytes)
#         encoded = base64.b64encode(encrypted).decode()

#         return {"public_key": public_key, "data": encoded}



# api_sender.py
import base64
import json
import requests
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
import logging
from phone_normalizer import normalize_phone_number

class API:
    """Restaurant ordering API client"""
    
    def __init__(self, server_url: str) -> None:
        self.base_url = server_url.rstrip('/')
        self.orders_url = f"{self.base_url}/api/orders/"
        self.menu_url = f"{self.base_url}/api/menu/"
        self.track_url = f"{self.base_url}/api/orders/track/"
    
    async def track_order(self, phone_number: str) -> dict:
        """Track order by phone number"""
        try:
            # Normalize phone number (remove spaces, convert Persian digits)
            normalized_phone = normalize_phone_number(phone_number)
            logging.info(f"📱 Normalizing phone: '{phone_number}' -> '{normalized_phone}'")
            
            response = requests.get(
                self.track_url,
                params={"phone_number": normalized_phone},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return {"success": True, "orders": data}
        except requests.exceptions.RequestException as e:
            logging.error(f"Error tracking order: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_menu_specials(self) -> dict:
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
            logging.error(f"Error getting menu specials: {e}")
            return {"success": False, "message": str(e)}
    
    async def search_menu_item(self, item_name: str, category: str = None) -> dict:
        """Search for menu item by name"""
        try:
            params = {}
            if category:
                params["category"] = category
            
            response = requests.get(self.menu_url, params=params, timeout=10)
            response.raise_for_status()
            all_items = response.json()
            
            # Simple fuzzy search
            search_lower = item_name.lower()
            matches = [item for item in all_items if search_lower in item['name'].lower()]
            
            return {"success": True, "items": matches[:5]}  # Return top 5 matches
        except requests.exceptions.RequestException as e:
            logging.error(f"Error searching menu: {e}")
            return {"success": False, "message": str(e)}
    
    async def create_order(self, customer_name: str, phone_number: str, 
                           address: str, items: list, notes: str = None) -> dict:
        """Create a new restaurant order"""
        try:
            logging.info("Creating order in backend")
            
            # Normalize phone number (remove spaces, convert Persian digits)
            normalized_phone = normalize_phone_number(phone_number)
            logging.info(f"📱 Normalizing phone: '{phone_number}' -> '{normalized_phone}'")
            
            # Get public key
            response = requests.get(self.orders_url, timeout=10)
            response.raise_for_status()
            public_key = response.json()["public_key"]
            
            # Prepare order data - need to match menu items with IDs
            order_data = {
                "customer_name": customer_name,
                "phone_number": normalized_phone,  # Use normalized phone
                "address": address,
                "notes": notes,
                "items": []
            }
            
            # For each item, we need to find its menu_item ID and unit_price
            for item in items:
                item_name = item.get("item_name")
                quantity = item.get("quantity", 1)
                
                # Search for the item in menu
                search_result = await self.search_menu_item(item_name)
                if search_result.get("success") and search_result.get("items"):
                    menu_item = search_result["items"][0]  # Take first match
                    order_data["items"].append({
                        "menu_item": menu_item["id"],
                        "quantity": quantity,
                        "unit_price": menu_item["final_price"]
                    })
            
            # Encrypt and send
            encrypted_data = self.encoder(public_key, order_data)
            
            response = requests.post(self.orders_url, json=encrypted_data, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            logging.info(f"Order created successfully: {result}")
            return {"success": True, "order": result.get("order", {})}
        except requests.exceptions.RequestException as e:
            logging.error(f"Error creating order: {e}")
            if hasattr(e, 'response') and e.response:
                logging.error(f"Response: {e.response.text}")
            return {"success": False, "message": str(e)}
    
    @staticmethod
    def encoder(public_key, data):
        """
        Hybrid encryption:
         - encrypt `data` (JSON) with AES-GCM (AES-256)
         - encrypt AES key with RSA-OAEP using `public_key`
         - package = { key, nonce, tag, ciphertext } (all base64)
         - final 'data' field is base64(JSON(package))
        """
        # 1) convert payload to bytes (utf-8 to support non-ascii)
        data_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

        # 2) generate a random AES-256 key and encrypt payload with AES-GCM
        sym_key = get_random_bytes(32)  # 32 bytes = AES-256
        cipher_aes = AES.new(sym_key, AES.MODE_GCM)  # GCM provides authenticity
        ciphertext, tag = cipher_aes.encrypt_and_digest(data_bytes)

        # 3) encrypt the symmetric key with RSA-OAEP
        recipient_key = RSA.import_key(public_key)
        cipher_rsa = PKCS1_OAEP.new(recipient_key)
        enc_sym_key = cipher_rsa.encrypt(sym_key)

        # 4) package components and base64-encode them
        package = {
            "key": base64.b64encode(enc_sym_key).decode(),
            "nonce": base64.b64encode(cipher_aes.nonce).decode(),
            "tag": base64.b64encode(tag).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode()
        }

        # 5) encode the package as a base64'd JSON string
        encoded = base64.b64encode(json.dumps(package, ensure_ascii=False).encode("utf-8")).decode()

        # Return in expected format
        return {"public_key": public_key, "data": encoded}
