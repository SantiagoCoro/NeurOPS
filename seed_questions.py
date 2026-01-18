from app import create_app,db
from app.models import DailyReportQuestion

app = create_app()

with app.app_context():
    questions = [
        {"text": "Win del día", "order": 1, "type": "text"},
        {"text": "Área de mejora / Obstáculo", "order": 2, "type": "text"},
        {"text": "¿Completaste el formulario de objeciones?", "order": 3, "type": "boolean"},
        {"text": "¿Actualizaste Notion?", "order": 4, "type": "boolean"}
    ]
    
    for q_data in questions:
        exists = DailyReportQuestion.query.filter_by(text=q_data["text"]).first()
        if not exists:
            q = DailyReportQuestion(text=q_data["text"], order=q_data["order"], question_type=q_data["type"])
            db.session.add(q)
            print(f"Added question: {q_data['text']}")
        else:
            print(f"Skipped existing: {q_data['text']}")
            
    db.session.commit()
    print("Seeding complete.")
