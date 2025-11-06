from django.urls import path

from Reservation_Module.views import (
    OrderListView, SettingView, AddOrderView, MenuAPIView,
    OrderTrackingView, update_order_status
)

urlpatterns = [
    # Frontend views
    path('', OrderListView.as_view(), name='order_list_url'),
    path('settings/', SettingView.as_view(), name='settings_url'),
    
    # API endpoints
    path('api/menu/', MenuAPIView.as_view(), name='api_menu'),
    path('api/orders/', AddOrderView.as_view(), name='api_add_order'),
    path('api/orders/track/', OrderTrackingView.as_view(), name='api_track_order'),
    path('api/orders/<int:order_id>/status/', update_order_status, name='api_update_status'),
]
