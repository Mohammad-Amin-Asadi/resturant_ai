# from django.contrib import admin
from django.urls import path, include
import os

urlpatterns = [
    # path('admin/', admin.site.urls),
]

# Route based on SERVER_TYPE environment variable
# SERVER_TYPE=restaurant -> Restaurant service (port 8000)
# SERVER_TYPE=taxi -> Taxi service (port 8001)
server_type = os.environ.get('SERVER_TYPE', 'restaurant')

if server_type == 'taxi':
    # Taxi service - serve taxi URLs at root
    urlpatterns.append(path('', include('Reservation_Module.taxi_urls')))
else:
    # Restaurant service (default) - serve restaurant URLs at root
    urlpatterns.append(path('', include('Reservation_Module.urls')))
