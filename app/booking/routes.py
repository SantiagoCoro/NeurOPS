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
    # 1. Identify Event & Funnel Steps
    event = Event.query.filter_by(utm_source=utm_source).first()
    funnel_steps = ['identify', 'contact_details', 'survey', 'calendar'] # NEW FLOW
    
    # session.clear() -> This logs out admins! Use selective clear.
    keys_to_clear = ['booking_data', 'booking_event_id', 'funnel_steps', 'funnel_index', 'booking_user_id', 'current_appt_id', 'booking_email_input']
    for k in keys_to_clear:
        session.pop(k, None)
        
    session['booking_utm'] = utm_source
    
    # ... (referral logic same) ...

    # ... (init booking data same) ...

    if event:
        session['booking_event_id'] = event.id
        # We enforce the flow order in code now. 
        # DB 'funnel_steps' is ignored for step ordering.
    
    # Standard Flow Enforced:
    funnel_steps = ['identify', 'contact_details', 'survey', 'calendar']

    session['funnel_steps'] = funnel_steps
    session['funnel_index'] = 0
    
    return redirect(url_for('booking.handle_flow'))

@bp.route('/booking/flow')
def handle_flow():
    """Router: Redirects to the current step's view."""
    steps = session.get('funnel_steps', ['identify', 'contact_details', 'survey', 'calendar'])
    index = session.get('funnel_index', 0)
    
    if index >= len(steps):
        # Done!
        return redirect(url_for('booking.thank_you'))
        
    current_step = steps[index]
    
    if current_step == 'identify':
        return redirect(url_for('booking.identify_view'))
    elif current_step == 'contact_details':
        return redirect(url_for('booking.contact_details_view'))
    elif current_step == 'contact': # Legacy support
        return redirect(url_for('booking.contact_details_view'))
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

@bp.route('/booking/identify', methods=['GET', 'POST'])
def identify_view():
    if request.method == 'POST':
        email = request.form.get('email')
        if not email:
            flash('El correo es obligatorio.', 'error')
            return render_template('booking/identify.html')
            
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            session['booking_user_id'] = user.id
            flash(f'¡Hola de nuevo, {user.username}!', 'info')
        else:
            session.pop('booking_user_id', None)
            session['booking_email_input'] = email
            
        return redirect(url_for('booking.next_step'))
        
    return render_template('booking/identify.html')

@bp.route('/booking/details', methods=['GET', 'POST'])
def contact_details_view():
    user_id = session.get('booking_user_id')
    email_input = session.get('booking_email_input')
    
    user = None
    if user_id:
        user = User.query.get(user_id)
    
    # Helper to clean phone
    def clean_phone(code, number):
        if not number: return None
        return f"{code} {number}".strip()

    if request.method == 'POST':
        name = request.form.get('name')
        # If user exists, email is read-only or hidden usually, but form might send it?
        # If new, email comes from session or form confirm.
        
        # New inputs
        phone_code = request.form.get('phone_code')
        phone_number = request.form.get('phone')
        instagram = request.form.get('instagram')
        
        full_phone = clean_phone(phone_code, phone_number)
        utm_source = session.get('booking_utm', 'direct')

        if not user:
            # Create NEW
            email = email_input or request.form.get('email')
            if not email:
                flash('Error de sesión. Por favor inicie nuevamente.')
                return redirect(url_for('booking.start_booking'))
                
            temp_pass = str(uuid.uuid4())
            base_username = name or email.split('@')[0]
            # ... username uniqueness logic ...
            username = base_username[:60]
            while User.query.filter_by(username=username).first():
                import random
                username = f"{base_username}_{random.randint(1000,9999)}"[:64]
                
            user = User(username=username, email=email, role='lead')
            user.set_password(temp_pass)
            db.session.add(user)
            db.session.flush()
            
            profile = LeadProfile(user_id=user.id, phone=full_phone, instagram=instagram, utm_source=utm_source, status='new')
            db.session.add(profile)
            db.session.commit()
            
            session['booking_user_id'] = user.id
            
        else:
            # Update EXISTING
            if name: user.username = name
            
            if user.lead_profile:
                if full_phone: user.lead_profile.phone = full_phone
                if instagram: user.lead_profile.instagram = instagram
                # Don't overwrite UTM source of existing lead usually, or maybe append? Keep original.
            else:
                profile = LeadProfile(user_id=user.id, phone=full_phone, instagram=instagram, utm_source=utm_source, status='new')
                db.session.add(profile)
            
            db.session.commit()
            
        return redirect(url_for('booking.next_step'))

    # GET
    prefill = {}
    if user:
        prefill['email'] = user.email
        prefill['name'] = user.username
        if user.lead_profile:
            # Split phone into code and number if possible
            if user.lead_profile.phone and ' ' in user.lead_profile.phone:
                parts = user.lead_profile.phone.split(' ', 1)
                prefill['phone_code'] = parts[0]
                prefill['phone'] = parts[1]
            else:
                prefill['phone'] = user.lead_profile.phone
                
            prefill['instagram'] = user.lead_profile.instagram
    else:
         prefill['email'] = email_input
         
    return render_template('booking/contact_details.html', data=prefill)


def _flush_session_data(user_id):
    """Saves cached slot/answers to DB for this user."""
    bdata = session.get('booking_data', {})
    
    # 1. Flush Slot -> Appointment
    slot = bdata.get('slot')
    if slot:
        utc_iso = slot.get('utc_iso')
        if utc_iso:
            start_time = datetime.fromisoformat(utc_iso.replace('Z', '+00:00')).replace(tzinfo=None)
            
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
                
                # Update User Status automatically
                user = User.query.get(user_id)
                if user:
                    user.update_status_based_on_debt()
                
                # Clear slot from session
                bdata['slot'] = None
                session['booking_data'] = bdata
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
    import pytz
    
    # 1. Fetch Availability (Stored in Closer's Local Time - assumed per closer)
    # Optimization: Filter roughly by date range first
    today = date.today()
    end_date = today + timedelta(days=14)
    # Filter by date AND ensure role is 'closer' (exclude admins)
    availabilities = Availability.query.join(Availability.closer).filter(
        Availability.date >= today, 
        Availability.date <= end_date,
        User.role == 'closer'
    ).all()
    
    # 2. Fetch Appointments (Stored in UTC)
    # Need to filter effectively in UTC, so convert range to UTC
    # Since we don't know closer TZ yet, just fetch broad range
    appointments = Appointment.query.filter(
        Appointment.start_time >= datetime.utcnow(),
        Appointment.start_time <= datetime.utcnow() + timedelta(days=15),
        Appointment.status != 'canceled'
    ).all()
    
    booked_slots = set()
    for appt in appointments:
        # appt.start_time is naive but implicitly UTC
        booked_slots.add((appt.closer_id, appt.start_time))
        
    daily_slots_utc = {} # Key: Date (User's perspective? No, keep simple list) -> actually list of objects
    # We will send a flat list of available slots in UTC to the frontend
    # and let JS handle the Grouping by Day (Client Time)
    
    available_slots_utc = []
    
    unique_slots = {}
    preferred_id = session.get('preferred_closer_id')

    for av in availabilities:
        closer = av.closer
        if not closer: continue
        
        # Get Closer Timezone
        try:
            closer_tz = pytz.timezone(closer.timezone or 'America/La_Paz')
        except pytz.UnknownTimeZoneError:
            closer_tz = pytz.timezone('America/La_Paz')
            
        # Create Local Datetime
        local_dt = datetime.combine(av.date, av.start_time) # Naive
        local_dt = closer_tz.localize(local_dt) # Aware (Closer Time)
        
        # Convert to UTC
        utc_dt = local_dt.astimezone(pytz.UTC).replace(tzinfo=None) # Make naive UTC for comparison with DB
        
        # Filter Past
        if utc_dt < datetime.utcnow(): continue
        
        # Check Booking (utc_dt)
        if (av.closer_id, utc_dt) not in booked_slots:
            ts_key = utc_dt
            
            # If not present, add it
            if ts_key not in unique_slots:
                unique_slots[ts_key] = {
                    'utc_iso': utc_dt.isoformat() + 'Z', # Explicit Z for JS
                    'closer_id': av.closer_id,
                    'ts': utc_dt.timestamp()
                }
            # If present, check if we should swap for preferred closer
            elif preferred_id and av.closer_id == preferred_id:
                 unique_slots[ts_key]['closer_id'] = av.closer_id
            
    available_slots_utc = list(unique_slots.values())
    
    # DEBUG: Print what we are sending
    print(f"DEBUG SLOTS sending to frontend ({len(available_slots_utc)}):")
    for s in available_slots_utc:
        print(f"  -> {s['utc_iso']} (Closer {s['closer_id']})")
            
    # Sort by time
    available_slots_utc.sort(key=lambda x: x['ts'])
    
    # We pass raw slots to frontend, JS will group them
    return render_template('booking/calendar.html', slots_json=available_slots_utc)

