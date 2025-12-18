import os
import numpy as np
import pandas as pd
import logging
from functools import wraps
from datetime import datetime, timedelta, timezone
from benchmarks import BENCHMARKS
from datetime import timezone
from zoneinfo import ZoneInfo



from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

from clinical_scoring import SepsisScoring
from config import config


# =====================================================
# APP INITIALIZATION
# =====================================================
app = Flask(__name__)

config_name = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[config_name])

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sepsis_scorer = SepsisScoring()
logger.info("Clinical scoring system initialized successfully")


# =====================================================
# DATABASE MODELS
# =====================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    patients = db.relationship('Patient', backref='doctor', lazy=True)


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class SofaTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)

    test_type = db.Column(db.String(20), nullable=False)

    timestamp = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    total_score = db.Column(db.Integer)
    sepsis_criteria_met = db.Column(db.Boolean)
    high_risk = db.Column(db.Boolean)
    septic_shock_present = db.Column(db.Boolean)
    mortality_risk = db.Column(db.String(50))
    interpretation = db.Column(db.Text)

    respiration_score = db.Column(db.Integer)
    coagulation_score = db.Column(db.Integer)
    liver_score = db.Column(db.Integer)
    cardiovascular_score = db.Column(db.Integer)
    cns_score = db.Column(db.Integer)
    renal_score = db.Column(db.Integer)
    delta_sofa = db.Column(db.Integer)
    baseline_sofa = db.Column(db.Integer)

    respiratory_rate_22_or_higher = db.Column(db.Boolean)
    altered_mentation = db.Column(db.Boolean)
    systolic_bp_100_or_lower = db.Column(db.Boolean)

    sepsis_present = db.Column(db.Boolean)
    persistent_hypotension_on_vasopressors = db.Column(db.Boolean)
    lactate_greater_than_2 = db.Column(db.Boolean)
    adequate_volume_resuscitation = db.Column(db.Boolean)

    pao2_fio2 = db.Column(db.Float)
    respiratory_support = db.Column(db.Boolean)
    respiratory_rate = db.Column(db.Float)

    map_mmhg = db.Column(db.Float)
    systolic_bp = db.Column(db.Float)
    dopamine_dose = db.Column(db.Float)
    dobutamine_dose = db.Column(db.Float)
    epinephrine_dose = db.Column(db.Float)
    norepinephrine_dose = db.Column(db.Float)

    platelets = db.Column(db.Float)
    bilirubin = db.Column(db.Float)
    creatinine = db.Column(db.Float)
    lactate = db.Column(db.Float)
    urine_output = db.Column(db.Float)

    gcs = db.Column(db.Integer)
    suspected_infection = db.Column(db.Boolean)
    on_vasopressors = db.Column(db.Boolean)


# =====================================================
# LOGIN HANDLING
# =====================================================
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def doctor_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# =====================================================
# ROUTES
# =====================================================
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        name = request.form.get('name')
        password = request.form.get('password')

        if not email or not password or not name:
            flash('All fields are required.', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))

        user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password)
        )

        db.session.add(user)
        db.session.commit()

        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
@doctor_required
def dashboard():
    patients = Patient.query.filter_by(doctor_id=current_user.id).all()

    total_tests = 0
    sepsis_alerts = 0
    recent_tests = 0

    patient_data = []

    now_utc = datetime.now(timezone.utc)

    for patient in patients:
        tests = SofaTest.query.filter_by(patient_id=patient.id).all()
        total_tests += len(tests)

        # Recent activity (last 24h)
        for t in tests:
            ts = t.timestamp
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            if ts and (now_utc - ts).total_seconds() < 86400:
                recent_tests += 1

            if t.high_risk or t.septic_shock_present:
                sepsis_alerts += 1

        # Latest NEWS2 risk for dashboard badge
        latest_news2 = (
            SofaTest.query
            .filter_by(patient_id=patient.id, test_type='news2')
            .order_by(SofaTest.timestamp.desc())
            .first()
        )

        news2_risk = None
        if latest_news2:
            if latest_news2.high_risk:
                news2_risk = "High"
            else:
                news2_risk = "Low"

        patient_data.append({
            'patient': patient,
            'news2_risk': news2_risk
        })

    return render_template(
        'dashboard.html',
        patient_data=patient_data,          # ✅ REQUIRED BY TEMPLATE
        total_patients=len(patients),       # ✅ FIXES "0 patients"
        total_tests=total_tests,
        sepsis_alerts=sepsis_alerts,
        recent_tests=recent_tests
    )

