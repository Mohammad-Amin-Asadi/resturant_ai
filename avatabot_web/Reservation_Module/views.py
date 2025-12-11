import base64
import json
import logging

from Crypto.Cipher import PKCS1_OAEP, AES
from django.views.generic import ListView
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view
from Crypto.PublicKey import RSA

# Try to import from new locations, fallback to old for backward compatibility
try:
    from restaurant.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings
    from taxi.models import ReservationModel, TelephoneTaxiModel, TaxiStatusLog
    from shared.models import InscriptionModel
    from shared.config_manager import ConfigManager
except ImportError:
    from Reservation_Module.models import (
        Customer, MenuItem, Order, OrderItem, RestaurantSettings, InscriptionModel,
        ReservationModel, TelephoneTaxiModel, TaxiStatusLog
    )
    try:
        from shared.config_manager import ConfigManager
    except ImportError:
        # Fallback for backward compatibility during migration
        try:
            from Reservation_Module.config_manager import ConfigManager
        except ImportError:
            logging.error("ConfigManager not found in shared or Reservation_Module")
            raise
# Try to import from new locations, fallback to old for backward compatibility
try:
    from restaurant.serializers import MenuItemSerializer, OrderSerializer, OrderItemSerializer, RestaurantSettingsSerializer
    from taxi.serializers import ReservationSerializer
except ImportError:
    from Reservation_Module.serializers import (
        MenuItemSerializer, OrderSerializer, OrderItemSerializer, RestaurantSettingsSerializer,
        ReservationSerializer
    )

from Reservation_Module.forms import TaxiSettingsForm

# Import services
try:
    from restaurant.services.order_service import OrderService
    from restaurant.services.customer_service import CustomerService
    from restaurant.services.menu_service import MenuService
    from restaurant.services.encryption_service import EncryptionService
    from restaurant.services.key_service import KeyService
    from taxi.services.reservation_service import ReservationService
except ImportError:
    # Fallback: services will be imported inline where needed
    OrderService = None
    CustomerService = None
    MenuService = None
    EncryptionService = None
    KeyService = None
    ReservationService = None

try:
    from Reservation_Module.sms_service import send_sms
except ImportError:
    send_sms = None


@method_decorator(ensure_csrf_cookie, name='dispatch')
class OrderListView(ListView):
    """Display all orders for the restaurant"""
    template_name = 'Reservation_Module/orders_list_template.html'
    model = Order
    context_object_name = 'orders'
    
    def get_queryset(self):
        return Order.objects.select_related('customer').prefetch_related('items__menu_item').all()


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CustomerListView(ListView):
    """Display all customers with their order IDs"""
    template_name = 'Reservation_Module/customers_list_template.html'
    model = Customer
    context_object_name = 'customers'
    
    def get_queryset(self):
        try:
            return Customer.objects.prefetch_related('orders').all().order_by('-updated_at', 'name')
        except Exception as e:
            # Handle case where Customer table doesn't exist yet (migration not run)
            logging.error(f"Error loading customers: {e}")
            return Customer.objects.none()


