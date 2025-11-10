from django.db import models
from django.utils import timezone
from .jdatetime_utils import get_tehran_now, datetime_to_jdatetime


class Customer(models.Model):
    """Customer model - stores customer information separately from orders"""
    name = models.CharField(max_length=300, verbose_name='نام مشتری')
    phone_number = models.CharField(max_length=15, verbose_name='شماره تلفن', db_index=True, unique=True)
    address = models.TextField(verbose_name='آدرس', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ ثبت')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخرین به‌روزرسانی')
    
    class Meta:
        verbose_name = 'مشتری'
        verbose_name_plural = 'مشتریان'
        ordering = ['-updated_at', 'name']
        db_table = 'customer'
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['-updated_at']),
        ]

    def __str__(self):
        return f"{self.name} - {self.phone_number}"
    
    def get_order_ids(self):
        """Get all order IDs for this customer"""
        return list(self.orders.values_list('id', flat=True))
    
    def get_all_addresses(self):
        """Get all unique addresses from customer's orders"""
        addresses = []
        # Get address from Customer model if exists
        if self.address:
            addresses.append(self.address)
        # Get all unique addresses from orders
        order_addresses = self.orders.exclude(address__isnull=True).exclude(address='').values_list('address', flat=True).distinct()
        for addr in order_addresses:
            if addr and addr not in addresses:  # Avoid duplicates
                addresses.append(addr)
        return addresses
    
    def save(self, *args, **kwargs):
        """Override save to ensure dates are in Tehran timezone"""
        # Ensure created_at and updated_at are in Tehran timezone
        if not self.pk:  # New object
            self.created_at = get_tehran_now()
        self.updated_at = get_tehran_now()
        super().save(*args, **kwargs)
    
    @property
    def created_at_jalali(self):
        """Get created_at as jdatetime (Persian calendar)"""
        return datetime_to_jdatetime(self.created_at)
    
    @property
    def updated_at_jalali(self):
        """Get updated_at as jdatetime (Persian calendar)"""
        return datetime_to_jdatetime(self.updated_at)


class MenuItem(models.Model):
    """Menu item model for restaurant"""
    name = models.CharField(max_length=300, verbose_name='نام غذا')
    category = models.CharField(max_length=100, verbose_name='دسته بندی')
    original_price = models.PositiveIntegerField(verbose_name='قیمت اصلی (تومان)')
    final_price = models.PositiveIntegerField(verbose_name='قیمت نهایی (تومان)')
    discount_percent = models.PositiveSmallIntegerField(default=0, verbose_name='درصد تخفیف')
    is_available = models.BooleanField(default=True, verbose_name='موجود است')
    is_special = models.BooleanField(default=False, verbose_name='پیشنهاد ویژه')
    
    class Meta:
        verbose_name = 'آیتم منو'
        verbose_name_plural = 'آیتم های منو'
        ordering = ['category', 'name']
        db_table = 'menu_item'
        indexes = [
            models.Index(fields=['category', 'is_available']),
            models.Index(fields=['is_special', 'is_available']),
        ]

    def __str__(self):
        return f"{self.name} - {self.final_price:,} تومان"


