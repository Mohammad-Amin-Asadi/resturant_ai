from django import forms
# Import from new app locations
try:
    from restaurant.models import RestaurantSettings
    from taxi.models import TelephoneTaxiModel
except ImportError:
    # Fallback for backward compatibility
    from Reservation_Module.models import RestaurantSettings, TelephoneTaxiModel


class SettingsForm(forms.ModelForm):
    class Meta:
        model = RestaurantSettings
        exclude = ['is_active']


class TaxiSettingsForm(forms.ModelForm):
    class Meta:
        model = TelephoneTaxiModel
        exclude = ['is_active']