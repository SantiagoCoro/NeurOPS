import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models import User, LeadProfile, Enrollment, Payment, Program, PaymentMethod, ROLE_LEAD
from datetime import datetime
import uuid

app = create_app()

def test_status_logic():
    with app.app_context():
        # Setup Data
        email = f"test_status_{uuid.uuid4()}@example.com"
        username = f"testUser_{uuid.uuid4().hex[:6]}"
        
        print(f"[TEST] Creating User {username}...")
        user = User(username=username, email=email, role=ROLE_LEAD)
        db.session.add(user)
        db.session.commit()
        
        # 1. New Status (No Enrollment)
        print("[TEST] Checking Initial Status (Should be New if logic run, or None initially)...")
        # Ensure profile exists
        if not user.lead_profile:
             profile = LeadProfile(user_id=user.id, status='new')
             db.session.add(profile)
             db.session.commit()
        
        user.update_status_based_on_debt()
        print(f"[RESULT] Status: {user.lead_profile.status}")
        assert user.lead_profile.status == 'new'
        
        # 2. Add Enrollment + Payment (Full) -> Completed
        print("[TEST] Adding Enrollment (Program Price 100) and Full Payment...")
        program = Program.query.first() 
        if not program:
            program = Program(name=f"TestProg_{uuid.uuid4().hex[:6]}", price=100.0)
            db.session.add(program)
            db.session.commit()
            
        enrollment = Enrollment(student_id=user.id, program_id=program.id, total_agreed=100.0, status='active')
        db.session.add(enrollment)
        db.session.flush()
        
        payment = Payment(enrollment_id=enrollment.id, amount=100.0, status='completed', payment_type='full')
        db.session.add(payment)
        db.session.commit()
        
        user.update_status_based_on_debt()
        print(f"[RESULT] Status after Full Pay: {user.lead_profile.status}")
        assert user.lead_profile.status == 'completed'
        
        # 3. Delete Payment -> New (because Enrollment should be auto-deleted if orphan)
        print("[TEST] Deleting Payment (Should auto-delete orphan enrollment and set New)...")
        db.session.delete(payment)
        # We need to simulate the route logic here because the model doesn't delete enrollment automatically, the ROUTE does.
        # So we manually delete enrollment here to match route logic for the test, 
        # OR we accept that this unit test tests the MODEL method `update_status_based_on_debt` given a state.
        # To test the route logic we'd need a client test. 
        # But let's simulate what the route does:
        if enrollment.payments.count() == 0:
            db.session.delete(enrollment)
            
        db.session.commit()
        
        user.update_status_based_on_debt()
        print(f"[RESULT] Status after Delete Payment + Auto cleanup: {user.lead_profile.status}")
        assert user.lead_profile.status == 'new'
        
        # 4. Explicit Enrollment Deletion Test (for Admin route coverage)
        print("[TEST] Create Enrollment only -> Delete Enrollment...")
        enrollment2 = Enrollment(student_id=user.id, program_id=program.id, total_agreed=100.0, status='active')
        db.session.add(enrollment2)
        db.session.commit()
        
        # Should initiate as New or Pending? Since debt > 0 (100 agreed - 0 paid) -> Pending check
        user.update_status_based_on_debt() 
        assert user.lead_profile.status == 'pending'
        
        # Delete Enrollment
        db.session.delete(enrollment2)
        db.session.commit()
        
        user.update_status_based_on_debt()
        print(f"[RESULT] Status after Explict Enrollment Delete: {user.lead_profile.status}")
        assert user.lead_profile.status == 'new'
        
        # Cleanup
        print("[TEST] Cleaning up...")
        db.session.delete(user)
        if user.lead_profile: db.session.delete(user.lead_profile)
        db.session.commit()
        print("[TEST] Success!")

if __name__ == "__main__":
    try:
        test_status_logic()
    except Exception as e:
        print(f"[ERROR] {e}")
        exit(1)
