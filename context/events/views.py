from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.urls import reverse
from django.conf import settings
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Count, Avg
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from .models import (
    Event, Category, Booking, Ticket, Review, UserProfile, MovieShowTime,
    TicketScanLog,
)
from .forms import (UserRegistrationForm, UserLoginForm, EventForm, 
                    BookingForm, ReviewForm, QRCodeValidationForm)
from .services.qr_service import make_share_token, verify_token, verify_ticket_signature, verify_share_token
from datetime import datetime, timedelta
from urllib.parse import urlencode
import json


# ============= Authentication Views =============

def register_view(request):
    """User registration"""
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Registration successful! Welcome to Ticketify.')
            return redirect('home')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'events/register.html', {'form': form})


def login_view(request):
    """User login"""
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {user.username}!')
                next_url = request.GET.get('next', '')
                # Only follow ?next= if it points back to this site (prevents open redirects)
                if next_url and url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
                ):
                    return redirect(next_url)
                return redirect('home')
    else:
        form = UserLoginForm()
    
    return render(request, 'events/login.html', {'form': form})


@login_required
def logout_view(request):
    """User logout"""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('home')


# ============= Public Views =============

def home_view(request):
    """Homepage with featured events"""
    featured_events = Event.objects.filter(
        status='published',
        is_featured=True,
        event_date__gte=timezone.now().date()
    )[:6]
    
    upcoming_events = Event.objects.filter(
        status='published',
        event_date__gte=timezone.now().date()
    ).order_by('event_date', 'start_time')[:8]
    
    categories = Category.objects.all()[:6]
    
    # Statistics
    total_events = Event.objects.filter(status='published').count()
    total_bookings = Booking.objects.filter(status='confirmed').count()
    
    context = {
        'featured_events': featured_events,
        'upcoming_events': upcoming_events,
        'categories': categories,
        'total_events': total_events,
        'total_bookings': total_bookings,
    }
    
    return render(request, 'events/home.html', context)


def events_list_view(request):
    """List all events with filtering and search"""
    events = Event.objects.filter(status='published')
    
    # Search - Enhanced to include category names
    search_query = request.GET.get('search', '')
    if search_query:
        events = events.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(city__icontains=search_query) |
            Q(venue__icontains=search_query) |
            Q(category__name__icontains=search_query)
        )
    
    # Category filter
    category_id = request.GET.get('category', '')
    if category_id:
        events = events.filter(category_id=category_id)
    
    # Date filter - Default to 'upcoming' for better UX
    date_filter = request.GET.get('date', '')
    if not date_filter or date_filter == 'all':
        # Show all events (past and future)
        pass
    elif date_filter == 'upcoming':
        events = events.filter(event_date__gte=timezone.now().date())
    elif date_filter == 'today':
        today = timezone.now().date()
        tomorrow = today + timedelta(days=1)
        events = events.filter(event_date__gte=today, event_date__lt=tomorrow)
    elif date_filter == 'tomorrow':
        tomorrow = timezone.now().date() + timedelta(days=1)
        day_after = tomorrow + timedelta(days=1)
        events = events.filter(event_date__gte=tomorrow, event_date__lt=day_after)
    elif date_filter == 'weekend':
        today = timezone.now().date()
        # Calculate next Saturday and Sunday
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0 and today.weekday() == 5:  # Today is Saturday
            weekend_start = today
        elif today.weekday() == 6:  # Today is Sunday
            weekend_start = today - timedelta(days=1)
        else:
            weekend_start = today + timedelta(days=days_until_saturday)
        weekend_end = weekend_start + timedelta(days=2)
        events = events.filter(event_date__gte=weekend_start, event_date__lt=weekend_end)
    
    # Price filter
    price_filter = request.GET.get('price', '')
    if price_filter == 'free':
        events = events.filter(price=0)
    elif price_filter == 'paid':
        events = events.filter(price__gt=0)
    
    # Sorting
    sort_by = request.GET.get('sort', 'date')
    if sort_by == 'date':
        events = events.order_by('event_date', 'start_time')
    elif sort_by == 'price_low':
        events = events.order_by('price')
    elif sort_by == 'price_high':
        events = events.order_by('-price')
    elif sort_by == 'popular':
        events = events.annotate(booking_count=Count('bookings')).order_by('-booking_count')
    
    # Pagination
    paginator = Paginator(events, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    categories = Category.objects.all()
    
    # Set defaults for template if not provided
    if not date_filter:
        date_filter = ''
    if not price_filter:
        price_filter = ''
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'search_query': search_query,
        'selected_category': category_id,
        'date_filter': date_filter,
        'price_filter': price_filter,
        'sort_by': sort_by,
    }
    
    return render(request, 'events/events_list.html', context)


