from django.urls import path

from Reservation_Module.views import (
    OrderListView, CustomerListView, AddOrderView, MenuAPIView,
    OrderTrackingView, CustomerInfoView, update_order_status, delete_order
)

urlpatterns = [
    # Frontend views
    path('', OrderListView.as_view(), name='order_list_url'),
    path('customers/', CustomerListView.as_view(), name='customers_list_url'),
    
    # API endpoints
    path('api/menu/', MenuAPIView.as_view(), name='api_menu'),
    path('api/orders/', AddOrderView.as_view(), name='api_add_order'),
    path('api/orders/track/', OrderTrackingView.as_view(), name='api_track_order'),
    path('api/customers/info/', CustomerInfoView.as_view(), name='api_customer_info'),
    path('api/orders/<int:order_id>/status/', update_order_status, name='api_update_status'),
    path('api/orders/<int:order_id>/delete/', delete_order, name='api_delete_order'),
]