class Order(models.Model):
    """Order model for restaurant orders"""
    
    STATUS_CHOICES = [
        ('pending', 'در انتظار تایید رستوران'),
        ('confirmed', 'تایید توسط رستوران'),
        ('preparing', 'در حال آماده سازی'),
        ('on_delivery', 'تحویل داده شده به پیک'),
        ('delivered', 'تحویل داده شده به مشتری توسط پیک'),
        ('cancelled', 'لغو شده'),
    ]
    
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        verbose_name='مشتری'
    )
    # Keep these fields for backward compatibility and data migration
    customer_name = models.CharField(max_length=300, verbose_name='نام مشتری')
    phone_number = models.CharField(max_length=15, verbose_name='شماره تلفن', db_index=True)
    address = models.TextField(verbose_name='آدرس', blank=True, null=True)
    order_date = models.DateTimeField(auto_now_add=True, verbose_name='تاریخ سفارش')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='وضعیت سفارش'
    )
    total_price = models.PositiveIntegerField(default=0, verbose_name='جمع کل (تومان)')
    notes = models.TextField(blank=True, null=True, verbose_name='یادداشت')
    
    class Meta:
        verbose_name = 'سفارش'
        verbose_name_plural = 'سفارشات'
        ordering = ['-order_date']
        db_table = 'order'
        indexes = [
            models.Index(fields=['-order_date']),
            models.Index(fields=['phone_number', '-order_date']),
            models.Index(fields=['status']),
            models.Index(fields=['customer', '-order_date']),
        ]

    def __str__(self):
        return f"سفارش {self.customer_name} - {self.phone_number} - {self.get_status_display()}"
    
    def calculate_total(self):
        """Calculate total price from order items"""
        total = sum(item.subtotal for item in self.items.all())
        self.total_price = total
        return total
    
    def save(self, *args, **kwargs):
        """Auto-link to Customer if phone_number matches and ensure order_date is in Tehran timezone"""
        # Ensure order_date is in Tehran timezone
        if not self.pk:  # New object
            self.order_date = get_tehran_now()
        
        # Auto-link to Customer if phone_number matches
        if not self.customer and self.phone_number:
            try:
                customer = Customer.objects.get(phone_number=self.phone_number)
                self.customer = customer
            except (Customer.DoesNotExist, Exception):
                # Handle case where Customer table doesn't exist or customer not found
                pass
        super().save(*args, **kwargs)
    
    @property
    def order_date_jalali(self):
        """Get order_date as jdatetime (Persian calendar)"""
        return datetime_to_jdatetime(self.order_date)


class OrderItem(models.Model):
    """Individual items in an order"""
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='سفارش'
    )
    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.PROTECT,
        verbose_name='آیتم منو'
    )
    quantity = models.PositiveSmallIntegerField(default=1, verbose_name='تعداد')
    unit_price = models.PositiveIntegerField(verbose_name='قیمت واحد (تومان)')
    subtotal = models.PositiveIntegerField(verbose_name='جمع (تومان)')
    
    class Meta:
        verbose_name = 'آیتم سفارش'
        verbose_name_plural = 'آیتم های سفارش'
        db_table = 'order_item'

    def __str__(self):
        return f"{self.menu_item.name} × {self.quantity}"
    
    def save(self, *args, **kwargs):
        """Auto-calculate subtotal before saving"""
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class RestaurantSettings(models.Model):
    """Restaurant settings - replaces TelephoneTaxiModel"""
    restaurant_name = models.CharField(max_length=300, default='رستوران بزرگمهر', verbose_name='نام رستوران')
    server_url = models.URLField(verbose_name='آدرس سرور')
    description = models.TextField(null=True, blank=True, verbose_name='توضیحات')
    is_active = models.BooleanField(default=True, verbose_name='فعال است')
    phone_number = models.CharField(max_length=15, blank=True, verbose_name='شماره تلفن رستوران')
    address = models.TextField(blank=True, verbose_name='آدرس رستوران')

    class Meta:
        verbose_name = 'تنظیمات رستوران'
        verbose_name_plural = 'تنظیمات رستوران'
        ordering = ['restaurant_name']
        db_table = 'restaurant_settings'

    def __str__(self):
        return f"{self.restaurant_name} - {self.server_url}"


class InscriptionModel(models.Model):
    """Encryption keys model - kept for compatibility"""
    private_key = models.TextField(verbose_name='کلید خصوصی')
    public_key  = models.TextField(verbose_name='کلید عمومی')
    use_count = models.PositiveIntegerField(verbose_name='تعداد استفاده')

    class Meta:
        verbose_name = 'کلید رمزنگاری'
        verbose_name_plural = 'کلیدهای رمزنگاری'
        ordering = ['id']
        db_table = 'inscription_model'

    def __str__(self):
        return f"Key {self.id} - Uses: {self.use_count}"


