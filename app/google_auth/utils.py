from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from flask import current_app
import json
from app import db
from app.models import GoogleCalendarToken

def get_calendar_service(user_id):
    """
    Retrieves an authenticated Google Calendar Service for the given user.
    Refreshes the token if expired.
    Returns None if no token or error.
    """
    token_entry = GoogleCalendarToken.query.filter_by(user_id=user_id).first()
    if not token_entry:
        return None
        
    try:
        creds_data = json.loads(token_entry.token_json)
        
        credentials = Credentials(
            token=creds_data.get('token'),
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes')
        )
        
        # Check expiry and refresh
        # Credentials object handles expiry check internally if we use Request?
        # Actually .valid or .expired properties.
        # But to refresh we need a Request object.
        from google.auth.transport.requests import Request
        
        if not credentials.valid:
             if credentials.expired and credentials.refresh_token:
                 try:
                     credentials.refresh(Request())
                     # Update DB with new token
                     creds_data['token'] = credentials.token
                     # refresh_token usually stays same, but if changed update it
                     if credentials.refresh_token:
                         creds_data['refresh_token'] = credentials.refresh_token
                         
                     token_entry.token_json = json.dumps(creds_data)
                     db.session.commit()
                 except Exception as e:
                     print(f"Error refreshing token for user {user_id}: {e}")
                     return None
             else:
                 # Invalid and no refresh?
                 return None
                 
        service = build('calendar', 'v3', credentials=credentials)
        return service
        
    except Exception as e:
        print(f"Error building calendar service for user {user_id}: {e}")
        return None
