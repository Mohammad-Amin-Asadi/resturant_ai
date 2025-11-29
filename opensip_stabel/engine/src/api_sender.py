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
        self.customer_info_url = f"{self.base_url}/api/customers/info/"
        
        # Alias mapping for common item names
        self.item_aliases = {
            # نوشابه کوچک (بدون ذکر رنگ) → نوشابه قوطی کوکا
            'نوشابه کوچک': 'نوشابه قوطی کوکا',
            'نوشابه کوچک کوکا': 'نوشابه قوطی کوکا',
            'کوکا کوچک': 'نوشابه قوطی کوکا',
            'کوکا قوطی': 'نوشابه قوطی کوکا',
            
            # نوشابه زرد کوچک → نوشابه شیشه فانتا (بطری = شیشه)
            'نوشابه زرد کوچک': 'نوشابه شیشه فانتا',
            'نوشابه کوچک زرد': 'نوشابه شیشه فانتا',
            'فانتا کوچک': 'نوشابه شیشه فانتا',
            'نوشابه زرد': 'نوشابه قوطی فانتا',  # اگر فقط "زرد" بگوید، قوطی فانتا
            
            # نوشابه خانواده یا بزرگ (بدون ذکر رنگ) → نوشابه خانواده کوکا
            'نوشابه خانواده': 'نوشابه خانواده کوکا',
            'نوشابه بزرگ': 'نوشابه خانواده کوکا',
            'نوشابه خانواده مشکی': 'نوشابه خانواده کوکا',
            'نوشابه خانواده سیاه': 'نوشابه خانواده کوکا',
            'نوشابه بزرگ مشکی': 'نوشابه خانواده کوکا',
            'نوشابه بزرگ سیاه': 'نوشابه خانواده کوکا',
            'کوکا خانواده': 'نوشابه خانواده کوکا',
            
            # نوشابه زرد خانواده یا بزرگ → نوشابه خانواده فانتا
            'نوشابه زرد خانواده': 'نوشابه خانواده فانتا',
            'نوشابه زرد بزرگ': 'نوشابه خانواده فانتا',
            'نوشابه خانواده زرد': 'نوشابه خانواده فانتا',
            'نوشابه بزرگ زرد': 'نوشابه خانواده فانتا',
            'فانتا خانواده': 'نوشابه خانواده فانتا',
            
            # Other common aliases
            'نوشابه مشکی': 'نوشابه قوطی کوکا',
            'نوشابه سیاه': 'نوشابه قوطی کوکا',
            'کوکا': 'نوشابه قوطی کوکا',
            'فانتا': 'نوشابه قوطی فانتا',
            
            # ته‌چین مرغ → ته‌چین مرغ (نه بره)
            'ته چین مرغ': 'تَه‌چین مرغ',
            'ته‌چین مرغ': 'تَه‌چین مرغ',
            'ته چین': 'تَه‌چین مرغ',  # اگر فقط "ته چین" بگوید، مرغ باشد
            
            # کباب کوبیده → کَباب کُوبیده (با اعراب)
            'کباب کوبیده': 'کَباب کُوبیده',
            'کوبیده': 'کَباب کُوبیده',
            'پرس کوبیده': 'کَباب کُوبیده',
            
            # زیتون → زیتون پرورده شرکتی (رایج‌ترین نوع)
            'زیتون': 'زیتون پَرورده شِرکتی',
            'زیتون پرورده': 'زیتون پَرورده شِرکتی',
        }
    
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
            
            # Handle 404 as "no orders" (not an error)
            if response.status_code == 404:
                logging.info("📭 No orders found (404) - customer has no orders")
                return {"success": True, "orders": []}
            
            response.raise_for_status()
            data = response.json()
            
            # Handle empty list response
            if isinstance(data, list) and len(data) == 0:
                logging.info("📭 No orders found (empty list)")
                return {"success": True, "orders": []}
            
            return {"success": True, "orders": data}
        except requests.exceptions.HTTPError as e:
            # Only log as error if it's not a 404 (already handled above)
            if hasattr(e, 'response') and e.response and e.response.status_code == 404:
                logging.info("📭 No orders found (404) - customer has no orders")
                return {"success": True, "orders": []}
            else:
                logging.error(f"Error tracking order: {e}")
                return {"success": False, "message": str(e)}
        except requests.exceptions.RequestException as e:
            logging.error(f"Error tracking order: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_customer_info(self, phone_number: str) -> dict:
        """Get customer information by phone number"""
        try:
            # Normalize phone number (remove spaces, convert Persian digits)
            normalized_phone = normalize_phone_number(phone_number)
            logging.info(f"📱 Getting customer info for phone: '{phone_number}' -> '{normalized_phone}'")
            
            response = requests.get(
                self.customer_info_url,
                params={"phone_number": normalized_phone},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting customer info: {e}")
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
    
    def _remove_diacritics(self, text: str) -> str:
        """Remove Persian/Arabic diacritics from text for comparison"""
        # Persian/Arabic diacritics (اعراب)
        diacritics = {
            '\u064B',  # Fathatan
            '\u064C',  # Dammatan
            '\u064D',  # Kasratan
            '\u064E',  # Fatha (َ)
            '\u064F',  # Damma (ُ)
            '\u0650',  # Kasra (ِ)
            '\u0651',  # Shadda
            '\u0652',  # Sukun
            '\u0653',  # Maddah
            '\u0654',  # Hamza Above
            '\u0655',  # Hamza Below
            '\u0656',  # Subscript Alef
            '\u0657',  # Inverted Damma
            '\u0658',  # Mark Noon Ghunna
            '\u0659',  # Zwarakay
            '\u065A',  # Vowel Sign Small V Above
            '\u065B',  # Vowel Sign Inverted Small V Above
            '\u065C',  # Vowel Sign Dot Below
            '\u065D',  # Reversed Damma
            '\u065E',  # Fatha With Two Dots
            '\u065F',  # Wavy Hamza Below
            '\u0670',  # Superscript Alef
        }
        # Remove all diacritics
        result = ''.join(char for char in text if char not in diacritics)
        return result
    
    def _normalize_for_search(self, text: str) -> str:
        """Normalize text for search: remove diacritics, convert to lowercase, remove spaces"""
        if not text:
            return ""
        normalized = self._remove_diacritics(text)
        normalized = normalized.lower().strip()
        # Remove zero-width non-joiner (نیم‌فاصله)
        normalized = normalized.replace('\u200C', ' ')
        # Normalize spaces - replace multiple spaces with single space
        normalized = ' '.join(normalized.split())
        return normalized
    
    def _expand_aliases(self, item_name: str) -> list:
        """Expand item name using alias mapping. Returns list of possible names to search."""
        if not item_name:
            return [item_name]
        
        # Normalize the input for alias lookup
        normalized_input = self._normalize_for_search(item_name)
        
        # Sort aliases by length (longest first) to match more specific aliases first
        sorted_aliases = sorted(self.item_aliases.items(), key=lambda x: len(x[0]), reverse=True)
        
        # Check exact alias match first
        for alias, actual_name in sorted_aliases:
            alias_normalized = self._normalize_for_search(alias)
            if normalized_input == alias_normalized:
                logging.info("🔗 Exact alias match: '%s' → '%s'", item_name, actual_name)
                return [actual_name, item_name]  # Try actual name first, then original
        
        # Check if alias is contained in search term (e.g., "نوشابه کوچک کوکا" contains "نوشابه کوچک")
        # Try longest aliases first for better matching
        best_match = None
        best_match_length = 0
        
        for alias, actual_name in sorted_aliases:
            alias_normalized = self._normalize_for_search(alias)
            # Check if alias is a complete word/phrase in the search term
            # This handles cases like "نوشابه زرد کوچک" containing "نوشابه زرد"
            if alias_normalized in normalized_input:
                # Make sure it's not just a partial match (e.g., "کوکا" shouldn't match "کوکاکولا")
                # Check word boundaries or if it's at the start/end
                start_pos = normalized_input.find(alias_normalized)
                if start_pos == 0 or (start_pos > 0 and normalized_input[start_pos - 1] == ' '):
                    # Alias found at start or after space - good match
                    # Prefer longer matches (more specific)
                    if len(alias_normalized) > best_match_length:
                        best_match = (alias, actual_name)
                        best_match_length = len(alias_normalized)
        
        if best_match:
            alias, actual_name = best_match
            logging.info("🔗 Partial alias match: '%s' contains '%s' → '%s'", item_name, alias, actual_name)
            return [actual_name, item_name]  # Try actual name first
        
        # No alias found, return original
        return [item_name]
    
    def _calculate_similarity(self, search_term: str, item_name: str) -> float:
        """Calculate similarity score between search term and item name"""
        search_normalized = self._normalize_for_search(search_term)
        item_normalized = self._normalize_for_search(item_name)
        
        if not search_normalized or not item_normalized:
            return 0.0
        
        # Exact match - highest priority
        if search_normalized == item_normalized:
            return 1.0
        
        # Check if search term starts the item name (e.g., "نوشابه خانواده" matches "نوشابه خانواده کوکا")
        if item_normalized.startswith(search_normalized):
            length_ratio = len(search_normalized) / len(item_normalized)
            # Higher score if search term covers more of the item name
            score = 0.85 + (0.1 * length_ratio)
            return min(score, 0.99)
        
        # Check if search term is in item name (substring match)
        if search_normalized in item_normalized:
            # Calculate score based on position and length
            position = item_normalized.find(search_normalized)
            length_ratio = len(search_normalized) / len(item_normalized)
            # Higher score if search term is at the beginning and covers more of the item name
            score = 0.5 + (0.3 * (1 - position / max(len(item_normalized), 1))) + (0.2 * length_ratio)
            return min(score, 0.84)  # Lower than starts_with matches
        
        # Check word-by-word matching - ALL words must be present for good score
        search_words = search_normalized.split()
        item_words = item_normalized.split()
        
        if search_words and item_words:
            # Check if ALL search words are present in item words (important for "نوشابه خانواده")
            all_words_match = True
            matched_words = 0
            for search_word in search_words:
                word_found = False
                for item_word in item_words:
                    # Exact word match
                    if search_word == item_word:
                        word_found = True
                        matched_words += 1
                        break
                    # Substring match (e.g., "کوبیده" in "کباب کوبیده")
                    elif search_word in item_word or item_word in search_word:
                        word_found = True
                        matched_words += 0.8  # Partial credit for substring
                        break
                if not word_found:
                    all_words_match = False
            
            # If ALL words match, give high score
            if all_words_match and matched_words >= len(search_words):
                # Check if words are in same order (bonus) - important for "نوشابه خانواده"
                search_first_word = search_words[0]
                item_first_word = item_words[0]
                # Bonus if first words match (e.g., "نوشابه" matches "نوشابه")
                order_bonus = 0.15 if (search_first_word == item_first_word or 
                                       search_first_word in item_first_word or 
                                       item_first_word in search_first_word) else 0.05
                word_score = (matched_words / len(search_words)) * 0.7 + order_bonus
                return min(word_score, 0.79)  # Lower than substring matches
            
            # Partial word matches (some words match) - lower score
            # This prevents "سالاد خانواده" from matching "نوشابه خانواده" well
            if matched_words > 0:
                # Penalize if first word doesn't match (e.g., "سالاد" vs "نوشابه")
                search_first_word = search_words[0]
                item_first_word = item_words[0]
                first_word_penalty = 0.3 if (search_first_word != item_first_word and 
                                             search_first_word not in item_first_word and 
                                             item_first_word not in search_first_word) else 0
                word_score = (matched_words / len(search_words)) * 0.5 - first_word_penalty
                return max(word_score, 0.0)  # Don't return negative
        
        return 0.0
    
    def _create_auto_alias(self, item_name_with_diacritics: str) -> str:
        """Create alias without diacritics for an item name with diacritics"""
        return self._remove_diacritics(item_name_with_diacritics)
    
    async def search_menu_item(self, item_name: str, category: str = None) -> dict:
        """Search for menu item by name (diacritic-insensitive)"""
        try:
            params = {}
            if category:
                params["category"] = category
            
            response = requests.get(self.menu_url, params=params, timeout=10)
            response.raise_for_status()
            all_items = response.json()
            
            # Expand aliases first
            search_terms = self._expand_aliases(item_name)
            primary_search_term = search_terms[0]  # Use first (aliased) term as primary
            
            # Normalize search term (remove diacritics)
            search_normalized = self._normalize_for_search(primary_search_term)
            logging.info("🔍 Searching for: '%s' (normalized: '%s', aliases expanded)", item_name, search_normalized)
            
            matches = []
            for item in all_items:
                item_name_db = item['name']
                item_name_normalized = self._normalize_for_search(item_name_db)
                
                # Quick check: if normalized versions match exactly, it's a perfect match
                if search_normalized == item_name_normalized:
                    matches.append((item, 1.0))
                    logging.info("  ✅ Exact match (after normalization): '%s' (normalized: '%s')", 
                                item_name_db, item_name_normalized)
                    continue
                
                # Create auto-alias: if item has diacritics, also try matching without diacritics
                item_name_without_diacritics = self._remove_diacritics(item_name_db)
                
                # Calculate similarity score using the primary (aliased) search term
                similarity = self._calculate_similarity(primary_search_term, item_name_db)
                
                # Also try matching with item name without diacritics (auto-alias)
                if item_name_without_diacritics != item_name_db:
                    # If item has diacritics, also check similarity with version without diacritics
                    similarity_no_diacritics = self._calculate_similarity(primary_search_term, item_name_without_diacritics)
                    similarity = max(similarity, similarity_no_diacritics)
                
                # Also check similarity with original term (in case alias didn't match but original does)
                if len(search_terms) > 1:
                    original_similarity = self._calculate_similarity(item_name, item_name_db)
                    # Also try original with item without diacritics
                    if item_name_without_diacritics != item_name_db:
                        original_similarity_no_diacritics = self._calculate_similarity(item_name, item_name_without_diacritics)
                        original_similarity = max(original_similarity, original_similarity_no_diacritics)
                    # Use the higher similarity score
                    similarity = max(similarity, original_similarity * 0.9)  # Slight penalty for non-aliased match
                
                # Only include matches with meaningful similarity
                if similarity > 0.3:  # Lowered threshold from 0.4 to 0.3 for better matching
                    matches.append((item, similarity))
                    logging.info("  ✅ Match found: '%s' (normalized: '%s', similarity: %.2f)", 
                                item_name_db, item_name_normalized, similarity)
            
            # Sort by similarity score (highest first)
            matches.sort(key=lambda x: x[1], reverse=True)
            
            # Prioritize items: if multiple matches, prefer:
            # 1. Items with is_special=True (if available)
            # 2. Items with lower price (more common/standard)
            # 3. Items with shorter names (simpler/more common)
            if len(matches) > 1:
                def priority_key(match):
                    item, score = match
                    priority = 0
                    # Prefer special items
                    if item.get('is_special'):
                        priority += 1000
                    # Prefer lower price (more common)
                    priority -= item.get('final_price', 0) / 1000
                    # Prefer shorter names
                    priority += len(item.get('name', '')) * 10
                    return priority
                
                # Sort by priority (highest first), then by similarity
                matches.sort(key=lambda x: (priority_key(x), x[1]), reverse=True)
            
            # Extract items from tuples
            result_items = [item for item, score in matches]
            
            logging.info("📊 Found %d matches for '%s'", len(result_items), item_name)
            return {"success": True, "items": result_items[:5]}  # Return top 5 matches
        except requests.exceptions.RequestException as e:
            logging.error(f"Error searching menu: {e}")
            return {"success": False, "message": str(e)}
    
    async def get_top_menu_items(self, limit: int = 10, include_drinks: bool = True) -> dict:
        """
        Get top menu items (special items + popular foods/drinks)
        
        Args:
            limit: Maximum number of items to return
            include_drinks: Whether to include drinks in the result
            
        Returns:
            dict: {"success": bool, "items": list}
        """
        try:
            # Get special items first
            special_params = {"special": "true", "is_available": "true"}
            special_response = requests.get(self.menu_url, params=special_params, timeout=10)
            special_response.raise_for_status()
            special_items = special_response.json()
            
            # Get regular items (foods)
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
            
            # Add special items (up to limit/2)
            special_limit = min(len(special_items), limit // 2)
            all_items.extend(special_items[:special_limit])
            
            # Add foods (fill remaining slots)
            remaining = limit - len(all_items)
            if remaining > 0:
                all_items.extend(food_items[:remaining])
            
            # Add drinks if we have space and include_drinks is True
            if include_drinks:
                remaining = limit - len(all_items)
                if remaining > 0:
                    all_items.extend(drink_items[:remaining])
            
            # Limit to requested number
            all_items = all_items[:limit]
            
            logging.info(f"📋 Retrieved {len(all_items)} top menu items ({len(special_items)} special, {len(food_items)} foods, {len(drink_items)} drinks)")
            return {"success": True, "items": all_items}
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting top menu items: {e}")
            return {"success": False, "message": str(e), "items": []}
        except Exception as e:
            logging.error(f"Unexpected error getting top menu items: {e}")
            return {"success": False, "message": str(e), "items": []}
    
    async def create_order(self, customer_name: str, phone_number: str, 
                           address: str, items: list, notes: str = None) -> dict:
        """Create a new restaurant order"""
        try:
            logging.info("Creating order in backend")
            
            # CRITICAL VALIDATION: Reject if items list is empty
            if not items or len(items) == 0:
                logging.error("❌ ORDER REJECTED: Empty items list")
                return {
                    "success": False,
                    "message": "خطا: لیست غذاها خالی است. نمی‌توان سفارش بدون غذا ثبت کرد."
                }
            
            # Validate required fields
            if not customer_name or not customer_name.strip():
                logging.error("❌ ORDER REJECTED: Missing customer_name")
                return {
                    "success": False,
                    "message": "خطا: نام مشتری مشخص نشده است."
                }
            
            if not address or not address.strip():
                logging.error("❌ ORDER REJECTED: Missing address")
                return {
                    "success": False,
                    "message": "خطا: آدرس تحویل مشخص نشده است."
                }
            
            # Normalize phone number (remove spaces, convert Persian digits)
            normalized_phone = normalize_phone_number(phone_number)
            logging.info(f"📱 Normalizing phone: '{phone_number}' -> '{normalized_phone}'")
            
            # Get public key
            response = requests.get(self.orders_url, timeout=10)
            response.raise_for_status()
            public_key = response.json()["public_key"]
            
            # Prepare order data - need to match menu items with IDs
            order_data = {
                "customer_name": customer_name.strip(),
                "phone_number": normalized_phone,  # Use normalized phone
                "address": address.strip(),
                "notes": notes,
                "items": []
            }
            
            # For each item, we need to find its menu_item ID and unit_price
            failed_items = []
            for item in items:
                item_name = item.get("item_name", "").strip()
                quantity = item.get("quantity", 1)
                
                if not item_name:
                    failed_items.append("نام غذا مشخص نشده")
                    continue
                
                # Validate quantity - must be a positive integer
                if not isinstance(quantity, int) or quantity <= 0:
                    failed_items.append(f"{item_name}: تعداد نامعتبر ({quantity}) - باید عدد مثبت باشد")
                    logging.error("❌ Invalid quantity for %s: %s (type: %s)", item_name, quantity, type(quantity))
                    continue
                
                logging.info("📦 Processing item: %s × %d", item_name, quantity)
                
                # Search for the item in menu
                search_result = await self.search_menu_item(item_name)
                if search_result.get("success") and search_result.get("items"):
                    menu_item = search_result["items"][0]  # Take first match
                    order_data["items"].append({
                        "menu_item": menu_item["id"],
                        "quantity": quantity,
                        "unit_price": menu_item["final_price"]
                    })
                    logging.info("✅ Matched item: %s -> ID %d", item_name, menu_item["id"])
                else:
                    failed_items.append(f"{item_name}: در منو یافت نشد")
                    logging.warning("⚠️  Item not found in menu: %s", item_name)
            
            # If any items failed to match, reject the order
            if failed_items:
                error_msg = f"خطا: برخی غذاها یافت نشدند یا نامعتبر هستند: {', '.join(failed_items)}"
                logging.error("❌ ORDER REJECTED: Failed items: %s", failed_items)
                return {
                    "success": False,
                    "message": error_msg,
                    "failed_items": failed_items
                }
            
            # Final check: ensure we have at least one valid item
            if len(order_data["items"]) == 0:
                logging.error("❌ ORDER REJECTED: No valid items after matching")
                return {
                    "success": False,
                    "message": "خطا: هیچ غذای معتبری در سفارش یافت نشد."
                }
            
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
