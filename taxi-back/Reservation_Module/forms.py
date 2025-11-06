from django import forms
from Reservation_Module.models import RestaurantSettings


class SettingsForm(forms.ModelForm):
    class Meta:
        model = RestaurantSettings
        exclude = ['is_active']