def event_detail_view(request, slug):
    """Detailed view of a single event"""
    event = get_object_or_404(Event, slug=slug, status='published')
    
    # Get reviews
    reviews = event.reviews.all()
    average_rating = reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
    
    # Check if user has already reviewed
    user_review = None
    can_review = False
    if request.user.is_authenticated:
        user_review = reviews.filter(user=request.user).first()
        # User can review if they have attended the event
        has_ticket = Ticket.objects.filter(
            user=request.user,
            event=event,
            status__in=['valid', 'used']
        ).exists()
        can_review = has_ticket and not user_review
    
    # Similar events
    similar_events = Event.objects.filter(
        category=event.category,
        status='published'
    ).exclude(id=event.id)[:4]
    
    context = {
        'event': event,
        'reviews': reviews,
        'average_rating': average_rating,
        'user_review': user_review,
        'can_review': can_review,
        'similar_events': similar_events,
    }
    
    return render(request, 'events/event_detail.html', context)


@login_required
def book_ticket_view(request, slug):
    """Book tickets for an event"""
    event = get_object_or_404(Event, slug=slug, status='published')
    
    # Create default show times for movie events if they don't exist
    if event.event_type == 'movie':
        event.create_default_show_times()
    
    if event.is_sold_out:
        messages.error(request, 'Sorry, this event is sold out.')
        return redirect('event_detail', slug=slug)
    
    if request.method == 'POST':
        form = BookingForm(event, request.POST)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.user = request.user
            booking.event = event

            # Save selected seats list if provided
            selected_seats_raw = form.cleaned_data.get('selected_seats', '')
            selected_seats = []
            if selected_seats_raw:
                selected_seats = [s.strip() for s in selected_seats_raw.split(',') if s.strip()]
                booking.selected_seats = selected_seats

            # Associate show_time if provided (for movies)
            show_time = form.cleaned_data.get('show_time')
            if show_time:
                booking.show_time = show_time

            # Create booking as PENDING first to avoid signal auto-creation
            booking.status = 'pending'
            booking.save()

            # Decrease availability and create tickets
            qty = booking.quantity
            if event.event_type == 'movie':
                if show_time:
                    show_time.available_tickets = max(0, show_time.available_tickets - qty)
                    show_time.save()
            else:
                event.available_tickets = max(0, event.available_tickets - qty)
                event.save()

            # Create ticket records
            seats_iter = iter(selected_seats)
            for i in range(qty):
                seat = next(seats_iter, None)
                Ticket.objects.create(
                    booking=booking,
                    event=event,
                    user=request.user,
                    attendee_name=request.user.get_full_name() or request.user.username,
                    attendee_email=booking.email,
                    seat_number=seat
                )

            # Finally mark booking as confirmed (signal won't recreate tickets because created=False)
            booking.status = 'confirmed'
            booking.save(update_fields=['status'])

            messages.success(request, f'Booking confirmed! Booking ID: {booking.booking_id}')
            return redirect('booking_confirmation', booking_id=booking.booking_id)
    else:
        # Pre-fill with user data
        initial_data = {
            'email': request.user.email,
            'phone': request.user.profile.phone if hasattr(request.user, 'profile') else ''
        }
        form = BookingForm(event, initial=initial_data)
    
    context = {
        'event': event,
        'form': form,
    }
    
    return render(request, 'events/book_ticket.html', context)


@login_required
def booking_confirmation_view(request, booking_id):
    """Booking confirmation page"""
    booking = get_object_or_404(Booking, booking_id=booking_id, user=request.user)
    tickets = booking.tickets.order_by('pk')
    qr_ticket = tickets.first()

    context = {
        'booking': booking,
        'tickets': tickets,
        'share_url': request.build_absolute_uri(
            reverse('ticket_share', kwargs={'share_token': make_share_token(qr_ticket.ticket_id)})
        ) if qr_ticket else '',
        'QR_REFRESH_INTERVAL': getattr(__import__('django.conf').conf.settings, 'QR_REFRESH_INTERVAL', 30),
    }
    
    return render(request, 'events/booking_confirmation.html', context)