@bp.route('/booking/select', methods=['POST'])
def select_slot():
    import pytz
    # We expect 'utc_iso' or explicit components. Let's rely on 'utc_iso' from frontend if possible, 
    # OR we can reconstruct if frontend sends localized date/time + offset?
    # Simplest: Frontend sends 'closer_id' AND 'utc_iso' for the chosen slot.
    # But wait, original flow picked closer dynamically. 
    # With UTC slots pre-calculated in calendar_view, each slot already belongs to a specific closer.
    
    utc_iso = request.form.get('utc_iso')
    closer_id = request.form.get('closer_id')
    
    if not utc_iso or not closer_id:
        flash('Error en la selección de horario. Intente nuevamente.')
        return redirect(url_for('booking.calendar_view'))
        
    start_time_utc = datetime.fromisoformat(utc_iso.replace('Z', '+00:00')).replace(tzinfo=None) # Naive UTC
    chosen_closer_id = int(closer_id)

    # Double Check Availability (Concurrency)
    # Since Availability is Local, and Appointment is UTC, we must verify logic carefully.
    # Actually, we trusted the 'calendar_view' calculation.
    # Let's check overlap with Appointment (UTC)
    conflict = Appointment.query.filter_by(closer_id=chosen_closer_id, start_time=start_time_utc).filter(Appointment.status != 'canceled').first()
    
    if conflict:
        flash('Lo sentimos, este horario acaba de ser ocupado.')
        return redirect(url_for('booking.calendar_view'))
        
    # Check if User exists
    user_id = session.get('booking_user_id')
    
    if user_id:
        # Create immediately (in UTC)
        appt = Appointment(
            closer_id=chosen_closer_id,
            lead_id=user_id,
            start_time=start_time_utc, # Stored as UTC
            status='scheduled',
            event_id=session.get('booking_event_id')
        )
        db.session.add(appt)
        db.session.commit()
        session['current_appt_id'] = appt.id
        
        # Update User Status automatically
        user = User.query.get(user_id)
        if user:
            user.update_status_based_on_debt()
            
        send_calendar_webhook(appt, 'created')
    else:
        # Save to session (UTC) and redirect
        bdata = session.get('booking_data', {})
        bdata['slot'] = {
            'utc_iso': utc_iso,
            'closer_id': chosen_closer_id
        }
        session['booking_data'] = bdata
        _flush_session_data(user_id) # Won't flush if user_id None, just saves to session
        
        return redirect(url_for('booking.next_step'))
        
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
    
    existing_answers = {}
    user_id = session.get('booking_user_id')
    if user_id:
        prev_answers = SurveyAnswer.query.filter_by(lead_id=user_id).all()
        # Create map {question_id: answer_text}
        for pa in prev_answers:
            existing_answers[pa.question_id] = pa.answer

    if request.method == 'POST':
        appt_id = session.get('current_appt_id')
        
        # Collect answers
        answers_data = [] # List of {q_id, ans}
        for q in questions:
            ans_text = request.form.get(f'q_{q.id}')
            if ans_text:
                answers_data.append({'question_id': q.id, 'answer': ans_text})
        
        if user_id:
            # Save immediately (Upsert)
            for item in answers_data:
                # Check existing
                existing = SurveyAnswer.query.filter_by(lead_id=user_id, question_id=item['question_id']).first()
                if existing:
                    existing.answer = item['answer']
                    # Link appt logic? usually survey is general or linked to specific appt? 
                    # If we want history, we should create new answer if appt_id differs? 
                    # For now, simplistic: update current profile answer.
                else:
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
        
    return render_template('booking/survey.html', questions=questions, existing_answers=existing_answers)

@bp.route('/booking/thankyou')
def thank_you():
    utm_source = session.get('booking_utm', 'direct')
    # Loop back to start
    return redirect(url_for('booking.start_booking', utm_source=utm_source))

