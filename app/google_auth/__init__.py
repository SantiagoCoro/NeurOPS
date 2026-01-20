from flask import Blueprint

bp = Blueprint('google_auth', __name__)

from app.google_auth import routes
