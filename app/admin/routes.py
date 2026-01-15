
from flask import render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.admin import bp
from app.admin.forms import UserForm, SurveyQuestionForm, EventForm, ProgramForm, PaymentMethodForm, ClientEditForm, PaymentForm, ExpenseForm, RecurringExpenseForm, EventGroupForm, ManualAddForm, AdminSaleForm
from app.closer.forms import SaleForm, LeadForm
from app.closer.utils import send_sales_webhook
from app.models import User, SurveyQuestion, Event, Program, PaymentMethod, db, Enrollment, Payment, Appointment, LeadProfile, Expense, RecurringExpense, EventGroup 
from datetime import datetime, date, time, timedelta
from sqlalchemy import or_


from functools import wraps

# Decorator to ensure admin access
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('No tienes permiso para acceder a esta página.')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

@bp.route('/dashboard')
@admin_required
def dashboard():
    # --- Date Filtering Logic ---
    today = date.today()
    period = request.args.get('period', 'this_month')
    start_date_arg = request.args.get('start_date')
    end_date_arg = request.args.get('end_date')

    if period == 'custom' and start_date_arg and end_date_arg:
        try:
            start_date = datetime.strptime(start_date_arg, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_arg, '%Y-%m-%d').date()
        except ValueError:
            # Fallback to this month if parse fails
            start_date = today.replace(day=1)
            next_month = today.replace(day=28) + timedelta(days=4)
            end_date = next_month - timedelta(days=next_month.day)
            period = 'this_month'
    elif period == 'last_3_months':
        end_date = today
        start_date = today - timedelta(days=90)
    else: # 'this_month' or default
        start_date = today.replace(day=1)
        next_month = today.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)
        period = 'this_month'

    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)

    # 1. Income This Month
    payments_month = Payment.query.filter(
        Payment.date >= start_dt,
        Payment.date <= end_dt,
        Payment.status == 'completed'
    ).all()
    
    income_month = 0
    total_commission_month = 0
    
    for p in payments_month:
        income_month += p.amount
        if p.method:
            comm = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
            total_commission_month += comm
            
    cash_collected_month = income_month - total_commission_month
    
    # 2. Expenses and Net Profit (Selected Period)
    total_expenses = db.session.query(db.func.sum(Expense.amount)).filter(
        Expense.date >= start_dt,
        Expense.date <= end_dt
    ).scalar() or 0
    
    net_profit = cash_collected_month - total_expenses
    
    # 3. Global Debt (Snapshot)
    total_active_agreed = db.session.query(db.func.sum(Enrollment.total_agreed)).filter(
        Enrollment.status == 'active'
    ).scalar() or 0
    
    total_active_paid = db.session.query(db.func.sum(Payment.amount)).join(Enrollment).filter(
        Enrollment.status == 'active',
        Payment.status == 'completed'
    ).scalar() or 0
    
    global_debt = total_active_agreed - total_active_paid
    if global_debt < 0: global_debt = 0
    
    # 3. Active Leads Count
    leads_count = User.query.filter_by(role='lead').count()
    
    # 4. Closing Rate This Month
    sales_count = Enrollment.query.filter(
        Enrollment.enrollment_date >= start_dt,
        Enrollment.enrollment_date <= end_dt
    ).count()
    
    appts_completed = Appointment.query.filter(
        Appointment.start_time >= start_dt,
        Appointment.start_time <= end_dt,
        Appointment.status == 'completed'
    ).count()
    
    closing_rate = 0
    if appts_completed > 0:
        closing_rate = (sales_count / appts_completed) * 100

    # 5. Top 5 Debtors
    active_enrollments = Enrollment.query.filter_by(status='active').all()
    debtors = []
    for enr in active_enrollments:
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
    
    # Sort by debt desc and take top 10
    top_debtors = sorted(debtors, key=lambda x: x['debt'], reverse=True)[:10]
    
    # 6. Chart Data: Revenue Trend (Selected Period)
    dates_labels = []
    daily_revenue_values = []
    
    # Use selected dates for chart
    start_date_chart = start_date
    end_date_chart = end_date
    
    # Fill dictionary with 0 for every day in range
    daily_data = {}
    current_d = start_date_chart
    while current_d <= end_date_chart:
        daily_data[current_d.strftime('%Y-%m-%d')] = 0
        current_d += timedelta(days=1)
        
    start_dt_chart = datetime.combine(start_date_chart, time.min)
    end_dt_chart = datetime.combine(end_date_chart, time.max)
    
    payments_30d = db.session.query(
        db.func.date(Payment.date).label('pdate'), 
        db.func.sum(Payment.amount)
    ).filter(
        Payment.date >= start_dt_chart,
        Payment.date <= end_dt_chart,
        Payment.status == 'completed'
    ).group_by(db.func.date(Payment.date)).all()
    
    for p_date, p_amount in payments_30d:
        # p_date might be string or date object depending on driver
        d_str = str(p_date) 
        if d_str in daily_data:
            daily_data[d_str] = p_amount
            
    dates_labels = list(daily_data.keys())
    daily_revenue_values = list(daily_data.values())
    
    # 7. Chart Data: Sales by Program (This Month - Sales Volume)
    # Using 'total_agreed' from Enrollments this month
    programs_data = db.session.query(Program.name, db.func.sum(Enrollment.total_agreed)).join(Enrollment).filter(
        Enrollment.enrollment_date >= start_dt,
        Enrollment.enrollment_date <= end_dt
    ).group_by(Program.name).all()
    
    prog_labels = [p[0] for p in programs_data]
    prog_values = [msg[1] for msg in programs_data]
    
    # 8. Chart Data: Clients by Status
    status_counts = db.session.query(
        LeadProfile.status, 
        db.func.count(LeadProfile.id)
    ).group_by(LeadProfile.status).all()
    
    status_labels = []
    status_values = []
    
    for s_status, s_count in status_counts:
        # Simple formatting
        label = s_status if s_status else 'Sin Estado'
        status_labels.append(label.capitalize())
        status_values.append(s_count)
        
    # 9. Chart Data: Sales by Payment Method (This Month)
    # Group payments by payment method name
    methods_data = db.session.query(
        PaymentMethod.name, 
        db.func.sum(Payment.amount)
    ).join(Payment).filter(
        Payment.date >= start_dt,
        Payment.date <= end_dt,
        Payment.status == 'completed'
    ).group_by(PaymentMethod.name).all()
    
    method_labels = [m[0] for m in methods_data]
    method_values = [m[1] for m in methods_data]

    # 10. Recent Activity Feed (Top 10 mixed events)
    recent_activity = []
    
    # Recent Payments
    r_payments = Payment.query.filter_by(status='completed').order_by(Payment.date.desc()).limit(5).all()
    for p in r_payments:
        recent_activity.append({
            'type': 'payment',
            'time': p.date,
            'message': f"Pago de ${p.amount:,.2f} recibido",
            'sub': f"Alumno: {p.enrollment.student.username}",
            'icon': 'currency-dollar'
        })
        
    # Recent Enrollments
    r_enrollments = Enrollment.query.order_by(Enrollment.enrollment_date.desc()).limit(5).all()
    for e in r_enrollments:
        recent_activity.append({
            'type': 'enrollment',
            'time': e.enrollment_date,
            'message': f"Nueva inscripción: {e.program.name}",
            'sub': f"Alumno: {e.student.username}",
            'icon': 'academic-cap' 
        })
        
    # Recent Leads
    r_leads = User.query.filter_by(role='lead').order_by(User.created_at.desc()).limit(5).all()
    for l in r_leads:
        recent_activity.append({
            'type': 'lead',
            'time': l.created_at,
            'message': f"Nuevo Lead registrado",
            'sub': f"{l.username} ({l.email})",
            'icon': 'user-add'
        })
        
    # Recent Appointments
    r_appts = Appointment.query.order_by(Appointment.start_time.desc()).limit(5).all()
    for a in r_appts:
        recent_activity.append({
            'type': 'appointment',
            'time': a.start_time, # Using start_time as proxy
            'message': f"Cita programada",
            'sub': f"Con {a.lead.username} para el {a.start_time.strftime('%d/%m %H:%M')}",
            'icon': 'calendar'
        })

    # Sort items by time desc and take top 10
    recent_activity.sort(key=lambda x: x['time'], reverse=True)
    recent_activity = recent_activity[:10]

    return render_template('admin/dashboard.html',
                           income_month=income_month,
                           global_debt=global_debt,
                           leads_count=leads_count,
                           closing_rate=closing_rate,
                           cash_collected_month=cash_collected_month,
                           top_debtors=top_debtors,
                           dates_labels=dates_labels,
                           daily_revenue_values=daily_revenue_values,
                           prog_labels=prog_labels,
                           prog_values=prog_values,
                           status_labels=status_labels,
                           status_values=status_values,
                           method_labels=method_labels,
                           method_values=method_values,
                           recent_activity=recent_activity,
                           current_period=period,
                           start_date_filter=start_date.strftime('%Y-%m-%d'),
                           end_date_filter=end_date.strftime('%Y-%m-%d'),
                           total_expenses=total_expenses,
                           net_profit=net_profit,
                           total_commission_month=total_commission_month)

