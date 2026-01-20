from flask_wtf import FlaskForm
from app.closer.forms import SaleForm
from wtforms import StringField, PasswordField, SelectField, SubmitField, TextAreaField, IntegerField, BooleanField, FloatField, DateField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange
import datetime

class UserForm(FlaskForm):
    username = StringField('Usuario', validators=[DataRequired(), Length(min=2, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Contraseña', validators=[Optional(), Length(min=4)]) # Optional for editing
    role = SelectField('Rol', choices=[('admin', 'Administrador'), ('closer', 'Closer'), ('lead', 'Lead'), ('student', 'Estudiante')], validators=[DataRequired()])
    timezone = SelectField('Zona Horaria', validators=[Optional()]) # Choices populated in route or init
    submit = SubmitField('Guardar')

class ManualAddForm(FlaskForm):
    username = StringField('Nombre Completo', validators=[DataRequired(), Length(min=2, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    phone = StringField('Teléfono', validators=[Optional(), Length(max=20)])
    instagram = StringField('Instagram', validators=[Optional(), Length(max=64)])
    
    role = SelectField('Tipo de Usuario', choices=[
        ('lead', 'Lead (Nuevo)'),
        ('agenda', 'Agendas (Cita Pendiente)'),
        ('student', 'Cliente (Venta)')
    ], validators=[DataRequired()])
    
    submit = SubmitField('Guardar')

class SurveyQuestionForm(FlaskForm):
    text = StringField('Pregunta', validators=[DataRequired(), Length(max=255)])
    question_type = SelectField('Tipo', choices=[('text', 'Texto Corto'), ('textarea', 'Texto Largo'), ('select', 'Selección'), ('email', 'Email'), ('tel', 'Teléfono')], validators=[DataRequired()])
    step = SelectField('Etapa', choices=[('survey', 'Encuesta (Post-Cita)'), ('landing', 'Landing (Datos de Contacto)')], default='survey')
    mapping_field = SelectField('Mapeo de Campo (Solo Landing)', choices=[('', 'Ninguno'), ('name', 'Nombre Completo'), ('email', 'Email'), ('phone', 'Teléfono'), ('instagram', 'Instagram')], validators=[Optional()])
    # target will store "global", "group_ID", "event_ID"
    target = SelectField('Asignar a', validators=[DataRequired()]) 
    options = TextAreaField('Opciones (para Selección)', description="Separar por comas (ej: Opción 1, Opción 2)")
    # order field removed, handled by builder
    is_active = BooleanField('Activa', default=True)
    submit = SubmitField('Guardar Pregunta')

class EventGroupForm(FlaskForm):
    name = StringField('Nombre del Grupo', validators=[DataRequired(), Length(max=100)])
    submit = SubmitField('Guardar Grupo')

class EventForm(FlaskForm):
    name = StringField('Nombre del Evento', validators=[DataRequired(), Length(max=100)])
    utm_source = StringField('UTM Source', validators=[DataRequired(), Length(max=64)], description="Identificador único para la URL (ej: 'vsl', 'workshop')")
    group_id = SelectField('Grupo', coerce=int, validators=[Optional()])
    is_active = BooleanField('Activo', default=True)
    submit = SubmitField('Guardar Evento')

class ProgramForm(FlaskForm):
    name = StringField('Nombre del Programa', validators=[DataRequired(), Length(max=100)])
    price = FloatField('Precio ($USD)', validators=[DataRequired()])
    submit = SubmitField('Guardar Programa')

class PaymentMethodForm(FlaskForm):
    name = StringField('Nombre del Método', validators=[DataRequired(), Length(max=50)])
    commission_percent = FloatField('Comisión (%)', default=0.0)
    commission_fixed = FloatField('Comisión Fija ($USD)', default=0.0)
    is_active = BooleanField('Activo', default=True)
    submit = SubmitField('Guardar Método')

class ClientEditForm(FlaskForm):
    username = StringField('Nombre', validators=[DataRequired(), Length(min=2, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    role = SelectField('Rol', choices=[('lead', 'Cliente'), ('agenda', 'Agenda'), ('student', 'Estudiante')], validators=[DataRequired()])
    phone = StringField('Teléfono', validators=[Optional(), Length(max=20)])
    instagram = StringField('Instagram', validators=[Optional(), Length(max=64)])
    status = SelectField('Estado', choices=[
        ('new', 'Nuevo'), 
        ('pending', 'Pendiente'), 
        ('agenda', 'Agenda'),
        ('completed', 'Completado'), 
        ('renewed', 'Renovado'), 
        ('canceled', 'Cancelado')
    ], validators=[Optional()])
    submit = SubmitField('Actualizar Cliente')

class PaymentForm(FlaskForm):
    amount = FloatField('Monto ($USD)', validators=[DataRequired()])
    date = DateField('Fecha de Pago', format='%Y-%m-%d', validators=[DataRequired()])
    payment_type = SelectField('Tipo de Pago', choices=[
        ('full', 'Pago Completo'),
        ('down_payment', 'Primer Pago'),
        ('installment', 'Cuota'),
        ('renewal', 'Renovación')
    ], validators=[DataRequired()])
    payment_method_id = SelectField('Método de Pago', coerce=int, validators=[Optional()])
    reference_id = StringField('Referencia / ID Transacción', validators=[Optional(), Length(max=100)])
    status = SelectField('Estado', choices=[
        ('completed', 'Completado'),
        ('pending', 'Pendiente'),
        ('failed', 'Fallido')
    ], default='completed')
    closer_id = SelectField('Closer Asignado', coerce=int, validators=[Optional()])
    submit = SubmitField('Guardar Pago')

class ExpenseForm(FlaskForm):
    description = StringField('Descripción', validators=[DataRequired()])
    amount = FloatField('Monto', validators=[DataRequired(), NumberRange(min=0)])
    date = DateField('Fecha', validators=[DataRequired()], default=lambda: datetime.date.today())
    category = SelectField('Categoría', choices=[('variable', 'Variable/Extra'), ('fixed', 'Fijo')], default='variable')
    submit = SubmitField('Registrar Gasto')

class RecurringExpenseForm(FlaskForm):
    description = StringField('Descripción del Gasto Fijo', validators=[DataRequired()])
    amount = FloatField('Monto Mensual', validators=[DataRequired(), NumberRange(min=0)])
    day_of_month = IntegerField('Día de Cobro (1-31)', validators=[DataRequired(), NumberRange(min=1, max=31)], default=1)
    is_active = SelectField('Estado', choices=[(1, 'Activo'), (0, 'Inactivo')], coerce=int, default=1)
    is_active = SelectField('Estado', choices=[(1, 'Activo'), (0, 'Inactivo')], coerce=int, default=1)
    submit = SubmitField('Guardar Configuración')

class AdminSaleForm(SaleForm):
    closer_id = SelectField('Closer Asignado', coerce=int, validators=[DataRequired()])

