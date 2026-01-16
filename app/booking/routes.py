from flask import render_template, redirect, url_for, flash, request, session
from app.booking import bp
from app import db
from app.models import User, LeadProfile, Event, Availability, Appointment, SurveyQuestion, SurveyAnswer
from werkzeug.security import generate_password_hash
import uuid
from datetime import datetime, timedelta, date, time
from sqlalchemy import or_
from app.closer.utils import send_calendar_webhook

@bp.route('/booking', methods=['GET'])
def start_booking():
    """Entry Point: Initializes the booking flow based on Event/Group settings."""
    utm_source = request.args.get('utm_source', 'direct')
    
    # 1. Identify Event & Funnel Steps
    event = Event.query.filter_by(utm_source=utm_source).first()
    funnel_steps = ['contact', 'calendar', 'survey'] # Default
    
    # session.clear() -> This logs out admins! Use selective clear.
    keys_to_clear = ['booking_data', 'booking_event_id', 'funnel_steps', 'funnel_index', 'booking_user_id', 'current_appt_id']
    for k in keys_to_clear:
        session.pop(k, None)
        
    session['booking_utm'] = utm_source
    
    # Init booking data container
    session['booking_data'] = {
        'answers': [],  # list of {question_id, answer}
        'slot': None    # {date, time, closer_id}
    }

    if event:
        session['booking_event_id'] = event.id
        if event.funnel_steps:
             funnel_steps = event.funnel_steps
        elif event.group and event.group.funnel_steps:
             funnel_steps = event.group.funnel_steps
    else:
        # Check if global group has defaults? For now uses hardcoded default.
        pass

    session['funnel_steps'] = funnel_steps
    session['funnel_index'] = 0
    
    return redirect(url_for('booking.handle_flow'))

@bp.route('/booking/flow')
def handle_flow():
    """Router: Redirects to the current step's view."""
    steps = session.get('funnel_steps', ['contact', 'calendar', 'survey'])
    index = session.get('funnel_index', 0)
    
    if index >= len(steps):
        # Done!
        return redirect(url_for('booking.thank_you'))
        
    current_step = steps[index]
    
    if current_step == 'contact':
        return redirect(url_for('booking.contact_view'))
    elif current_step == 'calendar':
        return redirect(url_for('booking.calendar_view'))
    elif current_step == 'survey':
        return redirect(url_for('booking.survey_view'))
    else:
        # Unknown step, skip
        session['funnel_index'] = index + 1
        return redirect(url_for('booking.handle_flow'))

@bp.route('/booking/next')
def next_step():
    """Increment step and route."""
    session['funnel_index'] = session.get('funnel_index', 0) + 1
    return redirect(url_for('booking.handle_flow'))

@bp.route('/booking/contact', methods=['GET', 'POST'])
def contact_view():
    # Helper to clean phone
    def clean_phone(code, number):
        if not number: return None
        return f"{code} {number}".strip()

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone_code = request.form.get('phone_code')
        phone_number = request.form.get('phone')
        instagram = request.form.get('instagram')
        
        full_phone = clean_phone(phone_code, phone_number)

        if not email or not name:
            flash('Nombre y Correo son obligatorios.')
            return render_template('booking/landing.html', utm=session.get('booking_utm'))

        # Create/Find User
        user = User.query.filter_by(email=email).first()
        utm_source = session.get('booking_utm', 'direct')
        
        if not user:
            temp_pass = str(uuid.uuid4())
            base_username = email
            if len(base_username) > 64:
                base_username = base_username[:64]
            
            username = base_username
            # Ensure uniqueness
            while User.query.filter_by(username=username).first():
                import random
                suffix = f"_{random.randint(1000, 9999)}"
                # Ensure suffix fits
                if len(base_username) + len(suffix) > 64:
                    username = base_username[:64-len(suffix)] + suffix
                else:
                    username = base_username + suffix
            
            user = User(username=username, email=email, role='lead')
            user.set_password(temp_pass)
            db.session.add(user)
            db.session.flush()
            
            # New leads start as 'new' status
            profile = LeadProfile(user_id=user.id, phone=full_phone, instagram=instagram, utm_source=utm_source, status='new')
            db.session.add(profile)
            db.session.commit()
        else:
            if user.lead_profile:
                if full_phone: user.lead_profile.phone = full_phone
                if instagram: user.lead_profile.instagram = instagram
                user.lead_profile.utm_source = utm_source 
            else:
                profile = LeadProfile(user_id=user.id, phone=full_phone, instagram=instagram, utm_source=utm_source, status='new')
                db.session.add(profile)
            
            # Update name if provided
            if name: user.username = name
            db.session.commit()
            
        session['booking_user_id'] = user.id
        
        # FLUSH CACHED DATA (If any from previous steps, though unlikely here)
        _flush_session_data(user.id)
        
        return redirect(url_for('booking.next_step'))
        
    return render_template('booking/landing.html', utm=session.get('booking_utm'))

def _flush_session_data(user_id):
    """Saves cached slot/answers to DB for this user."""
    bdata = session.get('booking_data', {})
    
    # 1. Flush Slot -> Appointment
    slot = bdata.get('slot')
    if slot:
        # Re-check availability? strictly yes
        # For MVP, assume still free or fail gracefully.
        
        start_time = datetime.strptime(f"{slot['date']} {slot['time']}", "%Y-%m-%d %H:%M:%S")
        
        # Check duplicate
        exists = Appointment.query.filter_by(closer_id=slot['closer_id'], start_time=start_time).filter(Appointment.status!='canceled').first()
        if not exists:
            appt = Appointment(
                closer_id=slot['closer_id'],
                lead_id=user_id,
                start_time=start_time,
                status='scheduled', # or pending_survey? if survey not done yet
                event_id=session.get('booking_event_id')
            )
            db.session.add(appt)
            db.session.commit()
            session['current_appt_id'] = appt.id
            
            # Trigger Webhook
            send_calendar_webhook(appt, 'created')
            
            # Update User Status to 'agenda' (but keep role as 'lead')
            user = User.query.get(user_id)
            if user and user.lead_profile:
                user.lead_profile.status = 'agenda'
                db.session.add(user.lead_profile)
                db.session.commit()
            
            # Clear slot from session
            bdata['slot'] = None
            session['booking_data'] = bdata

    # 2. Flush Answers -> SurveyAnswer
    answers = bdata.get('answers')
    if answers:
        if 'current_appt_id' in session:
            appt_id = session['current_appt_id']
        else:
            appt_id = None # Should we link to existing appointment?
            # Start logic: if user has an upcoming appointment, maybe link?
            # For dynamic flow, usually appointment is created in same session.
        
        for ans in answers:
            new_ans = SurveyAnswer(
                lead_id=user_id,
                question_id=ans['question_id'],
                answer=ans['answer'],
                appointment_id=appt_id
            )
            db.session.add(new_ans)
        
        db.session.commit()
        bdata['answers'] = []
        session['booking_data'] = bdata

@bp.route('/booking/calendar')
def calendar_view():
    # Logic same as before for fetching slots
    today = date.today()
    end_date = today + timedelta(days=14)
    availabilities = Availability.query.filter(Availability.date >= today, Availability.date <= end_date).all()
    appointments = Appointment.query.filter(
        Appointment.start_time >= datetime.combine(today, time.min),
        Appointment.start_time <= datetime.combine(end_date, time.max),
        Appointment.status != 'canceled'
    ).all()
    
    booked_slots = set()
    for appt in appointments:
        booked_slots.add((appt.closer_id, appt.start_time))
        
    daily_slots = {}
    for i in range(15):
        d = today + timedelta(days=i)
        daily_slots[d] = set()
    
    for av in availabilities:
        slot_dt = datetime.combine(av.date, av.start_time)
        if slot_dt < datetime.now(): continue
        if (av.closer_id, slot_dt) not in booked_slots:
            daily_slots[av.date].add(av.start_time)
            
    sorted_schedule = []
    days_map = {0:'Lunes', 1:'Martes', 2:'Miércoles', 3:'Jueves', 4:'Viernes', 5:'Sábado', 6:'Domingo'}
    months_map = {1:'Enero', 2:'Febrero', 3:'Marzo', 4:'Abril', 5:'Mayo', 6:'Junio', 7:'Julio', 8:'Agosto', 9:'Septiembre', 10:'Octubre', 11:'Noviembre', 12:'Diciembre'}

    for d in sorted(daily_slots.keys()):
        times = sorted(list(daily_slots[d]))
        if times:
            display_str = f"{days_map[d.weekday()]} {d.day} de {months_map[d.month]}"
            sorted_schedule.append({'date': d, 'display': display_str, 'slots': times})
            
    return render_template('booking/calendar.html', schedule=sorted_schedule)