class MenuAPIView(APIView):
    """Get menu items - can filter by category or get specials"""
    
    def get(self, request: Request):
        category = request.query_params.get('category', None)
        special = request.query_params.get('special', None)
        special_only = special and special.lower() in ['true', '1', 'yes']
        
        try:
            if MenuService is not None:
                items = MenuService.get_available_items(category=category, special_only=special_only)
            else:
                queryset = MenuItem.objects.filter(is_available=True)
                if category:
                    queryset = queryset.filter(category=category)
                if special_only:
                    queryset = queryset.filter(is_special=True)
                items = queryset
            
            serializer = MenuItemSerializer(items, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logging.error(f"Error in MenuAPIView: {e}", exc_info=True)
            return Response(
                {'error': f'خطا در دریافت منو: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class OrderTrackingView(APIView):
    """Track order by phone number"""
    
    def get(self, request: Request):
        phone_number = request.query_params.get('phone_number')
        
        if not phone_number:
            return Response(
                {'error': 'شماره تلفن الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if OrderService:
            orders = OrderService.get_orders_by_phone(phone_number)
        else:
            orders = Order.objects.filter(phone_number=phone_number).prefetch_related('items__menu_item')
        
        if not orders:
            return Response(
                {'error': 'سفارشی با این شماره تلفن یافت نشد'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CustomerInfoView(APIView):
    """Get customer information by phone number"""
    
    def get(self, request: Request):
        phone_number = request.query_params.get('phone_number')
        
        if not phone_number:
            return Response(
                {'error': 'شماره تلفن الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if CustomerService:
            customer = CustomerService.get_customer_by_phone(phone_number)
        else:
            try:
                customer = Customer.objects.get(phone_number=phone_number)
            except Customer.DoesNotExist:
                customer = None
            except (ValueError, TypeError) as e:
                logging.error(f"Invalid input for customer info: {e}", exc_info=True)
                return Response({
                    'success': False,
                    'message': 'شماره تلفن نامعتبر است'
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logging.error(f"Unexpected error getting customer info: {e}", exc_info=True)
                return Response({
                    'success': False,
                    'message': 'خطا در دریافت اطلاعات مشتری'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        if not customer:
            return Response({
                'success': False,
                'message': 'مشتری یافت نشد'
            }, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'success': True,
            'customer': {
                'name': customer.name,
                'phone_number': customer.phone_number,
                'address': customer.address,
            }
        }, status=status.HTTP_200_OK)


@api_view(['PATCH'])
def update_order_status(request, order_id):
    """Update order status"""
    new_status = request.data.get('status')
    
    if not new_status:
        return Response(
            {'error': 'وضعیت الزامی است'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    tenant_id = request.query_params.get('tenant_id')
    
    if OrderService:
        result = OrderService.update_order_status(order_id, new_status, tenant_id)
        if 'error' in result:
            status_code = status.HTTP_404_NOT_FOUND if 'یافت نشد' in result['error'] else status.HTTP_400_BAD_REQUEST
            return Response({'error': result['error']}, status=status_code)
        
        order = result['order']
        serializer = OrderSerializer(order)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        # Fallback to old implementation
        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            return Response(
                {'error': 'سفارش یافت نشد'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        old_status = order.status
        valid_statuses = dict(Order.STATUS_CHOICES).keys()
        if new_status not in valid_statuses:
            return Response(
                {'error': 'وضعیت نامعتبر است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order.status = new_status
        order.save()
        
        if old_status != new_status and order.phone_number and send_sms:
            try:
                status_display = dict(Order.STATUS_CHOICES).get(new_status, new_status)
                old_status_display = dict(Order.STATUS_CHOICES).get(old_status, old_status)
                # Prefetch items and menu_item to avoid N+1
                order = Order.objects.prefetch_related('items__menu_item').get(id=order.id)
                items_text = []
                for item in order.items.all():
                    items_text.append(f"{item.quantity}× {item.menu_item.name}")
                
                message = f"📋 به‌روزرسانی سفارش #{order.id}\n\n"
                if items_text:
                    message += "موارد سفارش:\n" + "\n".join(items_text[:5])
                    if len(items_text) > 5:
                        message += f"\nو {len(items_text) - 5} مورد دیگر..."
                    message += "\n\n"
                message += f"وضعیت سفارش شما از «{old_status_display}» به «{status_display}» تغییر کرد."
                
                import threading
                threading.Thread(target=send_sms, args=(order.phone_number, message), daemon=True).start()
                logging.info(f"📱 Status change SMS queued for order #{order.id}")
            except Exception as e:
                logging.error(f"❌ Failed to send status change SMS: {e}", exc_info=True)
        
        serializer = OrderSerializer(order)
        return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_order(request, order_id):
    """Delete an order from database"""
    if OrderService:
        result = OrderService.delete_order(order_id)
        if 'error' in result:
            status_code = status.HTTP_404_NOT_FOUND if 'یافت نشد' in result['error'] else status.HTTP_500_INTERNAL_SERVER_ERROR
            return Response({'error': result['error']}, status=status_code)
        return Response(result, status=status.HTTP_200_OK)
    else:
        # Fallback to old implementation
        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            return Response(
                {'error': 'سفارش یافت نشد'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        order_id_deleted = order.id
        order.delete()
        
        return Response(
            {'message': f'سفارش #{order_id_deleted} با موفقیت حذف شد', 'order_id': order_id_deleted},
            status=status.HTTP_200_OK
        )


@api_view(['DELETE'])
def delete_customer(request, customer_id):
    """Delete a customer from database"""
    if CustomerService:
        result = CustomerService.delete_customer(customer_id)
        if 'error' in result:
            status_code = status.HTTP_404_NOT_FOUND if 'یافت نشد' in result['error'] else status.HTTP_500_INTERNAL_SERVER_ERROR
            return Response({'error': result['error']}, status=status_code)
        return Response({'message': result['message']}, status=status.HTTP_200_OK)
    else:
        # Fallback to old implementation
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            return Response(
                {'error': 'مشتری یافت نشد'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        customer_name = customer.name
        customer_phone = customer.phone_number
        customer.delete()
        
        return Response(
            {'message': f'مشتری {customer_name} ({customer_phone}) با موفقیت حذف شد'},
            status=status.HTTP_200_OK
        )


class AddOrderView(APIView):
    """Add new order with encryption support"""
    
    def post(self, request: Request):
        public_key = request.data.get('public_key')
        data = request.data.get('data')
        
        if not public_key or not data:
            return Response(
                'Public key and data are required',
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if KeyService and EncryptionService:
            private_key = KeyService.get_private_key_by_public(public_key)
            if not private_key:
                return Response(
                    'Inscription does not exist',
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                decrypted_data = EncryptionService.decrypt_data(private_key, data)
            except ValueError as e:
                return Response(
                    f'Decryption failed: {e}',
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Fallback to old implementation
            inscription = InscriptionModel.objects.filter(public_key=public_key).first()
            if not inscription:
                return Response(
                    'Inscription does not exist',
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                decrypted_data = self._decrypt_fallback(inscription.private_key, data)
            except Exception as e:
                return Response(
                    f'Decryption failed: {e}',
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        customer = None
        phone_number = decrypted_data.get('phone_number')
        
        if phone_number and CustomerService:
            try:
                customer, _ = CustomerService.get_or_create_customer(
                    phone_number=phone_number,
                    name=decrypted_data.get('customer_name'),
                    address=decrypted_data.get('address')
                )
            except Exception as e:
                logging.warning(f"Could not create/update customer record: {e}")
        
        if OrderService:
            order, result = OrderService.create_order_from_decrypted_data(decrypted_data, customer)
            if 'error' in result:
                return Response(
                    {'error': result['error']},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return Response(
                {"message": "سفارش با موفقیت ثبت شد", "order": result['order']},
                status=status.HTTP_201_CREATED
            )
        else:
            # Fallback to old implementation
            return self._create_order_fallback(decrypted_data, customer)

    def get(self, request: Request):
        """Get public key for encryption"""
        if KeyService:
            public_key, is_new = KeyService.get_or_create_public_key()
            return Response({'public_key': public_key}, status=status.HTTP_200_OK)
        else:
            # Fallback to old implementation
            inscription = InscriptionModel.objects.filter(use_count__lt=15).first()
            if inscription:
                public_key = inscription.public_key
                inscription.use_count += 1
                inscription.save()
                return Response({'public_key': public_key}, status=status.HTTP_200_OK)
            else:
                private_key, public_key = self._generate_keys_fallback()
                new_inscription = InscriptionModel(
                    private_key=private_key,
                    public_key=public_key,
                    use_count=1
                )
                new_inscription.save()
                return Response({'public_key': public_key}, status=status.HTTP_200_OK)
    
    @staticmethod
    def _decrypt_fallback(private_key, data: str):
        """Fallback decryption method"""
        package_json = base64.b64decode(data)
        package = json.loads(package_json)
        enc_key = base64.b64decode(package['key'])
        nonce = base64.b64decode(package['nonce'])
        tag = base64.b64decode(package['tag'])
        ciphertext = base64.b64decode(package['ciphertext'])
        private_key_obj = RSA.import_key(private_key)
        cipher_rsa = PKCS1_OAEP.new(private_key_obj)
        sym_key = cipher_rsa.decrypt(enc_key)
        cipher_aes = AES.new(sym_key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher_aes.decrypt_and_verify(ciphertext, tag)
        return json.loads(plaintext.decode("utf-8"))
    
    @staticmethod
    def _generate_keys_fallback():
        """Fallback key generation"""
        key = RSA.generate(2048)
        private_key = key.export_key().decode("utf-8")
        public_key = key.publickey().export_key().decode("utf-8")
        return private_key, public_key
    
    def _create_order_fallback(self, decrypted_data, customer):
        """Fallback order creation"""
        items = decrypted_data.get('items', [])
        if not items:
            return Response(
                {'error': 'سفارش باید حداقل یک آیتم داشته باشد. لیست غذاها خالی است.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        for idx, item in enumerate(items):
            menu_item_id = item.get('menu_item')
            quantity = item.get('quantity', 0)
            if not menu_item_id:
                return Response(
                    {'error': f'آیتم {idx + 1} نامعتبر است: شناسه غذا مشخص نشده'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if quantity <= 0:
                return Response(
                    {'error': f'آیتم {idx + 1} نامعتبر است: تعداد باید بیشتر از صفر باشد'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        customer_name = decrypted_data.get('customer_name', '').strip()
        address = decrypted_data.get('address', '').strip()
        
        if not customer_name:
            return Response(
                {'error': 'نام مشتری الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not address:
            return Response(
                {'error': 'آدرس تحویل الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order_serializer = OrderSerializer(data=decrypted_data)
        if order_serializer.is_valid():
            order = order_serializer.save()
            if customer:
                order.customer = customer
                order.save()
            return Response(
                {"message": "سفارش با موفقیت ثبت شد", "order": order_serializer.data},
                status=status.HTTP_201_CREATED
            )
        else:
            return Response(order_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ==================== Taxi Service Views ====================

from django.urls import reverse_lazy
from django.views.generic import UpdateView
from django.views.decorators.csrf import csrf_exempt


class TaxiSettingView(UpdateView):
    """Taxi service settings view"""
    template_name = 'Reservation_Module/taxi_settings_form_template.html'
    form_class = TaxiSettingsForm
    success_url = reverse_lazy('taxi:reservation_list_url')

    def get_context_data(self, **kwargs):
        context = super(TaxiSettingView, self).get_context_data(**kwargs)
        settings = TelephoneTaxiModel.objects.filter(is_active=True).first()
        context['settings'] = settings
        return context

    def get_object(self, queryset=None):
        return TelephoneTaxiModel.objects.filter(is_active=True).first()

    def form_valid(self, form):
        form.save()
        return super(TaxiSettingView, self).form_valid(form)


class TaxiReservationListView(ListView):
    """Taxi لیست رزرو تاکسی های VIP view"""
    template_name = 'Reservation_Module/reservations_list_template.html'
    model = ReservationModel
    context_object_name = 'reservations'
    
    def get_queryset(self):
        """Optimize query with prefetch_related for status logs"""
        return ReservationModel.objects.prefetch_related('status_logs').all().order_by('-date_time')


class AddReservationView(APIView):
    """Taxi reservation API endpoint"""
    
    def post(self, request: Request):
        public_key = request.data.get('public_key')
        data = request.data.get('data')
        if not public_key or not data:
            return Response('Public key and data are required', status=status.HTTP_400_BAD_REQUEST)
        
        if KeyService and EncryptionService:
            private_key = KeyService.get_private_key_by_public(public_key)
            if not private_key:
                return Response('Inscription does not exist', status=status.HTTP_400_BAD_REQUEST)
            
            try:
                decrypted_data = EncryptionService.decrypt_data(private_key, data)
            except ValueError as e:
                return Response(f'Decryption failed: {e}', status=status.HTTP_400_BAD_REQUEST)
        else:
            # Fallback to old implementation
            inscription = InscriptionModel.objects.filter(public_key=public_key).first()
            if not inscription:
                return Response('Inscription does not exist', status=status.HTTP_400_BAD_REQUEST)
            
            try:
                decrypted_data = self._decrypt_fallback(inscription.private_key, data)
            except Exception as e:
                return Response(f'Decryption failed: {e}', status=status.HTTP_400_BAD_REQUEST)
        
        if ReservationService:
            reservation, result = ReservationService.create_reservation_from_decrypted_data(decrypted_data)
            if 'error' in result:
                return Response(result['error'], status=status.HTTP_400_BAD_REQUEST)
            return Response("OK", status=status.HTTP_201_CREATED)
        else:
            # Fallback to old implementation
            serializer = ReservationSerializer(data=decrypted_data)
            if serializer.is_valid():
                serializer.save()
                return Response("OK", status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request: Request):
        """Get public key for encryption"""
        if KeyService:
            public_key, _ = KeyService.get_or_create_public_key()
            return Response({'public_key': public_key}, status=status.HTTP_200_OK)
        else:
            # Fallback to old implementation
            inscription = InscriptionModel.objects.filter(use_count__lt=15).first()
            if inscription:
                public_key = inscription.public_key
                inscription.use_count += 1
                inscription.save()
                return Response({'public_key': public_key}, status=status.HTTP_200_OK)
            else:
                private_key, public_key = self._generate_keys_fallback()
                new_inscription = InscriptionModel(
                    private_key=private_key,
                    public_key=public_key,
                    use_count=1
                )
                new_inscription.save()
                return Response({'public_key': public_key}, status=status.HTTP_200_OK)
    
    @staticmethod
    def _decrypt_fallback(private_key, data: str):
        """Fallback decryption method"""
        package_json = base64.b64decode(data)
        package = json.loads(package_json)
        enc_key = base64.b64decode(package['key'])
        nonce = base64.b64decode(package['nonce'])
        tag = base64.b64decode(package['tag'])
        ciphertext = base64.b64decode(package['ciphertext'])
        private_key_obj = RSA.import_key(private_key)
        cipher_rsa = PKCS1_OAEP.new(private_key_obj)
        sym_key = cipher_rsa.decrypt(enc_key)
        cipher_aes = AES.new(sym_key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher_aes.decrypt_and_verify(ciphertext, tag)
        return json.loads(plaintext.decode("utf-8"))
    
    @staticmethod
    def _generate_keys_fallback():
        """Fallback key generation"""
        key = RSA.generate(2048)
        private_key = key.export_key().decode("utf-8")
        public_key = key.publickey().export_key().decode("utf-8")
        return private_key, public_key


@method_decorator(csrf_exempt, name='dispatch')
class UpdateTaxiStatusView(APIView):
    """Update taxi status and log the change"""
    
    def post(self, request: Request, reservation_id: int):
        new_status = None
        old_status_param = None
        
        try:
            if hasattr(request, 'data') and request.data:
                new_status = request.data.get('new_status')
                old_status_param = request.data.get('old_status')
            elif hasattr(request, 'body') and request.body:
                import json
                try:
                    body_data = json.loads(request.body.decode('utf-8'))
                    new_status = body_data.get('new_status')
                    old_status_param = body_data.get('old_status')
                except json.JSONDecodeError:
                    pass
            elif hasattr(request, 'POST'):
                new_status = request.POST.get('new_status')
                old_status_param = request.POST.get('old_status')
        except Exception as e:
            logging.error(f"Error parsing request: {e}", exc_info=True)
        
        if not new_status:
            return Response(
                {'success': False, 'error': 'وضعیت جدید الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        changed_by = request.user.username if request.user.is_authenticated else 'system'
        
        if ReservationService:
            result = ReservationService.update_reservation_status(
                reservation_id,
                new_status,
                old_status=old_status_param,
                changed_by=changed_by
            )
            
            if 'error' in result:
                status_code = status.HTTP_404_NOT_FOUND if 'یافت نشد' in result['error'] else status.HTTP_400_BAD_REQUEST
                return Response(
                    {'success': False, 'error': result['error']},
                    status=status_code
                )
            
            return Response(result, status=status.HTTP_200_OK)
        else:
            # Fallback to old implementation
            try:
                reservation = ReservationModel.objects.get(id=reservation_id)
            except ReservationModel.DoesNotExist:
                return Response(
                    {'success': False, 'error': 'رزرو یافت نشد'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            old_status = reservation.status
            if not old_status_param:
                old_status_param = old_status
            
            valid_statuses = [choice[0] for choice in ReservationModel.STATUS_CHOICES]
            if new_status not in valid_statuses:
                return Response(
                    {'success': False, 'error': f'وضعیت نامعتبر است. وضعیت‌های معتبر: {", ".join(valid_statuses)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if old_status == new_status:
                return Response({
                    'success': True,
                    'message': 'وضعیت تغییر نکرده است',
                    'status': new_status
                }, status=status.HTTP_200_OK)
            
            reservation.status = new_status
            reservation.save()
            
            try:
                TaxiStatusLog.objects.create(
                    reservation=reservation,
                    old_status=old_status_param,
                    new_status=new_status,
                    changed_by=changed_by
                )
            except Exception as e:
                logging.error(f"❌ Failed to create status log: {e}")
            
            return Response({
                'success': True,
                'message': f'وضعیت از {dict(ReservationModel.STATUS_CHOICES).get(old_status, old_status)} به {dict(ReservationModel.STATUS_CHOICES).get(new_status, new_status)} تغییر کرد',
                'old_status': old_status,
                'new_status': new_status
            }, status=status.HTTP_200_OK)


class DeleteReservationView(APIView):
    """Delete taxi reservation"""
    
    def delete(self, request: Request, reservation_id: int):
        if ReservationService:
            result = ReservationService.delete_reservation(reservation_id)
            if 'error' in result:
                status_code = status.HTTP_404_NOT_FOUND if 'not found' in result['error'].lower() else status.HTTP_500_INTERNAL_SERVER_ERROR
                return Response({'error': result['error']}, status=status_code)
            return Response({'message': result['message']}, status=status.HTTP_200_OK)
        else:
            # Fallback to old implementation
            try:
                reservation = ReservationModel.objects.get(id=reservation_id)
                reservation.delete()
                return Response({'message': 'Reservation deleted successfully'}, status=status.HTTP_200_OK)
            except ReservationModel.DoesNotExist:
                return Response({'error': 'Reservation not found'}, status=status.HTTP_404_NOT_FOUND)


class TenantConfigView(APIView):
    """API endpoint for retrieving tenant configuration by DID"""
    
    def get(self, request: Request, tenant_id: str):
        """
        Get tenant configuration by tenant_id (DID number).
        Used by OpenSIPS engine to fetch tenant configs.
        """
        try:
            config = ConfigManager.get_config(tenant_id)
            
            if not config:
                return Response(
                    {'error': 'Tenant not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            return Response(config, status=status.HTTP_200_OK)
            
        except Exception as e:
            logging.error(f"Error fetching tenant config for {tenant_id}: {e}")
            return Response(
                {'error': 'Internal server error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )