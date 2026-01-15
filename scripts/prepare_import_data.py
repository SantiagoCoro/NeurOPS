import csv
import os
from datetime import datetime
from collections import defaultdict

INPUT_FILE = 'Ventas-Usuarios (5).csv'
OUTPUT_DIR = 'data_import'

# Mapping for Payment Types
# Logic from user: "Los que digan deposit y sean 100$ o menos marcalos como seña, si son mas de 100 marcalo como primer pago"
# We will apply this to "Primer Pago" as well if it's small.

def parse_date(date_str):
    try:
        # csv seems to use d/m/y like 2/9/2024
        return datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
    except ValueError:
        return None

def clean_amount(amount_str):
    if not amount_str:
        return 0.0
    return float(amount_str.replace(',', ''))

def process_csv():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # DEBUG_LIMIT = 100
    allowed_emails = set()
    
    # --- 1. Process Agendas FIRST (Llamadas-Grid view.csv) ---
    agendas_file = 'Llamadas-Grid view.csv'
    all_agendas_list = []
    
    today = datetime.now()
    
    if os.path.exists(agendas_file):
        print(f"Procesando agendas desde: {agendas_file}") 
        with open(agendas_file, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            
            for row in reader:
                email = row.get('Mail', '').strip().lower()
                if not email:
                    continue
                             
                raw_date = row.get('Registro', '')
                date_iso = parse_date(raw_date)

                if not date_iso:
                    continue
                
                # Check for future dates
                dt_obj = datetime.strptime(date_iso, '%Y-%m-%d')
                if dt_obj > today:
                    print(f"Skipping future date: {date_iso} for {email}")
                    continue
                
                new_entry = {
                    'email': email,
                    'username': row.get('Nombre', '').strip(),
                    'created_at': date_iso,
                    'phone': row.get('Whatsapp', '').strip(),
                    'instagram': row.get('Instagram', '').strip(),
                    'closer': row.get('Closer', '').strip(),
                    'role': 'lead' 
                }
                all_agendas_list.append(new_entry)
                        
    else:
        print(f"Advertencia: No se encontró {agendas_file}")

    # Sort by created_at DESC (Newest first)
    all_agendas_list.sort(key=lambda x: x['created_at'], reverse=True)
    
    # Take top 1000
    top_1000 = all_agendas_list[:1000]
    print(f"Total Agendas found: {len(all_agendas_list)}. Keeping top {len(top_1000)} most recent.")

    agendas_map = {}
    allowed_emails = set()
    
    for entry in top_1000:
        email = entry['email']
        # Deduplicate logic: Since we sorted via created_at DESC, the first one encountered is the newest.
        # But wait, original logic kept EARLIEST date? 
        # "Deduplicate: Keep earliest date" was in the previous code.
        # Use case: User registered multiple times. Which one is the "entry" date? Usually the first one.
        # However, "1000 most recent users" usually means users who interacted recently?
        # If I have [OldEntry 2020, NewEntry 2025] for same email.
        # If I sort DESC, I see 2025 first.
        # If I keep it, I have the 2025 entry.
        # If I want the "earliest" date to be the registration date, I should handle that.
        
        # Let's stick to "1000 most recent users" -> The users who appear recently.
        # If a user appears in 2025 and 2023, they are a "recent user".
        # But their 'created_at' should probably be the original registration?
        # Actually, let's keep it simple: Just take the unique emails corresponding to the 1000 most recent ENTRIES.
        # But we need to build a map.
        
        if email not in agendas_map:
            agendas_map[email] = entry
            allowed_emails.add(email)
        # If email exists, we already have the NEWER entry (because of sort). 
        # If we want the OLDER date, we should look at the full list?
        # Let's trust the "most recent" entry for now as the active lead data.

    # Write agendas immediately
    with open(os.path.join(OUTPUT_DIR, 'agendas_clean.csv'), 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['email', 'username', 'created_at', 'phone', 'instagram', 'closer', 'role']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for email, data in agendas_map.items():
            writer.writerow(data)

    print(f"Agendas únicas extraídas: {len(agendas_map)}")


    # --- 2. Process Users & Payments (Ventas-Usuarios (5).csv) ---
    # Only for allowed_emails
    
    users = {} # email -> {data}
    programs = {} # name -> price
    payments = []
    
    with open(INPUT_FILE, mode='r', encoding='utf-8-sig') as infile:
        reader = csv.DictReader(infile)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        
        print(f"Procesando ventas para usuarios permitidos (2025-2026)...")
        
        for row in reader:
            email = row.get('Correo', '').strip().lower()
            if not email:
                continue
            
            # --- FILTER: ONLY ALLOWED EMAILS ---
            if email not in allowed_emails:
                continue

            # --- User Processing ---
            raw_date = row.get('Marca temporal', '')
            date_iso = parse_date(raw_date)
            
            if not date_iso:
                # print(f"DEBUG: Failed to parse date '{raw_date}' for {email}")
                # If date is invalid but email is allowed, assume it's an old user?
                # But for sales we usually want a date.
                # Let's skip if no valid date found to match user complaint.
                continue

            # Check for future dates
            dt_obj = datetime.strptime(date_iso, '%Y-%m-%d')
            if dt_obj > today:
                print(f"Skipping future sale date: {date_iso} for {email}")
                continue

            fullname = row.get('Nombre', '').strip()
            phone = row.get('Teléfono', '').strip()
            
            if '@' not in email:
                instagram_handle = email
                instagram = instagram_handle
                real_email = f"no_email_{instagram_handle}@example.com"
            else:
                instagram = '' 
                real_email = email
            
            # Update user dict 
            program_name = row.get('Programa', '').strip()
            
            if real_email not in users:
                users[real_email] = {
                    'username': fullname or real_email.split('@')[0],
                    'email': real_email,
                    'phone': phone,
                    'instagram': instagram,
                    'role': 'lead', 
                    'last_program': program_name,
                    'has_paid_real_money': False
                }
            else:
                if fullname: users[real_email]['username'] = fullname
                if phone: users[real_email]['phone'] = phone
                if instagram: users[real_email]['instagram'] = instagram
                if program_name: users[real_email]['last_program'] = program_name
            
            # --- Payment & Program Processing ---
            amount_str = row.get('Monto abonado', '0')
            amount = clean_amount(amount_str)
            
            raw_pago_type = row.get('Pago', '').strip()
            
            final_payment_type = 'installment' # default
            
            if 'seña' in raw_pago_type.lower():
                final_payment_type = 'deposit'
            elif 'primer pago' in raw_pago_type.lower():
                if amount <= 100:
                    final_payment_type = 'deposit'
                else:
                    final_payment_type = 'down_payment'
            elif 'completo' in raw_pago_type.lower():
                final_payment_type = 'full'
            elif 'cuota' in raw_pago_type.lower():
                final_payment_type = 'installment'
            
            if final_payment_type in ['full', 'down_payment', 'installment', 'deposit']:
                if final_payment_type != 'deposit':
                    users[real_email]['has_paid_real_money'] = True
            
            # --- Programs ---
            if program_name:
                current_max = programs.get(program_name, 0.0)
                if final_payment_type == 'full' and amount > current_max:
                    programs[program_name] = amount
                elif program_name not in programs:
                    programs[program_name] = 0.0 
            
            # --- Add Payment Record ---
            payments.append({
                'email': real_email,
                'program': program_name,
                'amount': amount,
                'date': date_iso,
                'type': final_payment_type,
                'method': row.get('Método de Pago', 'Otro') if row.get('Método de Pago', '') in ['PayPal', 'Stripe', 'Hotmart'] else 'Otro',
                'closer': row.get('Closer', ''),
                'raw_type': raw_pago_type
            })

    # write users
    with open(os.path.join(OUTPUT_DIR, 'users_clean.csv'), 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['username', 'email', 'phone', 'instagram', 'role', 'program_interest']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for email, u in users.items():
            role = 'student' if u['has_paid_real_money'] else 'lead'
            writer.writerow({
                'username': u['username'],
                'email': u['email'],
                'phone': u['phone'],
                'instagram': u['instagram'],
                'role': role,
                'program_interest': u['last_program']
            })

    # write programs
    with open(os.path.join(OUTPUT_DIR, 'programs_clean.csv'), 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['name', 'price']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for name, price in programs.items():
            writer.writerow({'name': name, 'price': price})

    # write payments
    with open(os.path.join(OUTPUT_DIR, 'payments_clean.csv'), 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['email', 'program', 'amount', 'date', 'type', 'method', 'closer', 'raw_type']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in payments:
            writer.writerow(p)

    print("Procesamiento completado. Archivos generados en folder 'data_import'.")

if __name__ == '__main__':
    process_csv()
