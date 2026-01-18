from flask import render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.admin import bp
from app.admin.forms import UserForm, SurveyQuestionForm, EventForm, ProgramForm, PaymentMethodForm, ClientEditForm, PaymentForm, ExpenseForm, RecurringExpenseForm, EventGroupForm, ManualAddForm, AdminSaleForm
from app.closer.forms import SaleForm, LeadForm
from app.closer.utils import send_sales_webhook
from app.models import User, CloserDailyStats, SurveyQuestion, Event, Program, PaymentMethod, db, Enrollment, Payment, Appointment, LeadProfile, Expense, RecurringExpense, EventGroup, UserViewSetting, Integration, DailyReportQuestion 
from datetime import datetime, date, time, timedelta
from sqlalchemy import or_
from app.decorators import role_required


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

@bp.route('/admin/closer-stats')
@login_required
@role_required('admin')
def closer_stats():
    # filters
    start_date_str = request.args.get('start_date', (datetime.today() - timedelta(days=30)).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))
    closer_id = request.args.get('closer_id', '')
    
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    
    # query
    query = CloserDailyStats.query.filter(CloserDailyStats.date >= start_date, CloserDailyStats.date <= end_date)
    
    if closer_id:
        query = query.filter(CloserDailyStats.closer_id == int(closer_id))
        
    stats_records = query.order_by(CloserDailyStats.date.desc()).all()
    
    # KPIs Calculation
    total_stats = {
        'slots': 0, 'slots_used': 0, 
        'calls_scheduled': 0, 'calls_completed': 0, 'calls_noshow': 0, 'calls_canceled': 0,
        'sales_count': 0, 'sales_amount': 0, 'cash_collected': 0,
        'self_generated': 0
    }
    
    for r in stats_records:
        # Approximate mapping from new model to old structure where possible
        total_stats['calls_scheduled'] += r.calls_scheduled
        total_stats['calls_completed'] += r.calls_completed
        total_stats['calls_noshow'] += r.calls_no_show
        total_stats['calls_canceled'] += r.calls_canceled
        
        total_stats['sales_count'] += r.sales_count
        total_stats['sales_amount'] += r.sales_amount
        total_stats['cash_collected'] += r.cash_collected
        total_stats['self_generated'] += r.self_generated_bookings

    # Define rates based on new simplified model
    def safe_div(n, d): return (n / d * 100) if d > 0 else 0
    
    kpis = {
        'show_rate': safe_div(total_stats['calls_completed'], total_stats['calls_scheduled']),
        'closing_rate': safe_div(total_stats['sales_count'], total_stats['calls_completed']),
        'avg_ticket': (total_stats['sales_amount'] / total_stats['sales_count']) if total_stats['sales_count'] > 0 else 0
    }
    
    closers = User.query.filter_by(role='closer').all()
    
    # Redirect to the new improved stats view if this old one is too broken/deprecated?
    # Or render a simplified version. For now, let's keep it but simplified.
    # Actually, the user asked to FIX the error about slots.
    # User said: "slots totales son los bloques totales del dia y los slots disponibles solo lo que quedaron sin agendar"
    # We do NOT have 'total slots' in the new model. We only have scheduled calls.
    # So we cannot calculate 'slots available' without knowing total capacity.
    # I will assume slots = calls_scheduled for now to prevent error, or 0.
    
    return render_template('admin/closer_stats.html', 
                           stats=stats_records, 
                           kpis=kpis, 
                           total=total_stats,
                           closers=closers,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           selected_closer=closer_id)

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
    payments_month = Payment.query.options(db.joinedload(Payment.enrollment)).filter(
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
    
    # Calculate Closer Commissions (10% of Net Cash from Assigned Sales)
    # Calculate Closer Commissions (10% of Net Cash from Assigned Sales)
    # Re-iterate payments (efficient enough)
    closer_commissions_month = 0
    for p in payments_month:
        if p.enrollment and p.enrollment.closer_id:
             # Net amount for this payment
            p_amount = p.amount
            if p.method:
                p_fee = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
                p_amount -= p_fee
            
            # 10% Commission
            closer_commissions_month += (p_amount * 0.10)
    # 2. Expenses and Net Profit (Selected Period)
    total_expenses = db.session.query(db.func.sum(Expense.amount)).filter(
        Expense.date >= start_dt,
        Expense.date <= end_dt
    ).scalar() or 0

    # Closer Commissions Calculation
    closer_commissions_month = 0
    for p in payments_month:
        if p.enrollment and p.enrollment.closer_id:
            # Net amount for this payment
            p_amount = p.amount
            if p.method:
                p_fee = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
                p_amount -= p_fee
            
            # 10% Commission
            closer_commissions_month += (p_amount * 0.10)

    total_expenses += closer_commissions_month
    
    net_profit = cash_collected_month - total_expenses
    
    # --- Financial KPIs (Filtered by Period) ---

    # --- Financial KPIs (Filtered by Period via User Registration - Cohort View) ---

    # 1. Fetch Period Users (Base for Debt, Revenue, Top Debtors)
    # We want users created between start_dt and end_dt
    # AND who have a role that implies being a client/lead (student, lead). Closers/Admins usually don't buy, but let's include 'student' and 'lead'.
    period_users = User.query.filter(
        User.created_at >= start_dt,
        User.created_at <= end_dt,
        User.role.in_(['student', 'lead'])
    ).all()

    period_debt = 0.0
    period_commission = 0.0
    period_gross_cash = 0.0
    period_revenue = 0.0 # This will now be Net Cash + Debt
    debtors = []

    for user in period_users:
        # Calculate financials for this user
        
        user_debt = user.current_active_debt
        user_gross_paid = 0.0
        user_commission = 0.0
        
        # Calculate Paid & Commission from all enrollments (Lifetime value of this cohort)
        for enr in user.enrollments:
            # Paid sum
            completed_payments = enr.payments.filter_by(status='completed').all()
            for p in completed_payments:
                user_gross_paid += p.amount
                if p.method:
                    comm = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
                    user_commission += comm
        
        user_net_cash = user_gross_paid - user_commission

        # Accumulate Cohort Totals
        period_gross_cash += user_gross_paid
        period_commission += user_commission
        
        if user_debt > 0:
            period_debt += user_debt
            
            # Find the main program they owe on (or just list them)
            active_enr = user.enrollments.filter_by(status='active').first()
            prog_name = active_enr.program.name if active_enr and active_enr.program else "Varios"
            
            debtors.append({
                'student': user,
                'program': {'name': prog_name}, 
                'debt': user_debt
            })

    # Revenue Definition per Leads View: Net Cash + Debt
    period_net_cash = period_gross_cash - period_commission
    period_revenue = period_net_cash + period_debt

    # Top 5 Debtors (Filtered by Period Cohort)
    top_debtors = sorted(debtors, key=lambda x: x['debt'], reverse=True)[:10]

    # 3. Global Stats (Count only) -> Now Period Based
    leads_count = User.query.filter(
        User.role == 'lead',
        User.created_at >= start_dt,
        User.created_at <= end_dt
    ).count()
    
    # 4. Closing Rate This Month (or Selected Period)
    # This might also need to ideally be cohort based? (Leads from this month vs Sales from THIS MONTH's leads)
    # But usually Closing Rate is Sales(t) / Appts(t). Let's keep it as is unless requested.
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
    # Using 'total_agreed' from Enrollments of users registered this month (Cohort)
    programs_data = db.session.query(Program.name, db.func.sum(Enrollment.total_agreed)).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Program).filter(
        User.created_at >= start_dt,
        User.created_at <= end_dt
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
        
    # 9. Chart Data: Sales by Payment Method (Cohort - Lifetime Payments)
    # Group payments by payment method name for users registered in period
    methods_data = db.session.query(
        PaymentMethod.name, 
        db.func.sum(Payment.amount)
    ).join(Payment).join(Enrollment).join(User, Enrollment.student_id == User.id).filter(
        User.created_at >= start_dt,
        User.created_at <= end_dt,
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
                           # global_debt removed
                           period_debt=period_debt,
                           period_cash_from_sales=period_net_cash,
                           period_gross_cash=period_gross_cash,
                           period_commission=period_commission,
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
                           total_commission_month=total_commission_month,
                           dashboard_revenue=period_revenue)

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
@login_required
def edit_user(id):
    if current_user.role != 'admin':
        flash('Acceso denegado.')
        return redirect(url_for('admin.dashboard'))
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    
    # Populate Timezone Choices
    import pytz
    form.timezone.choices = [(tz, tz) for tz in pytz.common_timezones]
    
    if request.method == 'GET' and not form.timezone.data:
         form.timezone.data = user.timezone or 'America/La_Paz'

    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.role = form.role.data
        user.timezone = form.timezone.data # Save timezone
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash('Usuario actualizado exitosamente.')
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
    # --- Persistence Logic ---
    view_name = 'leads_list'
    relevant_keys = ['search', 'program', 'status', 'start_date', 'end_date', 'sort_by']
    
    if request.args.get('clear'):
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if setting:
             db.session.delete(setting)
             db.session.commit()
        return redirect(url_for('admin.leads_list'))
        
    has_args = any(key in request.args for key in relevant_keys)
    
    # Check if we are paginating or loading more (in which case we shouldn't force-reload from DB, but we SHOULD save context if present)
    is_paginating = request.args.get('page') or request.args.get('load_more') or request.args.get('ajax')

    if has_args:
        # Save current state
        new_settings = {k: request.args.get(k) for k in relevant_keys if request.args.get(k) is not None}
        
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if not setting:
            setting = UserViewSetting(user_id=current_user.id, view_name=view_name)
            db.session.add(setting)
        
        if setting.settings != new_settings:
            setting.settings = new_settings
            db.session.commit()
            
    elif not is_paginating:
        # Load from DB only if not navigating page/load_more and no args provided
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if setting and setting.settings:
             return redirect(url_for('admin.leads_list', **setting.settings))

    # --- End Persistence Logic ---

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
        query = query.join(LeadProfile, User.id == LeadProfile.user_id).filter(LeadProfile.status == status_filter)
    
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
    
    is_load_more = request.args.get('load_more')

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
        
    if status_filter:
        kpi_query = kpi_query.join(LeadProfile, User.id == LeadProfile.user_id).filter(LeadProfile.status == status_filter)
        
    if program_filter:
        kpi_query = kpi_query.join(Enrollment, Enrollment.student_id == User.id).filter(Enrollment.program_id == program_filter)
        
    total_users = kpi_query.count()
    
    # Statuses KPI
    status_counts = db.session.query(LeadProfile.status, db.func.count(User.id)).select_from(User).join(LeadProfile, User.id == LeadProfile.user_id).filter(User.role.in_(['lead', 'student', 'agenda']))
    if start_date_str: status_counts = status_counts.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: status_counts = status_counts.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    status_counts = status_counts.group_by(LeadProfile.status).all()

    # Programs KPI
    program_counts = db.session.query(Program.name, db.func.count(Enrollment.id)).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Program).filter(User.role.in_(['lead', 'student', 'agenda']))
    if start_date_str: program_counts = program_counts.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: program_counts = program_counts.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    program_counts = program_counts.group_by(Program.name).all()

    # --- Financial KPIs (Filtered) ---
    # Cash Collected (Sum of completed payments for filtered users)
    # We need to re-apply filters to Payment query or join User
    fin_query = db.session.query(db.func.sum(Payment.amount)).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Payment).filter(Payment.status == 'completed')
    if start_date_str: fin_query = fin_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: fin_query = fin_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: fin_query = fin_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: fin_query = fin_query.join(LeadProfile, LeadProfile.user_id == User.id).filter(LeadProfile.status == status_filter)
    if program_filter: fin_query = fin_query.filter(Enrollment.program_id == program_filter)
    
    total_revenue = fin_query.scalar() or 0.0

    # Calculate Commissions (payment method fees) for filtered payments
    comm_query = db.session.query(
        db.func.sum(
            (Payment.amount * (PaymentMethod.commission_percent / 100.0)) + PaymentMethod.commission_fixed
        )
    ).select_from(User).join(Enrollment, Enrollment.student_id == User.id).join(Payment).join(PaymentMethod).filter(Payment.status == 'completed')
    
    # Apply same filters to comm_query
    if start_date_str: comm_query = comm_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: comm_query = comm_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: comm_query = comm_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: comm_query = comm_query.join(LeadProfile, LeadProfile.user_id == User.id).filter(LeadProfile.status == status_filter)
    if program_filter: comm_query = comm_query.filter(Enrollment.program_id == program_filter)
    
    total_commission = comm_query.scalar() or 0.0
    cash_collected = total_revenue - total_commission

    # Total Debt (Active enrollments only) - Approximated via Python for complexity reasons or simplified SQL
    # SQL Approach: Sum(Agreed) - Sum(Paid) for active enrollments of filtered users
    # Note: This is an approximation if we have partial payments or complex logic, but aligned with models.py
    # For robust debt calculation we might iterate the current page or limit, but user wants TOTAL for filter.
    # Let's try a hybrid SQL approach
    
    # 1. Get functional enrollment IDs for filter
    enr_query = db.session.query(Enrollment).join(User, Enrollment.student_id == User.id).filter(Enrollment.status == 'active')
    if start_date_str: enr_query = enr_query.filter(User.created_at >= datetime.strptime(start_date_str, '%Y-%m-%d'))
    if end_date_str: enr_query = enr_query.filter(User.created_at < datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1))
    if search: enr_query = enr_query.filter(or_(User.username.ilike(search_term), User.email.ilike(search_term)))
    if status_filter: enr_query = enr_query.join(LeadProfile, LeadProfile.user_id == User.id).filter(LeadProfile.status == status_filter)
    if program_filter: enr_query = enr_query.filter(Enrollment.program_id == program_filter)
    
    active_enrollments = enr_query.all()
    total_debt = 0.0
    total_agreed_sum = 0.0 # For subtitle
    total_paid_debt_sum = 0.0 # For subtitle (paid amount within debt calculation context)
    
    for enr in active_enrollments:
        # Reuse model logic for consistency
        paid = enr.total_paid
        agreed = enr.total_agreed if enr.total_agreed is not None else (enr.program.price if enr.program else 0.0)
        debt = agreed - paid
        if debt > 0:
            total_debt += debt
            total_agreed_sum += agreed
            total_paid_debt_sum += paid

    # Revenue (Projected) = Cash Collect + Debt
    projected_revenue = cash_collected + total_debt

    # Calculate Totals for Percentages
    total_statuses_count = sum(c[1] for c in status_counts)
    total_programs_count = sum(c[1] for c in program_counts)

    kpis = {
        'total': total_users,
        'statuses': dict(status_counts),
        'programs': dict(program_counts),
        'total_statuses': total_statuses_count,
        'total_programs': total_programs_count,
        'revenue': total_revenue, # Gross
        'debt': total_debt,
        'commission': total_commission,
        'cash_collected': cash_collected, # Net
        'debt_agreed': total_agreed_sum,
        'debt_paid': total_paid_debt_sum,
        'projected_revenue': projected_revenue
    }
    
    # Context for filters
    all_programs = Program.query.order_by(Program.name).all()
    # Unique statuses for dropdown
    all_statuses = db.session.query(LeadProfile.status).distinct().filter(LeadProfile.status != None).all()
    all_statuses = [s[0] for s in all_statuses]

    if request.args.get('ajax'):
        return jsonify({
            'html': render_template('admin/partials/leads_rows.html', leads=leads, start_index=start_index),
            'kpis': kpis,
            'has_next': pagination.has_next
        })

    if is_load_more:
        # Keep returning plain HTML for legacy load_more script for now, 
        # but optimally we should switch load_more to use the JSON endpoint too.
        # However, to avoid breaking 'loadMoreBtn' logic which expects HTML in .then(text => ...), 
        # I will leave this as is OR update the JS to handle JSON.
        # The JS I saw in sales_list handled JSON? No, the sales_list JS I wrote previously used .text() for load more?
        # Let's check leads_list JS. It uses .text().
        # So for load_more, return HTML. For filter (ajax=1), return JSON.
        return render_template('admin/partials/leads_rows.html', leads=leads, start_index=start_index)

    # Closers for bulk assign
    closers = User.query.filter_by(role='closer').all()

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
                           all_statuses=all_statuses,
                           closers=closers)

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
    
    # Populate Closers
    closers = User.query.filter(or_(User.role == 'closer', User.role == 'admin')).all()
    form.closer_id.choices = [(u.id, u.username) for u in closers]

    if request.method == 'GET':
        if enrollment.closer_id:
            form.closer_id.data = enrollment.closer_id
        else:
            # Default to current user if they are a closer/admin? 
            # Or leave empty? Let's leave empty if not assigned, or assign current user if appropriate.
            # User request just says "falta una opcion", implying they want to set it.
            pass
    
    if form.validate_on_submit():
        # Update Enrollment Closer
        if form.closer_id.data:
            enrollment.closer_id = form.closer_id.data
            db.session.add(enrollment)
            
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
    
    # Populate Closers
    closers = User.query.filter(or_(User.role == 'closer', User.role == 'admin')).all()
    form.closer_id.choices = [(u.id, u.username) for u in closers]
    
    if request.method == 'GET':
         # Handle datetime to date for form
         if payment.date:
             form.date.data = payment.date.date()
         
         if payment.enrollment.closer_id:
             form.closer_id.data = payment.enrollment.closer_id
    
    if form.validate_on_submit():
        # Update Enrollment Closer
        if form.closer_id.data:
            payment.enrollment.closer_id = form.closer_id.data
            db.session.add(payment.enrollment)

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

