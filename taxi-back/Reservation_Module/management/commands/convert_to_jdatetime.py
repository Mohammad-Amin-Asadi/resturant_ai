"""
Management command to convert existing datetime values to jdatetime (Persian calendar)
This ensures all dates are stored in Iran/Tehran timezone
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from Reservation_Module.models import Customer, Order
from Reservation_Module.jdatetime_utils import get_tehran_now, datetime_to_jdatetime, format_jdatetime
try:
    import pytz
    TEHRAN_TZ = pytz.timezone('Asia/Tehran')
except ImportError:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo('Asia/Tehran')


class Command(BaseCommand):
    help = 'Convert existing datetime values to use Iran/Tehran timezone and ensure they are correct'

    def handle(self, *args, **options):
        self.stdout.write('Starting datetime conversion to Tehran timezone...')
        
        # Convert Customer dates
        customers_updated = 0
        for customer in Customer.objects.all():
            updated = False
            
            # Convert created_at to Tehran timezone if needed
            if customer.created_at:
                if timezone.is_naive(customer.created_at):
                    customer.created_at = timezone.make_aware(customer.created_at, TEHRAN_TZ)
                else:
                    customer.created_at = customer.created_at.astimezone(TEHRAN_TZ)
                updated = True
            
            # Convert updated_at to Tehran timezone if needed
            if customer.updated_at:
                if timezone.is_naive(customer.updated_at):
                    customer.updated_at = timezone.make_aware(customer.updated_at, TEHRAN_TZ)
                else:
                    customer.updated_at = customer.updated_at.astimezone(TEHRAN_TZ)
                updated = True
            
            if updated:
                customer.save(update_fields=['created_at', 'updated_at'])
                customers_updated += 1
                jdt_created = datetime_to_jdatetime(customer.created_at)
                self.stdout.write(f'  Customer {customer.id}: {format_jdatetime(jdt_created) if jdt_created else "N/A"}')
        
        self.stdout.write(self.style.SUCCESS(f'Updated {customers_updated} customers'))
        
        # Convert Order dates
        orders_updated = 0
        for order in Order.objects.all():
            updated = False
            
            # Convert order_date to Tehran timezone if needed
            if order.order_date:
                if timezone.is_naive(order.order_date):
                    order.order_date = timezone.make_aware(order.order_date, TEHRAN_TZ)
                else:
                    order.order_date = order.order_date.astimezone(TEHRAN_TZ)
                updated = True
            
            if updated:
                order.save(update_fields=['order_date'])
                orders_updated += 1
                jdt_order = datetime_to_jdatetime(order.order_date)
                self.stdout.write(f'  Order {order.id}: {format_jdatetime(jdt_order) if jdt_order else "N/A"}')
        
        self.stdout.write(self.style.SUCCESS(f'Updated {orders_updated} orders'))
        self.stdout.write(self.style.SUCCESS('Datetime conversion completed!'))