# --- User Management Routes ---

@bp.route('/users')
@admin_required
def users_list():
    users = User.query.filter(User.role.in_(['admin', 'closer'])).all()
    return render_template('admin/users_list.html', users=users)

@bp.route('/users/create', methods=['GET', 'POST'])
@admin_required
def create_user():
    form = UserForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'Usuario {user.username} creado exitosamente.')
        return redirect(url_for('admin.users_list'))
    return render_template('admin/user_form.html', form=form, title='Nuevo Usuario')

@bp.route('/users/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_user(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.role = form.role.data
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f'Usuario {user.username} actualizado.')
        return redirect(url_for('admin.users_list'))
    return render_template('admin/user_form.html', form=form, title='Editar Usuario')

@bp.route('/users/delete/<int:id>')
@admin_required
def delete_user(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('No puedes eliminar tu propio usuario.')
        return redirect(url_for('admin.users_list'))
    
    # Capture role before delete to determine redirect
    deleted_role = user.role
    
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {user.username} eliminado.')
    
    if deleted_role in ['lead', 'student']:
        return redirect(url_for('admin.leads_list'))
        
    return redirect(url_for('admin.users_list'))

@bp.route('/leads')
@admin_required
def leads_list():
    # Filter Params
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    program_filter = request.args.get('program')
    status_filter = request.args.get('status')
    sort_by = request.args.get('sort_by', 'newest') # newest, oldest, a-z, z-a

    # Base query for leads/students/agendas
    query = User.query.filter(User.role.in_(['lead', 'student', 'agenda']))

    # Joins for filtering
    if status_filter:
        query = query.join(LeadProfile).filter(LeadProfile.status == status_filter)
    
    if program_filter:
        query = query.join(Enrollment, Enrollment.student_id == User.id).filter(Enrollment.program_id == program_filter)

    # Date Filter
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
    else: # newest (default)
        query = query.order_by(User.created_at.desc())

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    leads = pagination.items

    start_index = (page -1) * per_page
    
    # KPI Calculations (Simplified query to avoid complex rebuilding)
    # Re-build base query for KPIs
    kpi_query = User.query.filter(User.role.in_(['lead', 'student', 'agenda']))
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        kpi_query = kpi_query.filter(User.created_at >= start_date)
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        kpi_query = kpi_query.filter(User.created_at < end_date)
    if search:
        search_term = f"%{search}%"
        kpi_query = kpi_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
        
    total_users = kpi_query.count()
    
    # Statuses KPI
    status_counts = db.session.query(LeadProfile.status, db.func.count(User.id)).select_from(User).join(LeadProfile).filter(User.role.in_(['lead', 'student', 'agenda']))
    if start_date_str: status_counts = status_counts.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: status_counts = status_counts.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    status_counts = status_counts.group_by(LeadProfile.status).all()

    # Programs KPI
    program_counts = db.session.query(Program.name, db.func.count(Enrollment.id)).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Program).filter(User.role.in_(['lead', 'student', 'agenda']))
    if start_date_str: program_counts = program_counts.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: program_counts = program_counts.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    program_counts = program_counts.group_by(Program.name).all()

    kpis = {
        'total': total_users,
        'statuses': dict(status_counts),
        'programs': dict(program_counts)
    }
    
    # Context for filters
    all_programs = Program.query.order_by(Program.name).all()
    # Unique statuses for dropdown
    all_statuses = db.session.query(LeadProfile.status).distinct().filter(LeadProfile.status != None).all()
    all_statuses = [s[0] for s in all_statuses]

    return render_template('admin/leads_list.html', 
                           title='Gestión de Clientes', 
                           leads=leads, 
                           pagination=pagination, 
                           search=search, 
                           start_date=start_date_str, 
                           end_date=end_date_str,
                           program_filter=program_filter,
                           status_filter=status_filter,
                           sort_by=sort_by,
                           kpis=kpis,
                           start_index=start_index,
                           all_programs=all_programs,
                           all_statuses=all_statuses)

