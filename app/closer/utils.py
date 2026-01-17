import requests
import os
from flask import current_app

def send_calendar_webhook(appointment, action, old_start_time=None):
    """
    Sends appointment data to an external webhook for Google Calendar sync.
    
    Args:
        appointment: The Appointment model instance.
        action: String describing the action ('created', 'rescheduled', 'canceled', 'status_changed').
        old_start_time: (Optional) Datetime of the appointment BEFORE the change (for finding event to update).
    """
    # Dynamic Integration
    from app.models import Integration
    integration = Integration.query.filter_by(key='calendar').first()
    webhook_url = None
    
    if integration:
        if integration.active_env == 'prod':
            webhook_url = integration.url_prod
        else:
            webhook_url = integration.url_dev
            
    # Fallback to Config
    if not webhook_url:
        webhook_url = os.environ.get('CALENDAR_WEBHOOK_URL')

    if not webhook_url:
        print(f"[{action}] Calendar Webhook URL not configured. Skipping sync for Appt ID {appointment.id}")
        return

    # Payload Enrichment
    lead = appointment.lead
    closer = appointment.closer
    lead_profile = lead.lead_profile
    
    payload = {
        'action': action,
        'appointment_id': appointment.id,
        # User Data
        'lead_name': lead.username,
        'lead_email': lead.email,
        'lead_phone': lead_profile.phone if lead_profile else '',
        'lead_instagram': lead_profile.instagram if lead_profile else '',
        'lead_role': lead.role,
        # Closer Data
        'closer_name': closer.username,
        'closer_email': closer.email,
        # Appointment Details
        # Force -04:00 (Bolivia/Local) Timezone for GCal (since app uses naive dates assumed to be local/UTC normalized)
        # Note: appointment.start_time is UTC.
        'start_time': f"{appointment.start_time.isoformat()}Z",
        'time_zone': 'America/La_Paz', # Helpful context
        'status': appointment.status,
        # Context
        'event_name': appointment.event.name if appointment.event else 'General',
        'utm_source': lead_profile.utm_source if lead_profile else 'direct',
        # Changes
        'old_start_time': f"{old_start_time.isoformat()}Z" if old_start_time else None,
        # Helper for Event Title
        'summary': f"{lead.username} y {closer.username}"
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        print(f"Calendar Webhook sent successfully for Appt {appointment.id} [{action}]")
    except Exception as e:
        print(f"Failed to send calendar webhook for Appt {appointment.id}: {e}")

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
