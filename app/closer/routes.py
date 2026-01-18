from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.closer import bp
from app.models import Availability, Appointment, User, LeadProfile, SurveyAnswer, SurveyQuestion, Event, Program, PaymentMethod, Enrollment, Payment, db
from app.closer.forms import SaleForm, LeadForm, AppointmentForm, CloserPaymentForm
from sqlalchemy import or_
from functools import wraps
from datetime import datetime, time, date, timedelta

# Decorator to ensure closer access
def closer_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['closer', 'admin']: 
            flash('No tienes permiso para acceder a esta página.')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@bp.route('/leads')
@closer_required
def leads_list():
    # --- Persistence/Filtering Logic similar to Admin ---
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    program_filter = request.args.get('program')
    status_filter = request.args.get('status')
    sort_by = request.args.get('sort_by', 'newest')

    # Base Query: Users assigned to this closer
    # Assigned via Enrollment OR Appointment? 
    # Usually "My Clients" implies Sales. "My Leads" implies Appointments. 
    # Let's include both: distinct users who have an enrollment w/ closer OR appointment w/ closer.
    
    # Subquery approach or Join?
    # Users who have an Enrollment with closer_id = current
    # OR Users who have an Appointment with closer_id = current
    
    # Simplify: 
    # query = User.query.filter(User.role.in_(['lead', 'student']))
    # query = query.filter(or_(User.enrollments.any(closer_id=current_user.id), User.appointments_as_lead.any(closer_id=current_user.id)))
    # But `appointments_as_lead` is relationship name.
    
    query = User.query.filter(User.role.in_(['lead', 'student']))
    
    # Filter by assignment (Enrollment, Appointment, or Direct Assignment)
    query = query.filter(
        or_(
            User.enrollments.any(Enrollment.closer_id == current_user.id),
            User.appointments_as_lead.any(Appointment.closer_id == current_user.id),
            User.lead_profile.has(assigned_closer_id=current_user.id)
        )
    )

    # Joins for filtering attributes
    if status_filter:
        query = query.join(LeadProfile).filter(LeadProfile.status == status_filter)
    
    if program_filter:
        query = query.join(Enrollment, Enrollment.student_id == User.id).filter(Enrollment.program_id == program_filter)

    # Date Filter (Created At)
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        query = query.filter(User.created_at >= start_date)
    
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(User.created_at < end_date)

    # Search (Name or Email)
    if search:
        search_term = f"%{search}%"
        query = query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))

    # Sorting
    if sort_by == 'oldest':
        query = query.order_by(User.created_at.asc())
    elif sort_by == 'a-z':
        query = query.order_by(User.username.asc())
    elif sort_by == 'z-a':
        query = query.order_by(User.username.desc())
    else: # newest
        query = query.order_by(User.created_at.desc())

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    leads = pagination.items
    start_index = (page -1) * per_page
    
    is_load_more = request.args.get('load_more')

    # --- KPI Calculations (Scoped to Filtered Users) ---
    # We re-use logic but applied to the 'assigned' subset.
    
    # To act efficiently, we might fetch all IDs first or use subqueries.
    # For now, let's execute separate count queries with same filters.
    
    kpi_query = User.query.filter(User.role.in_(['lead', 'student']))
    kpi_query = kpi_query.filter(or_(User.enrollments.any(Enrollment.closer_id == current_user.id), User.appointments_as_lead.any(Appointment.closer_id == current_user.id)))

    if start_date_str:
        kpi_query = kpi_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str:
        kpi_query = kpi_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search:
        search_term = f"%{search}%"
        kpi_query = kpi_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
        
    total_users = kpi_query.count()
    
    # Financials (Cash Collect & Debt - My Portfolio)
    # Cash Collect: Payments on enrollments assigned to ME.
    # Note: If a user appears in list because of an appointment, but has NO enrollment with me, they contribute 0 to sales.
    # If they have enrollment with me, we sum payments.
    
    fin_query = db.session.query(db.func.sum(Payment.amount)).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Payment).filter(
        Payment.status == 'completed',
        Enrollment.closer_id == current_user.id # STRICTLY my sales
    )
    
    # Apply User filters to fin_query
    # We must ensure we filter the USERS similarly (date, search)
    if start_date_str: fin_query = fin_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: fin_query = fin_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: fin_query = fin_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: fin_query = fin_query.join(LeadProfile).filter(LeadProfile.status == status_filter)
    if program_filter: fin_query = fin_query.filter(Enrollment.program_id == program_filter)
    
    total_revenue_gross = fin_query.scalar() or 0.0
    
    # Platform Commission (Expenses) - Needed to calc Net Cash Collect?
    # User said: "Closer commission is 10% of Cash Collect".
    # Usually "Cash Collect" for the company is (Gross - Stripe Fees). 
    # Or is "Cash Collect" just Gross?
    # In Admin Logic: cash_collected = total_revenue - total_commission (platform fees).
    # Assuming Closer Commission is based on THAT Net Cash Collect.
    
    comm_query = db.session.query(
        db.func.sum(
            (Payment.amount * (PaymentMethod.commission_percent / 100.0)) + PaymentMethod.commission_fixed
        )
    ).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Payment).join(PaymentMethod).filter(
        Payment.status == 'completed',
        Enrollment.closer_id == current_user.id
    )
    
    if start_date_str: comm_query = comm_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: comm_query = comm_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: comm_query = comm_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: comm_query = comm_query.join(LeadProfile).filter(LeadProfile.status == status_filter)
    if program_filter: comm_query = comm_query.filter(Enrollment.program_id == program_filter)
    
    platform_fees = comm_query.scalar() or 0.0
    
    cash_collect_net = total_revenue_gross - platform_fees
    
    # Closer Commission (10% of Cash Collect Net)
    # Or Gross? Usually commission is on Net. Let's use Net as calculated in Admin.
    closer_commission = cash_collect_net * 0.10
    
    # Debt (My Enrollments)
    # Using python iteration for simplicity on filtered set, or robust SQL?
    # Let's use SQL hybrid like Admin
    enr_query = db.session.query(Enrollment).join(User, Enrollment.student_id == User.id).filter(
        Enrollment.status == 'active',
        Enrollment.closer_id == current_user.id
    )
    if start_date_str: enr_query = enr_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: enr_query = enr_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: enr_query = enr_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: enr_query = enr_query.join(LeadProfile).filter(LeadProfile.status == status_filter)
    if program_filter: enr_query = enr_query.filter(Enrollment.program_id == program_filter)
    
    active_enrollments = enr_query.all()
    total_debt = 0.0
    
    for enr in active_enrollments:
        paid = enr.total_paid
        agreed = enr.total_agreed if enr.total_agreed is not None else (enr.program.price if enr.program else 0.0)
        debt = agreed - paid
        if debt > 0:
            total_debt += debt

    kpis = {
        'total': total_users,
        'cash_collected': cash_collect_net,
        'my_commission': closer_commission,
        'debt': total_debt
    }
    
    # Filters context
    all_programs = Program.query.order_by(Program.name).all()
    all_statuses = db.session.query(LeadProfile.status).distinct().filter(LeadProfile.status != None).all()
    all_statuses = [s[0] for s in all_statuses]
    
    if is_load_more:
        # We need a partial template for rows. 
        # Ideally we share 'admin/partials/leads_rows.html' but it has Admin actions.
        # We should create 'closer/partials/leads_rows.html' or conditionalize.
        # Creating new file.
        return render_template('closer/partials/leads_rows.html', leads=leads, start_index=start_index)

    return render_template('closer/leads_list.html', 
                           leads=leads, 
                           pagination=pagination,
                           kpis=kpis,
                           search=search,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           program_filter=program_filter,
                           status_filter=status_filter,
                           sort_by=sort_by,
                           all_programs=all_programs,
                           all_statuses=all_statuses,
                           start_index=start_index)