@login_required
def my_bookings_view(request):
    """View user's bookings"""
    bookings = Booking.objects.filter(user=request.user).order_by('-created_at')
    
    paginator = Paginator(bookings, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
    }
    
    return render(request, 'events/my_bookings.html', context)


@login_required
def my_tickets_view(request):
    """View user's tickets"""
    tickets = Ticket.objects.filter(user=request.user).order_by('-created_at')
    
    paginator = Paginator(tickets, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
    }
    
    return render(request, 'events/my_tickets.html', context)


@login_required
def ticket_detail_view(request, ticket_id):
    """View ticket details with QR code"""
    ticket = get_object_or_404(Ticket, ticket_id=ticket_id, user=request.user)
    qr_ticket = ticket.booking.tickets.order_by('pk').first()

    context = {
        'ticket': ticket,
        'qr_ticket_id': qr_ticket.ticket_id if qr_ticket else ticket.ticket_id,
        'share_url': request.build_absolute_uri(
            reverse('ticket_share', kwargs={'share_token': make_share_token(qr_ticket.ticket_id)})
        ) if qr_ticket else '',
        'QR_REFRESH_INTERVAL': getattr(__import__('django.conf').conf.settings, 'QR_REFRESH_INTERVAL', 30),
    }
    
    return render(request, 'events/ticket_detail.html', context)


def ticket_share_view(request, share_token):
    """Render a public live ticket view accessed through a signed share link."""
    ticket_id = verify_share_token(share_token)
    if not ticket_id:
        raise Http404('Invalid ticket sharing link.')

    ticket = get_object_or_404(
        Ticket.objects.select_related('event', 'booking', 'booking__show_time'),
        ticket_id=ticket_id,
    )
    canonical_ticket = ticket.booking.tickets.order_by('pk').first()
    if not canonical_ticket or canonical_ticket.ticket_id != ticket.ticket_id:
        raise Http404('Invalid ticket sharing link.')

    booking_tickets = ticket.booking.tickets.all()
    if booking_tickets.filter(status='used').exists() or booking_tickets.filter(qr_status='USED').exists():
        share_status = 'used'
    elif booking_tickets.filter(status='cancelled').exists():
        share_status = 'cancelled'
    else:
        share_status = 'valid'

    return render(request, 'events/ticket_share.html', {
        'ticket': ticket,
        'share_token': share_token,
        'share_status': share_status,
    })


def qr_scan_view(request):
    """Show signed QR details to scanners; only its event organizer can check it in."""
    token = request.GET.get('token', '')
    ticket = None
    scan_status = 'invalid'
    can_validate = False

    try:
        payload, signature = verify_token(token)
        ticket_id = payload.get('ticket_id')
        event_id = payload.get('event_id')
        timestamp = int(payload.get('ts'))
    except (AttributeError, TypeError, ValueError):
        payload = None
        signature = None
        ticket_id = None
        event_id = None
        timestamp = None

    if ticket_id and event_id is not None:
        ticket = Ticket.objects.select_related(
            'event', 'booking', 'booking__show_time'
        ).filter(ticket_id=ticket_id).first()
        if ticket and str(ticket.event_id) == str(event_id) and verify_ticket_signature(ticket, payload, signature, timestamp):
            booking_tickets = ticket.booking.tickets.all()
            if booking_tickets.filter(status='used').exists() or booking_tickets.filter(qr_status='USED').exists():
                scan_status = 'used'
            elif booking_tickets.filter(status='cancelled').exists():
                scan_status = 'cancelled'
            elif timestamp > int(timezone.now().timestamp()) or int(timezone.now().timestamp()) - timestamp >= settings.QR_REFRESH_INTERVAL:
                scan_status = 'expired'
            else:
                scan_status = 'valid'
                can_validate = (
                    request.user.is_authenticated
                    and (
                        request.user.is_superuser
                        or ticket.event.organizer_id == request.user.id
                    )
                )

    context = {
        'ticket': ticket if scan_status != 'invalid' else None,
        'scan_status': scan_status,
        'can_validate': can_validate,
        'organizer_login_url': (
            f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}"
            if scan_status == 'valid' and not request.user.is_authenticated
            else None
        ),
        'token': token,
        'QR_REFRESH_INTERVAL': settings.QR_REFRESH_INTERVAL,
    }
    return render(request, 'events/qr_scan_result.html', context)


