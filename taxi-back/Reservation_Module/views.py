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

from Reservation_Module.models import Customer, MenuItem, Order, OrderItem, RestaurantSettings, InscriptionModel
from Reservation_Module.serializers import (
    MenuItemSerializer, OrderSerializer, OrderItemSerializer, RestaurantSettingsSerializer
)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class OrderListView(ListView):
    """Display all orders for the restaurant"""
    template_name = 'Reservation_Module/orders_list_template.html'
    model = Order
    context_object_name = 'orders'
    
    def get_queryset(self):
        return Order.objects.prefetch_related('items__menu_item').all()


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
        
        queryset = MenuItem.objects.filter(is_available=True)
        
        if category:
            queryset = queryset.filter(category=category)
        
        if special and special.lower() in ['true', '1', 'yes']:
            queryset = queryset.filter(is_special=True)
        
        serializer = MenuItemSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class OrderTrackingView(APIView):
    """Track order by phone number"""
    
    def get(self, request: Request):
        phone_number = request.query_params.get('phone_number')
        
        if not phone_number:
            return Response(
                {'error': 'شماره تلفن الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get latest order for this phone number
        orders = Order.objects.filter(phone_number=phone_number).prefetch_related('items__menu_item')
        
        if not orders.exists():
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
        
        try:
            customer = Customer.objects.get(phone_number=phone_number)
            return Response({
                'success': True,
                'customer': {
                    'name': customer.name,
                    'phone_number': customer.phone_number,
                    'address': customer.address,
                }
            }, status=status.HTTP_200_OK)
        except Customer.DoesNotExist:
            return Response({
                'success': False,
                'message': 'مشتری یافت نشد'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Handle case where Customer table doesn't exist yet
            logging.error(f"Error getting customer info: {e}")
            return Response({
                'success': False,
                'message': 'خطا در دریافت اطلاعات مشتری'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
def update_order_status(request, order_id):
    """Update order status"""
    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        return Response(
            {'error': 'سفارش یافت نشد'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    new_status = request.data.get('status')
    
    valid_statuses = dict(Order.STATUS_CHOICES).keys()
    if new_status not in valid_statuses:
        return Response(
            {'error': 'وضعیت نامعتبر است'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    order.status = new_status
    order.save()
    
    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_order(request, order_id):
    """Delete an order from database"""
    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        return Response(
            {'error': 'سفارش یافت نشد'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Delete the order (CASCADE will delete related OrderItems automatically)
    order_id_deleted = order.id
    order.delete()
    
    return Response(
        {'message': f'سفارش #{order_id_deleted} با موفقیت حذف شد', 'order_id': order_id_deleted},
        status=status.HTTP_200_OK
    )


@api_view(['DELETE'])
def delete_customer(request, customer_id):
    """Delete a customer from database"""
    try:
        customer = Customer.objects.get(id=customer_id)
    except Customer.DoesNotExist:
        return Response(
            {'error': 'مشتری یافت نشد'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Store customer info before deletion
    customer_name = customer.name
    customer_phone = customer.phone_number
    
    # Delete the customer (CASCADE will handle related orders if configured)
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

        inscription = InscriptionModel.objects.filter(public_key=public_key).first()
        if not inscription:
            return Response(
                'Inscription does not exist',
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            decrypted_data = self.decoder(inscription.private_key, data)
        except Exception as e:
            return Response(
                f'Decryption failed: {e}',
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create or update Customer record first
        customer_name = decrypted_data.get('customer_name')
        phone_number = decrypted_data.get('phone_number')
        address = decrypted_data.get('address')
        
        if phone_number:
            try:
                customer, created = Customer.objects.get_or_create(
                    phone_number=phone_number,
                    defaults={
                        'name': customer_name or '',
                        'address': address or ''
                    }
                )
                # Update customer info if it exists (in case name or address changed)
                if not created:
                    if customer_name and customer.name != customer_name:
                        customer.name = customer_name
                    if address and customer.address != address:
                        customer.address = address
                    customer.save()
            except Exception as e:
                # Handle case where Customer table doesn't exist yet
                logging.warning(f"Could not create/update customer record: {e}")
                # Continue with order creation even if customer creation fails
        
        # CRITICAL VALIDATION: Ensure order has items
        items = decrypted_data.get('items', [])
        if not items or len(items) == 0:
            logging.error("❌ ORDER REJECTED: No items in order")
            return Response(
                {'error': 'سفارش باید حداقل یک آیتم داشته باشد. لیست غذاها خالی است.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate each item
        for idx, item in enumerate(items):
            menu_item_id = item.get('menu_item')
            quantity = item.get('quantity', 0)
            if not menu_item_id:
                logging.error("❌ ORDER REJECTED: Item %d missing menu_item ID", idx + 1)
                return Response(
                    {'error': f'آیتم {idx + 1} نامعتبر است: شناسه غذا مشخص نشده'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if quantity <= 0:
                logging.error("❌ ORDER REJECTED: Item %d has invalid quantity: %d", idx + 1, quantity)
                return Response(
                    {'error': f'آیتم {idx + 1} نامعتبر است: تعداد باید بیشتر از صفر باشد'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validate required fields
        customer_name = decrypted_data.get('customer_name', '').strip()
        address = decrypted_data.get('address', '').strip()
        
        if not customer_name:
            logging.error("❌ ORDER REJECTED: Missing customer_name")
            return Response(
                {'error': 'نام مشتری الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not address:
            logging.error("❌ ORDER REJECTED: Missing address")
            return Response(
                {'error': 'آدرس تحویل الزامی است'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create order with items
        order_serializer = OrderSerializer(data=decrypted_data)
        if order_serializer.is_valid():
            order = order_serializer.save()
            # Link order to customer if customer was found/created
            if phone_number:
                try:
                    customer = Customer.objects.get(phone_number=phone_number)
                    order.customer = customer
                    order.save()
                except (Customer.DoesNotExist, Exception) as e:
                    # Handle case where Customer table doesn't exist or other errors
                    logging.debug(f"Could not link order to customer: {e}")
                    pass
            
            return Response(
                {"message": "سفارش با موفقیت ثبت شد", "order": order_serializer.data},
                status=status.HTTP_201_CREATED
            )
        else:
            return Response(order_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request: Request):
        """Get public key for encryption"""
        inscription = InscriptionModel.objects.filter(use_count__lt=15).first()
        if inscription:
            public_key = inscription.public_key
            inscription.use_count += 1
            inscription.save()
            return Response({'public_key': public_key}, status=status.HTTP_200_OK)
        else:
            private_key, public_key = self.generate_keys()
            new_inscription = InscriptionModel(
                private_key=private_key,
                public_key=public_key,
                use_count=1
            )
            new_inscription.save()
            return Response({'public_key': public_key}, status=status.HTTP_200_OK)

    @staticmethod
    def decoder(private_key, data: str):
        """
        Hybrid decoder:
         - decode base64(JSON(package))
         - decrypt AES key with RSA private key
         - decrypt ciphertext with AES-GCM
        """
        # 1) decode wrapper base64 → JSON string
        package_json = base64.b64decode(data)
        package = json.loads(package_json)

        # 2) decode components
        enc_key = base64.b64decode(package['key'])
        nonce = base64.b64decode(package['nonce'])
        tag = base64.b64decode(package['tag'])
        ciphertext = base64.b64decode(package['ciphertext'])

        # 3) RSA decrypt the AES key
        private_key_obj = RSA.import_key(private_key)
        cipher_rsa = PKCS1_OAEP.new(private_key_obj)
        sym_key = cipher_rsa.decrypt(enc_key)

        # 4) AES-GCM decrypt
        cipher_aes = AES.new(sym_key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher_aes.decrypt_and_verify(ciphertext, tag)

        return json.loads(plaintext.decode("utf-8"))

    @staticmethod
    def generate_keys():
        key = RSA.generate(2048)
        private_key = key.export_key().decode("utf-8")
        public_key = key.publickey().export_key().decode("utf-8")
        return private_key, public_key