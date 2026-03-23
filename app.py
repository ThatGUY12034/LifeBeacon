import os
import io
import secrets
import hashlib
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_file, abort)
import psycopg2
import psycopg2.extras

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
DATABASE_URL = os.environ.get('DATABASE_URL', '')

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

REPORT_CATEGORIES = [
    'General', 'Blood Pressure', 'Diabetes / Sugar',
    'Heart', 'Kidney', 'Liver', 'Thyroid',
    'X-Ray / Scan', 'Prescription', 'Other'
]

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    # Create tables with ALL required columns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'patient',
            qr_token TEXT UNIQUE,
            dob TEXT,
            blood_group TEXT,
            allergies TEXT,
            chronic_conditions TEXT,
            medications TEXT,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            emergency_contact_relation TEXT,
            medical_summary TEXT,
            primary_doctor_id INTEGER,
            last_diagnosis TEXT,
            last_diagnosis_date TIMESTAMP,
            current_condition_status TEXT DEFAULT 'stable'
        );
        
        CREATE TABLE IF NOT EXISTS health_readings (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            reading_type TEXT NOT NULL,
            value1 REAL,
            value2 REAL,
            notes TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS medical_reports (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_data BYTEA NOT NULL,
            file_type TEXT NOT NULL,
            category TEXT NOT NULL,
            notes TEXT,
            uploaded_at TIMESTAMP DEFAULT NOW(),
            doctor_id INTEGER REFERENCES users(id),
            diagnosis TEXT,
            severity TEXT DEFAULT 'normal'
        );
    """)
    
    # Add missing columns if upgrading existing DB (for backward compatibility)
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS medical_summary TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_doctor_id INTEGER;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_diagnosis TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_diagnosis_date TIMESTAMP;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_condition_status TEXT DEFAULT 'stable';")
        cur.execute("ALTER TABLE medical_reports ADD COLUMN IF NOT EXISTS doctor_id INTEGER;")
        cur.execute("ALTER TABLE medical_reports ADD COLUMN IF NOT EXISTS diagnosis TEXT;")
        cur.execute("ALTER TABLE medical_reports ADD COLUMN IF NOT EXISTS severity TEXT DEFAULT 'normal';")
        conn.commit()
    except Exception as e:
        print(f"Column addition error (may already exist): {e}")
        conn.rollback()
    
    conn.commit()
    cur.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"DB init error: {e}")

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────

def hash_password(p):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + p).encode()).hexdigest()
    return f"{salt}:{h}"

def check_password(stored, p):
    salt, h = stored.split(':', 1)
    return hashlib.sha256((salt + p).encode()).hexdigest() == h

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_user(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    cur.close(); conn.close()
    return user

def current_user():
    if 'user_id' in session:
        return get_user(session['user_id'])
    return None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─── DYNAMIC SUMMARY ──────────────────────────────────────────────────────────

def generate_summary(user, readings_bp=None, readings_sugar=None):
    parts = []
    name = (user.get('name') or 'The patient').split()[0]

    if user.get('blood_group'):
        parts.append(f"{name} has blood group {user['blood_group']}.")

    if user.get('chronic_conditions'):
        parts.append(f"Known chronic conditions: {user['chronic_conditions'].strip()}.")

    if user.get('allergies'):
        parts.append(f"Allergic to: {user['allergies'].strip()}. Avoid these substances.")

    if user.get('medications'):
        parts.append(f"Currently on: {user['medications'].strip()}.")

    urgent = False

    if readings_bp and readings_bp[0]['value1']:
        sys_val = readings_bp[0]['value1']
        dia_val = readings_bp[0]['value2'] or 0
        if sys_val >= 180 or dia_val >= 120:
            bp_status = "critically high (hypertensive crisis)"
            urgent = True
        elif sys_val >= 140 or dia_val >= 90:
            bp_status = "high (hypertension stage 2)"
        elif sys_val >= 130 or dia_val >= 80:
            bp_status = "elevated (hypertension stage 1)"
        elif sys_val >= 120:
            bp_status = "slightly elevated (pre-hypertension)"
        else:
            bp_status = "normal"
        parts.append(f"Latest BP {int(sys_val)}/{int(dia_val)} mmHg — {bp_status}.")

    if readings_sugar and readings_sugar[0]['value1']:
        sg = readings_sugar[0]['value1']
        if sg >= 300:
            sg_status = "critically high — immediate attention required"
            urgent = True
        elif sg >= 200:
            sg_status = "high (diabetic range — uncontrolled)"
        elif sg >= 126:
            sg_status = "elevated (diabetic range)"
        elif sg >= 100:
            sg_status = "slightly elevated (pre-diabetic range)"
        else:
            sg_status = "normal"
        parts.append(f"Latest blood sugar {int(sg)} mg/dL — {sg_status}.")

    if urgent:
        parts.append("⚠️ Condition may be unstable. Immediate medical attention required.")
    elif parts:
        parts.append("Monitor vitals and follow prescribed treatment plan.")

    if not parts:
        return "No medical summary available. Please complete your medical profile."

    return " ".join(parts)

def refresh_summary(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='bp' ORDER BY timestamp DESC LIMIT 5", (user_id,))
    bp = cur.fetchall()
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='sugar' ORDER BY timestamp DESC LIMIT 5", (user_id,))
    sugar = cur.fetchall()
    summary = generate_summary(user, bp, sugar)
    cur2 = conn.cursor()
    cur2.execute("UPDATE users SET medical_summary=%s WHERE id=%s", (summary, user_id))
    conn.commit()
    cur.close(); cur2.close(); conn.close()
    return summary

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        role = request.form.get('role', 'patient')
        token = secrets.token_urlsafe(32)
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(
                "INSERT INTO users (name,email,password_hash,role,qr_token) VALUES (%s,%s,%s,%s,%s) RETURNING id,role",
                (name, email, hash_password(password), role, token)
            )
            row = cur.fetchone()
            conn.commit()
            session['user_id'] = row['id']
            session['role'] = row['role']
            return redirect(url_for('dashboard'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return render_template('register.html', error='Email already registered.')
        finally:
            cur.close(); conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE email=%s", (request.form['email'],))
        user = cur.fetchone()
        cur.close(); conn.close()
        if user and check_password(user['password_hash'], request.form['password']):
            session['user_id'] = user['id']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid email or password.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='bp' ORDER BY timestamp DESC LIMIT 20", (user['id'],))
    readings_bp = cur.fetchall()
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='sugar' ORDER BY timestamp DESC LIMIT 20", (user['id'],))
    readings_sugar = cur.fetchall()
    cur.execute("SELECT id,original_name,category,file_type,uploaded_at FROM medical_reports WHERE user_id=%s ORDER BY uploaded_at DESC LIMIT 5", (user['id'],))
    recent_reports = cur.fetchall()
    cur.close(); conn.close()
    return render_template('dashboard.html', user=user,
                           readings_bp=readings_bp, readings_sugar=readings_sugar,
                           recent_reports=recent_reports)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user()
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users SET dob=%s,blood_group=%s,allergies=%s,chronic_conditions=%s,
            medications=%s,emergency_contact_name=%s,emergency_contact_phone=%s,
            emergency_contact_relation=%s WHERE id=%s
        """, (
            request.form.get('dob'), request.form.get('blood_group'),
            request.form.get('allergies'), request.form.get('chronic_conditions'),
            request.form.get('medications'), request.form.get('emergency_contact_name'),
            request.form.get('emergency_contact_phone'), request.form.get('emergency_contact_relation'),
            user['id']
        ))
        conn.commit()
        cur.close(); conn.close()
        refresh_summary(user['id'])
        return redirect(url_for('dashboard'))
    return render_template('profile.html', user=user)

@app.route('/add_reading', methods=['POST'])
@login_required
def add_reading():
    user = current_user()
    rtype = request.form['type']
    v1 = float(request.form['systolic'] if rtype == 'bp' else request.form['sugar'])
    v2 = float(request.form['diastolic']) if rtype == 'bp' else None
    notes = request.form.get('notes', '')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO health_readings (user_id,reading_type,value1,value2,notes) VALUES (%s,%s,%s,%s,%s)",
        (user['id'], rtype, v1, v2, notes)
    )
    conn.commit()
    cur.close(); conn.close()
    refresh_summary(user['id'])
    return redirect(url_for('dashboard'))

@app.route('/api/readings/<rtype>')
@login_required
def api_readings(rtype):
    user = current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM health_readings WHERE user_id=%s AND reading_type=%s ORDER BY timestamp ASC LIMIT 30",
        (user['id'], rtype)
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([{
        'timestamp': r['timestamp'].strftime('%b %d, %H:%M') if r['timestamp'] else '',
        'value1': r['value1'], 'value2': r['value2']
    } for r in rows])

@app.route('/api/summary')
@login_required
def api_summary():
    user = current_user()
    summary = refresh_summary(user['id'])
    return jsonify({'summary': summary})

@app.route('/qr')
@login_required
def qr_page():
    return render_template('qr.html', user=current_user())

@app.route('/qr/svg')
@login_required
def qr_svg():
    user = current_user()
    url = request.host_url + 'emergency/' + user['qr_token']
    from qr_generator import qr_to_svg
    return qr_to_svg(url), 200, {'Content-Type': 'image/svg+xml'}

@app.route('/qr/image')
@login_required
def qr_image():
    user = current_user()
    url = request.host_url + 'emergency/' + user['qr_token']
    from qr_generator import qr_to_png_bytes
    buf = qr_to_png_bytes(url)
    return send_file(buf, mimetype='image/png', download_name='lifebeacon-qr.png')

@app.route('/emergency/<token>')
def emergency_view(token):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE qr_token=%s", (token,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user:
        return "Invalid QR code.", 404
    summary = user.get('medical_summary') or refresh_summary(user['id'])
    return render_template('emergency.html', user=user, summary=summary)

# ─── MEDICAL REPORTS ──────────────────────────────────────────────────────────

@app.route('/reports')
@login_required
def reports():
    user = current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    category = request.args.get('category', '')
    if category:
        cur.execute(
            "SELECT id,original_name,category,file_type,notes,uploaded_at FROM medical_reports WHERE user_id=%s AND category=%s ORDER BY uploaded_at DESC",
            (user['id'], category)
        )
    else:
        cur.execute(
            "SELECT id,original_name,category,file_type,notes,uploaded_at FROM medical_reports WHERE user_id=%s ORDER BY uploaded_at DESC",
            (user['id'],)
        )
    reports_list = cur.fetchall()
    cur.execute(
        "SELECT category, COUNT(*) as cnt FROM medical_reports WHERE user_id=%s GROUP BY category",
        (user['id'],)
    )
    cat_counts = {r['category']: r['cnt'] for r in cur.fetchall()}
    cur.close(); conn.close()
    return render_template('reports.html', user=user, reports=reports_list,
                           categories=REPORT_CATEGORIES, cat_counts=cat_counts,
                           active_category=category)

@app.route('/reports/upload', methods=['POST'])
@login_required
def upload_report():
    user = current_user()
    if 'file' not in request.files:
        return redirect(url_for('reports'))
    file = request.files['file']
    if not file.filename or not allowed_file(file.filename):
        return redirect(url_for('reports'))
    file_data = file.read()
    if len(file_data) > MAX_FILE_SIZE:
        return redirect(url_for('reports'))
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = secrets.token_hex(16) + '.' + ext
    category = request.form.get('category', 'General')
    notes = request.form.get('notes', '')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO medical_reports (user_id,filename,original_name,file_data,file_type,category,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (user['id'], filename, file.filename, psycopg2.Binary(file_data), ext, category, notes)
    )
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('reports'))

@app.route('/reports/view/<int:report_id>')
@login_required
def view_report(report_id):
    user = current_user()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM medical_reports WHERE id=%s AND user_id=%s", (report_id, user['id']))
    report = cur.fetchone()
    cur.close(); conn.close()
    if not report:
        abort(404)
    mime_map = {'pdf': 'application/pdf', 'png': 'image/png',
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}
    mime = mime_map.get(report['file_type'], 'application/octet-stream')
    return send_file(
        io.BytesIO(bytes(report['file_data'])),
        mimetype=mime,
        download_name=report['original_name']
    )

