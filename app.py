import os
import io
import secrets
import hashlib
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_file)
import psycopg2
import psycopg2.extras

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
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
            emergency_contact_relation TEXT
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
    """)
    conn.commit()
    cur.close()
    conn.close()

# Auto-initialize DB on startup
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
    cur.close(); conn.close()
    return render_template('dashboard.html', user=user,
                           readings_bp=readings_bp, readings_sugar=readings_sugar)


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
    data = []
    for r in rows:
        ts = r['timestamp'].strftime('%b %d, %H:%M') if r['timestamp'] else ''
        data.append({'timestamp': ts, 'value1': r['value1'], 'value2': r['value2']})
    return jsonify(data)


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
    return render_template('emergency.html', user=user)


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
        "SELECT id,name,email,blood_group FROM users WHERE role='patient' AND (name ILIKE %s OR email ILIKE %s) LIMIT 10",
        (f'%{q}%', f'%{q}%')
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/doctor/patient/<int:pid>')
@login_required
def doctor_patient_view(pid):
    user = current_user()
    if user['role'] != 'doctor':
        return redirect(url_for('dashboard'))
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s", (pid,))
    patient = cur.fetchone()
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='bp' ORDER BY timestamp DESC LIMIT 20", (pid,))
    readings_bp = cur.fetchall()
    cur.execute("SELECT * FROM health_readings WHERE user_id=%s AND reading_type='sugar' ORDER BY timestamp DESC LIMIT 20", (pid,))
    readings_sugar = cur.fetchall()
    cur.close(); conn.close()
    if not patient:
        return "Patient not found", 404
    return render_template('doctor_patient.html', patient=patient,
                           readings_bp=readings_bp, readings_sugar=readings_sugar, doctor=user)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