@bp.route('/users/bulk_delete', methods=['POST'])
@admin_required
def bulk_delete_users():
    user_ids = request.form.getlist('user_ids')
    
    if not user_ids:
        flash('No se seleccionaron usuarios.')
        return redirect(url_for('admin.leads_list'))
        
    count = 0
    for uid in user_ids:
        if str(uid) == str(current_user.id):
            continue # Don't delete yourself
            
        user = User.query.get(uid)
        if user:
            db.session.delete(user)
            count += 1
            
    try:
        db.session.commit()
        flash(f'{count} usuarios eliminados correctamente.')
    except Exception as e:
        db.session.rollback()
        flash('Error al eliminar usuarios. Verifique dependencias (citas, pagos).')
        
    return redirect(url_for('admin.leads_list'))



@bp.route('/users/bulk_assign', methods=['POST'])
@admin_required
def bulk_assign_closer():
    closer_id = request.form.get('closer_id')
    user_ids = request.form.getlist('user_ids')
    
    if not closer_id or not user_ids:
        flash('Seleccione un Closer y al menos un cliente.')
        return redirect(url_for('admin.leads_list'))
        
    closer = User.query.get(closer_id)
    if not closer or closer.role != 'closer':
        flash('Closer inválido.')
        return redirect(url_for('admin.leads_list'))
        
    count = 0
    for uid in user_ids:
        user = User.query.get(uid)
        if user:
            if not user.lead_profile:
                profile = LeadProfile(user_id=user.id)
                db.session.add(profile)
            user.lead_profile.assigned_closer_id = closer.id
            count += 1
            
    db.session.commit()
    flash(f'{count} clientes asignados a {closer.username}.')
    return redirect(url_for('admin.leads_list'))

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
            
