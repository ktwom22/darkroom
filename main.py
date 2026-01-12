import os
import uuid
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from flask_mail import Mail, Message
import zipfile
import smtplib
from email.message import EmailMessage

# --- SETUP & CONFIG ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)

# --- CORE SETTINGS ---
# Pulls from Railway Variables, falls back to a string for local dev
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'vows-and-views-studio-secret')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- DATABASE ENGINE (The Darkroom Archive) ---
db_url = os.environ.get('DATABASE_URL')
if db_url:
    # Fix for Railway/SQLAlchemy compatibility
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # Fallback to local SQLite for offline development
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "studio.db")}'

# --- FOLDER MANAGEMENT (The Negative Archive) ---
# Ensure these match the paths you mount your Railway Volumes to
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
app.config['EXPORT_FOLDER'] = os.path.join(basedir, 'static', 'exports')

for folder in [app.config['UPLOAD_FOLDER'], app.config['EXPORT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# --- EMAIL SYSTEM (Darkroom Notifications) ---
# We use os.environ.get so you can change passwords in Railway without redeploying code
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'ktwom22@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'nagx lauv zyir xnvl')
app.config['MAIL_DEFAULT_SENDER'] = ('The Darkroom', app.config['MAIL_USERNAME'])

# --- INITIALIZE CORE SYSTEMS ---
db = SQLAlchemy(app)
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Auto-create tables on startup (Essential for fresh Railway Postgres)
with app.app_context():
    db.create_all()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- DATABASE MODELS ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

    # Business Identity
    business_name = db.Column(db.String(150))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    phone_number = db.Column(db.String(20))
    logo_url = db.Column(db.String(255), nullable=True)

    # Custom SMTP Settings (For personal email dispatch)
    smtp_server = db.Column(db.String(150), nullable=True)   # e.g., smtp.gmail.com
    smtp_port = db.Column(db.Integer, default=587)          # usually 587 or 465
    smtp_user = db.Column(db.String(150), nullable=True)     # their email login
    smtp_password = db.Column(db.String(150), nullable=True) # their App Password

class Session(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_name = db.Column(db.String(100), nullable=False)
    session_type = db.Column(db.String(50))
    date = db.Column(db.String(50))
    client_email = db.Column(db.String(100))
    client_phone = db.Column(db.String(20))
    notes = db.Column(db.Text)
    status = db.Column(db.String(50), default="In Planning")
    follow_up_date = db.Column(db.String(50))
    total_fee = db.Column(db.Float, default=0.0)
    amount_paid = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    photos = db.relationship('Photo', backref='session', lazy=True, cascade="all, delete-orphan")
    selection_submitted = db.Column(db.Boolean, default=False)
    location = db.Column(db.String(200), default="Studio") # <--- MUST EXIST

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    is_selected = db.Column(db.Boolean, default=False)
    session_id = db.Column(db.String(36), db.ForeignKey('session.id'))


# --- AUTH ROUTES ---
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        # 1. Collect data from form
        email = request.form.get('email')
        password = request.form.get('password')
        business_name = request.form.get('business_name')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        phone_number = request.form.get('phone_number')

        # 2. Check if user already exists (Prevents generic errors)
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for('signup'))

        # 3. Securely hash the password
        # Note: pbkdf2:sha256 is the secure standard for Flask-Login
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')

        # 4. Create the new User object
        new_user = User(
            email=email,
            password=hashed_pw,
            business_name=business_name,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number
        )

        try:
            db.session.add(new_user)
            db.session.commit()

            # 5. Automatically log them in and head to dashboard
            login_user(new_user)
            return redirect(url_for('dashboard'))

        except Exception as e:
            db.session.rollback()  # Undo any partial changes
            flash("An error occurred during registration. Please try again.")
            print(f"Database Error: {e}")  # This helps you debug in the terminal

    return render_template('auth.html', mode='signup')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash("Invalid credentials.")
    return render_template('auth.html', mode='login')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- DASHBOARD & MANAGEMENT ---
@app.route('/')
@login_required
def dashboard():
    sessions = Session.query.filter_by(user_id=current_user.id).all()
    total_rev = sum((s.total_fee or 0.0) for s in sessions)
    total_col = sum((s.amount_paid or 0.0) for s in sessions)
    pending_balance = total_rev - total_col

    today = datetime.now().strftime('%Y-%m-%d')
    reminders = [s for s in sessions if s.follow_up_date and s.follow_up_date != ""]
    reminders.sort(key=lambda x: x.follow_up_date)

    return render_template('dashboard.html', sessions=sessions, reminders=reminders,
                           now_string=today, total_collected=total_col, pending_balance=pending_balance)


@app.route('/create-session', methods=['POST'])
@login_required
def create_session():
    # 1. Capture numeric data with safety defaults
    val_total = float(request.form.get('total_fee') or 0.0)
    val_paid = float(request.form.get('amount_paid') or 0.0)

    # 2. Create the new session object
    new_session = Session(
        id=str(uuid.uuid4()),                    # Generate a unique ID for the Portal link
        client_name=request.form.get('client_name'),
        client_email=request.form.get('client_email'), # <--- THIS WAS MISSING
        session_type=request.form.get('session_type'),
        location=request.form.get('location'),   # Make sure this matches your HTML input name
        date=request.form.get('date'),
        total_fee=val_total,
        amount_paid=val_paid,
        user_id=current_user.id
    )

    # 3. Save to database
    try:
        db.session.add(new_session)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"DATABASE ERROR: {e}")

    return redirect(url_for('dashboard'))


@app.route('/client-manager')
@login_required
def client_manager():
    # Fetching sessions and sorting by date (newest first)
    # We use .filter_by(user_id=current_user.id) to ensure you only see YOUR clients
    sessions = Session.query.filter_by(user_id=current_user.id) \
        .order_by(Session.date.desc()) \
        .all()

    # Pre-calculating a string of today's date for the template
    now_string = datetime.now().strftime('%Y-%m-%d')

    return render_template('client_manager.html',
                           sessions=sessions,
                           now_string=now_string)


@app.route('/update-client-info/<id>', methods=['POST'])
@login_required
def update_client_info(id):
    session = Session.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    session.total_fee = float(request.form.get('total_fee') or 0.0)
    session.amount_paid = float(request.form.get('amount_paid') or 0.0)
    session.status = request.form.get('status')
    session.follow_up_date = request.form.get('follow_up_date')
    session.client_email = request.form.get('client_email')
    session.client_phone = request.form.get('client_phone')
    db.session.commit()
    return redirect(url_for('client_manager'))


# --- PORTAL & ASSET LOGIC ---
@app.route('/portal/<id>')
def portal(id):
    session_data = Session.query.get_or_404(id)
    return render_template('portal.html', session=session_data)


@app.route('/upload/<session_id>', methods=['POST'])
@login_required
def upload(session_id):
    files = request.files.getlist('file')
    for file in files:
        if file.filename:
            filename = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            db.session.add(Photo(filename=filename, session_id=session_id))
    db.session.commit()
    return redirect(url_for('portal', id=session_id))


@app.route('/delete-session/<id>')
@login_required
def delete_session(id):
    session = Session.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    db.session.delete(session)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/display/<filename>')
def display_image(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/toggle-selection/<int:photo_id>')
def toggle_selection(photo_id):
    # Find the photo in the database
    photo = Photo.query.get_or_404(photo_id)

    # Flip the boolean value (True to False, or False to True)
    photo.is_selected = not photo.is_selected

    # Save the change
    db.session.commit()

    # Refresh the portal page so they see the red heart
    return redirect(url_for('portal', id=photo.session_id))


import os


@app.route('/delete-photo/<int:photo_id>')
@login_required
def delete_photo(photo_id):
    # 1. Find the photo
    photo = Photo.query.get_or_404(photo_id)
    session_id = photo.session_id

    # 2. Construct the path to the physical file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)

    try:
        # 3. Physically delete the file from the static/uploads folder
        if os.path.exists(file_path):
            os.remove(file_path)

        # 4. Remove the record from the database
        db.session.delete(photo)
        db.session.commit()
        flash("Image permanently removed from archive.")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting file: {e}")

    # 5. Send you back to the portal you were just looking at
    return redirect(url_for('portal', id=session_id))


@app.route('/submit-selections/<id>')
def submit_selections(id):
    session_data = Session.query.get_or_404(id)
    selected_photos = Photo.query.filter_by(session_id=id, is_selected=True).all()

    if not selected_photos:
        flash("Please select photos before submitting.")
        return redirect(url_for('portal', id=id))

    # 1. Generate a unique filename for the ZIP
    zip_filename = f"Selections_{session_data.client_name.replace(' ', '_')}_{id}.zip"
    zip_path = os.path.join(app.config['EXPORT_FOLDER'], zip_filename)

    # 2. Create the ZIP on the server disk
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for photo in selected_photos:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            if os.path.exists(file_path):
                zf.write(file_path, photo.filename)

    # 3. Create the Download URL (Points to your server)
    # If running locally, this will be http://127.0.0.1:5000/static/exports/...
    download_url = url_for('static', filename=f'exports/{zip_filename}', _external=True)

    # 4. Construct the HTML Email
    msg = Message(
        subject=f"NEW ASSET REQUEST: {session_data.client_name}",
        recipients=['kptwombley@gmail.com']
    )

    msg.body = f"Client {session_data.client_name} has submitted {len(selected_photos)} photos for retouching.\n\nDownload Link: {download_url}"

    # Optional: Send a pretty HTML version of the email
    msg.html = f"""
    <div style="font-family: sans-serif; border: 2px solid black; padding: 20px; max-width: 500px;">
        <h2 style="text-transform: uppercase; letter-spacing: 2px;">Retouching Request</h2>
        <p><strong>Client:</strong> {session_data.client_name}</p>
        <p><strong>Count:</strong> {len(selected_photos)} Images</p>
        <hr style="border: 1px solid #eee; margin: 20px 0;">
        <a href="{download_url}" 
           style="background: black; color: white; padding: 15px 25px; text-decoration: none; display: inline-block; font-weight: bold; border-radius: 10px;">
           DOWNLOAD ZIP ARCHIVE
        </a>
    </div>
    """

    try:
        mail.send(msg)
        session_data.selection_submitted = True
        session_data.status = "Pending Retouch"
        db.session.commit()
        flash("Selections locked and sent to studio!")
    except Exception as e:
        print(f"Mail Error: {e}")
        flash("Submission error. Please contact the studio.")

    return redirect(url_for('portal', id=id))


@app.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    if request.method == 'POST':
        subject_category = request.form.get('subject')
        user_message = request.form.get('message')

        # Construct the email to YOU
        msg = Message(
            subject=f"STUDIO SUPPORT: {subject_category} from {current_user.business_name}",
            recipients=['kptwombley@gmail.com'],  # Your destination email
            body=f"""
            NEW SUPPORT REQUEST
            -------------------
            Studio Name: {current_user.business_name}
            Owner: {current_user.first_name} {current_user.last_name}
            Email: {current_user.email}
            Phone: {current_user.phone_number}

            CATEGORY: {subject_category}

            MESSAGE:
            {user_message}
            """
        )

        try:
            mail.send(msg)
            flash(f"Success! Your request has been routed to our lead developer.")
        except Exception as e:
            print(f"Mail Error: {e}")
            flash("System busy. Please try again or contact us via Instagram.")

        return redirect(url_for('dashboard'))

    return render_template('support.html')

@app.route('/complete-job/<id>') # Removing 'int:' allows for the UUID string
@login_required
def complete_job(id):
    session_data = Session.query.get_or_404(id)
    # Marking it as archived/completed hides it from the "Ready" banner
    session_data.selection_submitted = False
    session_data.status = "Completed"
    db.session.commit()
    flash(f"Archive for {session_data.client_name} marked as complete.")
    return redirect(url_for('dashboard'))


@app.route('/send-quick-email/<id>')
@login_required
def send_quick_email(id):
    print(f"\n--- MAIL TRIGGERED FOR SESSION: {id} ---")
    session_data = Session.query.get_or_404(id)

    if not session_data.client_email:
        print("DEBUG: Client email missing in database.")
        flash("No email address found for this client.")
        return redirect(url_for('client_manager'))

    # We use your GLOBAL config settings here
    msg = Message(
        subject=f"[{current_user.business_name}] Update Regarding Your Photos",
        # Shows the Shop Name but uses your authenticated email
        sender=(current_user.business_name, app.config['MAIL_USERNAME']),
        recipients=[session_data.client_email],
        reply_to=current_user.email
    )

    msg.body = (
        f"Hi {session_data.client_name},\n\n"
        f"I've updated your photo archive at {current_user.business_name}.\n"
        f"Access your portal here: {url_for('portal', id=id, _external=True)}\n\n"
        f"Best,\n{current_user.first_name}"
    )

    try:
        print(f"DEBUG: Attempting system dispatch via {app.config['MAIL_USERNAME']}...")
        mail.send(msg)
        print("--- SUCCESS: EMAIL SENT ---")
        flash(f"Email sent to {session_data.client_name}!")
    except Exception as e:
        print(f"--- MAIL ERROR: {str(e)} ---")
        flash(f"System Mail Error: {str(e)}")

    return redirect(url_for('client_manager'))


@app.route('/update-session/<id>', methods=['POST'])
@login_required
def update_session(id):
    session_data = Session.query.get_or_404(id)

    # 1. Capture the data from the form
    session_data.client_name = request.form.get('client_name')
    session_data.location = request.form.get('location')  # <--- MAKE SURE THIS IS HERE
    session_data.date = request.form.get('date')

    # Handle numbers safely
    session_data.total_fee = float(request.form.get('total_fee') or 0)
    session_data.amount_paid = float(request.form.get('amount_paid') or 0)

    # 2. Save to database
    db.session.commit()

    flash(f"Details updated for {session_data.client_name}")
    return redirect(url_for('client_manager'))


@app.route('/retouching-queue')
@login_required
def retouching_queue():
    # Only pull sessions where the client has clicked 'Submit Selections'
    sessions = Session.query.filter_by(selection_submitted=True).all()
    # Calculate stats for the header
    total_collected = sum(s.amount_paid or 0 for s in sessions)
    pending_balance = sum((s.total_fee or 0) - (s.amount_paid or 0) for s in sessions)

    return render_template('dashboard.html',
                           sessions=sessions,
                           is_queue_view=True,
                           total_collected=total_collected,
                           pending_balance=pending_balance)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)