@app.route('/patient/<int:patient_id>/test/new', methods=['GET', 'POST'])
@doctor_required
def new_sofa_test(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    if request.method == 'POST':
        test_type = request.form.get('test_type')

        if test_type == 'qsofa':
            return redirect(url_for('qsofa_test', patient_id=patient_id))
        elif test_type == 'sofa':
            return redirect(url_for('sofa_test', patient_id=patient_id))
        elif test_type =="septic_shock":
            return redirect(url_for('septic_shock_test', patient_id=patient_id))
        elif test_type == 'news2':
            return redirect(url_for('news2_test', patient_id=patient_id))

    return render_template('new_sofa_test.html', patient=patient)


@app.route('/patient/new', methods=['GET', 'POST'])
@doctor_required
def new_patient():
    if request.method == 'POST':
        name = request.form.get('name')
        age = request.form.get('age')
        gender = request.form.get('gender')

        patient = Patient(
            doctor_id=current_user.id,
            name=name,
            age=age,
            gender=gender
        )

        db.session.add(patient)
        db.session.commit()

        flash('Patient added successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('new_patient.html')

@app.route('/patient/<int:patient_id>/delete', methods=['POST'])
@doctor_required
def delete_patient(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    # Delete all associated tests first
    SofaTest.query.filter_by(patient_id=patient.id).delete()

    # Delete patient
    db.session.delete(patient)
    db.session.commit()

    flash(f'Patient {patient.name} deleted successfully.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/patient/<int:patient_id>')
@doctor_required
def patient_detail(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    sofa_tests = (
        SofaTest.query
        .filter_by(patient_id=patient.id)
        .order_by(SofaTest.timestamp.asc())
        .all()
    )

    # =========================
    # GRAPH DATA
    # =========================
    graph_points = [
        {
            "x": t.timestamp.isoformat(),
            "y": t.total_score
        }
        for t in sofa_tests
        if t.total_score is not None
    ]

    # =========================
    # TREND CALCULATION
    # =========================
    scores = [t.total_score for t in sofa_tests if t.total_score is not None]

    if len(scores) < 2:
        trend = "Insufficient data"
    else:
        if scores[-1] > scores[0]:
            trend = "Worsening"
        elif scores[-1] < scores[0]:
            trend = "Improving"
        else:
            trend = "Stable"

    return render_template(
    "patient_detail.html",
    patient=patient,
    sofa_tests=sofa_tests,
    graph_points=graph_points,
    trend=trend,
    benchmarks=BENCHMARKS   # ✅ FIX
)

@app.route('/patient/<int:patient_id>/test/<int:test_id>/delete', methods=['POST'])
@doctor_required
def delete_sofa_test(patient_id, test_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    test = SofaTest.query.filter_by(
        id=test_id,
        patient_id=patient.id
    ).first_or_404()

    db.session.delete(test)
    db.session.commit()

    flash('SOFA test deleted successfully.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient.id))

@app.route('/patient/<int:patient_id>/test/qsofa', methods=['GET', 'POST'])
@doctor_required
def qsofa_test(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    if request.method == 'POST':
        try:
            respiratory_rate = float(request.form.get('respiratory_rate'))
            systolic_bp = float(request.form.get('systolic_bp'))
            gcs = int(request.form.get('gcs'))
            suspected_infection = request.form.get('suspected_infection') == 'yes'

            result = sepsis_scorer.calculate_qsofa(
                respiratory_rate=respiratory_rate,
                systolic_bp=systolic_bp,
                gcs=gcs,
                suspected_infection=suspected_infection
            )

            test = SofaTest(
                patient_id=patient.id,
                test_type='qsofa',
                total_score=result['qsofa_score'],
                high_risk=result['high_risk_for_poor_outcomes'],
                interpretation=result['interpretation'],
                respiratory_rate=respiratory_rate,
                systolic_bp=systolic_bp,
                gcs=gcs,
                suspected_infection=suspected_infection,
                respiratory_rate_22_or_higher=result['criteria_met']['respiratory_rate_22_or_higher'],
                altered_mentation=result['criteria_met']['altered_mentation'],
                systolic_bp_100_or_lower=result['criteria_met']['systolic_bp_100_or_lower']
            )

            db.session.add(test)
            db.session.commit()

            flash('qSOFA test added successfully.', 'success')
            return redirect(url_for('patient_detail', patient_id=patient.id))

        except Exception as e:
            flash(f'Error calculating qSOFA: {str(e)}', 'error')

    return render_template('qsofa_test_form.html', patient=patient)

@app.route('/patient/<int:patient_id>/test/news2', methods=['GET', 'POST'])
@doctor_required
def news2_test(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    if request.method == 'POST':
        try:
            respiratory_rate = int(request.form['respiratory_rate'])
            spo2_scale_1 = int(request.form['SpO2_Scale_1'])
            spo2_scale_2 = int(request.form['SpO2_Scale_2'])
            supplemental_oxygen = request.form.get('supplemental_oxygen') == 'on'
            systolic_bp = int(request.form['systolic_bp'])
            heart_rate = int(request.form['heart_rate'])
            level_of_consciousness = request.form['level_of_consciousness']
            temperature = float(request.form['temperature'])
            age = float(request.form['Age'])

            result = sepsis_scorer.calculate_news2(
                respiratory_rate,
                spo2_scale_1,
                spo2_scale_2,
                supplemental_oxygen,
                systolic_bp,
                heart_rate,
                level_of_consciousness,
                temperature,
                age
            )

            test = SofaTest(
                patient_id=patient.id,
                test_type='news2',
                total_score=result['total_score'],
                high_risk=(result.get('risk_band') == 'High'),
                interpretation=result.get('interpretation'),
                respiratory_rate=respiratory_rate,
                systolic_bp=systolic_bp
            )

            db.session.add(test)
            db.session.commit()

            flash('NEWS2 test added successfully.', 'success')
            return redirect(url_for('patient_detail', patient_id=patient.id))

        except Exception as e:
            flash(f'Error calculating NEWS2: {str(e)}', 'error')

    return render_template('news2_test_form.html', patient=patient)


@app.route('/api/patient/<int:patient_id>/sofa-tests')
@doctor_required
def patient_sofa_tests_api(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    tests = (
        SofaTest.query
        .filter_by(patient_id=patient.id)
        .order_by(SofaTest.timestamp.asc())
        .all()
    )

    IST = ZoneInfo("Asia/Kolkata")

    timestamps = []
    for t in tests:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        timestamps.append(ts.astimezone(IST).isoformat())

    return jsonify({
        "timestamps": timestamps,
        "scores": [t.total_score for t in tests],
        "types": [t.test_type for t in tests]
    })


# ==============================
# CALCULATOR ROUTES (REQUIRED)
# ==============================

@app.route('/calculator/qsofa_calculator', methods=['GET', 'POST'])
@doctor_required
def qsofa_calculator():
    result = None
    if request.method == 'POST':
        try:
            respiratory_rate = float(request.form.get('respiratory_rate', 20))
            systolic_bp = float(request.form.get('systolic_bp', 120))
            gcs = int(request.form.get('gcs', 15))
            suspected_infection = request.form.get('suspected_infection') == 'yes'

            result = sepsis_scorer.calculate_qsofa(
                respiratory_rate=respiratory_rate,
                systolic_bp=systolic_bp,
                gcs=gcs,
                suspected_infection=suspected_infection
            )
        except Exception as e:
            flash(f'Error calculating qSOFA: {str(e)}', 'error')

    return render_template('qsofa_calculator.html', result=result)


@app.route('/calculator/sofa_calculator', methods=['GET', 'POST'])
@doctor_required
def sofa_calculator():
    return render_template('sofa_calculator.html')


@app.route('/calculator/septic_shock_calculator', methods=['GET', 'POST'])
@doctor_required
def septic_shock_calculator():
    return render_template('septic_shock_calculator.html')


@app.route('/calculator/news2_calculator', methods=['GET', 'POST'])
@doctor_required
def news2_calculator():
    return render_template('news2_calculator.html')

@app.route('/patient/<int:patient_id>/test/sofa', methods=['GET', 'POST'])
@doctor_required
def sofa_test(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    if request.method == 'POST':
        try:
            pao2_fio2 = float(request.form.get('pao2_fio2', 300))
            platelets = float(request.form.get('platelets', 150))
            bilirubin = float(request.form.get('bilirubin', 1.0))
            map_mmhg = float(request.form.get('map_mmhg', 70))
            gcs = int(request.form.get('gcs', 15))
            creatinine = float(request.form.get('creatinine', 1.0))
            respiratory_support = request.form.get('respiratory_support') == 'yes'
            baseline_sofa = int(request.form.get('baseline_sofa', 0))

            dopamine_dose = float(request.form.get('dopamine_dose', 0))
            dobutamine_dose = float(request.form.get('dobutamine_dose', 0))
            epinephrine_dose = float(request.form.get('epinephrine_dose', 0))
            norepinephrine_dose = float(request.form.get('norepinephrine_dose', 0))
            urine_output = float(request.form.get('urine_output', 0)) if request.form.get('urine_output') else None

            result = sepsis_scorer.calculate_total_sofa(
                pao2_fio2=pao2_fio2,
                platelets=platelets,
                bilirubin=bilirubin,
                map_mmhg=map_mmhg,
                gcs=gcs,
                creatinine=creatinine,
                respiratory_support=respiratory_support,
                dopamine_dose=dopamine_dose,
                dobutamine_dose=dobutamine_dose,
                epinephrine_dose=epinephrine_dose,
                norepinephrine_dose=norepinephrine_dose,
                urine_output_ml_day=urine_output,
                baseline_sofa=baseline_sofa
            )

            test = SofaTest(
                patient_id=patient.id,
                test_type='sofa',
                total_score=result['total_sofa'],
                sepsis_criteria_met=result['sepsis_criteria_met'],
                mortality_risk=f"{result['estimated_mortality_risk_percent']}%",
                interpretation=result['interpretation'],
                respiration_score=result['individual_scores']['respiration'],
                coagulation_score=result['individual_scores']['coagulation'],
                liver_score=result['individual_scores']['liver'],
                cardiovascular_score=result['individual_scores']['cardiovascular'],
                cns_score=result['individual_scores']['cns'],
                renal_score=result['individual_scores']['renal'],
                delta_sofa=result['delta_sofa'],
                baseline_sofa=baseline_sofa,
                pao2_fio2=pao2_fio2,
                respiratory_support=respiratory_support,
                map_mmhg=map_mmhg,
                gcs=gcs,
                platelets=platelets,
                bilirubin=bilirubin,
                creatinine=creatinine,
                dopamine_dose=dopamine_dose,
                dobutamine_dose=dobutamine_dose,
                epinephrine_dose=epinephrine_dose,
                norepinephrine_dose=norepinephrine_dose,
                urine_output=urine_output
            )

            db.session.add(test)
            db.session.commit()

            flash('SOFA test added successfully.', 'success')
            return redirect(url_for('patient_detail', patient_id=patient.id))

        except Exception as e:
            flash(f'Error calculating SOFA: {str(e)}', 'error')

    return render_template('sofa_test_form.html', patient=patient)

@app.route('/patient/<int:patient_id>/test/septic-shock', methods=['GET', 'POST'])
@doctor_required
def septic_shock_test(patient_id):
    patient = Patient.query.filter_by(
        id=patient_id,
        doctor_id=current_user.id
    ).first_or_404()

    if request.method == 'POST':
        try:
            map_mmhg = float(request.form.get('map_mmhg'))
            lactate = float(request.form.get('lactate'))
            vasopressors = request.form.get('vasopressors') == 'yes'

            result = sepsis_scorer.calculate_septic_shock(
                map_mmhg=map_mmhg,
                lactate=lactate,
                vasopressors=vasopressors
            )

            test = SofaTest(
                patient_id=patient.id,
                test_type="septic_shock",          # ✅ REQUIRED
                total_score=0,
                high_risk=result['septic_shock_present'],
                septic_shock_present=result['septic_shock_present'],
                interpretation=result['interpretation']
)

            db.session.add(test)
            db.session.commit()


            flash('Septic shock assessment saved.', 'success')
            return redirect(url_for('patient_detail', patient_id=patient.id))

        except Exception as e:
            flash(f'Error calculating septic shock: {str(e)}', 'error')

    return render_template('septic_shock_test_form.html', patient=patient)


# =====================================================
# DB INIT
# =====================================================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