# ============= Event Organizer Views =============

@login_required
def owner_dashboard_view(request):
    """Show site-wide operational data only to the site superuser."""
    if not request.user.is_superuser:
        return HttpResponseForbidden('Owner access is required.')

    context = {
        'total_events': Event.objects.count(),
        'total_users': UserProfile.objects.count(),
        'total_bookings': Booking.objects.filter(status='confirmed').count(),
        'used_tickets': Ticket.objects.filter(status='used').count(),
        'recent_scans': _recent_scan_data(),
    }
    return render(request, 'events/owner_dashboard.html', context)


def _recent_scan_data():
    return list(
        TicketScanLog.objects.select_related(
            'ticket', 'ticket__event', 'ticket__booking', 'scanner'
        ).order_by('-scanned_at').values(
            'id',
            'ticket__ticket_id',
            'ticket__booking__booking_id',
            'ticket__event__title',
            'ticket__status',
            'scanned_at',
            'success',
            'notes',
            'scanner__username',
        )[:50]
    )


@login_required
@require_GET
def owner_scan_history_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Owner access is required.')
    scans = [
        {
            'id': scan['id'],
            'ticket_id': scan['ticket__ticket_id'],
            'booking_id': scan['ticket__booking__booking_id'],
            'event': scan['ticket__event__title'],
            'ticket_status': scan['ticket__status'],
            'scanned_at': scan['scanned_at'].isoformat(),
            'success': scan['success'],
            'notes': scan['notes'],
            'scanner': scan['scanner__username'] or '—',
        }
        for scan in _recent_scan_data()
    ]
    return JsonResponse({
        'scans': scans,
        'statistics': {
            'total_users': UserProfile.objects.count(),
            'total_events': Event.objects.count(),
            'total_bookings': Booking.objects.filter(status='confirmed').count(),
            'used_tickets': Ticket.objects.filter(status='used').count(),
        },
    })


@login_required
def event_scanner_view(request):
    events = Event.objects.all() if request.user.is_superuser else Event.objects.filter(organizer=request.user)
    events = events.order_by('event_date', 'title')
    if not events.exists():
        return HttpResponseForbidden('You are not authorized to scan tickets for an event.')
    return render(request, 'events/event_scanner.html', {'events': events})


@login_required
def create_event_view(request):
    """Create new event"""
    if not hasattr(request.user, 'profile') or not request.user.profile.is_organizer:
        messages.error(request, 'You need to be registered as an organizer.')
        return redirect('home')
    
    if request.method == 'POST':
        form = EventForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save(commit=False)
            event.organizer = request.user
            event.save()
            messages.success(request, 'Event created successfully!')
            return redirect('event_scanner')
    else:
        form = EventForm()
    
    context = {
        'form': form,
        'title': 'Create Event',
    }
    
    return render(request, 'events/event_form.html', context)


@login_required
def edit_event_view(request, slug):
    """Edit existing event"""
    event = get_object_or_404(Event, slug=slug, organizer=request.user)
    
    if request.method == 'POST':
        form = EventForm(request.POST, request.FILES, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, 'Event updated successfully!')
            return redirect('event_scanner')
    else:
        form = EventForm(instance=event)
    
    context = {
        'form': form,
        'title': 'Edit Event',
        'event': event,
    }
    
    return render(request, 'events/event_form.html', context)


@login_required
def delete_event_view(request, slug):
    """Delete event"""
    event = get_object_or_404(Event, slug=slug, organizer=request.user)
    
    if request.method == 'POST':
        event.delete()
        messages.success(request, 'Event deleted successfully!')
        return redirect('event_scanner')
    
    context = {
        'event': event,
    }
    
    return render(request, 'events/event_confirm_delete.html', context)


@login_required
def event_bookings_view(request, slug):
    """View bookings for a specific event"""
    event = get_object_or_404(Event, slug=slug, organizer=request.user)
    bookings = event.bookings.all().order_by('-created_at')
    
    # Statistics
    total_bookings = bookings.filter(status='confirmed').count()
    total_revenue = sum([b.total_amount for b in bookings.filter(status='confirmed')])
    tickets_sold = event.tickets_sold
    
    paginator = Paginator(bookings, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'event': event,
        'page_obj': page_obj,
        'total_bookings': total_bookings,
        'total_revenue': total_revenue,
        'tickets_sold': tickets_sold,
    }
    
    return render(request, 'events/event_bookings.html', context)


