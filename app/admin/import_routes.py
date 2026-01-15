import csv
import io
from flask import render_template, redirect, url_for, flash, request, send_file
from werkzeug.utils import secure_filename
from app.admin import bp
from app.admin.routes import admin_required
from app import db
from app.models import User, LeadProfile, Program, Enrollment, Payment, PaymentMethod
from datetime import datetime
from sqlalchemy import or_

@bp.route('/import/dashboard')
@admin_required
def import_dashboard():
    return render_template('admin/import_dashboard.html')

@bp.route('/import/programs', methods=['POST'])
@admin_required
def import_programs():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('admin.import_dashboard'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('admin.import_dashboard'))

    if file:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        count = 0
        updated = 0
        
        for row in csv_input:
            name = row.get('name')
            try:
                price = float(row.get('price'))
            except ValueError:
                price = 0.0
                
            if not name:
                continue
                
            program = Program.query.filter_by(name=name).first()
            if not program:
                program = Program(name=name, price=price, is_active=True)
                db.session.add(program)
                count += 1
            else:
                # Update price if it was 0? or just keep existing?
                # Let's update only if price > 0 and current is 0?
                # User didn't specify, but for import usually we trust the file or skip.
                # Let's skip update unless needed.
                pass
        
        db.session.commit()
        flash(f'Importados {count} nuevos programas.')
        
    return redirect(url_for('admin.import_dashboard'))

@bp.route('/import/agendas', methods=['POST'])
@admin_required
def import_agendas():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('admin.import_dashboard'))
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('admin.import_dashboard'))
    
    if file:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        count_created = 0
        count_updated = 0
        
        for row in csv_input:
            email = row.get('email', '').strip().lower()
            if not email:
                continue
                
            username = row.get('username', '').strip()
            created_at_str = row.get('created_at')
            phone = row.get('phone', '')
            instagram = row.get('instagram', '')
            
            created_at = datetime.utcnow()
            if created_at_str:
                try:
                    created_at = datetime.strptime(created_at_str, '%Y-%m-%d')
                except ValueError:
                    pass
            
            user = User.query.filter_by(email=email).first()
            if not user:
                base_username = username or email.split('@')[0]
                clean_username = base_username
                counter = 1
                
                # Ensure username is unique for NEW users
                while User.query.filter_by(username=clean_username).first():
                    clean_username = f"{base_username}{counter}"
                    counter += 1
                
                user = User(
                    username=clean_username,
                    email=email,
                    role='lead',
                    created_at=created_at
                )
                user.set_password('temporal123') # Default password
                db.session.add(user)
                count_created += 1
            else:
                # Update created_at if the new one is older (trusting Agendas as source of truth for registration)
                if created_at < user.created_at:
                    user.created_at = created_at
                count_updated += 1

            db.session.flush() # Get ID
            
            # Profile
            # Commit in batches to avoid locking the DB for too long
            if (count_created + count_updated) % 50 == 0:
                db.session.commit()

            if not user.lead_profile:
                profile = LeadProfile(
                    user_id=user.id,
                    phone=phone,
                    instagram=instagram,
                    status='new'
                )
                db.session.add(profile)
            else:
                # Update contact info if missing
                if phone and not user.lead_profile.phone:
                    user.lead_profile.phone = phone
                if instagram and not user.lead_profile.instagram:
                    user.lead_profile.instagram = instagram

        db.session.commit()
        flash(f'Agendas importadas: {count_created} nuevos, {count_updated} actualizados.')
        return redirect(url_for('admin.import_dashboard'))

@bp.route('/import/users', methods=['POST'])
@admin_required
def import_users():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('admin.import_dashboard'))
    
    file = request.files['file']
    if file:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        count_new = 0
        count_updated = 0
        
        for row in csv_input:
            email = row.get('email', '').strip().lower()
            if not email:
                continue
            
            user = User.query.filter_by(email=email).first()
            username = row.get('username')
            role = row.get('role', 'lead')
            phone = row.get('phone')
            instagram = row.get('instagram')
            
            if not user:
                # Create User
                user = User(
                    username=username,
                    email=email,
                    role=role
                )
                
                # Handle Duplicate Username
                base_username = username
                counter = 1
                while User.query.filter_by(username=username).first() is not None:
                    username = f"{base_username} {counter}"
                    counter += 1
                
                user.username = username
                
                user.set_password('temp1234') # Temporary password
                db.session.add(user)
                db.session.flush() # get ID
                
                profile = LeadProfile(
                    user_id=user.id,
                    phone=phone,
                    instagram=instagram,
                    status='new', 
                    utm_source='import'
                )
                db.session.add(profile)
                count_new += 1
            else:
                # Update existing?
                # Maybe update role if currently lead and import says student?
                if user.role != 'student' and role == 'student':
                    user.role = 'student'
                    # Status update removed; will be handled by payments or manual update
                    count_updated += 1
                    
                # Update phone if missing
                profile = LeadProfile.query.filter_by(user_id=user.id).first()
                if profile:
                    if not profile.phone and phone:
                        profile.phone = phone
                        count_updated += 1
                if instagram and not user.lead_profile.instagram:
                    user.lead_profile.instagram = instagram

            # Commit in batches
            if (count_new + count_updated) % 50 == 0:
                db.session.commit()
        
        db.session.commit()
        flash(f'Usuarios procesados. Nuevos: {count_new}, Actualizados: {count_updated}')
        
    return redirect(url_for('admin.import_dashboard'))