@bp.route('/stats/closer')
@admin_required
def stats_closer():
    today = date.today()
    
    # Filters
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    closer_id = request.args.get('closer_id')
    
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    else:
        start_date = today.replace(day=1)
        
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    else:
        end_date = today
        
    # Query Stats
    query = CloserDailyStats.query.filter(
        CloserDailyStats.date >= start_date,
        CloserDailyStats.date <= end_date
    ).order_by(CloserDailyStats.date.desc())
    
    if closer_id:
        query = query.filter(CloserDailyStats.closer_id == closer_id)
        
    stats = query.all()
    
    # Totals (Aggregations)
    total_sales = sum(s.sales_count for s in stats)
    total_cash = sum(s.cash_collected for s in stats)
    total_calls = sum(s.calls_completed for s in stats)
    
    closing_rate = (total_sales / total_calls * 100) if total_calls > 0 else 0
    
    closers = User.query.filter_by(role='closer').all()
    questions = DailyReportQuestion.query.order_by(DailyReportQuestion.order).all()

    return render_template('admin/stats_closer.html', 
                           stats=stats, 
                           total_sales=total_sales,
                           total_cash=total_cash,
                           total_calls=total_calls,
                           closing_rate=closing_rate,
                           closers=closers,
                           questions=questions,
                           start_date=start_date,
                           end_date=end_date)

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
        if request.args.get('ajax'):
            return jsonify({'success': True})
        flash('Gasto registrado.')
        return redirect(url_for('admin.finances'))

    # Query Expenses
    expenses_query = Expense.query.filter(
        Expense.date >= start_date,
        Expense.date <= end_date
    ).order_by(Expense.date.desc())
    expenses = expenses_query.all()
    expenses = expenses_query.all()
    # Inject Virtual Expense for Closer Commissions (Calculated later, but need to append to list)
    # We calculate it below.
    
    total_expenses = sum(e.amount for e in expenses)

    # 3. Recurring Expenses Configurations
    recurring_form = RecurringExpenseForm()
    recurring_expenses = RecurringExpense.query.all()
    
    # 4. Income & Cash Collected Logic (Cash Flow - Payment Date based)
    # Filter payments made in the selected period
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    
    period_payments = Payment.query.options(db.joinedload(Payment.enrollment)).filter(
        Payment.date >= start_dt,
        Payment.date <= end_dt,
        Payment.status == 'completed'
    ).all()
    
    gross_revenue = 0
    total_commission = 0
    
    for p in period_payments:
        gross_revenue += p.amount
        if p.method:
            comm = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
            total_commission += comm
            
    cash_collected = gross_revenue - total_commission

    # Closer Commissions
    closer_commission_total = 0
    for p in period_payments:
         if p.enrollment and p.enrollment.closer_id:
            p_amount = p.amount
            if p.method:
                p_fee = (p.amount * (p.method.commission_percent / 100)) + p.method.commission_fixed
                p_amount -= p_fee
            closer_commission_total += (p_amount * 0.10)
    
    # Add to Total Expenses (for calculation)
    net_profit = cash_collected - (total_expenses + closer_commission_total)
    
    # Update Total Expenses Display variable
    total_expenses += closer_commission_total
    
    # Pass date *strings* for input values if they were provided, else format dates
    s_date_val = start_date_str if start_date_str else start_date.strftime('%Y-%m-%d')
    e_date_val = end_date_str if end_date_str else end_date.strftime('%Y-%m-%d')

    if request.args.get('ajax'):
        active_recurring = sum(r.amount for r in recurring_expenses if r.is_active)
        return jsonify({
            'html_expenses': render_template('admin/partials/expenses_table.html', expenses=expenses),
            'html_recurring': render_template('admin/partials/recurring_table.html', recurring_expenses=recurring_expenses),
            'kpis': {
                'total_expenses': "{:,.2f}".format(total_expenses),
                'active_recurring': "{:,.2f}".format(active_recurring),
                'cash_collected': "{:,.2f}".format(cash_collected),
                'gross_revenue': "{:,.2f}".format(gross_revenue),
                'total_commission': "{:,.2f}".format(total_commission),
                'net_profit': "{:,.2f}".format(net_profit),
                'net_profit_positive': net_profit >= 0
            }
        })

    # Append virtual expense to list for display
    if closer_commission_total > 0:
        # Create a simple object-like structure
        class VirtualExpense:
            def __init__(self, date, description, category, amount, id=None):
                self.date = date
                self.description = description
                self.category = category
                self.amount = amount
                self.id = id
        
        v_exp = VirtualExpense(end_date, 'Comisiones Closers (Calculado)', 'variable', closer_commission_total)
        expenses.append(v_exp)
        # Re-sort by date desc
        expenses.sort(key=lambda x: x.date, reverse=True)

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
        if request.args.get('ajax'):
             return jsonify({'success': True})
        flash('Gasto fijo configurado.')
    else:
        if request.args.get('ajax'):
             return jsonify({'success': False, 'errors': form.errors}), 400
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
    if request.args.get('ajax'):
        return jsonify({'success': True, 'message': f'Se generaron {count} gastos fijos.'})
    flash(f'Se generaron {count} gastos fijos para este mes.')
    return redirect(url_for('admin.finances'))

