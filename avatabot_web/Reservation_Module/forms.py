from django import forms
from Reservation_Module.models import RestaurantSettings, TelephoneTaxiModel


class SettingsForm(forms.ModelForm):
    class Meta:
        model = RestaurantSettings
        exclude = ['is_active']


class TaxiSettingsForm(forms.ModelForm):
    class Meta:
        model = TelephoneTaxiModel
        exclude = ['is_active']