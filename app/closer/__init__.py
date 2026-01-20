from flask import Blueprint

bp = Blueprint('closer', __name__)

from app.closer import routes

@bp.context_processor
def inject_calendar_status():
    from flask_login import current_user
    from app.models import GoogleCalendarToken
    
    connected = False
    if current_user.is_authenticated:
        connected = GoogleCalendarToken.query.filter_by(user_id=current_user.id).first() is not None
        
    return dict(calendar_connected=connected)