@bp.route('/admin/recurring-expense/delete/<int:id>', methods=['POST'])
@admin_required
def delete_recurring_expense(id):
    rex = RecurringExpense.query.get_or_404(id)
    db.session.delete(rex)
    db.session.commit()
    if request.args.get('ajax'):
        return jsonify({'success': True})
    flash('Gasto fijo eliminado correctamente.')
    return redirect(url_for('admin.finances'))

@bp.route('/admin/recurring-expense/toggle/<int:id>', methods=['POST'])
@admin_required
def toggle_recurring_expense(id):
    rex = RecurringExpense.query.get_or_404(id)
    rex.is_active = not rex.is_active
    db.session.commit()
    status = 'activado' if rex.is_active else 'desactivado'
    if request.args.get('ajax'):
        return jsonify({'success': True})
    flash(f'Gasto fijo {status} correctamente.')
    return redirect(url_for('admin.finances'))

@bp.route('/admin/expense/delete/<int:id>', methods=['POST'])
@admin_required
def delete_expense(id):
    expense = Expense.query.get_or_404(id)
    db.session.delete(expense)
    db.session.commit()
    if request.args.get('ajax'):
        return jsonify({'success': True})
    flash('Gasto eliminado correctamente.')
    return redirect(url_for('admin.finances'))

