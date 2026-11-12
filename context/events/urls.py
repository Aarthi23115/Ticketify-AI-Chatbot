from django.urls import path
from . import views
from .api_views import QRValidateAPIView, QRGenerateAPIView

urlpatterns = [
    # Public URLs
    path('', views.home_view, name='home'),
    path('events/', views.events_list_view, name='events_list'),
    path('events/<slug:slug>/', views.event_detail_view, name='event_detail'),
    path('categories/', views.categories_view, name='categories'),
    path('about/', views.about_view, name='about'),
    path('contact/', views.contact_view, name='contact'),
    
    # Authentication URLs
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Booking URLs
    path('events/<slug:slug>/book/', views.book_ticket_view, name='book_ticket'),
    path('booking/<str:booking_id>/', views.booking_confirmation_view, name='booking_confirmation'),
    path('my-bookings/', views.my_bookings_view, name='my_bookings'),
    path('my-tickets/', views.my_tickets_view, name='my_tickets'),
    path('ticket/<str:ticket_id>/', views.ticket_detail_view, name='ticket_detail'),
    path('ticket/share/<str:share_token>/', views.ticket_share_view, name='ticket_share'),
    path('qr/scan/', views.qr_scan_view, name='qr_scan'),
    
    # Organizer URLs
    path('owner/dashboard/', views.owner_dashboard_view, name='owner_dashboard'),
    path('owner/dashboard/scan-history/', views.owner_scan_history_view, name='owner_scan_history'),
    path('organizer/scanner/', views.event_scanner_view, name='event_scanner'),
    path('organizer/event/create/', views.create_event_view, name='create_event'),
    path('organizer/event/<slug:slug>/edit/', views.edit_event_view, name='edit_event'),
    path('organizer/event/<slug:slug>/delete/', views.delete_event_view, name='delete_event'),
    path('organizer/event/<slug:slug>/bookings/', views.event_bookings_view, name='event_bookings'),
    path('organizer/event/<slug:slug>/validate/', views.validate_ticket_view, name='validate_ticket'),
    
    # Review URLs
    path('events/<slug:slug>/review/', views.add_review_view, name='add_review'),
    # API endpoints
    path('api/qr/validate/', QRValidateAPIView.as_view(), name='api_qr_validate'),
    path('api/qr/generate/<str:ticket_id>/', QRGenerateAPIView.as_view(), name='api_qr_generate'),
]
