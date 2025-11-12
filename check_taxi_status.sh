#!/bin/bash
# Quick script to check taxi reservation status

RESERVATION_ID=${1:-1}  # Default to ID 1 if not provided

echo "🚕 Checking Taxi Reservation #${RESERVATION_ID}..."
echo "=========================================="

docker exec server python manage.py shell -c "
from Reservation_Module.models import ReservationModel, TaxiStatusLog
from django.utils import timezone

try:
    r = ReservationModel.objects.get(id=${RESERVATION_ID})
    print(f'📋 Reservation ID: {r.id}')
    print(f'👤 User: {r.user_fullname}')
    print(f'📍 Origin: {r.origin}')
    print(f'🎯 Destination: {r.destination}')
    print(f'📅 Date/Time: {r.date_time}')
    print(f'🚦 Current Status: {r.get_status_display()} ({r.status})')
    print()
    print('📜 Recent Status Changes:')
    print('-' * 50)
    logs = TaxiStatusLog.objects.filter(reservation=r).order_by('-changed_at')[:10]
    if logs:
        for log in logs:
            old = log.old_status or 'N/A'
            new = log.new_status
            time = log.changed_at.strftime('%Y-%m-%d %H:%M:%S')
            print(f'{time} | {old} → {new}')
    else:
        print('No status changes logged yet.')
except ReservationModel.DoesNotExist:
    print(f'❌ Reservation with ID ${RESERVATION_ID} not found!')
except Exception as e:
    print(f'❌ Error: {e}')
"

