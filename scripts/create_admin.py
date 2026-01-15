import sys
import os

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import User

app = create_app()

def create_admin():
    with app.app_context():
        # Check if admin exists
        username = input("Admin Username (default: admin): ") or 'admin'
        email = input("Admin Email (default: admin@learnation.com): ") or 'admin@learnation.com'
        
        existing = User.query.filter_by(email=email).first()
        if existing:
            print(f"User with email {email} already exists.")
            return

        password = input("Admin Password (default: admin123): ") or 'admin123'
        
        user = User(username=username, email=email, role='admin')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin user '{username}' created successfully!")

if __name__ == "__main__":
    create_admin()
