from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from config import Config

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'auth.login'
login.login_message = 'Por favor inicia sesión para acceder a esta página.'

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Enable CSRF Protection globally
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)

    # Init extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # Register Blueprints
    from app.routes import main
    app.register_blueprint(main)

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.closer import bp as closer_bp
    app.register_blueprint(closer_bp, url_prefix='/closer')
    
    from app.booking import bp as booking_bp
    app.register_blueprint(booking_bp)

    return app
