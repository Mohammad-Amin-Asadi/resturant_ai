# from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import os

urlpatterns = [
    # path('admin/', admin.site.urls),
]

# Support both restaurant and taxi services simultaneously
# If SERVER_TYPE is set, use that; otherwise serve both
server_type = os.environ.get('SERVER_TYPE', '')

if server_type == 'taxi':
    # Taxi-only mode - serve taxi URLs at root
    urlpatterns.append(path('', include('Reservation_Module.taxi_urls')))
elif server_type == 'restaurant':
    # Restaurant-only mode - serve restaurant URLs at root
    urlpatterns.append(path('', include('Reservation_Module.urls')))
else:
    # Both services mode - serve both with prefixes
    urlpatterns.append(path('restaurant/', include('Reservation_Module.urls'))),
    urlpatterns.append(path('taxi/', include('Reservation_Module.taxi_urls'))),
    # Default to restaurant at root
    urlpatterns.append(path('', include('Reservation_Module.urls')))

# Serve static files (both dev and production)
# Note: In production, consider using whitenoise or nginx for better performance
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
