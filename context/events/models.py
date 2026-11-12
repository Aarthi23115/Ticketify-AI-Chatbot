from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import datetime
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image
import uuid


class UserProfile(models.Model):
    """Extended user profile with additional information"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=15, blank=True, null=True)
    is_organizer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.username}'s Profile"


class Category(models.Model):
    """Event categories for better organization"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='🎪')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Event(models.Model):
    """Event model with comprehensive details"""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]
    
    EVENT_TYPE_CHOICES = [
        ('movie', 'Movie'),
        ('sports', 'Sports'),
        ('concert', 'Concert'),
        ('conference', 'Conference'),
        ('workshop', 'Workshop'),
        ('other', 'Other'),
    ]
    
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='events')
    organizer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='organized_events')
    
    # Event type
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default='other')
    
    # Event details
    venue = models.CharField(max_length=300)
    address = models.TextField()
    city = models.CharField(max_length=100)
    
    # Date and time (single date + optional time)
    event_date = models.DateField(blank=True, null=True)
    start_time = models.TimeField(blank=True, null=True)
    
    # Pricing and capacity
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    total_tickets = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    available_tickets = models.PositiveIntegerField()
    
    # Media
    image = models.ImageField(upload_to='events/', blank=True, null=True)
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    is_featured = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-event_date', '-start_time']
        indexes = [
            models.Index(fields=['status', 'event_date']),
            models.Index(fields=['slug']),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if not self.available_tickets:
            self.available_tickets = self.total_tickets
        super().save(*args, **kwargs)
    
    def create_default_show_times(self, show_date=None):
        """Create default show times (9 AM, 2 PM, 6 PM, 10 PM) for movie events"""
        if self.event_type != 'movie':
            return
        
        if show_date is None:
            show_date = self.event_date
        
        if not show_date:
            return
        
        # Default show times: 9 AM, 2 PM, 6 PM, 10 PM
        default_times = [
            ('09:00', '12:00'),  # 9 AM - 12 PM (3 hours)
            ('14:00', '17:00'),  # 2 PM - 5 PM (3 hours)
            ('18:00', '21:00'),  # 6 PM - 9 PM (3 hours)
            ('22:00', '01:00'),  # 10 PM - 1 AM (3 hours)
        ]
        
        from datetime import time
        for start_str, end_str in default_times:
            start_time = time(*map(int, start_str.split(':')))
            end_time = time(*map(int, end_str.split(':')))
            
            MovieShowTime.objects.get_or_create(
                event=self,
                show_date=show_date,
                start_time=start_time,
                defaults={'end_time': end_time, 'available_tickets': self.total_tickets}
            )
    
    @property
    def is_sold_out(self):
        return self.available_tickets <= 0
    
    @property
    def is_upcoming(self):
        # Compare date/time if available
        now = timezone.now()
        if self.start_time:
            try:
                dt = timezone.make_aware(datetime.combine(self.event_date, self.start_time))
            except Exception:
                # Fallback: compare dates
                return self.event_date > now.date()
            return dt > now
        return self.event_date > now.date()
    
    @property
    def is_ongoing(self):
        now = timezone.now()
        # For movie events use show times to determine ongoing; at event level, check date
        if self.event_type == 'movie':
            return self.event_date == now.date()
        if self.start_time:
            try:
                dt = timezone.make_aware(datetime.combine(self.event_date, self.start_time))
            except Exception:
                return self.event_date == now.date()
            return dt <= now
        return self.event_date == now.date()
    
    @property
    def is_past(self):
        now = timezone.now()
        if self.event_type == 'movie':
            return self.event_date < now.date()
        if self.start_time:
            try:
                dt = timezone.make_aware(datetime.combine(self.event_date, self.start_time))
            except Exception:
                return self.event_date < now.date()
            return dt < now
        return self.event_date < now.date()
    
    @property
    def tickets_sold(self):
        return self.total_tickets - self.available_tickets
    
    @property
    def sales_percentage(self):
        if self.total_tickets > 0:
            return (self.tickets_sold / self.total_tickets) * 100
        return 0
    
    def get_category_image(self):
        """Return a category-specific default image URL if no image is uploaded"""
        if self.image:
            return self.image.url
        
        # Check for specific event types in title (case-insensitive)
        title_lower = self.title.lower()
        
        # Cricket and IPL matches - specific image (use word boundaries for better matching)
        if any(keyword in title_lower for keyword in ['cricket', ' ipl ', 'ipl:', 'ipl 20', ' rcb ', 'rcb vs', ' csk ', 'csk vs', ' mi vs', ' dc ', 'dc vs', ' kkr ', 'kkr vs', ' t20 ']):
            return 'https://images.unsplash.com/photo-1540747913346-19e32dc3e97e?w=800&h=600&fit=crop'
        
        # Basketball - specific image
        if 'basketball' in title_lower:
            return 'https://images.unsplash.com/photo-1546519638-68e109498ffc?w=800&h=600&fit=crop'
        
        # Football/Soccer - specific image
        if any(keyword in title_lower for keyword in ['football', 'soccer', 'fifa']):
            return 'https://images.unsplash.com/photo-1579952363873-27f3bade9f55?w=800&h=600&fit=crop'
        
        # Category-based default images from Unsplash
        category_images = {
            'Music': 'https://images.unsplash.com/photo-1459749411175-04bf5292ceea?w=800&h=600&fit=crop',
            'Sports': 'https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=800&h=600&fit=crop',
            'Technology': 'https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800&h=600&fit=crop',
            'Business': 'https://images.unsplash.com/photo-1560439514-4e9645039924?w=800&h=600&fit=crop',
            'Arts': 'https://images.unsplash.com/photo-1460661419201-fd4cecdf8a8b?w=800&h=600&fit=crop',
            'Food': 'https://images.unsplash.com/photo-1555939594-58d7cb561ad1?w=800&h=600&fit=crop',
            'Comedy': 'https://images.unsplash.com/photo-1585699324551-f6c309eedeca?w=800&h=600&fit=crop',
            'Education': 'https://images.unsplash.com/photo-1503676260728-1c00da094a0b?w=800&h=600&fit=crop',
            'Movies': 'https://images.unsplash.com/photo-1536440136628-849c177e76a1?w=800&h=600&fit=crop',
        }
        
        if self.category and self.category.name in category_images:
            return category_images[self.category.name]
        
        # Default fallback image
        return 'https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=800&h=600&fit=crop'


class MovieShowTime(models.Model):
    """Show times for movie events (e.g., 9:00-12:00, 1:00-3:00 PM)"""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='show_times', limit_choices_to={'event_type': 'movie'})
    show_date = models.DateField()  # The date of the show
    start_time = models.TimeField()  # e.g., 09:00
    end_time = models.TimeField()    # e.g., 12:00
    available_tickets = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    
    class Meta:
        ordering = ['show_date', 'start_time']
        unique_together = ['event', 'show_date', 'start_time']
        verbose_name = 'Movie Show Time'
        verbose_name_plural = 'Movie Show Times'
    
    def __str__(self):
        return f"{self.start_time.strftime('%I:%M %p')} - {self.end_time.strftime('%I:%M %p')} ({self.available_tickets} seats)"


