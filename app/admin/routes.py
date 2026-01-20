
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
import json

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
        total_stats['calls_scheduled'] += (r.calls_scheduled or 0)
        total_stats['calls_completed'] += (r.calls_completed or 0)
        total_stats['calls_noshow'] += (r.calls_no_show or 0)
        total_stats['calls_canceled'] += (r.calls_canceled or 0)
        
        total_stats['sales_count'] += (r.sales_count or 0)
        total_stats['sales_amount'] += (r.sales_amount or 0)
        total_stats['cash_collected'] += (r.cash_collected or 0)
        total_stats['self_generated'] += (r.self_generated_bookings or 0)

    # Define rates based on new simplified model
    def safe_div(n, d): return (n / d * 100) if d > 0 else 0
    
    kpis = {
        'show_rate': safe_div(total_stats['calls_completed'], total_stats['calls_scheduled']),
        'closing_rate': safe_div(total_stats['sales_count'], total_stats['calls_completed']),
        'avg_ticket': (total_stats['sales_amount'] / total_stats['sales_count']) if total_stats['sales_count'] > 0 else 0
    }
    
    closers = User.query.filter_by(role='closer').all()
    
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
    with open('tiles_config.json') as f:
        tiles_config = json.load(f)

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

    tiles_data = []
    for tile in tiles_config:
        # Execute query
        result = db.engine.execute(tile['query'].replace("DATE('now', 'start of month')", f"'{start_date}'"))
        value = result.fetchone()[0]
        tiles_data.append({
            "title": tile["title"],
            "value": value
        })

    return render_template('admin/dashboard.html', tiles_data=tiles_data)

# ... (the rest of the file remains the same)