@bp.route('/import/payments', methods=['POST'])
@admin_required
def import_payments():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('admin.import_dashboard'))
    
    file = request.files['file']
    if file:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        count_payments = 0
        count_enrollments = 0
        
        # Ensure default payment method exists
        pm_import = PaymentMethod.query.filter_by(name='Imported').first()
        if not pm_import:
            pm_import = PaymentMethod(name='Imported')
            db.session.add(pm_import)
            db.session.commit()
            
        # Track affected users to update status later
        affected_user_ids = set()

        for row in csv_input:
            email = row.get('email', '').strip().lower()
            program_name = row.get('program')
            amount = float(row.get('amount', 0))
            date_str = row.get('date')
            p_type = row.get('type')
            
            if not email or not program_name:
                continue
                
            user = User.query.filter_by(email=email).first()
            if not user:
                # Should have been imported first
                continue
                
            program = Program.query.filter_by(name=program_name).first()
            if not program:
                # Should have been imported first, but we can try to find/create or skip
                continue
            
            # Find Enrollment
            enrollment = Enrollment.query.filter_by(student_id=user.id, program_id=program.id).first()
            if not enrollment:
                enrollment = Enrollment(
                    student_id=user.id,
                    program_id=program.id,
                    status='active', # Default
                    enrollment_date=datetime.now(), # Rough approx if not in CSV users
                    total_agreed=program.price
                )
                db.session.add(enrollment)
                db.session.flush()
                count_enrollments += 1
            
            affected_user_ids.add(user.id)

            # Create Payment
            # Check duplicates? Same amount, same date, same enrollment?
            # Date from CSV is YYYY-MM-DD
            try:
                pay_date = datetime.strptime(date_str, '%Y-%m-%d')
            except:
                pay_date = datetime.now()
                
            # Basic duplicate check
            exists = Payment.query.filter_by(
                enrollment_id=enrollment.id,
                amount=amount,
                payment_type=p_type
            ).filter(db.func.date(Payment.date) == pay_date.date()).first()
            
            if not exists:
                payment = Payment(
                    enrollment_id=enrollment.id,
                    payment_method_id=pm_import.id,
                    amount=amount,
                    date=pay_date,
                    payment_type=p_type,
                    status='completed',
                    reference_id='IMPORT'
                )
                db.session.add(payment)
                count_payments += 1
                # Batch commit
                if count_payments % 50 == 0:
                    db.session.commit()
        
        # Update Statuses for affected users
        for uid in affected_user_ids:
            u = User.query.get(uid)
            if not u: continue
            
            if not u.lead_profile:
                 profile = LeadProfile(user_id=u.id, status='new')
                 db.session.add(profile)
                 u.lead_profile = profile

            # Calculate logic
            # 1. Total Debt across all active enrollments
            total_agreed = db.session.query(db.func.sum(Enrollment.total_agreed)).filter_by(student_id=u.id, status='active').scalar() or 0
            
            # 2. Total Paid across all active enrollments
            total_paid = db.session.query(db.func.sum(Payment.amount)).join(Enrollment).filter(
                Enrollment.student_id == u.id,
                Enrollment.status == 'active',
                Payment.status == 'completed'
            ).scalar() or 0
            
            debt = total_agreed - total_paid
            
            # 3. Check for Renewal Payment
            # Check if ANY payment for this user has type 'Renovación' (case insensitive roughly)
            has_renewal = db.session.query(Payment.id).join(Enrollment).filter(
                Enrollment.student_id == u.id,
                Payment.status == 'completed',
                or_(Payment.payment_type.ilike('Renovación'), Payment.payment_type.ilike('Renovacion'))
            ).first() is not None

            new_status = u.lead_profile.status
            
            if debt > 0:
                new_status = 'pending'
            elif has_renewal:
                new_status = 'renewed' # "Renovado" mapped to 'renewed'
            elif debt <= 0:
                 new_status = 'completed' # "Completo" mapped to 'completed'
            
            u.lead_profile.status = new_status
                
        db.session.commit()
        flash(f'Importación completada. Enrollments creados: {count_enrollments}, Pagos creados: {count_payments}, Usuarios actualizados: {len(affected_user_ids)}')
        
    return redirect(url_for('admin.import_dashboard'))