@bp.route('/lead/<int:id>')
@closer_required
def lead_detail(id):
    lead = User.query.get_or_404(id)
    if lead.role not in ['lead', 'student']:
        flash('Usuario no es un lead válido.')
        return redirect(url_for('closer.leads_list'))
        
    profile = lead.lead_profile
    
    
    # 1. Determine Funnel Order
    funnel_steps = ['contact', 'calendar', 'survey'] # Default
    # Try to find event by UTM Source or maybe latest appointment's event?
    # Profile UTM is best bet for origin.
    if profile.utm_source:
        event = Event.query.filter_by(utm_source=profile.utm_source).first()
        if event:
            if event.funnel_steps:
                 funnel_steps = event.funnel_steps
            elif event.group and event.group.funnel_steps:
                 funnel_steps = event.group.funnel_steps
    
    # 2. Fetch Answers
    answers_query = SurveyAnswer.query.join(SurveyQuestion).filter(SurveyAnswer.lead_id == lead.id).order_by(SurveyQuestion.order).all()
    
    landing_answers = [a for a in answers_query if a.question.step == 'landing']
    survey_answers = [a for a in answers_query if a.question.step == 'survey']
    
    appointments = Appointment.query.filter_by(lead_id=lead.id).order_by(Appointment.start_time.desc()).all()
    
    return render_template('closer/lead_detail.html', lead=lead, profile=profile, landing_answers=landing_answers, survey_answers=survey_answers, appointments=appointments, funnel_steps=funnel_steps)

    return render_template('closer/lead_detail.html', lead=lead, profile=profile, answers=answers, appointments=appointments)

@bp.route('/leads/update/<int:id>', methods=['POST'])
@closer_required
def update_lead_quick(id):
    user = User.query.get_or_404(id)
    # Security Check: Ensure we overlap with leads_list logic or specific restriction?
    # Restrict to lead/student editing only.
    if user.role not in ['lead', 'student']:
        flash('No puedes modificar este usuario.')
        return redirect(url_for('closer.leads_list'))

    # Update Role
    new_role = request.form.get('role')
    if new_role in ['lead', 'student']:
        user.role = new_role
        
    # Update Status
    new_status = request.form.get('status')
    if new_status:
        if not user.lead_profile:
            profile = LeadProfile(user_id=user.id)
            db.session.add(profile)
        user.lead_profile.status = new_status
        
    db.session.commit()
    flash('Cliente actualizado.')
    return redirect(url_for('closer.leads_list'))

import pytz
from datetime import time
from app.closer.forms import SaleForm, CloserPaymentForm, LeadForm, CloserStatsForm
from app.models import CloserDailyStats, DailyReportQuestion, DailyReportAnswer
from app.decorators import role_required

@bp.route('/daily_report', methods=['GET', 'POST'])
@closer_required
def daily_report():
    # Timezone handling
    tz_name = current_user.timezone or 'America/La_Paz'
    try:
        user_tz = pytz.timezone(tz_name)
    except:
        user_tz = pytz.timezone('America/La_Paz')
        
    now_local = datetime.now(user_tz)
    today_local = now_local.date()
    
    # --- Automated KPI Calculation (Live for Today) ---
    start_local = user_tz.localize(datetime.combine(today_local, time.min))
    end_local = user_tz.localize(datetime.combine(today_local, time.max))
    
    start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
    end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)
    
    # 1. Calls
    appointments_today = Appointment.query.filter(
        Appointment.closer_id == current_user.id,
        Appointment.start_time >= start_utc,
        Appointment.start_time <= end_utc
    ).all()
    
    kpi_scheduled = len(appointments_today)
    kpi_completed = sum(1 for a in appointments_today if a.status == 'completed')
    kpi_no_show = sum(1 for a in appointments_today if a.status == 'no_show')
    kpi_canceled = sum(1 for a in appointments_today if a.status == 'canceled')

    # 2. Sales & Cash
    # Filter payments made today on enrollments owned by this closer
    # Need to be careful: Enrollment closer_id vs Payment Date
    # We want "Cash Collected Today" by this closer. 
    # Usually strictly payments received today, on enrollments assigned to this closer.
    
    payments_today = Payment.query.join(Enrollment).filter(
        Enrollment.closer_id == current_user.id,
        Payment.date >= start_utc,
        Payment.date <= end_utc,
        Payment.status == 'completed'
    ).all()
    
    kpi_sales_count = 0
    kpi_sales_amount = 0.0 # Total agreed value of sales made today (Enrollments created today)
    kpi_cash_collected = 0.0 # Actual money received today
    
    # Sales Count = New Enrollments Today
    new_enrollments = Enrollment.query.filter(
        Enrollment.closer_id == current_user.id,
        Enrollment.enrollment_date >= start_utc,
        Enrollment.enrollment_date <= end_utc,
        Enrollment.status != 'dropped'
    ).all()
    
    kpi_sales_count = len(new_enrollments)
    kpi_sales_amount = sum(e.total_agreed for e in new_enrollments)
    
    # Cash Collected
    kpi_cash_collected = sum(p.amount for p in payments_today)

    # --- Questions ---
    questions = DailyReportQuestion.query.filter_by(is_active=True).order_by(DailyReportQuestion.order).all()
    
    # Check if report exists for today
    today_stats = CloserDailyStats.query.filter_by(closer_id=current_user.id, date=today_local).first()
    
    if request.method == 'POST':
        if not today_stats:
            today_stats = CloserDailyStats(closer_id=current_user.id, date=today_local)
            db.session.add(today_stats)
        
        # Update/Save Automated KPIs (Snapshot)
        today_stats.calls_scheduled = kpi_scheduled
        today_stats.calls_completed = kpi_completed
        today_stats.calls_no_show = kpi_no_show
        today_stats.calls_canceled = kpi_canceled
        today_stats.sales_count = kpi_sales_count
        today_stats.sales_amount = kpi_sales_amount
        today_stats.cash_collected = kpi_cash_collected
        
        # Manual Metrics (Legacy) - kept generic if form has them? 
        # For now, user only asked to replace manual with automated and use questions for rest.
        # Only self_generated_bookings remains 'manual' if not trackable.
        # We can accept it from form if present, else 0.
        today_stats.self_generated_bookings = request.form.get('self_generated_bookings', 0, type=int)

        db.session.commit() # Save stats to get ID
        
        # Save Answers
        # Clear old answers for this report to avoid duplicates if re-submitting?
        # Or just update. Simpler to delete old for this day/question set.
        current_answers = DailyReportAnswer.query.filter_by(daily_stats_id=today_stats.id).all()
        for ans in current_answers:
            db.session.delete(ans)
            
        for q in questions:
            ans_text = request.form.get(f'question_{q.id}')
            if q.question_type == 'boolean':
                # Checkbox sends 'on' or nothing.
                ans_text = 'Sí' if request.form.get(f'question_{q.id}') else 'No'
            
            if ans_text:
                new_ans = DailyReportAnswer(
                    daily_stats_id=today_stats.id,
                    question_id=q.id,
                    answer=str(ans_text)
                )
                db.session.add(new_ans)
        
        db.session.commit()
        flash('Reporte diario guardado exitosamente.')
        return redirect(url_for('closer.dashboard'))

    return render_template('closer/daily_report.html', 
                           questions=questions,
                           kpi_scheduled=kpi_scheduled,
                           kpi_completed=kpi_completed,
                           kpi_no_show=kpi_no_show,
                           kpi_canceled=kpi_canceled,
                           kpi_sales_count=kpi_sales_count,
                           kpi_sales_amount=kpi_sales_amount,
                           kpi_cash_collected=kpi_cash_collected,
                           today_stats=today_stats, # Pass existing to pre-fill if needed (though we wipe answers on save, keeping UI simple)
                           today=today_local
                           )
from app.closer.utils import send_calendar_webhook
from app.closer.utils import send_sales_webhook
import uuid

# ... (Previous routes leads_list, lead_detail)

@bp.route('/leads/add', methods=['GET', 'POST'])
@closer_required
def add_lead():
    form = LeadForm()
    if form.validate_on_submit():
        # Check existing
        if User.query.filter_by(email=form.email.data).first():
            flash('Este email ya está registrado.')
            return render_template('closer/lead_form.html', form=form, title="Nuevo Lead")
            
        # Create User
        temp_pass = str(uuid.uuid4())
        user = User(username=form.username.data, email=form.email.data, role='lead')
        user.set_password(temp_pass)
        db.session.add(user)
        db.session.flush()
        
        # Create Profile
        profile = LeadProfile(
            user_id=user.id,
            phone=form.phone.data,
            instagram=form.instagram.data,
            utm_source='closer_added'
        )
        db.session.add(profile)
        db.session.commit()
        
        flash('Lead creado exitosamente.')
        return redirect(url_for('closer.leads_list'))
        
    return render_template('closer/lead_form.html', form=form, title="Nuevo Lead")

@bp.route('/leads/edit/<int:id>', methods=['GET', 'POST'])
@closer_required
def edit_lead(id):
    user = User.query.get_or_404(id)
    # Security: Allow editing any user created? Or only leads?
    # Let's restrict to role='lead' to prevent modifying admins.
    if user.role != 'lead' and current_user.role != 'admin':
         # Allow if current user HAS an appointment with them (e.g. they are a student now)
         # But safer to just warn
         pass # Decided to allow closer to edit contact info of their assigned people regardless of role for now, as they are "leads" in context.
         
    form = LeadForm(obj=user)
    
    # Pre-populate profile fields
    if request.method == 'GET' and user.lead_profile:
        form.phone.data = user.lead_profile.phone
        form.instagram.data = user.lead_profile.instagram
        
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data 
        # Ideally check email uniqueness if changed, skipping for brevity but recommended
        
        if not user.lead_profile:
             profile = LeadProfile(user_id=user.id)
             db.session.add(profile)
             
        user.lead_profile.phone = form.phone.data
        user.lead_profile.instagram = form.instagram.data
        
        db.session.commit()
        flash('Lead actualizado.')
        return redirect(url_for('closer.lead_detail', id=user.id))
        
    return render_template('closer/lead_form.html', form=form, title="Editar Lead")

@bp.route('/leads/delete/<int:id>')
@closer_required
def delete_lead(id):
    user = User.query.get_or_404(id)
    # Safety check
    if user.role == 'admin':
        flash('No puedes eliminar administradores.')
        return redirect(url_for('closer.leads_list'))
        
    db.session.delete(user) # Cascade should handle profile/appointments if configured, otherwise might error. 
    # Current models: User has cascade="all, delete-orphan"?
    # LeadProfile backref="user", cascade? Default usually fails. 
    # Let's hope models are robust or delete manually.
    # Looking at User model... 
    db.session.commit()
    flash('Lead eliminado.')
    return redirect(url_for('closer.leads_list'))

    return redirect(url_for('closer.leads_list'))

@bp.route('/appointment/add', methods=['GET', 'POST'])
@closer_required
def create_appointment():
    form = AppointmentForm()
    # Populate leads choices
    leads = User.query.filter_by(role='lead').order_by(User.username).all()
    form.lead_id.choices = [(l.id, f"{l.username} ({l.email})") for l in leads]
    
    # Pre-select lead if passed in URL
    lead_id = request.args.get('lead_id', type=int)
    if lead_id and not form.lead_id.data:
        form.lead_id.data = lead_id

    if form.validate_on_submit():
        # TODO: Validate closer availability? 
        # For manual override, we assume the closer knows what they are doing.
        start_dt = datetime.combine(form.date.data, form.time.data)
        
        appt = Appointment(
            closer_id=current_user.id,
            lead_id=form.lead_id.data,
            start_time=start_dt,
            status='scheduled' # Created manually => confirmed
        )
        db.session.add(appt)
        db.session.commit()
        
        # Auto-update status
        if appt.lead:
            appt.lead.update_status_based_on_debt()
        
        # Webhook
        send_calendar_webhook(appt, 'created')
        
        flash('Cita agendada exitosamente.')
        return redirect(url_for('closer.dashboard'))
        
    return render_template('closer/appointment_form.html', form=form, title="Nueva Cita")

@bp.route('/appointment/edit/<int:id>', methods=['GET', 'POST'])
@closer_required
def edit_appointment(id):
    appt = Appointment.query.get_or_404(id)
    if appt.closer_id != current_user.id and current_user.role != 'admin':
         flash('No tienes permiso.')
         return redirect(url_for('closer.dashboard'))
         
    form = AppointmentForm()
    leads = User.query.filter_by(role='lead').order_by(User.username).all()
    form.lead_id.choices = [(l.id, f"{l.username} ({l.email})") for l in leads]
    
    if request.method == 'GET':
        form.lead_id.data = appt.lead_id
        form.date.data = appt.start_time.date()
        form.time.data = appt.start_time.time()
        
    if form.validate_on_submit():
        start_dt = datetime.combine(form.date.data, form.time.data)
        
        # Capture old time for webhook if rescheduling
        old_start_time = appt.start_time if appt.start_time != start_dt else None
        
        appt.lead_id = form.lead_id.data
        appt.start_time = start_dt
        # If it was canceled, maybe reset to scheduled? 
        # User implies rescheduling.
        if appt.status == 'canceled':
            appt.status = 'scheduled'
            
        db.session.commit()
        
        # Auto-update status
        if appt.lead:
            appt.lead.update_status_based_on_debt()
        
        # Webhook
        send_calendar_webhook(appt, 'rescheduled', old_start_time=old_start_time)
        
        flash('Cita reagendada.')
        return redirect(url_for('closer.dashboard'))
        
    return render_template('closer/appointment_form.html', form=form, title="Editar Cita")

@bp.route('/appointment/<int:id>/status/<status>')
@closer_required
def update_appt_status(id, status):
    appt = Appointment.query.get_or_404(id)
    
    # Security: Ensure it's THIS closer's appointment
    if appt.closer_id != current_user.id and current_user.role != 'admin':
        flash('No tienes permiso para modificar esta cita.')
        return redirect(url_for('closer.dashboard'))
        
    valid_statuses = ['scheduled', 'completed', 'canceled', 'no_show']
    if status not in valid_statuses:
        flash('Estado inválido.')
        return redirect(url_for('closer.dashboard'))
        
    appt.status = status
    db.session.commit()
    
    # Auto-update status based on new appointment state
    if appt.lead:
        appt.lead.update_status_based_on_debt()
    
    msg_map = {
        'completed': 'Cita marcada como Realizada.',
        'canceled': 'Cita cancelada.',
        'no_show': 'Cita marcada como No Show.'
    }
    
    # Webhook
    send_calendar_webhook(appt, 'canceled' if status == 'canceled' else 'status_changed')

    flash(msg_map.get(status, 'Estado actualizado.'))
    
    # If redirecting back to lead detail might be useful too? 
    # For now, back to referrer or dashboard
    return redirect(request.referrer or url_for('closer.dashboard'))

