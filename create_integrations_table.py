from app import create_app, db
from app.models import Integration

app = create_app()

with app.app_context():
    print("Creating tables...")
    db.create_all()
    print("Tables created.")