@bp.route('/users/add-manual', methods=['GET', 'POST'])
@admin_required
def add_manual_user():
    form = ManualAddForm()
    if form.validate_on_submit():
        # Determine status based on role
        status = 'new'
        if form.role.data == 'student':
            status = 'completed' # Assuming completed sale if added as client
        
        # Create User
        user = User(
            username=form.username.data,
            email=form.email.data,
            role=form.role.data
        )
        # Set default generic password or handle auth differently. 
        # Ideally, send invite email. For now, set a default.
        user.set_password('12345678') 
        
        try:
            db.session.add(user)
            db.session.flush() # To get user ID
            
            # Create Profile
            profile = LeadProfile(
                user_id=user.id,
                phone=form.phone.data,
                instagram=form.instagram.data,
                status=status,
                utm_source='manual'
            )
            db.session.add(profile)
            db.session.commit()
            flash(f'Usuario {user.username} agregado exitosamente.')
            return redirect(url_for('admin.leads_list'))
            
        except Exception as e:
            db.session.rollback()
            flash('Error: El email o usuario ya existe.')
            print(e)
            
    return render_template('admin/add_manual_user.html', form=form, title='Agregar Nuevo Cliente')

@bp.route('/leads/profile/<int:id>')
@admin_required
def lead_profile(id):
    user = User.query.get_or_404(id)
    if user.role not in ['lead', 'student', 'agenda']:
        flash('Perfil no disponible para este rol.')
        return redirect(url_for('admin.leads_list'))
    
    # Fetch related data
    enrollments = user.enrollments.order_by(Enrollment.enrollment_date.desc()).all()
    appointments = user.appointments_as_lead.order_by(Appointment.start_time.desc()).all()
    
    return render_template('admin/lead_profile.html', user=user, enrollments=enrollments, appointments=appointments)

@bp.route('/leads/update/<int:id>', methods=['POST'])
@admin_required
def update_lead_quick(id):
    user = User.query.get_or_404(id)
    
    # Update Role
    new_role = request.form.get('role')
    if new_role in ['lead', 'student', 'agenda']:
        user.role = new_role
        
    # Update Status
    new_status = request.form.get('status')
    if new_status:
        if not user.lead_profile:
            # Create profile if missing (unlikely for leads but possible)
            profile = LeadProfile(user_id=user.id)
            db.session.add(profile)
        
        user.lead_profile.status = new_status
        
    db.session.commit()
    flash('Usuario actualizado.')
    return redirect(url_for('admin.leads_list'))

@bp.route('/leads/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_client(id):
    user = User.query.get_or_404(id)
    form = ClientEditForm()
    
    if request.method == 'GET':
        form.username.data = user.username
        form.email.data = user.email
        form.role.data = user.role
        if user.lead_profile:
            form.phone.data = user.lead_profile.phone
            form.instagram.data = user.lead_profile.instagram
            form.status.data = user.lead_profile.status
    
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.role = form.role.data
        
        if not user.lead_profile:
            profile = LeadProfile(user_id=user.id)
            db.session.add(profile)
        
        user.lead_profile.phone = form.phone.data
        user.lead_profile.instagram = form.instagram.data
        user.lead_profile.status = form.status.data
        
        try:
            db.session.commit()
            flash('Cliente actualizado exitosamente.')
            return redirect(url_for('admin.edit_client', id=user.id))
        except Exception as e:
            db.session.rollback()
            flash('Error al actualizar datos.')

    enrollments = user.enrollments.all()
    
    return render_template('admin/client_edit.html', user=user, form=form, enrollments=enrollments)

@bp.route('/payments/add/<int:enrollment_id>', methods=['GET', 'POST'])
@admin_required
def add_payment(enrollment_id):
    enrollment = Enrollment.query.get_or_404(enrollment_id)
    form = PaymentForm()
    next_url = request.args.get('next')
    
    # Populate methods
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    form.payment_method_id.choices = [(m.id, m.name) for m in methods]
    
    if form.validate_on_submit():
        payment = Payment(
            enrollment_id=enrollment.id,
            amount=form.amount.data,
            date=datetime.combine(form.date.data, datetime.now().time()), # Use current time
            payment_type=form.payment_type.data,
            payment_method_id=form.payment_method_id.data,
            reference_id=form.reference_id.data,
            status=form.status.data
        )
        db.session.add(payment)
        db.session.commit()
        
        # Webhook
        send_sales_webhook(payment, current_user.username)
        
        # Auto-update status
        user = User.query.get(enrollment.student_id)
        if user:
            user.update_status_based_on_debt()
        
        flash('Pago registrado.')
        if next_url:
            return redirect(next_url)
            
        # Default behavior: go to edit client (legacy) or lead profile (if user came from there but no next?)
        # Let's default to edit_client to preserve old behavior unless 'next' is used.
        return redirect(url_for('admin.edit_client', id=enrollment.student_id))
        
    return render_template('admin/payment_form.html', form=form, title=f"Nuevo Pago - {enrollment.program.name}")

@bp.route('/payments/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_payment(id):
    payment = Payment.query.get_or_404(id)
    form = PaymentForm(obj=payment)
    
    methods = PaymentMethod.query.filter_by(is_active=True).all()
    form.payment_method_id.choices = [(m.id, m.name) for m in methods]
    
    if request.method == 'GET':
         # Handle datetime to date for form
         if payment.date:
             form.date.data = payment.date.date()
    
    if form.validate_on_submit():
        payment.amount = form.amount.data
        payment.date = datetime.combine(form.date.data, datetime.min.time())
        payment.payment_type = form.payment_type.data
        payment.payment_method_id = form.payment_method_id.data
        payment.reference_id = form.reference_id.data
        payment.status = form.status.data
        
        db.session.commit()
        
        # Auto-update status
        user = User.query.get(payment.enrollment.student_id)
        if user:
            user.update_status_based_on_debt()
        
        flash('Pago actualizado.')
        
        next_url = request.args.get('next')
        if next_url:
            return redirect(next_url)
            
        return redirect(url_for('admin.edit_client', id=payment.enrollment.student_id))
        
    return render_template('admin/payment_form.html', form=form, title="Editar Pago")

@bp.route('/payments/delete/<int:id>')
@admin_required
def delete_payment(id):
    payment = Payment.query.get_or_404(id)
    enrollment = payment.enrollment
    student_id = enrollment.student_id # Store before deleting payment
    
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
    
    next_url = request.args.get('next')
    if next_url:
        return redirect(next_url)
        
    return redirect(url_for('admin.edit_client', id=student_id))



# --- Survey Management Routes ---

@bp.route('/survey', methods=['GET', 'POST'])
@admin_required
def survey_mgmt():
    form = SurveyQuestionForm()
    
    # Populate Choices (Global, Groups, Events)
    events = Event.query.all()
    groups = EventGroup.query.all()
    
    choices = [('global_0', 'Global (Todos los eventos)')]
    choices += [(f'group_{g.id}', f'Grupo: {g.name}') for g in groups]
    choices += [(f'event_{e.id}', f'Evento: {e.name}') for e in events]
    form.target.choices = choices

    if form.validate_on_submit():
        target = form.target.data
        t_type, t_id = target.split('_')
        t_id = int(t_id)
        
        evt_id = None
        grp_id = None
        
        if t_type == 'event':
            evt_id = t_id
        elif t_type == 'group':
            grp_id = t_id
            
        q = SurveyQuestion(
            text=form.text.data,
            question_type=form.question_type.data,
            options=form.options.data,
            order=0, # Default order, managed by builder
            is_active=form.is_active.data,
            step=form.step.data,
            mapping_field=form.mapping_field.data if form.step.data == 'landing' else None,
            event_id=evt_id,
            event_group_id=grp_id
        )
        db.session.add(q)
        db.session.commit()
        flash('Pregunta añadida exitosamente.')
        return redirect(url_for('admin.survey_mgmt'))
        
    # Filter Logic
    selected_target = request.args.get('target')
    selected_step = request.args.get('step', 'all') # 'all', 'landing', 'survey'
    
    query = SurveyQuestion.query
    
    if selected_target:
        t_type, t_id = selected_target.split('_')
        t_id = int(t_id)
        if t_type == 'global':
             query = query.filter(SurveyQuestion.event_id == None, SurveyQuestion.event_group_id == None)
        elif t_type == 'group':
             query = query.filter(SurveyQuestion.event_group_id == t_id)
        elif t_type == 'event':
             query = query.filter(SurveyQuestion.event_id == t_id)

    if selected_step != 'all':
        query = query.filter(SurveyQuestion.step == selected_step)

    all_questions = query.order_by(SurveyQuestion.order).all()
    
    landing_questions = [q for q in all_questions if q.step == 'landing']
    survey_questions = [q for q in all_questions if q.step == 'survey']
    
    return render_template('admin/survey_mgmt.html', form=form, landing_questions=landing_questions, survey_questions=survey_questions, title="Nueva Pregunta", choices=choices, selected_target=selected_target)

@bp.route('/survey/funnel', methods=['GET'])
@login_required
@admin_required
def funnel_builder():
    target = request.args.get('target', 'global')
    
    # 1. Determine Scope & Funnel Steps
    funnel_steps = ['contact', 'calendar', 'survey'] # Default
    
    if target.startswith('event_'):
        evt_id = int(target.split('_')[1])
        event = Event.query.get_or_404(evt_id)
        if event.funnel_steps: funnel_steps = event.funnel_steps
        
        # Fetch Questions for this Event (Global + Group + Event)
        # For builder, we usually want to see ALL active questions that apply here?
        # OR just the ones specific to this event? 
        # For "reordering", we probably want to see the *resolved* list of questions the user would see.
        # But editing "step" on a global question from an event view might be dangerous.
        # Let's start simpler: Show ALL questions, but indicate their origin? 
        # For now, let's just show questions that match the filter logic used in booking.
        
        # Actually, if I reorder a global question from an event scope, does it affect global? Yes.
        # So usually builders like this work on a specific scope. 
        # If I am in "Global" mode, I order global questions.
        # If I am in "Event" mode, I see inherited questions + event questions.
        # If I move a global question, does it change for everyone? Yes.
        # This is complex. Let's simplify: 
        # The builder only SHOWS questions that belong to the current scope + inherited ones.
        # Reordering updates the "order" field. 
        # CAUTION: Shared questions have a single "order" field. Reordering in one event changes it everywhere.
        # To fix this properly requires a many-to-many "EventQuestion" link with order. 
        # BUT for this MVP, let's assume the user manages Global questions in Global view, and Event questions in Event view.
        # If they mix, the order is global. 
        
        query = SurveyQuestion.query.filter_by(is_active=True)
        # Filter logic similar to booking, but maybe we just show ALL valid questions for this context
        conditions = [SurveyQuestion.event_id == evt_id]
        if event.group_id:
             conditions.append(SurveyQuestion.event_group_id == event.group_id)
        global_condition = (SurveyQuestion.event_id == None) & (SurveyQuestion.event_group_id == None)
        conditions.append(global_condition)
        query = query.filter(or_(*conditions))
        
    elif target.startswith('group_'):
        grp_id = int(target.split('_')[1])
        group = EventGroup.query.get_or_404(grp_id)
        if group.funnel_steps: funnel_steps = group.funnel_steps
        
        query = SurveyQuestion.query.filter_by(is_active=True)
        conditions = [SurveyQuestion.event_group_id == grp_id]
        global_condition = (SurveyQuestion.event_id == None) & (SurveyQuestion.event_group_id == None)
        conditions.append(global_condition)
        query = query.filter(or_(*conditions))
        
    else:
        # Global
        target = 'global'
        # Check if we have a global setting for steps? Currently models only have it on Event/Group.
        # Let's assume Global uses default or maybe we add a SystemConfig later. 
        # For now, default `['contact', 'calendar', 'survey']`.
        
        query = SurveyQuestion.query.filter_by(is_active=True)
        query = query.filter((SurveyQuestion.event_id == None) & (SurveyQuestion.event_group_id == None))

    all_questions = query.order_by(SurveyQuestion.step, SurveyQuestion.order).all()
    
    questions_landing = [q for q in all_questions if q.step == 'landing']
    questions_survey = [q for q in all_questions if q.step == 'survey']

    events = Event.query.all()
    groups = EventGroup.query.all()

    return render_template('admin/funnel_builder.html', 
                           funnel_steps=funnel_steps,
                           questions_landing=questions_landing,
                           questions_survey=questions_survey,
                           selected_target=target,
                           events=events,
                           groups=groups)

@bp.route('/survey/funnel/save', methods=['POST'])
@login_required
@admin_required
def save_funnel_state():
    data = request.json
    target = data.get('target')
    new_steps = data.get('funnel_steps')
    questions_data = data.get('questions') # List of {id, step, order}
    
    if not new_steps or not questions_data:
        return jsonify({'status': 'error', 'message': 'Datos incompletos'}), 400
        
    # 1. Save Funnel Steps Order
    if target.startswith('event_'):
        evt_id = int(target.split('_')[1])
        event = Event.query.get(evt_id)
        if event: event.funnel_steps = new_steps
    elif target.startswith('group_'):
        grp_id = int(target.split('_')[1])
        group = EventGroup.query.get(grp_id)
        if group: group.funnel_steps = new_steps
    # Global doesn't have a storage for this yet (model limitation), ignore for now or add later.
    
    # 2. Save Questions Step & Order
    # Note: This updates the questions GLOBALLY if they are global questions.
    for q_item in questions_data:
        q = SurveyQuestion.query.get(q_item['id'])
        if q:
            q.step = q_item['step']
            q.order = q_item['order']
    
    db.session.commit()
    return jsonify({'status': 'success'})

@bp.route('/survey/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_question(id):
    q = SurveyQuestion.query.get_or_404(id)
    form = SurveyQuestionForm(obj=q)
    
    # Populate Choices
    events = Event.query.all()
    groups = EventGroup.query.all()
    
    choices = [('global_0', 'Global (Todos los eventos)')]
    choices += [(f'group_{g.id}', f'Grupo: {g.name}') for g in groups]
    choices += [(f'event_{e.id}', f'Evento: {e.name}') for e in events]
    form.target.choices = choices
    
    if request.method == 'GET':
        if q.event_id:
            form.target.data = f'event_{q.event_id}'
        elif q.event_group_id:
            form.target.data = f'group_{q.event_group_id}'
        else:
            form.target.data = 'global_0'
    
    if form.validate_on_submit():
        q.text = form.text.data
        q.question_type = form.question_type.data
        q.options = form.options.data
        # q.order = form.order.data # Removed order assignment
        q.is_active = form.is_active.data
        
        q.step = form.step.data
        q.mapping_field = form.mapping_field.data if form.step.data == 'landing' else None
        
        t_val = form.target.data
        t_type, t_id = t_val.split('_')
        t_id = int(t_id)
        
        q.event_id = None
        q.event_group_id = None
        
        if t_type == 'event':
            q.event_id = t_id
        elif t_type == 'group':
            q.event_group_id = t_id
        
        db.session.commit()
        flash('Pregunta actualizada.')
        return redirect(url_for('admin.survey_mgmt'))
        
    questions = SurveyQuestion.query.order_by(SurveyQuestion.order).all()
    return render_template('admin/survey_mgmt.html', form=form, questions=questions, title="Editar Pregunta", editing_id=q.id, choices=choices)

@bp.route('/survey/delete/<int:id>')
@admin_required
def delete_question(id):
    q = SurveyQuestion.query.get_or_404(id)
    db.session.delete(q)
    db.session.commit()
    flash('Pregunta eliminada.')
    return redirect(url_for('admin.survey_mgmt'))

# --- Event Management Routes ---

@bp.route('/events/groups', methods=['GET', 'POST'])
@admin_required
def event_groups():
    form = EventGroupForm()
    if form.validate_on_submit():
        group = EventGroup(name=form.name.data)
        db.session.add(group)
        db.session.commit()
        flash('Grupo de eventos creado exitosamente.')
        return redirect(url_for('admin.event_groups'))
    
    groups = EventGroup.query.all()
    return render_template('admin/event_groups_list.html', form=form, groups=groups, title="Grupos de Eventos")

@bp.route('/events/groups/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_event_group(id):
    group = EventGroup.query.get_or_404(id)
    form = EventGroupForm(obj=group)
    if form.validate_on_submit():
        group.name = form.name.data
        db.session.commit()
        flash('Grupo actualizado.')
        return redirect(url_for('admin.event_groups'))
    return render_template('admin/event_group_form.html', form=form, title="Editar Grupo")

@bp.route('/events/groups/delete/<int:id>')
@admin_required
def delete_event_group(id):
    group = EventGroup.query.get_or_404(id)
    # Check dependencies - optional logic to warn if events attached?
    db.session.delete(group)
    db.session.commit()
    flash('Grupo eliminado.')
    return redirect(url_for('admin.event_groups'))

@bp.route('/events')
@admin_required
def events_list():
    events = Event.query.all()
    return render_template('admin/events_list.html', events=events)

@bp.route('/events/create', methods=['GET', 'POST'])
@admin_required
def create_event():
    form = EventForm()
    
    # Populate Groups
    groups = EventGroup.query.all()
    form.group_id.choices = [(0, 'Ninguno')] + [(g.id, g.name) for g in groups]
    
    if form.validate_on_submit():
        grp_id = form.group_id.data
        if grp_id == 0: grp_id = None
        
        event = Event(
            name=form.name.data,
            utm_source=form.utm_source.data,
            is_active=form.is_active.data,
            group_id=grp_id
        )
        db.session.add(event)
        try:
            db.session.commit()
            flash('Evento creado exitosamente.')
            return redirect(url_for('admin.events_list'))
        except Exception as e:
            db.session.rollback()
            flash('Error: El nombre o UTM ya existen.')
            
    return render_template('admin/event_form.html', form=form, title="Nuevo Evento")

@bp.route('/events/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_event(id):
    event = Event.query.get_or_404(id)
    form = EventForm(obj=event)
    
    # Populate Groups
    groups = EventGroup.query.all()
    form.group_id.choices = [(0, 'Ninguno')] + [(g.id, g.name) for g in groups]
    
    if request.method == 'GET':
        form.group_id.data = event.group_id if event.group_id else 0
    
    if form.validate_on_submit():
        event.name = form.name.data
        event.utm_source = form.utm_source.data
        event.is_active = form.is_active.data
        
        grp_id = form.group_id.data
        event.group_id = None if grp_id == 0 else grp_id
        
        try:
            db.session.commit()
            flash('Evento actualizado.')
            return redirect(url_for('admin.events_list'))
        except Exception:
            db.session.rollback()
            flash('Error al actualizar.')
            
    return render_template('admin/event_form.html', form=form, title="Editar Evento")

@bp.route('/events/delete/<int:id>')
@admin_required
def delete_event(id):
    event = Event.query.get_or_404(id)
    if event.appointments.count() > 0:
         flash('No se puede eliminar evento con citas asociadas.')
         return redirect(url_for('admin.events_list'))
         
    db.session.delete(event)
    db.session.commit()
    db.session.commit()
    flash('Evento eliminado.')
    return redirect(url_for('admin.events_list'))

# --- Program Management Routes ---

@bp.route('/programs')
@admin_required
def programs_list():
    programs = Program.query.all()
    return render_template('admin/programs_list.html', programs=programs)

@bp.route('/programs/create', methods=['GET', 'POST'])
@admin_required
def create_program():
    form = ProgramForm()
    if form.validate_on_submit():
        program = Program(
            name=form.name.data,
            price=form.price.data
        )
        db.session.add(program)
        try:
            db.session.commit()
            flash('Programa creado exitosamente.')
            return redirect(url_for('admin.programs_list'))
        except Exception:
            db.session.rollback()
            flash('Error: El nombre del programa ya existe.')
            
    return render_template('admin/program_form.html', form=form, title="Nuevo Programa")

@bp.route('/programs/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_program(id):
    program = Program.query.get_or_404(id)
    form = ProgramForm(obj=program)
    
    if form.validate_on_submit():
        program.name = form.name.data
        program.price = form.price.data
        try:
            db.session.commit()
            flash('Programa actualizado.')
            return redirect(url_for('admin.programs_list'))
        except Exception:
            db.session.rollback()
            flash('Error al actualizar programa.')
            
    return render_template('admin/program_form.html', form=form, title="Editar Programa")

@bp.route('/programs/delete/<int:id>')
@admin_required
def delete_program(id):
    program = Program.query.get_or_404(id)
    # Check if used?
    if program.enrollments:
        flash('No se puede eliminar programa con alumnos inscritos.')
        return redirect(url_for('admin.programs_list'))
        
    db.session.delete(program)
    db.session.commit()
    flash('Programa eliminado.')
    return redirect(url_for('admin.programs_list'))

# --- Finances & Dashboard ---

@bp.route('/finances', methods=['GET', 'POST'])
@admin_required
def finances():
    today = date.today()
    
    # 1. Date Filters
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    else:
        # Default to current month start
        start_date = datetime.combine(today.replace(day=1), time.min)

    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        # if input is date only, make it end of day
        if end_date.hour == 0:
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Default to current month end
        next_month = today.replace(day=28) + timedelta(days=4)
        end_date = datetime.combine(next_month - timedelta(days=next_month.day), time.max)

    # 2. Expenses Logic
    # New Expense Form
    expense_form = ExpenseForm()
    if expense_form.validate_on_submit():
        expense = Expense(
            description=expense_form.description.data,
            amount=expense_form.amount.data,
            date=datetime.combine(expense_form.date.data, datetime.now().time()),
            category=expense_form.category.data,
            is_recurring=False
        )
        db.session.add(expense)
        db.session.commit()
        flash('Gasto registrado.')
        return redirect(url_for('admin.finances'))

    # Query Expenses
    expenses_query = Expense.query.filter(
        Expense.date >= start_date,
        Expense.date <= end_date
    ).order_by(Expense.date.desc())
    expenses = expenses_query.all()
    total_expenses = sum(e.amount for e in expenses)

    # 3. Recurring Expenses Configurations
    recurring_form = RecurringExpenseForm()
    recurring_expenses = RecurringExpense.query.all()
    
    # 4. Income & Cash Collected Logic
    payments = Payment.query.filter(
        Payment.date >= start_date,
        Payment.date <= end_date,
        Payment.status == 'completed'
    ).all()
    
    gross_revenue = 0
    total_commission = 0
    
    for p in payments:
        gross_revenue += p.amount
        if p.method:
            comm = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
            total_commission += comm
            
    cash_collected = gross_revenue - total_commission
    net_profit = cash_collected - total_expenses
    
    # Pass date *strings* for input values if they were provided, else format dates
    s_date_val = start_date_str if start_date_str else start_date.strftime('%Y-%m-%d')
    e_date_val = end_date_str if end_date_str else end_date.strftime('%Y-%m-%d')

    return render_template('admin/finances.html',
                           start_date=s_date_val,
                           end_date=e_date_val,
                           gross_revenue=gross_revenue,
                           total_commission=total_commission,
                           cash_collected=cash_collected,
                           total_expenses=total_expenses,
                           net_profit=net_profit,
                           expenses=expenses,
                           recurring_expenses=recurring_expenses,
                           expense_form=expense_form,
                           recurring_form=recurring_form)

@bp.route('/finances/recurring/add', methods=['POST'])
@admin_required
def add_recurring_expense():
    form = RecurringExpenseForm()
    if form.validate_on_submit():
        rexp = RecurringExpense(
            description=form.description.data,
            amount=form.amount.data,
            day_of_month=form.day_of_month.data,
            is_active=bool(form.is_active.data)
        )
        db.session.add(rexp)
        db.session.commit()
        flash('Gasto fijo configurado.')
    else:
        flash('Error al agregar gasto fijo. Verifique los datos.')
    return redirect(url_for('admin.finances'))

@bp.route('/finances/generate', methods=['POST'])
@admin_required
def generate_monthly_expenses():
    # Copy active recurring expenses to actual expenses for THIS month
    active_recurring = RecurringExpense.query.filter_by(is_active=True).all()
    today = date.today()
    count = 0
    
    for rex in active_recurring:
        # Check if already generated for this month?
        start_month = datetime.combine(today.replace(day=1), time.min)
        exists = Expense.query.filter(
            Expense.is_recurring == True,
            Expense.description == rex.description,
            Expense.date >= start_month
        ).first()
        
        if not exists:
            try:
                # Handle day calculation safely
                month_range = today.replace(day=28) + timedelta(days=4)
                last_day = (month_range - timedelta(days=month_range.day)).day
                day = min(rex.day_of_month, last_day)
                
                exp_date = datetime.combine(today.replace(day=day), datetime.now().time())
                
                exp = Expense(
                    description=rex.description,
                    amount=rex.amount,
                    date=exp_date,
                    category='fixed',
                    is_recurring=True
                )
                db.session.add(exp)
                count += 1
            except Exception:
                pass
                
    db.session.commit()
    flash(f'Se generaron {count} gastos fijos para este mes.')
    return redirect(url_for('admin.finances'))

@bp.route('/appointments')
@admin_required
def appointments_list():
    today = date.today()
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    else:
        start_date = None
        
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    else:
        end_date = None

    query = db.session.query(Appointment, User).join(User, Appointment.closer_id == User.id).order_by(Appointment.start_time.desc())
    
    search = request.args.get('search')
    if search:
        search_term = f"%{search}%"
        query = Appointment.query.join(User, Appointment.lead_id == User.id).filter(
            db.or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term)
            )
        ).order_by(Appointment.start_time.desc())
        
        query = db.session.query(Appointment, User).join(User, Appointment.closer_id == User.id)
        LeadUser = db.aliased(User)
        query = query.join(LeadUser, Appointment.lead_id == LeadUser.id, isouter=True).filter(
            db.or_(
                User.username.ilike(search_term),
                LeadUser.username.ilike(search_term),
                LeadUser.email.ilike(search_term)
            )
        ).order_by(Appointment.start_time.desc())

    if start_date:
        start_dt = datetime.combine(start_date, time.min)
        query = query.filter(Appointment.start_time >= start_dt)
        
    if end_date:
        end_dt = datetime.combine(end_date, time.max)
        query = query.filter(Appointment.start_time <= end_dt)

    appointments = query.all()

    return render_template('admin/appointments_list.html', 
                           appointments=appointments,
                           start_date=start_date,
                           end_date=end_date,
                           search=search) 
                           
@bp.route('/sales')
@admin_required
def sales_list():
    # Filters
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    program_id = request.args.get('program_id')
    method_id = request.args.get('method_id')
    payment_type = request.args.get('payment_type')
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    is_load_more = request.args.get('load_more')

    # Base Query
    query = Payment.query.filter(Payment.status == 'completed').join(Enrollment).join(User, Enrollment.student_id == User.id)

    # Apply Filters
    if search:
        term = f"%{search}%"
        query = query.filter(
            db.or_(
                User.username.ilike(term),
                User.email.ilike(term)
            )
        )
    
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        query = query.filter(Payment.date >= datetime.combine(start_date, time.min))
        
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        query = query.filter(Payment.date <= datetime.combine(end_date, time.max))

    if program_id:
        query = query.filter(Enrollment.program_id == program_id)
        
    if method_id:
        query = query.filter(Payment.payment_method_id == method_id)

    if payment_type:
        query = query.filter(Payment.payment_type == payment_type)
    
    # Sorting
    query = query.order_by(Payment.date.desc())
    
    # KPIs (Calculate on filtered data before pagination)
    # We can use aggregations for performance
    # Total Revenue
    kpi_revenue = db.session.query(db.func.sum(Payment.amount)).select_from(Payment).join(Enrollment).join(User, Enrollment.student_id == User.id).filter(Payment.status == 'completed')
    if search:
        kpi_revenue = kpi_revenue.filter(db.or_(User.username.ilike(term), User.email.ilike(term)))
    if start_date_str:
        kpi_revenue = kpi_revenue.filter(Payment.date >= datetime.combine(start_date, time.min))
    if end_date_str:
        kpi_revenue = kpi_revenue.filter(Payment.date <= datetime.combine(end_date, time.max))
    if program_id: kpi_revenue = kpi_revenue.filter(Enrollment.program_id == program_id)
    if method_id: kpi_revenue = kpi_revenue.filter(Payment.payment_method_id == method_id)
    if payment_type: kpi_revenue = kpi_revenue.filter(Payment.payment_type == payment_type)
    
    total_revenue = kpi_revenue.scalar() or 0
    total_sales_count = kpi_revenue.with_entities(db.func.count(Payment.id)).scalar() or 0

    # Execute Pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    payments = pagination.items
    
    # Calculate Commission/Cash for current view or total? 
    # Usually KPIs should reflect total of filters.
    # Commission calculation is complex (per row). 
    # Let's approximate or just summing up for "Cash Collected" card is expensive if we do it for ALL rows in Python.
    # We can try to do it via SQL if Commission logic is simple?
    # Commission = amount * (percent/100) + fixed.
    # SQL: sum(amount * (percent/100.0) + fixed)
    # Need to join PaymentMethod.
    
    total_commission = 0
    # SQL optimized commission calc
    comm_query = db.session.query(
        db.func.sum(
            (Payment.amount * (PaymentMethod.commission_percent / 100.0)) + PaymentMethod.commission_fixed
        )
    ).select_from(Payment).join(PaymentMethod).join(Enrollment).join(User, Enrollment.student_id == User.id).filter(Payment.status == 'completed')
    
    # Apply same filters to comm_query
    if search: comm_query = comm_query.filter(db.or_(User.username.ilike(term), User.email.ilike(term)))
    if start_date_str: comm_query = comm_query.filter(Payment.date >= datetime.combine(start_date, time.min))
    if end_date_str: comm_query = comm_query.filter(Payment.date <= datetime.combine(end_date, time.max))
    if program_id: comm_query = comm_query.filter(Enrollment.program_id == program_id)
    if method_id: comm_query = comm_query.filter(Payment.payment_method_id == method_id)
    if payment_type: comm_query = comm_query.filter(Payment.payment_type == payment_type)
    
    total_commission = comm_query.scalar() or 0
    cash_collected = total_revenue - total_commission

    # Context Data
    all_programs = Program.query.order_by(Program.name).all()
    all_methods = PaymentMethod.query.filter_by(is_active=True).all()
    payment_types = [
        ('full', 'Pago Completo'),
        ('down_payment', 'Primer Pago'),
        ('installment', 'Cuota'),
        ('deposit', 'Seña')
    ]

    if is_load_more:
        return render_template('admin/partials/sales_rows.html', payments=payments)
    
    return render_template('admin/sales_list.html',
                           payments=payments,
                           pagination=pagination,
                           search=search,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           program_id=program_id,
                           method_id=method_id,
                           payment_type=payment_type,
                           total_revenue=total_revenue,
                           total_commission=total_commission,
                           cash_collected=cash_collected,
                           total_sales_count=total_sales_count,
                           all_programs=all_programs,
                           all_methods=all_methods,
                           payment_types=payment_types)

@bp.route('/appointments/delete/<int:id>')
@admin_required
def delete_appointment(id):
    appt = Appointment.query.get_or_404(id)
    db.session.delete(appt)
    db.session.commit()
    flash('Cita eliminada.')
    return redirect(url_for('admin.appointments_list'))

@bp.route('/payment-methods')
@admin_required
def payment_methods_list():
    methods = PaymentMethod.query.all()
    return render_template('admin/payment_methods_list.html', methods=methods)

@bp.route('/payment-methods/create', methods=['GET', 'POST'])
@admin_required
def create_payment_method():
    form = PaymentMethodForm()
    if form.validate_on_submit():
        method = PaymentMethod(
            name=form.name.data,
            commission_percent=form.commission_percent.data,
            commission_fixed=form.commission_fixed.data,
            is_active=form.is_active.data
        )
        db.session.add(method)
        try:
            db.session.commit()
            flash('Método de pago creado.')
            return redirect(url_for('admin.payment_methods_list'))
        except:
            db.session.rollback()
            flash('Error: El nombre ya existe.')
    return render_template('admin/payment_method_form.html', form=form, title="Nuevo Método")

@bp.route('/payment-methods/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_payment_method(id):
    method = PaymentMethod.query.get_or_404(id)
    form = PaymentMethodForm(obj=method)
    if form.validate_on_submit():
        method.name = form.name.data
        method.commission_percent = form.commission_percent.data
        method.commission_fixed = form.commission_fixed.data
        method.is_active = form.is_active.data
        try:
            db.session.commit()
            flash('Método actualizado.')
            return redirect(url_for('admin.payment_methods_list'))
        except:
            db.session.rollback()
            flash('Error al actualizar.')
    return render_template('admin/payment_method_form.html', form=form, title="Editar Método")

@bp.route('/payment-methods/delete/<int:id>')
@admin_required
def delete_payment_method(id):
    method = PaymentMethod.query.get_or_404(id)
    try:
        db.session.delete(method)
        db.session.commit()
        flash('Método eliminado.')
    except:
        db.session.rollback()
        flash('No se puede eliminar porque tiene pagos asociados. Desactívalo mejor.')
    return redirect(url_for('admin.payment_methods_list'))

@bp.route('/sales/new', methods=['GET', 'POST'])
@admin_required
def create_sale():
    form = AdminSaleForm()
    next_url = request.args.get('next')
    
    # Populate Choices
    form.program_id.choices = [(p.id, f"{p.name} (${p.price})") for p in Program.query.all()]
    form.payment_method_id.choices = [(m.id, m.name) for m in PaymentMethod.query.filter_by(is_active=True).all()]
    
    # Populate Closer Choices for Admin
    closers = User.query.filter(or_(User.role == 'closer', User.role == 'admin')).all()
    form.closer_id.choices = [(u.id, u.username) for u in closers]
    
    # Default closer to current user if not set
    if not form.closer_id.data:
        form.closer_id.data = current_user.id

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
        closer_id = form.closer_id.data
        
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
                     student_id=lead_id,
                     program_id=program_id,
                     total_agreed=amount if pay_type == 'full' else program.price, 
                     status='active',
                     closer_id=closer_id 
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
            profile = LeadProfile(user_id=user.id, status='new')
            db.session.add(profile)
        else:
            profile = user.lead_profile

        # Renewal Validation
        if pay_type == 'renewal':
            if profile.status != 'completed':
                flash('Error: Solo se puede renovar si el estado del cliente es "Completado".')
                db.session.rollback()
                return render_template('sales/new_sale.html', form=form, title="Nueva Venta")
            profile.status = 'renewed'
            
        elif pay_type == 'full':
            profile.status = 'completed'
            
        elif pay_type == 'down_payment':
            profile.status = 'pending'
            
        elif pay_type == 'installment':
            db.session.flush()
            total_paid = db.session.query(db.func.sum(Payment.amount)).filter_by(enrollment_id=enrollment.id).scalar() or 0
            
            if total_paid >= program.price:
                profile.status = 'completed'
            else:
                if profile.status != 'completed' and profile.status != 'renewed':
                    profile.status = 'pending'

        db.session.commit()
        
        # Webhook
        send_sales_webhook(payment, current_user.username)
        
        flash('Venta registrada exitosamente.')
        if next_url:
            return redirect(next_url)
            
        return redirect(url_for('admin.sales_list'))

    return render_template('sales/new_sale.html', form=form, title="Nueva Venta", next_url=next_url)



@bp.route('/enrollment/delete/<int:id>')
@admin_required
def delete_enrollment(id):
    enrollment = Enrollment.query.get_or_404(id)
    student_id = enrollment.student_id
    
    db.session.delete(enrollment)
    db.session.commit()
    
    # Auto-update status
    user = User.query.get(student_id)
    if user:
        user.update_status_based_on_debt()
    
    flash('Inscripción eliminada.')
    return redirect(url_for('admin.lead_profile', id=student_id))

