"""
Function handlers for OpenAI engine - handles all function calls from OpenAI.
"""

import logging
import time
import asyncio
import re
import os
import requests
from datetime import datetime
from phone_normalizer import normalize_phone_number
from api_sender import API
from sms_service import SMSService
from .utils import DateTimeUtils, WeatherService
from .meeting_utils import MeetingUtils

logger = logging.getLogger(__name__)

# Backend server URL - defaults to Docker service name for containerized deployments
BACKEND_SERVER_URL = os.getenv("BACKEND_SERVER_URL", "http://backend-restaurant:8000")


class FunctionHandlers:
    """Handles all function calls from OpenAI"""
    
    def __init__(self, openai_instance):
        """
        Initialize function handlers.
        
        Args:
            openai_instance: OpenAI instance (for access to call, api, db, etc.)
        """
        self.openai = openai_instance
    
    async def handle_function_call(self, name, call_id, args):
        """Route function calls to appropriate handlers"""
        if name == "terminate_call":
            await self._handle_terminate_call()
        elif name == "transfer_call":
            await self._handle_transfer_call()
        elif name == "get_wallet_balance":
            await self._handle_get_wallet_balance(call_id, args)
        elif name == "schedule_meeting":
            await self._handle_schedule_meeting(call_id, args)
        elif name == "get_origin_destination_userame" or name == "get_origin_destination_username":
            await self._handle_taxi_booking(call_id, args)
        elif name == "get_weather":
            await self._handle_get_weather(call_id, args)
        elif name == "track_order":
            await self._handle_track_order(call_id, args)
        elif name == "get_menu_specials":
            await self._handle_get_menu_specials(call_id)
        elif name == "search_menu_item":
            await self._handle_search_menu_item(call_id, args)
        elif name == "create_order":
            await self._handle_create_order(call_id, args)
        elif name == "answer_faq":
            await self._handle_answer_faq(call_id, args)
        elif name == "get_contact_info":
            await self._handle_get_contact_info(call_id, args)
        elif name == "get_resume_info":
            await self._handle_get_resume_info(call_id, args)
        elif name == "send_resume_pdf":
            await self._handle_send_resume_pdf(call_id, args)
        elif name == "send_website_info":
            await self._handle_send_website_info(call_id, args)
        else:
            logger.debug("FLOW tool: unhandled function name: %s", name)
    
    async def _handle_terminate_call(self):
        """Handle terminate_call function"""
        logger.info("FLOW tool: terminate_call requested")
        self.openai.terminate_call()
    
    async def _handle_transfer_call(self):
        """Handle transfer_call function"""
        if self.openai.transfer_to:
            logger.info("FLOW tool: Transferring call via REFER")
            self.openai.call.ua_session_update(method="REFER", headers={
                "Refer-To": f"<{self.openai.transfer_to}>",
                "Referred-By": f"<{self.openai.transfer_by}>"
            })
        else:
            logger.warning("FLOW tool: transfer_call requested but transfer_to not configured")
    
    async def _handle_get_wallet_balance(self, call_id, args):
        """Handle get_wallet_balance function"""
        def _lookup():
            return self.openai.db.get_wallet_balance(
                customer_id=args.get("customer_id"),
                phone=args.get("phone_number")
            )
        result = await self.openai.run_in_thread(_lookup)
        await self.openai._send_function_output(call_id, result)
    
    async def _handle_schedule_meeting(self, call_id, args):
        """Handle schedule_meeting function"""
        date_str, time_str = MeetingUtils.interpret_meeting_datetime(
            args, self.openai.timezone
        )
        def _schedule():
            return self.openai.db.schedule_meeting(
                date=date_str, time=time_str,
                customer_id=args.get("customer_id"),
                duration_minutes=args.get("duration_minutes") or 30,
                subject=args.get("subject")
            )
        result = await self.openai.run_in_thread(_schedule)
        await self.openai._send_function_output(call_id, result)
    
    async def _handle_taxi_booking(self, call_id, args):
        """Handle taxi booking function call"""
        unique_time = time.time()
        origin = args.get("origin")
        destination = args.get("destination")
        user_name = args.get("user_name")
        logger.info("FLOW tool: Taxi booking - user=%s origin=%s dest=%s", 
                   user_name, origin, destination)
        
        # Store in temp_data
        if user_name is not None:
            self.openai.temp_data[unique_time] = self.openai.temp_data.get(unique_time, {})
            self.openai.temp_data[unique_time]["user_name"] = user_name
        if origin is not None:
            self.openai.temp_data[unique_time] = self.openai.temp_data.get(unique_time, {})
            self.openai.temp_data[unique_time]["origin"] = origin
        if destination is not None:
            self.openai.temp_data[unique_time] = self.openai.temp_data.get(unique_time, {})
            self.openai.temp_data[unique_time]["destination"] = destination
        
        # Send to backend API
        api_result = False
        try:
            # Use ConfigLoader to resolve backend URL properly
            from .config_loader import ConfigLoader
            backend_url = ConfigLoader.resolve_backend_url(self.openai.did_config)
            reservation_url = f"{backend_url.rstrip('/')}/add-reservation/"
            
            def _send_taxi_reservation():
                try:
                    response = requests.get(reservation_url, timeout=10)
                    response.raise_for_status()
                    public_key = response.json()["public_key"]
                    
                    data = {
                        "user_fullname": user_name,
                        "origin": origin,
                        "destination": destination
                    }
                    
                    encrypted_data = API.encoder(public_key, data)
                    response = requests.post(reservation_url, json=encrypted_data, timeout=10)
                    response.raise_for_status()
                    return True
                except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                    logger.error("Network error sending taxi reservation: %s", e)
                    return False
                except (KeyError, ValueError, json.JSONDecodeError) as e:
                    logger.error("Invalid response from taxi reservation API: %s", e)
                    return False
                except Exception as e:
                    logger.error("Unexpected error sending taxi reservation: %s", e, exc_info=True)
                    return False
            
            api_result = await self.openai.run_in_thread(_send_taxi_reservation)
            logger.info(f"Taxi reservation API result: {api_result}")
        except Exception as e:
            logger.error("Unexpected error in taxi booking handler: %s", e, exc_info=True)
            api_result = False
        
        # Check if all required info is available
        temp_entry = self.openai.temp_data.get(unique_time, {})
        if (temp_entry.get("user_name") and temp_entry.get("origin") and temp_entry.get("destination")):
            output = {
                "origin": origin, 
                "destination": destination, 
                "user_name": user_name
            }
            await self.openai._send_function_output(call_id, output)
        else:
            missing = []
            if not temp_entry.get("user_name"):
                missing.append("نام")
            if not temp_entry.get("origin"):
                missing.append("مبدا")
            if not temp_entry.get("destination"):
                missing.append("مقصد")
            output = {
                "error": f"لطفاً {' و '.join(missing)} را مجدداً بفرمایید."
            }
            await self.openai._send_function_output(call_id, output)
    
    async def _handle_get_weather(self, call_id, args):
        """Handle get_weather function call for taxi service"""
        # Only allow weather for taxi service
        if not (self.openai.did_config and self.openai.did_config.get('service_id') == 'taxi_vip'):
            logger.warning("FLOW tool: get_weather called but not a taxi service")
            output = {"error": "این قابلیت فقط برای سرویس تاکسی در دسترس است."}
            await self.openai._send_function_output(call_id, output)
            return
        
        city = args.get("city")
        handler_start_time = time.time()
        self.openai._last_weather_call_time = handler_start_time
        self.openai._weather_audio_started = False
        
        logger.info(f"🌤️  Weather Handler: Starting weather request for city: {city} at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        
        def _get_weather():
            return WeatherService.fetch_weather(city, self.openai.did_config)
        
        result = await self.openai.run_in_thread(_get_weather)
        
        output_send_time = time.time()
        logger.info(f"📤 Weather Handler: Sending function output to OpenAI at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Handler processing time: {(output_send_time - handler_start_time) * 1000:.2f}ms")
        
        await self.openai._send_function_output(call_id, result)
        
        response_create_time = time.time()
        logger.info(f"🎤 Weather Handler: Requesting OpenAI to generate response (response.create) at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} | Time since handler start: {(response_create_time - handler_start_time) * 1000:.2f}ms")
        
        logger.info(f"⏱️  Weather Handler: Total handler time: {(time.time() - handler_start_time) * 1000:.2f}ms")
    
    async def _handle_track_order(self, call_id, args):
        """Handle track_order function call"""
        phone_number = args.get("phone_number") or self.openai.call.from_number
        if not phone_number:
            output = {"success": False, "message": "شماره تلفن در دسترس نیست."}
            await self.openai._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        try:
            result = await self.openai.api.track_order(normalized_phone)
            if result and result.get("success"):
                orders = result.get("orders", [])
                if orders:
                    latest = orders[0]
                    output = {
                        "success": True,
                        "message": f"سفارش شما {latest['status_display']} است.",
                        "order": latest
                    }
                else:
                    output = {
                        "success": True,
                        "message": "شما سفارشی ثبت نکرده‌اید خوشحال می‌شوم اطلاعات سفارش جدید را بدونم",
                        "orders": []
                    }
            else:
                output = {"success": False, "message": "خطا در پیگیری سفارش"}
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.error("Network error tracking order: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        except (ValueError, KeyError) as e:
            logger.error("Invalid data in order tracking response: %s", e)
            output = {"success": False, "message": "خطا در پردازش اطلاعات"}
        except Exception as e:
            logger.error("Unexpected error tracking order: %s", e, exc_info=True)
            output = {"success": False, "message": "خطا در پیگیری سفارش"}
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_get_menu_specials(self, call_id):
        """Handle get_menu_specials function call"""
        try:
            result = await self.openai.api.get_menu_specials()
            if result and result.get("success"):
                output = {"success": True, "specials": result.get("items", [])}
            else:
                output = {"success": False, "message": "خطا در دریافت پیشنهادات"}
        except Exception as e:
            logger.error("Exception getting specials: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_search_menu_item(self, call_id, args):
        """Handle search_menu_item function call"""
        item_name = args.get("item_name")
        category = args.get("category")
        try:
            result = await self.openai.api.search_menu_item(item_name, category)
            if result and result.get("success"):
                output = {"success": True, "items": result.get("items", [])}
            else:
                output = {"success": False, "message": "غذایی با این نام یافت نشد"}
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.error("Network error searching menu: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        except (ValueError, KeyError) as e:
            logger.error("Invalid data in menu search response: %s", e)
            output = {"success": False, "message": "خطا در پردازش اطلاعات"}
        except Exception as e:
            logger.error("Unexpected error searching menu: %s", e, exc_info=True)
            output = {"success": False, "message": "خطا در جستجو"}
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_create_order(self, call_id, args):
        """Handle create_order function call"""
        current_time = time.time()
        if self.openai.last_order_time and (current_time - self.openai.last_order_time) < 10:
            output = {
                "success": False, 
                "message": "سفارش قبلی شما در حال پردازش است. لطفا چند لحظه صبر کنید."
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        customer_name = args.get("customer_name")
        phone_number = self.openai.call.from_number or args.get("phone_number")
        if not phone_number:
            output = {"success": False, "message": "شماره تلفن در دسترس نیست. لطفا دوباره تماس بگیرید."}
            await self.openai._send_function_output(call_id, output)
            return
        
        address = args.get("address")
        items = args.get("items", [])
        notes = args.get("notes")
        
        validation_errors = []
        if not customer_name or not customer_name.strip():
            validation_errors.append("نام مشتری")
        if not address or not address.strip():
            validation_errors.append("آدرس")
        if not items:
            validation_errors.append("لیست غذاها (هیچ غذایی ثبت نشده)")
        else:
            for idx, item in enumerate(items):
                item_name = item.get('item_name', '').strip()
                quantity = item.get('quantity', 0)
                if not item_name:
                    validation_errors.append(f"نام غذا در آیتم {idx + 1}")
                if not quantity or quantity <= 0:
                    validation_errors.append(f"تعداد در آیتم {idx + 1} (باید عدد مثبت باشد، مقدار فعلی: {quantity})")
        
        if validation_errors:
            error_message = f"خطا: اطلاعات ناقص است. لطفا موارد زیر را تکمیل کنید: {', '.join(validation_errors)}"
            output = {
                "success": False,
                "message": error_message,
                "missing_fields": validation_errors
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        logger.info("Creating order: Customer=%s, Items=%d", customer_name, len(items))
        
        try:
            result = await self.openai.api.create_order(
                customer_name=customer_name,
                phone_number=normalized_phone,
                address=address,
                items=items,
                notes=notes
            )
            
            if result and result.get("success"):
                order = result.get("order", {})
                order_id = order.get('id')
                self.openai.last_order_time = time.time()
                self.openai.recent_order_ids.add(order_id)
                self.openai._order_confirmed = True
                
                # Send SMS receipt to customer
                asyncio.create_task(self._send_order_receipt_sms(order, normalized_phone))
                
                output = {
                    "success": True,
                    "message": f"سفارش شما با موفقیت ثبت شد. جمع کل: {order.get('total_price'):,} تومان",
                    "order_id": order.get("id"),
                    "total_price": order.get("total_price")
                }
                logger.info("Order created: ID=%s, Total=%s", order_id, order.get('total_price'))
            else:
                output = {"success": False, "message": result.get("message", "خطا در ثبت سفارش")}
                logger.error("Order failed: %s", result.get("message"))
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.error("Network error creating order: %s", e)
            output = {"success": False, "message": "خطا در اتصال به سرور"}
        except (ValueError, KeyError) as e:
            logger.error("Invalid data in order creation response: %s", e)
            output = {"success": False, "message": "خطا در پردازش اطلاعات"}
        except Exception as e:
            logger.error("Unexpected error creating order: %s", e, exc_info=True)
            output = {"success": False, "message": "خطا در ثبت سفارش"}
        
        await self.openai._send_function_output(call_id, output)
    
    async def _send_order_receipt_sms(self, order: dict, phone_number: str):
        """Send order receipt via SMS"""
        try:
            order_id = order.get('id')
            total_price = order.get('total_price', 0)
            status = order.get('status', 'pending')
            status_display = dict([
                ('pending', 'در انتظار تایید رستوران'),
                ('confirmed', 'تایید توسط رستوران'),
                ('preparing', 'در حال آماده سازی'),
                ('on_delivery', 'تحویل داده شده به پیک'),
                ('delivered', 'تحویل داده شده به مشتری'),
                ('cancelled', 'لغو شده')
            ]).get(status, status)
            
            items = order.get('items', [])
            items_text = []
            for item in items:
                item_name = item.get('menu_item_name') or (item.get('menu_item', {}).get('name') if isinstance(item.get('menu_item'), dict) else '') or ''
                quantity = item.get('quantity', 1)
                unit_price = item.get('unit_price', 0)
                if item_name:
                    items_text.append(f"{quantity}× {item_name} ({unit_price:,} تومان)")
            
            receipt = f"📋 فاکتور سفارش #{order_id}\n\n"
            if items_text:
                receipt += "موارد سفارش:\n" + "\n".join(items_text) + "\n\n"
            receipt += f"💰 جمع کل: {total_price:,} تومان\n"
            receipt += f"📊 وضعیت: {status_display}"
            
            self.openai.sms_service.send_sms(phone_number, receipt)
            logger.info(f"📱 Order receipt SMS sent to {phone_number} for order #{order_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send order receipt SMS: {e}", exc_info=True)
    
    def _normalize_faq_text(self, text: str):
        """Normalize text for FAQ matching"""
        if not isinstance(text, str):
            return []
        t = DateTimeUtils.to_ascii_digits(text)
        t = t.replace("؟", " ").replace("?", " ")
        t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", t)
        tokens = [tok for tok in t.split() if tok]
        return tokens
    
    def _match_faq_locally(self, user_question, faq_entries, not_found_answer):
        """Fallback Jaccard-based matcher"""
        best_answer = not_found_answer
        best_question = None
        best_score = 0.0
        
        if not user_question or not faq_entries:
            return best_answer, best_question, best_score
        
        q_tokens = set(self._normalize_faq_text(user_question))
        if not q_tokens:
            return best_answer, best_question, best_score
        
        for entry in faq_entries:
            fq = entry.get("question") or ""
            fa = entry.get("answer") or ""
            if not fq or not fa:
                continue
            f_tokens = set(self._normalize_faq_text(fq))
            if not f_tokens:
                continue
            inter = len(q_tokens & f_tokens)
            union = len(q_tokens | f_tokens) or 1
            jaccard = inter / union
            
            bonus = 0.0
            if fq in user_question or user_question in fq:
                bonus = 0.15
            score = jaccard + bonus
            
            if score > best_score:
                best_score = score
                best_answer = fa
                best_question = fq
        
        return best_answer, best_question, best_score
    
    def _match_faq_with_openai(self, user_question, faq_entries, not_found_answer):
        """Use OpenAI Chat Completion API to semantically match FAQ"""
        try:
            if not user_question or not faq_entries:
                return not_found_answer, None, 0.0
            
            questions_text = []
            for idx, entry in enumerate(faq_entries):
                q = (entry.get("question") or "").strip()
                if not q:
                    continue
                questions_text.append(f"{idx}: {q}")
            
            if not questions_text:
                return not_found_answer, None, 0.0
            
            prompt_user = (
                "سوال کاربر:\n"
                f"{user_question}\n\n"
                "لیست سوالات متداول (FAQ):\n"
                + "\n".join(questions_text)
                + "\n\n"
                "کار تو این است که فقط تشخیص بدهی کدام سوال در این لیست از نظر معنا "
                "بیشترین شباهت را با سوال کاربر دارد.\n"
                "اگر هیچکدام از سوال‌ها مناسب نبود، عدد -1 را برگردان.\n\n"
                "خروجی نهایی تو باید فقط و فقط یک عدد صحیح باشد:\n"
                "- اگر یک سوال مناسب پیدا کردی: شماره آن سوال (ایندکس) به صورت عدد صحیح ۰-بنیان.\n"
                "- اگر سوال مناسبی پیدا نکردی: عدد -1.\n"
                "هیچ متن دیگری غیر از همین عدد ننویس."
            )
            
            api_key = self.openai.key or os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.error("FAQ matcher: OPENAI_API_KEY not set, falling back to local matcher")
                return self._match_faq_locally(user_question, faq_entries, not_found_answer)
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a semantic FAQ matcher. You only answer with a single integer index."
                    },
                    {"role": "user", "content": prompt_user},
                ],
                "temperature": 0.0,
            }
            
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            logger.info("FAQ matcher (OpenAI) raw content: %s", content)
            
            m = re.search(r"-?\d+", content)
            if not m:
                logger.warning("FAQ matcher: no integer index in response, falling back to local")
                return self._match_faq_locally(user_question, faq_entries, not_found_answer)
            
            idx = int(m.group(0))
            if idx < 0 or idx >= len(faq_entries):
                logger.info("FAQ matcher: index %s out of range, treating as no match", idx)
                return not_found_answer, None, 0.0
            
            matched_entry = faq_entries[idx]
            answer = matched_entry.get("answer") or not_found_answer
            question = matched_entry.get("question") or None
            
            return answer, question, 0.9
            
        except Exception as e:
            logger.error("FAQ matcher (OpenAI) error: %s", e, exc_info=True)
            return self._match_faq_locally(user_question, faq_entries, not_found_answer)
    
    async def _handle_answer_faq(self, call_id, args):
        """Handle answer_faq function call"""
        user_question = (args.get("user_question") or "").strip()
        logger.info("FLOW tool: answer_faq - user_question=%s", user_question)
        
        faq_entries = []
        if self.openai.did_config:
            custom_context = self.openai.did_config.get("custom_context", {})
            faq_entries = custom_context.get("faq_entries", []) or []
        
        not_found_answer = (
            "برای این سوال، پاسخ دقیقی در فهرست سؤالات متداول من ثبت نشده است. "
            "لطفاً سوال را کمی تغییر دهید یا سوال دیگری بپرسید."
        )
        
        best_answer, best_question, best_score = self._match_faq_with_openai(
            user_question, faq_entries, not_found_answer
        )
        
        output = {
            "answer": best_answer,
            "matched_question": best_question,
            "similarity_score": best_score,
        }
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_get_contact_info(self, call_id, args):
        """Handle get_contact_info function call - loads contact info from DID config"""
        contact_type = args.get("contact_type", "direct")
        topic = args.get("topic")
        
        logger.info(f"FLOW tool: Get contact info - type={contact_type}, topic={topic}")
        
        # Load contact info from DID config
        custom_context = self.openai.did_config.get('custom_context', {}) if self.openai.did_config else {}
        mahdi_info = custom_context.get('mahdi_info', {})
        contact_messages = custom_context.get('contact_messages', {})
        
        # Get email from config, with fallback
        email = mahdi_info.get('email', '')
        if not email:
            logger.warning("Contact email not found in DID config, cannot provide contact info")
            output = {
                "error": "اطلاعات تماس در پیکربندی یافت نشد."
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        # Get message template from config
        if contact_type == "direct":
            message_template = contact_messages.get('email_only', 
                f"حتماً. بهترین مسیر ارتباط مستقیم ایمیلشه: 📧 {email} اگه یکی‌دو خط بگی موضوعت چیه، کمک می‌کنه مکالمه‌تون سریع‌تر و دقیق‌تر پیش بره.")
        else:  # professional
            message_template = contact_messages.get('professional',
                f"این موضوع دقیقاً در حوزه کاریشه. می‌تونی مستقیم براش بنویسی: 📧 {email} ایمیلات رو با دقت بررسی می‌کنه.")
        
        # Format message with email
        message = message_template.replace('{email}', email) if '{email}' in message_template else message_template.replace('Mahdi.meshkani@gmail.com', email)
        
        output = {
            "email": email,
            "message": message
        }
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_get_resume_info(self, call_id, args):
        """Handle get_resume_info function call"""
        section = args.get("section", "full")
        
        logger.info(f"FLOW tool: Get resume info - section={section}")
        
        custom_context = self.openai.did_config.get('custom_context', {}) if self.openai.did_config else {}
        resume_data = custom_context.get('resume_summary', {})
        mahdi_info = custom_context.get('mahdi_info', {})
        resume_messages = custom_context.get('resume_messages', {})
        
        # Validate config exists
        if not mahdi_info and not resume_data:
            logger.warning("Resume info not found in DID config")
            output = {
                "error": "اطلاعات رزومه در پیکربندی یافت نشد."
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        output = {}
        
        if section == "full" or not section:
            output = {
                "name": mahdi_info.get("name", ""),
                "title": mahdi_info.get("title", ""),
                "experience": resume_data.get("experience", ""),
                "achievements": resume_data.get("achievements", []),
                "education": resume_data.get("education", []),
                "memberships": resume_data.get("memberships", []),
                "skills": resume_data.get("skills", []),
                "message": resume_messages.get("full_intro", "بذار یه تصویر واقعی از مسیر حرفه‌ای بهت بدم...")
            }
        elif section == "experience":
            output = {
                "section": "experience",
                "content": resume_data.get("experience", ""),
                "message": resume_messages.get("experience", "تجربه حرفه‌ای شامل...")
            }
        elif section == "education":
            output = {
                "section": "education",
                "content": resume_data.get("education", []),
                "message": resume_messages.get("education", "تحصیلات شامل...")
            }
        elif section == "achievements":
            output = {
                "section": "achievements",
                "content": resume_data.get("achievements", []),
                "message": resume_messages.get("achievements", "برخی از دستاوردها شامل...")
            }
        elif section == "skills":
            output = {
                "section": "skills",
                "content": resume_data.get("skills", []),
                "message": resume_messages.get("skills", "مهارت‌ها شامل...")
            }
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_send_resume_pdf(self, call_id, args):
        """Handle send_resume_pdf function call"""
        phone_number = self.openai.call.from_number
        
        logger.info(f"FLOW tool: Send resume PDF - automatically sending to caller phone: {phone_number}")
        
        # Load contact info from config
        custom_context = self.openai.did_config.get('custom_context', {}) if self.openai.did_config else {}
        mahdi_info = custom_context.get('mahdi_info', {})
        email = mahdi_info.get('email', '')
        website = mahdi_info.get('website', '')
        
        if not phone_number:
            error_msg = f"شماره تماس در دسترس نیست."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        
        if not normalized_phone:
            error_msg = f"شماره تماس معتبر نیست."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        # Build SMS message from config
        name = mahdi_info.get('name', '')
        sms_template = custom_context.get('resume_messages', {}).get('sms_template', 
            f"رزومه کامل {name} در وبسایت {website} موجود است یا می‌تونید از طریق ایمیل {email} درخواست بدید.")
        sms_message = sms_template.replace('{name}', name).replace('{website}', website).replace('{email}', email)
        
        def _send_sms():
            return self.openai.sms_service.send_sms(normalized_phone, sms_message)
        
        try:
            sms_result = await self.openai.run_in_thread(_send_sms)
            if sms_result:
                output = {
                    "success": True,
                    "method": "sms",
                    "phone": normalized_phone,
                    "message": f"لینک دانلود رزومه به شماره شما ارسال شد."
                }
                logger.info(f"📱 Resume PDF link sent via SMS to {normalized_phone}")
            else:
                error_msg = "متأسفانه ارسال پیامک با مشکل مواجه شد."
                if email:
                    error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
                output = {
                    "success": False,
                    "error": error_msg
                }
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.error(f"Network error sending resume PDF SMS: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid data in resume PDF SMS: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending resume PDF SMS: {e}", exc_info=True)
            error_msg = "متأسفانه ارسال پیامک با مشکل مواجه شد."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
        
        await self.openai._send_function_output(call_id, output)
    
    async def _handle_send_website_info(self, call_id, args):
        """Handle send_website_info function call"""
        phone_number = self.openai.call.from_number
        
        logger.info(f"FLOW tool: Send website info - automatically sending to caller phone: {phone_number}")
        
        # Load contact info from config
        custom_context = self.openai.did_config.get('custom_context', {}) if self.openai.did_config else {}
        mahdi_info = custom_context.get('mahdi_info', {})
        email = mahdi_info.get('email', '')
        website = mahdi_info.get('website', '')
        name = mahdi_info.get('name', '')
        
        if not phone_number:
            error_msg = "شماره تماس در دسترس نیست."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        normalized_phone = normalize_phone_number(phone_number)
        
        if not normalized_phone:
            error_msg = "شماره تماس معتبر نیست."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
            await self.openai._send_function_output(call_id, output)
            return
        
        # Build SMS message from config
        website_guidance = custom_context.get('website_guidance', {})
        sms_template = website_guidance.get('sms_template',
            f"🌐 اطلاعات و نمونه‌کارهای {name}:\n{website}\n\nبرای تماس مستقیم:\n📧 {email}")
        sms_message = sms_template.replace('{name}', name).replace('{website}', website).replace('{email}', email)
        
        def _send_sms():
            return self.openai.sms_service.send_sms(normalized_phone, sms_message)
        
        try:
            sms_result = await self.openai.run_in_thread(_send_sms)
            if sms_result:
                output = {
                    "success": True,
                    "method": "sms",
                    "phone": normalized_phone,
                    "website": website,
                    "message": f"لینک سایت به شماره شما ارسال شد."
                }
                logger.info(f"📱 Website info sent via SMS to {normalized_phone}")
            else:
                error_msg = "متأسفانه ارسال پیامک با مشکل مواجه شد."
                if email:
                    error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
                output = {
                    "success": False,
                    "error": error_msg
                }
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.error(f"Network error sending website info SMS: {e}")
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid data in website info SMS: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending website info SMS: {e}", exc_info=True)
            error_msg = "متأسفانه ارسال پیامک با مشکل مواجه شد."
            if email:
                error_msg += f" لطفا از طریق ایمیل {email} درخواست بدید."
            output = {
                "success": False,
                "error": error_msg
            }
        
        await self.openai._send_function_output(call_id, output)
