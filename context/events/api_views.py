from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from .serializers import QRValidateSerializer
from .models import Booking, Ticket, TicketScanLog
from .services.qr_service import (
    verify_token,
    verify_ticket_signature,
    make_token,
    generate_qr_base64,
    verify_share_token,
)
import hmac
import hashlib
import ipaddress
import json
import logging
import socket
from urllib.parse import urlencode
from urllib.parse import urlsplit, urlunsplit


logger = logging.getLogger(__name__)


def _ticket_details(ticket):
    show_time = ticket.booking.show_time
    return {
        'ticket_id': ticket.ticket_id,
        'booking_id': ticket.booking.booking_id,
        'tickets_booked': ticket.booking.quantity,
        'tickets_available': (
            show_time.available_tickets if show_time else ticket.event.available_tickets
        ),
        'attendee_name': ticket.attendee_name,
        'attendee_email': ticket.attendee_email,
        'event': ticket.event.title,
        'ticket_status': ticket.status,
        'validated_at': ticket.validated_at.isoformat() if ticket.validated_at else None,
    }


def _ticket_scan_base_url(request, validation_path):
    configured_base = settings.QR_PUBLIC_BASE_URL
    if configured_base:
        return f'{configured_base}{validation_path}'

    absolute_url = request.build_absolute_uri(validation_path)
    parsed_url = urlsplit(absolute_url)
    if not settings.DEBUG or parsed_url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        return absolute_url

    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        logger.warning('Could not resolve a LAN address for the mobile QR scan URL.')
        return absolute_url

    for address in addresses:
        candidate = ipaddress.ip_address(address[4][0])
        if candidate.is_private and not candidate.is_loopback and not candidate.is_link_local:
            host = str(candidate)
            if parsed_url.port:
                host = f'{host}:{parsed_url.port}'
            return urlunsplit((parsed_url.scheme, host, parsed_url.path, parsed_url.query, parsed_url.fragment))

    logger.warning('No private LAN address found for the mobile QR scan URL.')
    return absolute_url


class QRValidateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = QRValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        device_info = serializer.validated_data.get('device_info', '')
        remote_addr = request.META.get('REMOTE_ADDR')

        # Basic rate limiting per ticket + IP
        # Parse token to get ticket_id
        try:
            payload, sig = verify_token(token)
            ticket_id = payload.get('ticket_id')
            event_id = payload.get('event_id')
            ts = int(payload.get('ts'))
        except (AttributeError, TypeError, ValueError):
            return Response({'detail': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        if not ticket_id or event_id is None:
            return Response({'detail': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

        # Rate limit key
        rate_key = f'qr_rate:{ticket_id}:{remote_addr}'
        attempts = cache.get(rate_key) or 0
        if attempts >= 10:
            return Response({'detail': 'Too many attempts'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        cache.incr(rate_key) if cache.get(rate_key) else cache.set(rate_key, 1, timeout=60)

        authorized_ticket = Ticket.objects.select_related(
            'event', 'booking', 'booking__show_time'
        ).filter(ticket_id=ticket_id)
        if not request.user.is_superuser:
            authorized_ticket = authorized_ticket.filter(event__organizer=request.user)
        ticket = get_object_or_404(authorized_ticket)

        # Verify event matches
        if str(ticket.event.id) != str(event_id):
            TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Event mismatch')
            return Response({'detail': 'Event mismatch'}, status=status.HTTP_400_BAD_REQUEST)

        if not verify_ticket_signature(ticket, payload, sig, ts):
            TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Bad signature')
            return Response({'detail': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=ticket.booking_id)
            booking_tickets = list(
                Ticket.objects.select_for_update()
                .filter(booking=booking)
                .select_related('event', 'booking', 'booking__show_time')
                .order_by('pk')
            )
            ticket = next(item for item in booking_tickets if item.pk == ticket.pk)
            booking_already_used = any(
                item.status == 'used' or item.qr_status == 'USED'
                for item in booking_tickets
            )

            if booking_already_used:
                TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Already used')
                return Response({
                    'detail': 'ALREADY USED',
                    'ticket': _ticket_details(ticket),
                }, status=status.HTTP_400_BAD_REQUEST)

            if any(item.status == 'cancelled' for item in booking_tickets):
                TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Cancelled')
                return Response({
                    'detail': 'Ticket Cancelled.',
                    'ticket': _ticket_details(ticket),
                }, status=status.HTTP_400_BAD_REQUEST)

            now_ts = int(timezone.now().timestamp())
            if ts > now_ts or now_ts - ts >= settings.QR_REFRESH_INTERVAL:
                TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Token expired')
                return Response({
                    'detail': 'QR Code Expired. Please Scan the Latest QR Code.',
                    'ticket': _ticket_details(ticket),
                }, status=status.HTTP_400_BAD_REQUEST)

            updated_count = Ticket.objects.filter(
                booking=booking,
                status='valid',
            ).exclude(qr_status='USED').update(
                status='used',
                qr_status='USED',
                validated_at=timezone.now(),
                validated_by=request.user,
            )
            eligible_count = sum(
                item.status == 'valid' and item.qr_status != 'USED'
                for item in booking_tickets
            )
            if updated_count != eligible_count:
                TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=False, remote_addr=remote_addr, device_info=device_info, notes='Already used')
                ticket.refresh_from_db()
                return Response({
                    'detail': 'ALREADY USED',
                    'ticket': _ticket_details(ticket),
                }, status=status.HTTP_400_BAD_REQUEST)

            ticket.refresh_from_db()
            TicketScanLog.objects.create(ticket=ticket, scanner=request.user, success=True, remote_addr=remote_addr, device_info=device_info, notes='Validated')

        return Response({
            'detail': 'VALID',
            'ticket': _ticket_details(ticket),
        }, status=status.HTTP_200_OK)


class QRGenerateAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, ticket_id):
        ticket = get_object_or_404(
            Ticket.objects.select_related('event', 'booking'),
            ticket_id=ticket_id,
        )
        is_owner = request.user.is_authenticated and ticket.user_id == request.user.id
        is_shared = verify_share_token(request.query_params.get('share_token', '')) == ticket.ticket_id
        if not (is_owner or is_shared):
            return Response({'detail': 'Authentication or a valid sharing link is required.'}, status=status.HTTP_403_FORBIDDEN)

        if ticket.status != 'valid' or ticket.qr_status == 'USED':
            return Response({
                'detail': 'Ticket Already Used.' if ticket.status == 'used' or ticket.qr_status == 'USED' else 'Ticket Cancelled.',
                'ticket': _ticket_details(ticket),
            }, status=status.HTTP_409_CONFLICT)

        booking_tickets = ticket.booking.tickets.order_by('pk')
        if booking_tickets.filter(status__in=['used', 'cancelled']).exists() or booking_tickets.filter(qr_status='USED').exists():
            return Response({
                'detail': 'Ticket Already Used.' if booking_tickets.filter(
                    status='used'
                ).exists() or booking_tickets.filter(qr_status='USED').exists() else 'Ticket Cancelled.',
                'ticket': _ticket_details(ticket),
            }, status=status.HTTP_409_CONFLICT)

        ticket = booking_tickets.first()
        generated_at = int(timezone.now().timestamp())
        token = make_token(ticket, now_ts=generated_at)
        validation_path = reverse('qr_scan')
        validation_url = _ticket_scan_base_url(
            request,
            validation_path,
        )
        scan_url = f'{validation_url}?{urlencode({"token": token})}'
        image_base64 = generate_qr_base64(scan_url)
        return Response({
            'image_base64': image_base64,
            'token': token,
            'scan_url': scan_url,
            'expires_in': max(
                1,
                generated_at + settings.QR_REFRESH_INTERVAL - int(timezone.now().timestamp()),
            ),
        })
