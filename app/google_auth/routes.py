from flask import Blueprint, redirect, url_for, session, request, current_app, flash, render_template
from flask_login import login_required, current_user
import google_auth_oauthlib.flow
import os
import json
from app import db
from app.models import GoogleCalendarToken

from app.google_auth import bp

# Scopes required
# Scopes required
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly'
]

def get_client_config():
    """Constructs client config from environment variables."""
    return {
        "web": {
            "client_id": os.environ.get("CLIENT_ID"),
            "client_project_id": "neuropops-calendar", # Optional placeholder
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": os.environ.get("CLIENT_SECRET"),
            "redirect_uris": [
                # Ideally this matches what's registered. 
                # We will construct the redirect_uri dynamically in the flow.
            ]
        }
    }

@bp.route('/authorize')
@login_required
def authorize():
    # Detect environment for redirect URI
    # In production (Railway), we might be behind a proxy (checked via HTTP_X_FORWARDED_PROTO usually)
    # But safer to assume if 'localhost' in url_root -> http, else https (usually)
    # Or just rely on url_for to build it based on request context
    
    # Force HTTPS in production if needed, or rely on Flask's ProxyFix/config
    redirect_uri = url_for('google_auth.callback', _external=True)
    
    # Force HTTPS for non-local environments (Railway/Production)
    if 'localhost' not in redirect_uri and '127.0.0.1' not in redirect_uri and not redirect_uri.startswith('https'):
        redirect_uri = redirect_uri.replace('http:', 'https:')
    
    # For local testing if http
    if 'localhost' in redirect_uri or '127.0.0.1' in redirect_uri:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
    client_config = get_client_config()
    
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        client_config,
        scopes=SCOPES
    )
    flow.redirect_uri = redirect_uri
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # Force consent to ensure refresh token is returned
    )
    
    session['google_auth_state'] = state
    return redirect(authorization_url)

@bp.route('/callback')
@login_required
def callback():
    state = session.get('google_auth_state')
    
    redirect_uri = url_for('google_auth.callback', _external=True)
    
    # Force HTTPS for non-local environments (Railway/Production)
    if 'localhost' not in redirect_uri and '127.0.0.1' not in redirect_uri and not redirect_uri.startswith('https'):
        redirect_uri = redirect_uri.replace('http:', 'https:')

    if 'localhost' in redirect_uri or '127.0.0.1' in redirect_uri:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    client_config = get_client_config()
    
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = redirect_uri
    
    # Fetch token
    authorization_response = request.url
    # Fix for http vs https mismatch in some proxy envs:
    if request.scheme == 'http' and 'https' in redirect_uri:
        authorization_response = authorization_response.replace('http:', 'https:')

    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception as e:
        flash(f'Error de autenticación: {e}')
        return redirect(url_for('admin.dashboard')) # Fallback
        
    credentials = flow.credentials
    
    # Save to DB
    # Check if exists
    token_entry = GoogleCalendarToken.query.filter_by(user_id=current_user.id).first()
    if not token_entry:
        token_entry = GoogleCalendarToken(user_id=current_user.id)
        db.session.add(token_entry)
        
    # Serialize credentials to JSON
    creds_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    # Note: refresh_token might be None if user already authorized and prompt!='consent', 
    # but we forced prompt='consent'.
    
    token_entry.token_json = json.dumps(creds_data)
    db.session.commit()
    
    flash('Google Calendar conectado exitosamente.')
    return redirect(url_for('closer.dashboard')) 

from app.google_auth.utils import get_calendar_service

@bp.route('/select-calendar', methods=['GET', 'POST'])
@login_required
def select_calendar():
    service = get_calendar_service(current_user.id)
    if not service:
        flash('Primero debes conectar tu cuenta de Google.', 'warning')
        return redirect(url_for('google_auth.authorize'))
    
    token = current_user.google_token

    if request.method == 'POST':
        selected_id = request.form.get('calendar_id')
        if selected_id:
            token.google_calendar_id = selected_id
            db.session.commit()
            flash('Calendario predeterminado actualizado.', 'success')
            return redirect(url_for('closer.dashboard'))
    
    try:
        # List calendars
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])
        
        return render_template('closer/select_calendar.html', calendars=calendars, current_selection=token.google_calendar_id)
        
    except Exception as e:
        flash(f'Error al obtener calendarios: {str(e)}', 'error')
        return redirect(url_for('closer.dashboard'))