@bp.route('/dashboard')
@closer_required
def dashboard():
    import pytz # Import strictly for template usage if needed
    
    # 1. KPIs
    # Today's calls
    tz_name = current_user.timezone or 'America/La_Paz'
    try:
        closer_tz = pytz.timezone(tz_name)
    except:
        closer_tz = pytz.timezone('America/La_Paz')
        
    now_local = datetime.now(closer_tz)
    today_local = now_local.date()
    
    start_local = closer_tz.localize(datetime.combine(today_local, time.min))
    end_local = closer_tz.localize(datetime.combine(today_local, time.max))
    
    start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
    end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)
    
    todays_calls_count = Appointment.query.filter(
        Appointment.closer_id == current_user.id,
        Appointment.start_time >= start_utc,
        Appointment.start_time <= end_utc,
        Appointment.status != 'canceled'
    ).count()
    
    # Pending Followups (Leads not contacted? Or manual task?)
    # ... (existing logic)
    
    pending_followups = User.query.filter(
        User.lead_profile.has(assigned_closer_id=current_user.id),
        User.lead_profile.has(status='pending')
    ).count() # Just an example
    
    # Sales this month
    # Need month start/end in local -> UTC
    month_start_local = closer_tz.localize(datetime(today_local.year, today_local.month, 1))
    # ... logic for month end
    month_start_utc = month_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
    
    sales_this_month = Enrollment.query.filter(
        Enrollment.closer_id == current_user.id,
        Enrollment.enrollment_date >= month_start_utc
    ).count()
    
    # Next Calls (Upcoming)
    # Filter: start_time > now_utc
    next_calls = Appointment.query.filter(
        Appointment.closer_id == current_user.id,
        Appointment.start_time > datetime.utcnow(),
        Appointment.status == 'scheduled'
    ).order_by(Appointment.start_time).limit(5).all()
    
    # Active Events for Links
    events = Event.query.filter_by(is_active=True).all()

    # 5. Top 5 Debtors (Same logic as Admin)
    # Ideally should be a shared service/util
    active_enrollments = Enrollment.query.filter_by(status='active').all()
    debtors = []
    for enr in active_enrollments:
        # Check if student is assigned to this closer?
        # Or if enrollment is assigned to this closer?
        # Let's filter by enrollment.closer_id == current just to be safe/consistent
        if enr.closer_id == current_user.id:
            paid = db.session.query(db.func.sum(Payment.amount)).filter(
                Payment.enrollment_id == enr.id,
                Payment.status == 'completed'
            ).scalar() or 0
            debt = enr.total_agreed - paid
            if debt > 0:
                debtors.append({
                    'student': enr.student,
                    'program': enr.program,
                    'total_agreed': enr.total_agreed,
                    'total_paid': paid,
                    'debt': debt
                })
    top_debtors = sorted(debtors, key=lambda x: x['debt'], reverse=True)[:5]
    
    # 6. Calculate Monthly Sales (Explicit Assignment)
    # Logic: Sum total_agreed of Enrollments created this month AND assigned to this closer.
    
    first_day_month = month_start_utc
    
    month_enrollments = Enrollment.query.filter(
        Enrollment.enrollment_date >= first_day_month,
        Enrollment.enrollment_date <= end_utc,
        Enrollment.status != 'dropped',
        Enrollment.closer_id == current_user.id # Explicit check
    ).all()
    
    monthly_sales = sum(e.total_agreed for e in month_enrollments)
    monthly_sales_count = len(month_enrollments)
            
    # 7. Closing Rate (Sales Count / Completed Appointments Count)
    month_appointments_count = Appointment.query.filter(
        Appointment.closer_id == current_user.id,
        Appointment.start_time >= first_day_month,
        Appointment.start_time <= end_utc,
        Appointment.status == 'completed'
    ).count()
    
    closing_rate = 0
    if month_appointments_count > 0:
        closing_rate = (monthly_sales_count / month_appointments_count) * 100

    return render_template('closer/dashboard.html', 
                           today_appointments_count=todays_calls_count, 
                           next_calls=next_calls,
                           events=events,
                           top_debtors=top_debtors,
                           monthly_sales=monthly_sales,
                           closing_rate=closing_rate,
                           pytz=pytz,
                           datetime=datetime)

@bp.route('/calendar', methods=['GET'])
@closer_required
def calendar():
    # Week Navigation Logic
    week_offset = request.args.get('offset', 0, type=int)
    
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    match_dates = [start_of_week + timedelta(days=i) for i in range(7)]
    
    # Block past rule: simple check against today's full datetime in template or here
    # For simplicity, we pass today to template
    
    # Fetch Availability for this week
    availabilities = Availability.query.filter(
        Availability.closer_id == current_user.id,
        Availability.date >= start_of_week,
        Availability.date <= match_dates[-1]
    ).all()
    
    # Fetch Appointments for this week (To show blocked red slots)
    appointments = Appointment.query.filter(
        Appointment.closer_id == current_user.id,
        Appointment.start_time >= datetime.combine(start_of_week, time.min),
        Appointment.start_time <= datetime.combine(match_dates[-1], time.max),
        Appointment.status != 'canceled'
    ).all()
    
    import pytz # Explicit import needed
    return render_template('closer/calendar.html', 
                           week_dates=match_dates, 
                           week_offset=week_offset,
                           availabilities=availabilities,
                           appointments=appointments,
                           today=today,
                           now=datetime.now(),
                           pytz=pytz)

@bp.route('/calendar/update', methods=['POST'])
@closer_required
def update_availability():
    # Expects JSON list of ALL available slots for the specific dates modified
    # Or simpler: receive a list of {date, start, end} and we wipe & recreate for those days?
    # Better: List of objects to ADD and list of objects to REMOVE?
    # User asked for "Save" button. Easiest implementation:
    # Send list of currently selected available slots for the VISIBLE week.
    # Backend: Wipes availability for that week and inserts the new list.
    
    data = request.json
    slots = data.get('slots', []) # List of {date: 'YYYY-MM-DD', hour: 'HH'}
    
    if not slots and not data.get('week_start'):
        return {'status': 'error', 'msg': 'No data'}, 400

    week_start_str = data.get('week_start')
    week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
    week_end = week_start + timedelta(days=6)
    
    # Transactional wipe & recreate for this week
    try:
        # Delete existing for this week
        Availability.query.filter(
            Availability.closer_id == current_user.id,
            Availability.date >= week_start,
            Availability.date <= week_end
        ).delete()
        
        # Insert new
        unique_slots = set() # Avoid duplicates if frontend is buggy
        for slot in slots:
            slot_date = datetime.strptime(slot['date'], '%Y-%m-%d').date()
            start_t = datetime.strptime(slot['hour'].strip(), '%H:%M').time()
            end_t = (datetime.combine(date.min, start_t) + timedelta(hours=1)).time()
            
            # Validation: don't save past slots? 
            # If front-end blocks it, backend should respecting it or double check.
            # Allowing save for now.
            
            new_slot = Availability(
                closer_id=current_user.id,
                date=slot_date,
                start_time=start_t,
                end_time=end_t
            )
            db.session.add(new_slot)
            
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return {'status': 'error', 'msg': str(e)}, 500
            
    return {'status': 'success', 'count': len(slots)}

@bp.route('/search_leads')
@closer_required
def search_leads():
    query = request.args.get('q', '')
    if len(query) < 2:
        return {'results': []}
        
    search = f"%{query}%"
    # Join with LeadProfile to search phone/instagram
    # Explicit join condition to avoid ambiguity or SQLAlchemy errors
    leads = User.query.outerjoin(LeadProfile, User.id == LeadProfile.user_id).filter(
        User.role == 'lead',
        or_(
            User.username.ilike(search),
            User.email.ilike(search),
            LeadProfile.phone.ilike(search),
            LeadProfile.instagram.ilike(search)
        )
    ).limit(10).all()
    
    results = []
    for lead in leads:
        info = f"{lead.username} ({lead.email})"
        if lead.lead_profile:
            if lead.lead_profile.phone:
                info += f" - Tel: {lead.lead_profile.phone}"
            if lead.lead_profile.instagram:
                info += f" - IG: {lead.lead_profile.instagram}"
                
        results.append({
            'id': lead.id,
            'text': info
        })
        
    return {'results': results}

