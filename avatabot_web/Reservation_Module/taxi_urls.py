"""Taxi service URLs"""
from django.urls import path
from Reservation_Module.views import (
    TaxiReservationListView, TaxiSettingView, AddReservationView, DeleteReservationView, UpdateTaxiStatusView
)

app_name = 'taxi'

urlpatterns = [
    path('', TaxiReservationListView.as_view(), name='reservation_list_url'),
    path('settings/', TaxiSettingView.as_view(), name='settings_url'),
    path('add-reservation/', AddReservationView.as_view(), name='add_reservation_url'),
    path('delete-reservation/<int:reservation_id>/', DeleteReservationView.as_view(), name='delete_reservation_url'),
    path('update-taxi-status/<int:reservation_id>/', UpdateTaxiStatusView.as_view(), name='update_taxi_status_url'),
]