class Booking(models.Model):
    """Booking/Order model for ticket purchases"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
    ]
    
    booking_id = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bookings')
    
    # For movie events, which show time is booked
    show_time = models.ForeignKey(MovieShowTime, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    
    # Booking details
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Selected seats for this booking (list of seat identifiers)
    selected_seats = models.JSONField(blank=True, null=True)
    
    # Contact information
    email = models.EmailField()
    phone = models.CharField(max_length=15)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['booking_id']),
            models.Index(fields=['user', 'status']),
        ]
    
    def __str__(self):
        return f"Booking {self.booking_id} - {self.event.title}"
    
    def save(self, *args, **kwargs):
        if not self.booking_id:
            self.booking_id = self.generate_booking_id()
        if not self.total_amount:
            self.total_amount = self.event.price * self.quantity
        super().save(*args, **kwargs)
    
    def generate_booking_id(self):
        """Generate unique booking ID"""
        return f"BK{timezone.now().strftime('%Y%m%d')}{uuid.uuid4().hex[:6].upper()}"


class Ticket(models.Model):
    """Individual ticket with QR code"""
    STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('used', 'Used'),
        ('cancelled', 'Cancelled'),
    ]
    
    ticket_id = models.CharField(max_length=20, unique=True, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='tickets')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='tickets')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tickets')
    
    # Ticket details
    attendee_name = models.CharField(max_length=200)
    attendee_email = models.EmailField()
    
    # Seat number / identifier (e.g., A1)
    seat_number = models.CharField(max_length=20, blank=True, null=True)
    
    # QR Code
    qr_code = models.ImageField(upload_to='qrcodes/', blank=True, null=True)
    verification_code = models.CharField(max_length=100, unique=True, editable=False)

    # Existing ticket status and validation (kept for backward compatibility)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='valid')
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='validated_tickets')

    # New QR management fields for dynamic QR feature
    qr_secret = models.CharField(max_length=128, blank=True, null=True)
    QR_STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('USED', 'Used'),
        ('EXPIRED', 'Expired'),
    ]
    qr_status = models.CharField(max_length=20, choices=QR_STATUS_CHOICES, default='ACTIVE')
    last_qr_generated_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ticket_id']),
            models.Index(fields=['verification_code']),
        ]
    
    def __str__(self):
        return f"Ticket {self.ticket_id} - {self.event.title}"
    
    def save(self, *args, **kwargs):
        if not self.ticket_id:
            self.ticket_id = self.generate_ticket_id()
        if not self.verification_code:
            self.verification_code = self.generate_verification_code()
        
        super().save(*args, **kwargs)
        
        # Generate QR code after saving
        if not self.qr_code:
            self.generate_qr_code()
    
    def generate_ticket_id(self):
        """Generate unique ticket ID"""
        return f"TK{timezone.now().strftime('%Y%m%d')}{uuid.uuid4().hex[:8].upper()}"
    
    def generate_verification_code(self):
        """Generate unique verification code for QR"""
        return uuid.uuid4().hex
    
    def generate_qr_code(self):
        """Generate QR code for ticket"""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        
        # QR code data contains verification code and ticket info
        qr_data = f"{self.verification_code}|{self.ticket_id}|{self.event.id}"
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Save to BytesIO
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        file_name = f'ticket_{self.ticket_id}.png'
        
        self.qr_code.save(file_name, File(buffer), save=True)
        buffer.close()
    
    def mark_as_used(self, validator=None):
        """Mark ticket as used during entry"""
        self.status = 'used'
        self.qr_status = 'USED'
        self.validated_at = timezone.now()
        self.validated_by = validator
        self.save()
    
    @property
    def is_valid(self):
        """Check if ticket is valid for entry"""
        return self.status == 'valid' and self.event.is_upcoming or self.event.is_ongoing


class Review(models.Model):
    """Event reviews and ratings"""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['event', 'user']
    
    def __str__(self):
        return f"{self.user.username} - {self.event.title} ({self.rating}★)"


class TicketScanLog(models.Model):
    """Log each QR scan attempt for auditing and anti-fraud"""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='scan_logs')
    scanner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_scan_logs',
    )
    scanned_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)
    remote_addr = models.CharField(max_length=100, blank=True, null=True)
    device_info = models.CharField(max_length=256, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-scanned_at']

    def __str__(self):
        return f"Scan {self.ticket.ticket_id} @ {self.scanned_at} - {'OK' if self.success else 'FAIL'}"
