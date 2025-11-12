#!/bin/bash
# Test script to verify taxi status update API

RESERVATION_ID=${1:-1}
NEW_STATUS=${2:-"at_source"}

echo "🧪 Testing Taxi Status Update API..."
echo "Reservation ID: $RESERVATION_ID"
echo "New Status: $NEW_STATUS"
echo ""

# Get current status
CURRENT_STATUS=$(docker exec server python manage.py shell -c "from Reservation_Module.models import ReservationModel; r = ReservationModel.objects.get(id=$RESERVATION_ID); print(r.status)" 2>/dev/null)

echo "Current Status: $CURRENT_STATUS"
echo ""

# Test the API endpoint directly
echo "📡 Testing API endpoint..."
docker exec server python manage.py shell << EOF
import requests
import json

url = "http://localhost:5001/update-taxi-status/$RESERVATION_ID/"
data = {
    "old_status": "$CURRENT_STATUS",
    "new_status": "$NEW_STATUS"
}

# Simulate the request
from django.test import Client
from django.contrib.sessions.middleware import SessionMiddleware
from django.middleware.csrf import get_token

client = Client()
response = client.post(url, json.dumps(data), content_type='application/json')

print(f"Status Code: {response.status_code}")
print(f"Response: {response.content.decode()}")
EOF

echo ""
echo "✅ Test completed. Check the response above."