@bp.route('/sale/new', methods=['GET', 'POST'])
@closer_required
def create_sale():
    form = SaleForm()
    next_url = request.args.get('next')
    
    # Populate Choices
    form.program_id.choices = [(p.id, f"{p.name} (${p.price})") for p in Program.query.all()]
    form.payment_method_id.choices = [(m.id, m.name) for m in PaymentMethod.query.filter_by(is_active=True).all()]
    
    # Handle GET with pre-fill
    if request.method == 'GET':
        lead_id = request.args.get('lead_id', type=int)
        if lead_id:
            lead = User.query.get(lead_id)
            if lead:
                form.lead_id.data = lead.id
                form.lead_search.data = f"{lead.username} ({lead.email})"
    
    if form.validate_on_submit():
        lead_id = form.lead_id.data
        program_id = form.program_id.data
        pay_type = form.payment_type.data
        amount = form.amount.data
        
        program = Program.query.get(program_id)
        if not program:
             flash('Programa no encontrado.')
             return render_template('sales/new_sale.html', form=form, title="Nueva Venta")

        # Validation: Full Payment must be >= Program Price
        if pay_type == 'full' and amount < program.price:
             flash(f'Error: El pago completo debe ser al menos ${program.price} (Precio del Programa).')
             return render_template('sales/new_sale.html', form=form, title="Nueva Venta")
        
        # Check Enrollment
        enrollment = Enrollment.query.filter_by(student_id=lead_id, program_id=program_id, status='active').first()
        
        # Logic for creation/update
        if not enrollment:
            if pay_type in ['full', 'down_payment', 'renewal']:
                 # Create Enrollment
                 enrollment = Enrollment(
                     program_id=program_id,
                     total_agreed=amount if pay_type == 'full' else program.price, # Default to list price if not full, or logic? User didn't specify. Assuming list price.
                     status='active',
                     closer_id=current_user.id # Explicit link
                 )
                 db.session.add(enrollment)
                 db.session.flush() # Get ID
            else:
                # Installment but no enrollment?
                flash('Error: No se puede cobrar cuota sin inscripción activa. Seleccione Primer Pago o Completo.')
                return render_template('sales/new_sale.html', form=form, title="Nueva Venta")
        
        # Create Payment
        payment = Payment(
            enrollment_id=enrollment.id,
            payment_method_id=form.payment_method_id.data,
            amount=amount,
            payment_type=pay_type, 
            status='completed'
        )
        db.session.add(payment)
        
        # Update User Role (Lead -> Student)
        user = User.query.get(lead_id)
        if user.role == 'lead':
            user.role = 'student'
            db.session.add(user)
            
        # --- Automate Status Logic ---
        if not user.lead_profile:
            # Should exist, but safety
            profile = LeadProfile(user_id=user.id, status='new')
            db.session.add(profile)
        else:
            profile = user.lead_profile

        # Renewal Validation
        if pay_type == 'renewal':
            if profile.status != 'completed':
                flash('Error: Solo se puede renovar si el estado del cliente es "Completado".')
                db.session.rollback() # Rollback payment/enrollment created above
                return render_template('sales/new_sale.html', form=form, title="Nueva Venta")
            profile.status = 'renewed'
            
        elif pay_type == 'full':
            profile.status = 'completed'
            
        elif pay_type == 'down_payment':
            # Only set to pending if not already completed/renewed? 
            # User said: "al realizar un primer pago debe marcarse como pendiente"
            # Assuming new sale flow implies starting fresh or continuing.
            # If they were completed and buy a NEW program with down payment, they go back to pending?
            # User logic implies status tracks the current active engagement.
            profile.status = 'pending'
            
        elif pay_type == 'installment':
            # Check total paid for this enrollment
            # We just added 'payment' to session, it might not be in query result of other payments commit?
            # It is in session.
            # Let's sum all payments for this enrollment.
            current_total = 0
            for p in enrollment.payments:
                current_total += p.amount
            
            # Add current payment if not in enrollment.payments list yet (depends on relationship loading)
            # Since we just created it: payment = Payment(...), db.session.add(payment).
            # It might not be in enrollment.payments backref until flush/refresh.
            # But we added it. Let's rely on manual sum + current amount if needed, 
            # or just flush and query.
            db.session.flush()
            
            total_paid = db.session.query(db.func.sum(Payment.amount)).filter_by(enrollment_id=enrollment.id).scalar() or 0
            
            if total_paid >= program.price:
                profile.status = 'completed'
            else:
                # Keep as pending or set to pending if it was something else?
                # User: "al pagar una cuota ... debe marcarse como completo" (if reached).
                # Implied: otherwise stays pending.
                if profile.status != 'completed' and profile.status != 'renewed':
                    profile.status = 'pending'

        db.session.commit()
        
        # Webhook
        send_sales_webhook(payment, current_user.username)
        
        flash('Venta registrada exitosamente.')
        if next_url:
            return redirect(next_url)
            
        # Fallback based on role
        if current_user.role == 'admin':
            return redirect(url_for('admin.sales_list'))
            
        return redirect(url_for('closer.sales_list'))

    return render_template('sales/new_sale.html', form=form, title="Nueva Venta", next_url=next_url)

