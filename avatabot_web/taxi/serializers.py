"""
DRF serializers for taxi domain.
"""

from rest_framework import serializers
from taxi.models import ReservationModel, TelephoneTaxiModel


class ReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReservationModel
        fields = '__all__'


class TelephoneTaxiSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelephoneTaxiModel
        fields = '__all__'