@bp.route('/admin/expense/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_expense(id):
    expense = Expense.query.get_or_404(id)
    form = ExpenseForm(obj=expense) # Pre-fill form
    
    if form.validate_on_submit():
        form.populate_obj(expense) # Update object
        db.session.commit()
        flash('Gasto actualizado correctamente.')
        return redirect(url_for('admin.finances'))
        
    return render_template('admin/edit_expense.html', form=form, expense=expense)

@bp.route('/admin/recurring-expense/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_recurring_expense(id):
    rex = RecurringExpense.query.get_or_404(id)
    # Convert bool is_active to int for select field if needed, or let wtforms handle coerce
    form = RecurringExpenseForm(obj=rex)
    
    # Manually set is_active data if needed because boolean vs int mismatch common in wtforms select with coerce
    if request.method == 'GET':
        form.is_active.data = 1 if rex.is_active else 0

    if form.validate_on_submit():
        rex.description = form.description.data
        rex.amount = form.amount.data
        rex.day_of_month = form.day_of_month.data
        rex.is_active = bool(form.is_active.data)
        
        db.session.commit()
        flash('Configuración de gasto fijo actualizada.')
        return redirect(url_for('admin.finances'))
        
    return render_template('admin/edit_recurring_expense.html', form=form, rex=rex)

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

@bp.route('/sales/bulk_assign', methods=['POST'])
@admin_required
def bulk_assign_sales_closer():
    closer_id = request.form.get('closer_id')
    payment_ids = request.form.getlist('payment_ids')
    
    if not closer_id:
        flash('Seleccione un closer.')
        return redirect(url_for('admin.sales_list'))
        
    count = 0
    for pid in payment_ids:
        payment = Payment.query.get(pid)
        if payment and payment.enrollment:
             payment.enrollment.closer_id = closer_id
             db.session.add(payment.enrollment)
             count += 1
             
    db.session.commit()
    flash(f'{count} ventas asignadas correctamente.')
    return redirect(url_for('admin.sales_list'))

@bp.route('/sales')
@admin_required
def sales_list():
    # --- Persistence Logic ---
    view_name = 'sales_list'
    relevant_keys = ['search', 'start_date', 'end_date', 'program_id', 'method_id', 'payment_type']
    
    if request.args.get('clear'):
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if setting:
             db.session.delete(setting)
             db.session.commit()
        return redirect(url_for('admin.sales_list'))

    has_args = any(key in request.args for key in relevant_keys)
    is_paginating = request.args.get('page') or request.args.get('load_more')

    if has_args:
        new_settings = {k: request.args.get(k) for k in relevant_keys if request.args.get(k) is not None}
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if not setting:
            setting = UserViewSetting(user_id=current_user.id, view_name=view_name)
            db.session.add(setting)
        if setting.settings != new_settings:
            setting.settings = new_settings
            db.session.commit()
            
    elif not is_paginating:
        setting = UserViewSetting.query.filter_by(user_id=current_user.id, view_name=view_name).first()
        if setting and setting.settings:
             return redirect(url_for('admin.sales_list', **setting.settings))
    # --- End Persistence Logic ---

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

    # KPI Average Ticket (Only 'full' and 'down_payment')
    # Reuse kpi_revenue structure but change filter
    # Note: kpi_revenue query object defines sum(amount). We can reuse it.
    avg_ticket_query = kpi_revenue.filter(Payment.payment_type.in_(['full', 'down_payment']))
    avg_ticket_sum = avg_ticket_query.scalar() or 0
    avg_ticket_count = avg_ticket_query.with_entities(db.func.count(Payment.id)).scalar() or 0
    
    avg_ticket = avg_ticket_sum / avg_ticket_count if avg_ticket_count else 0

    # Context Data
    all_programs = Program.query.order_by(Program.name).all()
    all_methods = PaymentMethod.query.filter_by(is_active=True).all()
    payment_types = [
        ('full', 'Pago Completo'),
        ('down_payment', 'Primer Pago'),
        ('installment', 'Cuota'),
        ('deposit', 'Seña')
    ]
    
    closers = User.query.filter(db.or_(User.role == 'closer', User.role == 'admin')).all()
    start_index = (page - 1) * per_page

    is_ajax = request.args.get('ajax')
    
    if is_load_more and not is_ajax:
        return render_template('admin/partials/sales_rows.html', payments=payments, start_index=start_index)
        
    if is_ajax:
        return jsonify({
            'html': render_template('admin/partials/sales_rows.html', payments=payments, start_index=start_index),
            'kpis': {
                'sales_count': total_sales_count,
                'total_revenue': "{:,.2f}".format(total_revenue),
                'cash_collected': "{:,.2f}".format(cash_collected),
                'avg_ticket': "{:,.2f}".format(avg_ticket)
            },
            'has_next': pagination.has_next,
            'next_page': pagination.next_num
        })
    
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
                           avg_ticket=avg_ticket,
                           total_sales_count=total_sales_count,
                           all_programs=all_programs,
                           all_methods=all_methods,
                           payment_types=payment_types,
                           closers=closers,
                           start_index=start_index)

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

@bp.route('/integrations', methods=['GET', 'POST'])
@admin_required
def integrations():
    # Define available integration keys and default names
    integration_config = {
        'sales': 'Ventas',
        'calendar': 'Agendamiento'
    }

    # Auto-create missing integrations
    for key, name in integration_config.items():
        exists = Integration.query.filter_by(key=key).first()
        if not exists:
            new_int = Integration(
                key=key,
                name=name,
                url_dev='',
                url_prod='',
                active_env='dev'
            )
            db.session.add(new_int)
    db.session.commit()
    
    # Fetch all
    integrations_list = Integration.query.all()
    # To Dict for easy access if needed, or just pass list
    
    if request.method == 'POST':
        # Identify which integration is being updated
        # We assume form fields are prefixed or we loop
        # Simple approach: Form submits ONE integration update at a time via hidden field 'key'
        key = request.form.get('key')
        integration = Integration.query.filter_by(key=key).first()
        
        if integration:
            integration.url_dev = request.form.get('url_dev')
            integration.url_prod = request.form.get('url_prod')
            
            # Active Env Radio
            active_env = request.form.get('active_env')
            if active_env in ['dev', 'prod']:
                integration.active_env = active_env
                
            db.session.commit()
            flash(f'Integración {integration.name} actualizada.')
        
        return redirect(url_for('admin.integrations'))
        
    return render_template('admin/integrations.html', integrations=integrations_list)

@bp.route('/database')
@admin_required
def database_schema():
    # Translation dictionary
    translations = {
        'users': 'Usuarios',
        'lead_profiles': 'Perfiles_de_Prospectos',
        'event_groups': 'Grupos_de_Eventos',
        'events': 'Eventos',
        'programs': 'Programas',
        'enrollments': 'Inscripciones',
        'payment_methods': 'Metodos_de_Pago',
        'payments': 'Pagos',
        'appointments': 'Citas',
        'availability': 'Disponibilidad',
        'survey_questions': 'Preguntas_Encuesta',
        'survey_answers': 'Respuestas_Encuesta',
        'expenses': 'Gastos',
        'recurring_expenses': 'Gastos_Recurrentes',
        'user_view_settings': 'Config_Vistas',
        'integrations': 'Integraciones',
        'closer_daily_stats': 'Estadisticas_Diarias',
        'alembic_version': 'Version_DB'
    }

    mermaid_str = "erDiagram\n"
    
    # helper to clean names for mermaid (no spaces)
    def clean(name):
        return name.replace(' ', '_')

    # Iterate over all tables in metadata
    # Ensure all models are imported so metadata is populated
    # (They are imported at top of file)
    
    sorted_tables = sorted(db.metadata.tables.items())
    
    for table_name, table in sorted_tables:
        t_label = translations.get(table_name, table_name)
        mermaid_str += f"    {t_label} {{\n"
        
        # Add columns
        for column in table.columns:
            # Type is a bit complex in SQLA, simplifying text
            col_type = str(column.type).replace('VARCHAR', 'string').replace('INTEGER', 'int').replace('BOOLEAN', 'bool').replace('DATETIME', 'datetime').replace('FLOAT', 'float').split('(')[0]
            
            # Key indicators
            key_suffix = ""
            if column.primary_key:
                key_suffix = " PK"
            elif column.foreign_keys:
                key_suffix = " FK"
                
            mermaid_str += f"        {col_type} {column.name}{key_suffix}\n"
            
        mermaid_str += "    }\n"

    # Add Relationships
    for table_name, table in sorted_tables:
        t_label_source = translations.get(table_name, table_name)
        
        for column in table.columns:
            for fk in column.foreign_keys:
                target_table = fk.column.table.name
                t_label_target = translations.get(target_table, target_table)
                
                # Relationship logic: 
                # Mermaid ER is usually One-to-Many: ||--o{
                # We assume FK implies Many (source) to One (target) usually, 
                # but in Mermaid standard: One ||--|{ Many
                # Here: Target (PK side) ||--o{ Source (FK side)
                
                mermaid_str += f"    {t_label_target} ||--o{{ {t_label_source} : \"tiene\"\n"
    
    return render_template('admin/database.html', mermaid_chart=mermaid_str)

# --- Daily Report Questions Management ---

@bp.route('/questions')
@login_required
def daily_report_questions():
    if current_user.role != 'admin':
        flash('Acceso denegado.')
        return redirect(url_for('admin.dashboard'))
    questions = DailyReportQuestion.query.order_by(DailyReportQuestion.order).all()
    return render_template('admin/questions_list.html', questions=questions)

@bp.route('/questions/new', methods=['POST'])
@login_required
def create_daily_report_question():
    if current_user.role != 'admin': return {'status': 'error'}, 403
    
    text = request.form.get('text')
    q_type = request.form.get('type', 'text')
    
    if not text:
        flash('El texto es obligatorio.')
        return redirect(url_for('admin.daily_report_questions'))
        
    max_order = db.session.query(db.func.max(DailyReportQuestion.order)).scalar() or 0
    
    q = DailyReportQuestion(text=text, question_type=q_type, order=max_order + 1)
    db.session.add(q)
    db.session.commit()
    flash('Pregunta creada.')
    return redirect(url_for('admin.daily_report_questions'))

@bp.route('/questions/edit/<int:id>', methods=['POST'])
@login_required
def edit_daily_report_question(id):
    if current_user.role != 'admin': return {'status': 'error'}, 403
    
    q = DailyReportQuestion.query.get_or_404(id)
    text = request.form.get('text')
    q_type = request.form.get('type')
    
    if text:
        q.text = text
    if q_type:
        q.question_type = q_type
        
    db.session.commit()
    flash('Pregunta actualizada.')
    return redirect(url_for('admin.daily_report_questions'))

@bp.route('/questions/toggle/<int:id>')
@login_required
def toggle_daily_report_question(id):
    if current_user.role != 'admin': return {'status': 'error'}, 403
    q = DailyReportQuestion.query.get_or_404(id)
    q.is_active = not q.is_active
    db.session.commit()
    return redirect(url_for('admin.daily_report_questions'))

@bp.route('/questions/delete/<int:id>')
@login_required
def delete_daily_report_question(id):
    if current_user.role != 'admin': return {'status': 'error'}, 403
    q = DailyReportQuestion.query.get_or_404(id)
    db.session.delete(q)
    db.session.commit()
    flash('Pregunta eliminada.')
    return redirect(url_for('admin.daily_report_questions'))

@bp.route('/questions/reorder', methods=['POST'])
@login_required
def reorder_daily_report_questions():
    if current_user.role != 'admin': return {'status': 'error'}, 403
    order_data = request.json.get('order', []) # List of ID
    for idx, q_id in enumerate(order_data):
        q = DailyReportQuestion.query.get(q_id)
        if q:
            q.order = idx + 1
    db.session.commit()
    return {'status': 'success'}
