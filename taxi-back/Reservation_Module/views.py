import base64
import json

from Crypto.Cipher import PKCS1_OAEP, AES
from django.urls import reverse_lazy
from django.views.generic import ListView, UpdateView
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view
from Crypto.PublicKey import RSA

from Reservation_Module.models import MenuItem, Order, OrderItem, RestaurantSettings, InscriptionModel
from Reservation_Module.forms import SettingsForm
from Reservation_Module.serializers import (
    MenuItemSerializer, OrderSerializer, OrderItemSerializer, RestaurantSettingsSerializer
)


class SettingView(UpdateView):
    """Restaurant settings view"""
    template_name = 'Reservation_Module/settings_form_template.html'
    form_class = SettingsForm
    success_url = reverse_lazy('order_list_url')

    def get_context_data(self, **kwargs):
        context = super(SettingView, self).get_context_data(**kwargs)
        settings = RestaurantSettings.objects.filter(is_active=True).first()
        context['settings'] = settings
        return context

    def get_object(self, queryset=None):
        return RestaurantSettings.objects.filter(is_active=True).first()

    def form_valid(self, form):
        form.save()
        return super(SettingView, self).form_valid(form)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class OrderListView(ListView):
    """Display all orders for the restaurant"""
    template_name = 'Reservation_Module/orders_list_template.html'
    model = Order
    context_object_name = 'orders'
    
    def get_queryset(self):
        return Order.objects.prefetch_related('items__menu_item').all()


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

        # Create order with items
        order_serializer = OrderSerializer(data=decrypted_data)
        if order_serializer.is_valid():
            order_serializer.save()
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