from app.google_auth.utils import get_calendar_service
from app import db
from datetime import timedelta

def send_calendar_webhook(appointment, action, old_start_time=None):
    """
    Syncs appointment with Google Calendar (Closer's Account).
    Replaces old n8n webhook.
    
    Args:
        appointment: Appointment model instance
        action: 'created', 'rescheduled', 'canceled', 'status_changed'
        old_start_time: Not used for direct API update usually, but kept for signature compatibility
    """
    service = get_calendar_service(appointment.closer_id)
    print(f"DEBUG: Starting GCal Sync for Appt {appointment.id} (Closer {appointment.closer_id})", flush=True)
    
    if not service:
        print(f"Skipping Calendar Sync: No Google Token for Closer {appointment.closer_id}", flush=True)
        return

    # Helper to format time as UTC ISO for Google
    def to_iso(dt):
        return dt.isoformat() + 'Z'

    try:
        # Determine duration (Default 45 mins per previous guide, or use Event settings if available)
        duration_minutes = 45 
        # If we had appointment.end_time we would use it.
        
        start_dt = appointment.start_time
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        
        lead = appointment.lead
        closer = appointment.closer
        profile = lead.lead_profile
        
        summary = f"Cita: {lead.username} con {closer.username}"
        description = (
            f"Cliente: {lead.username}\n"
            f"Email: {lead.email}\n"
            f"Tel: {profile.phone if profile else 'N/A'}\n"
            f"Rol: {lead.role}\n"
            f"Estado: {appointment.status}"
        )
        
        print(f"DEBUG: Adding attendee {lead.email} to event", flush=True)

        event_body = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': to_iso(start_dt),
                'timeZone': 'UTC', 
            },
            'end': {
                'dateTime': to_iso(end_dt),
                'timeZone': 'UTC',
            },
            'attendees': [
                {'email': lead.email, 'responseStatus': 'needsAction'},
                # Closer is organizer, implied attendee usually, or add explicit:
                # {'email': closer.email, 'responseStatus': 'accepted'} 
            ],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 30},
                ],
            },
        }

        # Handling Actions
        # Get preferred calendar or default to primary
        target_calendar_id = 'primary'
        if appointment.closer.google_token and appointment.closer.google_token.google_calendar_id:
            target_calendar_id = appointment.closer.google_token.google_calendar_id

        if action == 'canceled' or appointment.status == 'canceled':
            if appointment.google_event_id:
                try:
                    service.events().delete(calendarId=target_calendar_id, eventId=appointment.google_event_id).execute()
                    appointment.google_event_id = None
                    db.session.commit()
                    print(f"GCal Event deleted for Appt {appointment.id}", flush=True)
                except Exception as e:
                    print(f"Error deleting GCal event: {e}", flush=True)
            return

        # Create or Update
        if appointment.google_event_id:
            try:
                # Try update
                service.events().patch(
                    calendarId=target_calendar_id, 
                    eventId=appointment.google_event_id, 
                    body=event_body,
                    sendUpdates='all'
                ).execute()
                print(f"GCal Event updated for Appt {appointment.id}", flush=True)
            except Exception as e:
                # If 404/Gone, recreate?
                print(f"Error updating GCal event (might be deleted), trying recreate: {e}", flush=True)
                # Fallback to create
                new_event = service.events().insert(
                    calendarId=target_calendar_id, 
                    body=event_body,
                    sendUpdates='all'
                ).execute()
                appointment.google_event_id = new_event.get('id')
                db.session.commit()
        else:
            # Create
            new_event = service.events().insert(
                calendarId=target_calendar_id, 
                body=event_body,
                sendUpdates='all'
            ).execute()
            appointment.google_event_id = new_event.get('id')
            db.session.commit()
            print(f"GCal Event created for Appt {appointment.id}", flush=True)

    except Exception as e:
        print(f"Critical GCal Sync Error for Appt {appointment.id}: {e}")

def send_sales_webhook(payment, closer_name):
    """
    Sends sales data to an external webhook (e.g. n8n).
    
    Data: Client, Closer, Amount, Cash Collect (Net), Payment Type, Program, Method
    """
    # Fetch dynamic integration
    from app.models import Integration
    
    integration = Integration.query.filter_by(key='sales').first()
    webhook_url = None
    
    if integration:
        if integration.active_env == 'prod':
            webhook_url = integration.url_prod
        else:
            webhook_url = integration.url_dev
            
    # Fallback to Config if DB entry not found or empty (though we create it on admin view)
    if not webhook_url:
        webhook_url = current_app.config.get('VENTAS_WEBHOOK')

    if not webhook_url:
        print("Sales Webhook URL not configured (DB or Config).", flush=True)
        return
    
    print(f"Sales Webhook initiated for {payment.payment_type_label}", flush=True)

    # Calculate Cash Collect (Amount - Commission)
    # Commission = (Amount * % + Fixed)
    if payment.method:
        # Handle potential None from DB
        pct = payment.method.commission_percent or 0.0
        fixed = payment.method.commission_fixed or 0.0
        commission = (payment.amount * (pct / 100)) + fixed
    else:
        commission = 0.0
        
    cash_collect = payment.amount - commission

    # Get phone safely
    student = payment.enrollment.student
    phone = student.lead_profile.phone if student.lead_profile else ''
    first_name = student.username.split(' ')[0] if student.username else ''

    payload = {
        'cliente': student.username,
        'first_name': first_name,
        'telefono': phone,
        'email': student.email,
        'closer': closer_name,
        'monto': payment.amount,
        'cash_collect': round(cash_collect, 2),
        'tipo_pago': payment.payment_type_label,
        'programa': payment.enrollment.program.name,
        'metodo_pago': payment.method.name if payment.method else 'Desconocido',
        'fecha': payment.date.isoformat(),
        'transaction_id': payment.transaction_id or '',
        'comision': round(commission, 2),
        'valor_programa': payment.enrollment.total_agreed
    }
    
    # Debug log
    # Debug log
    print(f"Sending Webhook Payload: {payload}", flush=True)

    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        print(f"Sales Webhook sent successfully for Payment {payment.id}", flush=True)
    except Exception as e:
        print(f"Failed to send sales webhook for Payment {payment.id}: {e}", flush=True)
