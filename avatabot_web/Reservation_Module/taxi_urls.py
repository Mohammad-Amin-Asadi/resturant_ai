"""Taxi service URLs"""
from django.urls import path
from Reservation_Module.views import (
    TaxiReservationListView, TaxiSettingView, AddReservationView, DeleteReservationView, UpdateTaxiStatusView,
    TenantConfigView
)
from Reservation_Module.health_views import health_check

app_name = 'taxi'

urlpatterns = [
    # Health check endpoint (for Docker healthchecks)
    path('healthz/', health_check, name='health_check'),
    
    # Frontend views
    path('', TaxiReservationListView.as_view(), name='reservation_list_url'),
    path('settings/', TaxiSettingView.as_view(), name='settings_url'),
    path('add-reservation/', AddReservationView.as_view(), name='add_reservation_url'),
    path('delete-reservation/<int:reservation_id>/', DeleteReservationView.as_view(), name='delete_reservation_url'),
    path('update-taxi-status/<int:reservation_id>/', UpdateTaxiStatusView.as_view(), name='update_taxi_status_url'),
    
    # API endpoints
    path('api/tenant-config/<str:tenant_id>/', TenantConfigView.as_view(), name='api_tenant_config'),
]