@app.route('/reports/delete/<int:report_id>', methods=['POST'])
@login_required
def delete_report(report_id):
    user = current_user()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM medical_reports WHERE id=%s AND user_id=%s", (report_id, user['id']))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('reports'))

# ─── DOCTOR ROUTES ────────────────────────────────────────────────────────────

@app.route('/doctor')
@login_required
def doctor_panel():
    user = current_user()
    if user['role'] != 'doctor':
        return redirect(url_for('dashboard'))
    return render_template('doctor.html', user=user)

@app.route('/doctor/search')
@login_required
def doctor_search():
    user = current_user()
    if user['role'] != 'doctor':
        return jsonify({'error': 'Unauthorized'}), 403
    q = request.args.get('q', '')
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """SELECT id, name, email, blood_group, current_condition_status 
           FROM users 
           WHERE role='patient' AND (name ILIKE %s OR email ILIKE %s) 
           LIMIT 10""",
        (f'%{q}%', f'%{q}%')
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/doctor/patient/<int:pid>', methods=['GET', 'POST'])
@login_required
def doctor_patient_view(pid):
    user = current_user()
    if user['role'] != 'doctor':
        return redirect(url_for('dashboard'))
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Get patient info
    cur.execute("SELECT * FROM users WHERE id=%s", (pid,))
    patient = cur.fetchone()
    
    if not patient:
        return "Patient not found", 404
    
    # Handle report upload from doctor
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(url_for('doctor_patient_view', pid=pid))
        
        file = request.files['file']
        if file.filename and allowed_file(file.filename):
            file_data = file.read()
            if len(file_data) <= MAX_FILE_SIZE:
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = secrets.token_hex(16) + '.' + ext
                category = request.form.get('category', 'General')
                notes = request.form.get('notes', '')
                diagnosis = request.form.get('diagnosis', '')
                severity = request.form.get('severity', 'normal')
                
                cur2 = conn.cursor()
                cur2.execute("""
                    INSERT INTO medical_reports 
                    (user_id, filename, original_name, file_data, file_type, 
                     category, notes, doctor_id, diagnosis, severity) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (pid, filename, file.filename, psycopg2.Binary(file_data), 
                      ext, category, notes, user['id'], diagnosis, severity))
                
                # Update patient's last diagnosis and condition
                if diagnosis:
                    cur2.execute("""
                        UPDATE users 
                        SET last_diagnosis = %s, 
                            last_diagnosis_date = NOW(),
                            current_condition_status = %s,
                            primary_doctor_id = %s
                        WHERE id = %s
                    """, (diagnosis, severity, user['id'], pid))
                
                conn.commit()
                cur2.close()
                
                # Refresh patient's summary
                refresh_summary(pid)
                
                return redirect(url_for('doctor_patient_view', pid=pid))
    
    # Get patient data for display
    cur.execute("""
        SELECT * FROM health_readings 
        WHERE user_id=%s AND reading_type='bp' 
        ORDER BY timestamp DESC LIMIT 20
    """, (pid,))
    readings_bp = cur.fetchall()
    
    cur.execute("""
        SELECT * FROM health_readings 
        WHERE user_id=%s AND reading_type='sugar' 
        ORDER BY timestamp DESC LIMIT 20
    """, (pid,))
    readings_sugar = cur.fetchall()
    
    # Get patient's reports with doctor names
    cur.execute("""
        SELECT mr.*, u.name as doctor_name 
        FROM medical_reports mr
        LEFT JOIN users u ON mr.doctor_id = u.id
        WHERE mr.user_id=%s 
        ORDER BY mr.uploaded_at DESC
    """, (pid,))
    reports = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('doctor_patient.html', 
                         patient=patient,
                         readings_bp=readings_bp, 
                         readings_sugar=readings_sugar,
                         reports=reports,
                         doctor=user,
                         categories=REPORT_CATEGORIES)

if __name__ == '__main__':
    app.run(debug=True, port=5000)