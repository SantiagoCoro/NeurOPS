from flask import flash
from app import db
from app.models import Payment, PaymentMethod, User, Enrollment, Appointment, Expense
from datetime import datetime, time
from sqlalchemy import func

def calculate_commission(payment):
    """Calculate commission for a payment."""
    if payment.method:
        return (payment.amount * (payment.method.commission_percent / 100)) + payment.method.commission_fixed
    return 0

def apply_date_filters(query, start_date_str, end_date_str, date_field='created_at'):
    """Apply date filters to a query."""
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        query = query.filter(getattr(query.column_descriptions[0]['entity'], date_field) >= start_date)
    
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(getattr(query.column_descriptions[0]['entity'], date_field) < end_date)
    
    return query

def safe_db_operation(operation_func):
    """Decorator to handle database operations with rollback on error."""
    def wrapper(*args, **kwargs):
        try:
            return operation_func(*args, **kwargs)
        except Exception as e:
            db.session.rollback()
            flash('Error en la operación. Verifique dependencias.')
            return None
    return wrapper

def paginate_query(query, page=1, per_page=50):
    """Paginate a query."""
    return query.paginate(page=page, per_page=per_page, error_out=False)

def calculate_leads_kpis(query, start_dt, end_dt):
    """Calculate KPIs for leads list."""
    # Total users
    total_users = query.count()
    
    # Statuses KPI
    status_counts = db.session.query(LeadProfile.status, func.count(User.id))\
        .select_from(User)\
        .join(LeadProfile, User.id == LeadProfile.user_id)\
        .filter(User.role.in_(['lead', 'student', 'agenda']))\
        .group_by(LeadProfile.status).all()
    
    # Programs KPI
    program_counts = db.session.query(Program.name, func.count(Enrollment.id))\
        .select_from(User)\
        .join(Enrollment, Enrollment.student_id == User.id)\
        .join(Program)\
        .filter(User.role.in_(['lead', 'student', 'agenda']))\
        .group_by(Program.name).all()
    
    # Financial KPIs
    fin_query = db.session.query(func.sum(Payment.amount))\
        .select_from(User)\
        .join(Enrollment, Enrollment.student_id == User.id)\
        .join(Payment)\
        .filter(Payment.status == 'completed')
    
    # Apply date filters to fin_query (simplified)
    total_revenue = fin_query.scalar() or 0
    
    # Commissions
    comm_query = db.session.query(
        func.sum((Payment.amount * (PaymentMethod.commission_percent / 100.0)) + PaymentMethod.commission_fixed)
    ).select_from(User)\
        .join(Enrollment, Enrollment.student_id == User.id)\
        .join(Payment)\
        .join(PaymentMethod)\
        .filter(Payment.status == 'completed')
    
    total_commission = comm_query.scalar() or 0
    cash_collected = total_revenue - total_commission
    
    # Debt calculation (simplified)
    total_debt = 0
    # ... (implement if needed)
    
    projected_revenue = cash_collected + total_debt
    
    kpis = {
        'total': total_users,
        'statuses': dict(status_counts),
        'programs': dict(program_counts),
        'revenue': total_revenue,
        'commission': total_commission,
        'cash_collected': cash_collected,
        'debt': total_debt,
        'projected_revenue': projected_revenue
    }
    
    return kpis