@bp.route('/booking/select', methods=['POST'])
def select_slot():
    date_str = request.form.get('date')
    time_str = request.form.get('time')
    selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    selected_time = datetime.strptime(time_str, '%H:%M').time()
    
    # Find closer
    candidates = Availability.query.filter_by(date=selected_date, start_time=selected_time).all()
    chosen_closer_id = None
    for cand in candidates:
        appt_time = datetime.combine(selected_date, selected_time)
        conflict = Appointment.query.filter_by(closer_id=cand.closer_id, start_time=appt_time).filter(Appointment.status != 'canceled').first()
        if not conflict:
            chosen_closer_id = cand.closer_id
            break
            
    if not chosen_closer_id:
        flash('Lo sentimos, este horario acaba de ser ocupado.')
        return redirect(url_for('booking.calendar_view'))
        
    # Check if User exists
    user_id = session.get('booking_user_id')
    print(f"DEBUG: select_slot user_id={user_id}")
    
    if user_id:
        # Create immediately
        appt = Appointment(
            closer_id=chosen_closer_id,
            lead_id=user_id,
            start_time=datetime.combine(selected_date, selected_time),
            status='scheduled',
            event_id=session.get('booking_event_id')
        )
        db.session.add(appt)
        db.session.commit()
        session['current_appt_id'] = appt.id
        
        # Update User Status to 'agenda'
        user = User.query.get(user_id)
        if user:
            print(f"DEBUG: User found {user.username}. Profile: {user.lead_profile}")
            if user.lead_profile:
                print(f"DEBUG: Updating status from {user.lead_profile.status} to agenda")
                user.lead_profile.status = 'agenda'
                db.session.add(user.lead_profile)
                db.session.commit()
                print("DEBUG: Status committed")
            else:
                 print("DEBUG: No lead_profile found")
        else:
             print("DEBUG: User not found in DB")
            
        send_calendar_webhook(appt, 'created')
    else:
        # Cache in Session
        bdata = session.get('booking_data', {})
        bdata['slot'] = {
            'date': date_str,
            'time': f"{selected_time.hour}:{selected_time.minute:02d}:00", # Stringify
            'closer_id': chosen_closer_id
        }
        session['booking_data'] = bdata
    
    return redirect(url_for('booking.next_step'))

@bp.route('/booking/survey', methods=['GET', 'POST'])
def survey_view():
    # Fetch questions
    query = SurveyQuestion.query.filter_by(is_active=True, step='survey')
    evt_id = session.get('booking_event_id')
    if evt_id:
        # ... (Same filter logic as before) ...
        # Simplified for brevity in this replace block, but must match logic
        event = Event.query.get(evt_id)
        conditions = [SurveyQuestion.event_id == evt_id]
        if event.group_id: conditions.append(SurveyQuestion.event_group_id == event.group_id)
        conditions.append((SurveyQuestion.event_id == None) & (SurveyQuestion.event_group_id == None))
        query = query.filter(or_(*conditions))
    else:
        query = query.filter((SurveyQuestion.event_id == None) & (SurveyQuestion.event_group_id == None))
        
    questions = query.order_by(SurveyQuestion.order).all()
    
    if request.method == 'POST':
        user_id = session.get('booking_user_id')
        appt_id = session.get('current_appt_id')
        
        # Collect answers
        answers_data = [] # List of {q_id, ans}
        for q in questions:
            ans_text = request.form.get(f'q_{q.id}')
            if ans_text:
                answers_data.append({'question_id': q.id, 'answer': ans_text})
        
        if user_id:
            # Save immediately
            for item in answers_data:
                ans = SurveyAnswer(lead_id=user_id, question_id=item['question_id'], answer=item['answer'], appointment_id=appt_id)
                db.session.add(ans)
            db.session.commit()
        else:
            # Cache
            bdata = session.get('booking_data', {})
            # Merge with existing?
            existing = bdata.get('answers', [])
            existing.extend(answers_data)
            bdata['answers'] = existing
            session['booking_data'] = bdata

        return redirect(url_for('booking.next_step'))
        
    return render_template('booking/survey.html', questions=questions)

@bp.route('/booking/thankyou')
def thank_you():
    utm_source = session.get('booking_utm', 'direct')
    # Loop back to start
    return redirect(url_for('booking.start_booking', utm_source=utm_source))