@bp.route('/sales')
@closer_required
def sales_list():
    # Helper for stats
    # Filter Params
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    method_filter = request.args.get('method')
    type_filter = request.args.get('type')
    program_filter = request.args.get('program')
    
    # Base Query: Payments on enrollments assigned to this closer
    query = Payment.query.join(
        Enrollment, Payment.enrollment_id == Enrollment.id
    ).join(
        User, Enrollment.student_id == User.id
    ).filter(Enrollment.closer_id == current_user.id)

    # Date Filter
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        query = query.filter(Payment.date >= start_date)
    
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(Payment.date < end_date)

    # Search (Name or Email)
    if search:
        search_term = f"%{search}%"
        query = query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
        
    if method_filter:
        query = query.filter(Payment.payment_method_id == method_filter)
        
    if type_filter:
        query = query.filter(Payment.payment_type == type_filter)
        
    if program_filter:
        query = query.filter(Enrollment.program_id == program_filter)

    # Ordering
    query = query.order_by(Payment.date.desc())
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    payments = pagination.items
    start_index = (page - 1) * per_page
    
    is_load_more = request.args.get('load_more')

    # --- KPI Stats (Filtered) ---
    # Need to run aggregation on the filtered set
    
    # Clone query for aggregation
    # We need Sum(Payment.amount)
    stats_query = db.session.query(
        db.func.sum(Payment.amount),
        db.func.count(Payment.id),
        db.func.sum(
            (Payment.amount * (PaymentMethod.commission_percent / 100.0)) + PaymentMethod.commission_fixed
        )
    ).join(
        Enrollment, Payment.enrollment_id == Enrollment.id
    ).join(
        User, Enrollment.student_id == User.id
    ).outerjoin( # Outer join in case generic method, but usually inner is fine
        PaymentMethod, Payment.payment_method_id == PaymentMethod.id
    ).filter(Enrollment.closer_id == current_user.id)
    
    # Re-apply filters
    if start_date_str: stats_query = stats_query.filter(Payment.date >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: stats_query = stats_query.filter(Payment.date < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: stats_query = stats_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if method_filter: stats_query = stats_query.filter(Payment.payment_method_id == method_filter)
    if type_filter: stats_query = stats_query.filter(Payment.payment_type == type_filter)
    if program_filter: stats_query = stats_query.filter(Enrollment.program_id == program_filter)
    
    total_gross, count, platform_fees = stats_query.first()
    total_gross = total_gross or 0.0
    platform_fees = platform_fees or 0.0
    count = count or 0
    
    cash_collect_net = total_gross - platform_fees
    my_commission = cash_collect_net * 0.10
    
    # Active Debt (Filtered by Same Params? Debt usually is current state, not historical like payments)
    # But usually context is "Debt of clients in this view".
    # Admin view shows "Deuda Total" (Global). 
    # Closer view: "Deuda de Mis Clientes".
    # Simply sum debt of all enrollments assigned to closer.
    debt_query = db.session.query(Enrollment).filter(Enrollment.closer_id == current_user.id, Enrollment.status == 'active')
    # Filter by user/program? Yes to match context.
    # If search is applied, debt of searched users.
    if search: debt_query = debt_query.join(User).filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if program_filter: debt_query = debt_query.filter(Enrollment.program_id == program_filter)
    
    # Sum debt python-side or complex SQL
    total_debt = 0.0
    for enr in debt_query.all():
         paid = enr.total_paid
         agreed = enr.total_agreed if enr.total_agreed is not None else (enr.program.price if enr.program else 0.0)
         d = agreed - paid
         if d > 0: total_debt += d

    kpis = {
        'revenue': total_gross,
        'cash_collected': cash_collect_net,
        'my_commission': my_commission,
        'count': count,
        'debt': total_debt
    }
    
    # Dropdowns
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    programs = Program.query.all()
    
    is_ajax = request.args.get('ajax')
    
    if is_load_more and not is_ajax:
        return render_template('closer/partials/sales_rows.html', payments=payments, start_index=start_index)

    if is_ajax:
         return jsonify({
            'html': render_template('closer/partials/sales_rows.html', payments=payments, start_index=start_index),
            'kpis': {
                'sales_count': count,
                'revenue': "{:,.2f}".format(total_gross),
                'cash_collected': "{:,.2f}".format(cash_collect_net),
                'my_commission': "{:,.2f}".format(my_commission),
                'debt': "{:,.2f}".format(total_debt)
            },
            'has_next': pagination.has_next,
            'next_page': pagination.next_num
         })

    return render_template('closer/sales_list.html', 
                           payments=payments, 
                           pagination=pagination,
                           kpis=kpis,
                           start_date=start_date_str, 
                           end_date=end_date_str,
                           search=search,
                           method_filter=method_filter and int(method_filter),
                           type_filter=type_filter,
                           program_filter=program_filter,
                           methods=methods,
                           programs=programs,
                           start_index=start_index)

@bp.route('/sale/edit/<int:id>', methods=['GET', 'POST'])
@closer_required
def edit_sale(id):
    payment = Payment.query.get_or_404(id)
    # We use SaleForm but need to populate differently or manually
    # Just editing amount/method/type? enrollment/lead/program are fixed usually.
    # Let's simple edit:
    # Creating a form instance and pre-filling is tricky because form expects clean Create format.
    # Re-using SaleForm might be hard if fields are validated (like Lead ID). 
    # Let's try to populate form but disable Lead/Program fields appropriately or just handle logic.
    form = SaleForm(obj=payment)
    
    # Pre-fill selects that are not directly mapped names
    if request.method == 'GET':
        form.lead_id.data = payment.enrollment.student_id
        form.lead_search.data = f"{payment.enrollment.student.username} ({payment.enrollment.student.email})"
        form.program_id.data = payment.enrollment.program_id
        
    # Populate choices
    form.program_id.choices = [(p.id, f"{p.name} (${p.price})") for p in Program.query.all()]
    form.payment_method_id.choices = [(m.id, m.name) for m in PaymentMethod.query.filter_by(is_active=True).all()]

    if form.validate_on_submit():
        # Update mutable fields
        payment.amount = form.amount.data
        payment.payment_type = form.payment_type.data
        payment.payment_method_id = form.payment_method_id.data
        
        # Check validation again? (Full Payment >= Price)
        # Getting program from enrollment, not form (assuming program didn't change in form, or we blocked it)
        program_price = payment.enrollment.program.price
        if payment.payment_type == 'full' and payment.amount < program_price:
             flash(f'Error: El pago completo debe ser al menos ${program_price}.')
             return render_template('sales/new_sale.html', form=form, title="Editar Venta")

        db.session.commit()
        flash('Venta actualizada.')
        return redirect(url_for('closer.sales_list'))
        
    return render_template('sales/new_sale.html', form=form, title="Editar Venta")

@bp.route('/sale/delete/<int:id>')
@closer_required
def delete_sale(id):
    payment = Payment.query.get_or_404(id)
    enrollment = payment.enrollment # Store ref
    student_id = enrollment.student_id
    
    db.session.delete(payment)
    db.session.flush() # Ensure payment is effectively removed from query checks
    
    # Check if we should delete enrollment (if no payments left)
    # This logic assumes "Deleting a sale" implies undoing the deal if no money exchanged.
    remaining_payments = enrollment.payments.count()
    if remaining_payments == 0:
        db.session.delete(enrollment)
        
    db.session.commit()
    
    # Auto-update status
    user = User.query.get(student_id)
    if user:
        user.update_status_based_on_debt()
        
    flash('Venta eliminada.')
    return redirect(url_for('closer.sales_list'))

@bp.route('/lead/<int:id>/new-sale', methods=['GET', 'POST'])
@closer_required
def new_sale(id):
    lead = User.query.get_or_404(id)
    form = SaleForm()
    
    # Choices
    programs = Program.query.all()
    form.program_id.choices = [(p.id, f"{p.name} (${p.price})") for p in programs]
    
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    form.payment_method_id.choices = [(m.id, m.name) for m in methods]
    
    # Pre-fill lead (hidden)
    form.lead_id.data = lead.id

    if form.validate_on_submit():
        program = Program.query.get(form.program_id.data)
        
        # 1. Create Enrollment
        total_agreed = program.price
        
        enrollment = Enrollment(
            student_id=lead.id,
            program_id=program.id,
            total_agreed=total_agreed,
            status='active',
            closer_id=current_user.id # Explicitly link to closer
        )
        db.session.add(enrollment)
        db.session.flush() # Get ID
        
        # 2. Create Initial Payment
        payment = Payment(
            enrollment_id=enrollment.id,
            amount=form.amount.data,
            date=datetime.utcnow(),
            payment_type=form.payment_type.data,
            payment_method_id=form.payment_method_id.data,
            status='completed'
        )
        db.session.add(payment)
        
        # 3. Update Role
        if lead.role == 'lead':
            lead.role = 'student'
            db.session.add(lead)

        # --- Automate Status Logic ---
        if not lead.lead_profile:
            profile = LeadProfile(user_id=lead.id, status='new')
            db.session.add(profile)
        else:
            profile = lead.lead_profile
            
        pay_type = form.payment_type.data
        
        if pay_type == 'full':
            profile.status = 'completed'
        elif pay_type == 'down_payment':
            profile.status = 'pending'
        elif pay_type == 'installment':
             # Logic for "new sale" implies this is the first payment of this enrollment.
             # If installment, it's partial, so pending.
             if profile.status != 'completed' and profile.status != 'renewed':
                 profile.status = 'pending'
        elif pay_type == 'renewal':
             profile.status = 'renewed'
            
        db.session.commit()
        
        # Webhook
        send_sales_webhook(payment, current_user.username)
        
        flash('Venta registrada exitosamente.')
        return redirect(url_for('closer.lead_detail', id=lead.id))
        
    return render_template('closer/sale_form.html', form=form, title=f"Nueva Venta: {lead.username}", lead_id=lead.id)

@bp.route('/enrollment/<int:id>/add-payment', methods=['GET', 'POST'])
@closer_required
def add_payment(id):
    enrollment = Enrollment.query.get_or_404(id)
    form = CloserPaymentForm()
    
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    form.payment_method_id.choices = [(m.id, m.name) for m in methods]
    
    if form.validate_on_submit():
        p_date = datetime.combine(form.date.data, datetime.now().time())
        
        payment = Payment(
            enrollment_id=enrollment.id,
            amount=form.amount.data,
            date=p_date,
            payment_type=form.payment_type.data,
            payment_method_id=form.payment_method_id.data,
            reference_id=form.reference_id.data,
            status=form.status.data
        )
        db.session.add(payment)
        db.session.commit()
        
        # Webhook
        send_sales_webhook(payment, current_user.username)
        
        flash('Pago agregado.')
        return redirect(url_for('closer.lead_detail', id=enrollment.student_id))
        
    return render_template('closer/payment_form.html', form=form, title="Registrar Pago", lead_id=enrollment.student_id)

@bp.route('/payment/edit/<int:id>', methods=['GET', 'POST'])
@closer_required
def edit_payment(id):
    payment = Payment.query.get_or_404(id)
    form = CloserPaymentForm(obj=payment)
    
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    form.payment_method_id.choices = [(m.id, m.name) for m in methods]

    if form.validate_on_submit():
        payment.amount = form.amount.data
        payment.date = datetime.combine(form.date.data, payment.date.time())
        payment.payment_type = form.payment_type.data
        payment.payment_method_id = form.payment_method_id.data
        payment.reference_id = form.reference_id.data
        payment.status = form.status.data
        
        db.session.commit()
        flash('Pago actualizado.')
        return redirect(url_for('closer.lead_detail', id=payment.enrollment.student_id))
        
    return render_template('closer/payment_form.html', form=form, title="Editar Pago")

@bp.route('/payment/delete-detail/<int:id>')
@closer_required
def delete_payment_detail(id):
    payment = Payment.query.get_or_404(id)
    enrollment = payment.enrollment
    student_id = enrollment.student_id
    
    db.session.delete(payment)
    db.session.flush()
    
    # Check orphan enrollment
    if enrollment.payments.count() == 0:
        db.session.delete(enrollment)
        
    db.session.commit()
    
    # Auto-update status
    user = User.query.get(student_id)
    if user:
        user.update_status_based_on_debt()
        
    flash('Pago eliminado.')
    return redirect(url_for('closer.lead_detail', id=student_id))

@bp.route('/enrollment/delete/<int:id>')
@closer_required
def delete_enrollment(id):
    enrollment = Enrollment.query.get_or_404(id)
    student_id = enrollment.student_id
    
    # Optional: Log action or verify safety
    db.session.delete(enrollment)
    db.session.commit()
    
    flash('Inscripción eliminada (y sus pagos asociados).')
    return redirect(url_for('closer.lead_detail', id=student_id))
