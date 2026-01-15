import sys
import os

# Add parent directory to path to find 'app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models import User, LeadProfile

app = create_app()

def reset_db():
    with app.app_context():
        # Check if local.db exists and remove it to be extra sure (optional, drop_all handles tables)
        # db_path = 'local.db'
        # if os.path.exists(db_path):
        #     os.remove(db_path)
        #     print(f"Removed {db_path}")

        print("Eliminando todas las tablas...")
        db.drop_all()
        
        print("Creando tablas...")
        db.create_all()
        
        print("Creando usuario administrador por defecto...")
        admin = User(
            username='Admin',
            email='admin@neurops.com',
            role='admin'
        )
        admin.set_password('admin123')
        
        db.session.add(admin)
        db.session.commit()
        
        print("-" * 30)
        print("Base de datos reseteada exitosamente.")
        print(f"Admin User: {admin.email}")
        print(f"Password: admin123")
        print("-" * 30)

if __name__ == '__main__':
    reset_db()
