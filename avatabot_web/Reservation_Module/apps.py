from django.apps import AppConfig


class ReservationModuleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Reservation_Module'
    
    def ready(self):
        """
        Prevent model registration for Reservation_Module models.
        Models are now in restaurant/taxi/shared apps.
        This app is kept only for URLs, views, and backward compatibility.
        """
        # Don't import models here - they're handled by restaurant/taxi/shared apps
        pass
    verbose_name = 'Reservation Module'
