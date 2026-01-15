from datetime import datetime
from app import db, login
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

@login.user_loader
def load_user(id):
    return User.query.get(int(id))

# Roles as Constants
ROLE_ADMIN = 'admin'
ROLE_CLOSER = 'closer'
ROLE_LEAD = 'lead'
ROLE_AGENDA = 'agenda'
ROLE_STUDENT = 'student'

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default=ROLE_LEAD)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    # Relationships
    lead_profile = db.relationship('LeadProfile', backref='user', uselist=False, cascade="all, delete-orphan")
    enrollments = db.relationship('Enrollment', foreign_keys='Enrollment.student_id', backref='student', lazy='dynamic', cascade="all, delete-orphan")
    
    # For Closers: appointments/leads they handle
    appointments_as_closer = db.relationship('Appointment', foreign_keys='Appointment.closer_id', backref='closer', lazy='dynamic')
    # For Leads: their appointments
    appointments_as_lead = db.relationship('Appointment', foreign_keys='Appointment.lead_id', backref='lead', lazy='dynamic', cascade="all, delete-orphan")
    
    # Survey Answers
    survey_answers = db.relationship('SurveyAnswer', foreign_keys='SurveyAnswer.lead_id', backref='lead_user', lazy='dynamic', cascade="all, delete-orphan")

    availability = db.relationship('Availability', backref='closer', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

    @property
    def total_lifetime_paid(self):
        """Sum of all payments made by this user across all enrollments."""
        total = 0
        # Iterate enrollments (dynamic relationship)
        for enr in self.enrollments:
            total += enr.total_paid
        return total

    @property
    def current_active_debt(self):
        """Sum of outstanding debt on ACTIVE enrollments only."""
        total_debt = 0
        # Filter for active only
        active_enrollments = self.enrollments.filter_by(status='active')
        for enr in active_enrollments:
            paid = enr.total_paid
            # Ensure we don't count negative debt (overpayment) as debt, unless desired? 
            # Usually debt is max(0, agreed - paid)
            agreed = enr.total_agreed if enr.total_agreed is not None else (enr.program.price if enr.program else 0.0)
            debt = agreed - paid
            if debt > 0:
                total_debt += debt
        return total_debt

class LeadProfile(db.Model):
    __tablename__ = 'lead_profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    instagram = db.Column(db.String(64))
    utm_source = db.Column(db.String(64)) # e.g., 'elias', 'vsl', 'workshop'
    status = db.Column(db.String(20), default='new') 
    notes = db.Column(db.Text)

    notes = db.Column(db.Text)
    
    def __repr__(self):
        return f'<LeadProfile {self.user_id}>'

class EventGroup(db.Model):
    __tablename__ = 'event_groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    funnel_steps = db.Column(db.JSON, default=['contact', 'calendar', 'survey'])
    
    events = db.relationship('Event', backref='group', lazy='dynamic')
    questions = db.relationship('SurveyQuestion', backref='group', lazy='dynamic')

    def __repr__(self):
        return f'<EventGroup {self.name}>'

class Event(db.Model):
    __tablename__ = 'events'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    utm_source = db.Column(db.String(64), unique=True, nullable=False) # e.g. 'vsl', 'workshop', 'elias'
    is_active = db.Column(db.Boolean, default=True)
    group_id = db.Column(db.Integer, db.ForeignKey('event_groups.id'), nullable=True)
    funnel_steps = db.Column(db.JSON, default=['contact', 'calendar', 'survey'])

    
    appointments = db.relationship('Appointment', backref='event', lazy='dynamic')

    def __repr__(self):
        return f'<Event {self.name}>'

class Program(db.Model):
    __tablename__ = 'programs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    # Description removed per user request

    enrollments = db.relationship('Enrollment', backref='program', lazy='dynamic')

    def __repr__(self):
        return f'<Program {self.name}>'

class Enrollment(db.Model):
    __tablename__ = 'enrollments'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('programs.id'), nullable=False)
    enrollment_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='active') # active, completed, dropped
    total_agreed = db.Column(db.Float) # The price agreed upon (might differ from list price)

    payments = db.relationship('Payment', backref='enrollment', lazy='dynamic', cascade="all, delete-orphan")
    
    # New: Explicit closer association
    closer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    closer = db.relationship('User', foreign_keys=[closer_id], backref='sales_made')

    @property
    def total_paid(self):
        # Calculate total paid amount for completed payments
        completed_payments = self.payments.filter_by(status='completed').all()
        return sum(p.amount for p in completed_payments)

class PaymentMethod(db.Model):
    __tablename__ = 'payment_methods'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    commission_percent = db.Column(db.Float, default=0.0) # e.g. 2.9 for 2.9%
    commission_fixed = db.Column(db.Float, default=0.0) # e.g. 0.30 for $0.30
    is_active = db.Column(db.Boolean, default=True)
    
    payments = db.relationship('Payment', backref='method', lazy='dynamic')

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey('enrollments.id'), nullable=False)
    payment_method_id = db.Column(db.Integer, db.ForeignKey('payment_methods.id'), nullable=True) # made nullable for migration but should be required
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    transaction_id = db.Column(db.String(100)) # Optional external ID reference
    payment_type = db.Column(db.String(20)) # full, down_payment, installment, renewal
    reference_id = db.Column(db.String(100))
    status = db.Column(db.String(20), default='completed') # completed, pending, failed

    @property
    def payment_type_label(self):
        labels = {
            'full': 'Pago Completo',
            'down_payment': 'Primer Pago',
            'installment': 'Cuota',
            'renewal': 'Renovación',
            'deposit': 'Seña'
        }
        return labels.get(self.payment_type, self.payment_type)

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    closer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=True) # Check nullable first to avoid migration issues? Let's say nullable=True for now.
    start_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='scheduled') # scheduled, completed, canceled, no_show

class Availability(db.Model):
    __tablename__ = 'availability'
    id = db.Column(db.Integer, primary_key=True)
    closer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False) 
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    def __repr__(self):
        return f'<Availability Date={self.date} {self.start_time}-{self.end_time}>'

class SurveyQuestion(db.Model):
    __tablename__ = 'survey_questions'
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    question_type = db.Column(db.String(50), default='text') # text, boolean, select
    options = db.Column(db.Text) # JSON string or comma-separated for select options
    order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    mapping_field = db.Column(db.String(50), nullable=True) # name, email, phone, instagram
    step = db.Column(db.String(20), default='survey') # landing, survey
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=True) # None = Global if group also None
    event_group_id = db.Column(db.Integer, db.ForeignKey('event_groups.id'), nullable=True) # Link to group
    
    event = db.relationship('Event', backref='questions')
    
    def __repr__(self):
        return f'<Question {self.text}>'

class SurveyAnswer(db.Model):
    __tablename__ = 'survey_answers'
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False) 
    question_id = db.Column(db.Integer, db.ForeignKey('survey_questions.id'), nullable=False)
    answer = db.Column(db.Text)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'))
    
    question = db.relationship('SurveyQuestion')
    appointment = db.relationship('Appointment', backref='survey_answers')

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(50), default='variable') # fixed (from recurring), variable
    is_recurring = db.Column(db.Boolean, default=False) # True if it came from a recurring template

    def __repr__(self):
        return f'<Expense {self.description} ${self.amount}>'

class RecurringExpense(db.Model):
    __tablename__ = 'recurring_expenses'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    day_of_month = db.Column(db.Integer, default=1) # Day to generate the expense
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<RecurringExpense {self.description} ${self.amount}>'