# ============= QR Code Validation Views =============

@login_required
def validate_ticket_view(request, slug):
    """Validate tickets using QR code"""
    authorized_events = Event.objects.all() if request.user.is_superuser else Event.objects.filter(organizer=request.user)
    event = get_object_or_404(authorized_events, slug=slug)
    
    if request.method == 'POST':
        form = QRCodeValidationForm(request.POST)
        if form.is_valid():
            verification_code = form.cleaned_data['verification_code']
            ticket_validation_result = None
            
            try:
                ticket = Ticket.objects.get(
                    verification_code=verification_code,
                    event=event
                )
                
                if ticket.status == 'used':
                    ticket_validation_result = 'used'
                    messages.warning(request, f'Ticket {ticket.ticket_id} has already been used on {ticket.validated_at.strftime("%Y-%m-%d %H:%M") if ticket.validated_at else "an earlier scan"}')
                elif ticket.status == 'cancelled':
                    ticket_validation_result = 'cancelled'
                    messages.error(request, f'Ticket {ticket.ticket_id} has been cancelled.')
                elif ticket.status == 'valid':
                    with transaction.atomic():
                        locked_ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
                        if locked_ticket.status == 'used' or locked_ticket.qr_status == 'USED':
                            ticket = locked_ticket
                            ticket_validation_result = 'used'
                            messages.warning(request, f'Ticket {ticket.ticket_id} has already been used.')
                        elif locked_ticket.status == 'valid':
                            locked_ticket.mark_as_used(request.user)
                            ticket = locked_ticket
                            ticket_validation_result = 'validated'
                            TicketScanLog.objects.create(
                                ticket=ticket,
                                scanner=request.user,
                                success=True,
                                remote_addr=request.META.get('REMOTE_ADDR'),
                                device_info=request.META.get('HTTP_USER_AGENT', '')[:256],
                                notes='Validated by manual verification code',
                            )
                            messages.success(request, f'✓ Ticket {ticket.ticket_id} validated successfully! Attendee: {ticket.attendee_name}')
                
                context = {
                    'event': event,
                    'form': QRCodeValidationForm(),
                    'ticket': ticket,
                    'ticket_validation_result': ticket_validation_result,
                }
                return render(request, 'events/validate_ticket.html', context)
                
            except Ticket.DoesNotExist:
                messages.error(request, 'Invalid ticket code.')
    else:
        form = QRCodeValidationForm()
    
    context = {
        'event': event,
        'form': form,
    }
    
    return render(request, 'events/validate_ticket.html', context)


# ============= Review Views =============

@login_required
def add_review_view(request, slug):
    """Add review for an event"""
    event = get_object_or_404(Event, slug=slug)
    
    # Check if user has a ticket
    has_ticket = Ticket.objects.filter(
        user=request.user,
        event=event,
        status__in=['valid', 'used']
    ).exists()
    
    if not has_ticket:
        messages.error(request, 'You can only review events you have attended.')
        return redirect('event_detail', slug=slug)
    
    # Check if user already reviewed
    existing_review = Review.objects.filter(user=request.user, event=event).first()
    
    if request.method == 'POST':
        if existing_review:
            form = ReviewForm(request.POST, instance=existing_review)
            message = 'Review updated successfully!'
        else:
            form = ReviewForm(request.POST)
            message = 'Review submitted successfully!'
        
        if form.is_valid():
            review = form.save(commit=False)
            review.user = request.user
            review.event = event
            review.save()
            messages.success(request, message)
            return redirect('event_detail', slug=slug)
    else:
        form = ReviewForm(instance=existing_review) if existing_review else ReviewForm()
    
    context = {
        'form': form,
        'event': event,
        'existing_review': existing_review,
    }
    
    return render(request, 'events/add_review.html', context)


# ============= Utility Views =============

def categories_view(request):
    """View all categories"""
    categories = Category.objects.all()
    
    context = {
        'categories': categories,
    }
    
    return render(request, 'events/categories.html', context)


def about_view(request):
    """About page"""
    return render(request, 'events/about.html')


def contact_view(request):
    """Contact page"""
    return render(request, 'events/contact.html')
