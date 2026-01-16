from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField, DateField, TimeField, FloatField, HiddenField, TextAreaField
from wtforms.validators import DataRequired, Email, Length

class LeadForm(FlaskForm):
    username = StringField('Nombre', validators=[DataRequired(), Length(min=2, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    phone = StringField('Teléfono', validators=[Length(max=20)])
    instagram = StringField('Instagram', validators=[Length(max=50)])
    submit = SubmitField('Guardar Lead')

class AppointmentForm(FlaskForm):
    lead_id = SelectField('Lead', coerce=int, validators=[DataRequired()])
    date = DateField('Fecha', validators=[DataRequired()])
    time = TimeField('Hora', validators=[DataRequired()])
    submit = SubmitField('Agendar Cita')

class SaleForm(FlaskForm):
    lead_id = HiddenField('Lead ID', validators=[DataRequired()])
    # We display name in a readonly field or just via JS updating a div
    lead_search = StringField('Buscar Lead') # Search input
    
    program_id = SelectField('Programa', coerce=int, validators=[DataRequired()])
    amount = FloatField('Monto ($USD)', validators=[DataRequired()])
    
    payment_type = SelectField('Tipo de Pago', choices=[
        ('full', 'Pago Completo'), 
        ('down_payment', 'Primer Pago (Inicial)'),
        ('installment', 'Cuota'),
        ('renewal', 'Renovación')
    ], validators=[DataRequired()])
    
    payment_method_id = SelectField('Método de Pago', coerce=int, validators=[DataRequired()])
    # Removed transaction_id and details per user request
    
    submit = SubmitField('Registrar Venta')

class CloserPaymentForm(FlaskForm):
    amount = FloatField('Monto ($USD)', validators=[DataRequired()])
    date = DateField('Fecha de Pago', format='%Y-%m-%d', validators=[DataRequired()])
    payment_type = SelectField('Tipo de Pago', choices=[
        ('full', 'Pago Completo'),
        ('down_payment', 'Primer Pago'),
        ('installment', 'Cuota'),
        ('renewal', 'Renovación')
    ], validators=[DataRequired()])
    payment_method_id = SelectField('Método de Pago', coerce=int, validators=[DataRequired()])
    reference_id = StringField('Referencia / ID Transacción', validators=[Length(max=100)])
    status = SelectField('Estado', choices=[
        ('completed', 'Completado'),
        ('pending', 'Pendiente'),
        ('failed', 'Fallido')
    ], default='completed')
    submit = SubmitField('Guardar Pago')

from wtforms import IntegerField, BooleanField

class CloserStatsForm(FlaskForm):
    date = DateField('Fecha', format='%Y-%m-%d', validators=[DataRequired()])
    
    # Slots
    slots_available = IntegerField('Slots Disponibles', default=0)
    
    # First Calls
    first_agendas = IntegerField('Primeras Agendas', default=0)
    first_agendas_attended = IntegerField('Primeras Asistencias', default=0)
    first_agendas_no_show = IntegerField('No show Primeras agendas', default=0)
    first_agendas_rescheduled = IntegerField('Reprogramaciones Primeras agendas', default=0)
    first_agendas_canceled = IntegerField('Cancelaciones Primeras agendas', default=0)
    
    # Second Calls
    second_agendas = IntegerField('Segundas Agendas', default=0)
    second_agendas_attended = IntegerField('Segundas Asistencias', default=0)
    second_agendas_no_show = IntegerField('No show Segundas agendas', default=0)
    second_agendas_rescheduled = IntegerField('Reprogramaciones Segundas agendas', default=0)
    second_agendas_canceled = IntegerField('Cancelaciones Segundas agendas', default=0)
    
    # Other Metrics
    second_calls_booked = IntegerField('2th Call Agendada', default=0)
    presentations = IntegerField('Presentaciones', default=0)
    sales_on_call = IntegerField('Ventas en llamada', default=0)
    sales_followup = IntegerField('Ventas en seguimiento', default=0)
    
    followups_started_booking = IntegerField('Seguimientos iniciados para agenda', default=0)
    followups_started_closing = IntegerField('Seguimientos iniciados para cierre', default=0)
    
    replies_booking = IntegerField('Respuestas para agenda', default=0)
    replies_sales = IntegerField('Respuestas para venta', default=0)
    
    self_generated_bookings = IntegerField('Agendas propias', default=0)
    
    # Checklist
    notion_completed = SelectField('¿Ya completaste el Notion?', choices=[('1', 'Sí'), ('0', 'No')])
    objection_form_completed = SelectField('¿Ya completaste el formulario de objeciones?', choices=[('1', 'Sí'), ('0', 'No')])
    
    win_of_day = TextAreaField('Win del día', validators=[Length(max=500)])
    improvement_area = TextAreaField('Cosa a mejorar', validators=[Length(max=500)])
    
    submit = SubmitField('Guardar Reporte')
