import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import logging
from functools import wraps
from clinical_scoring import SepsisScoring #bhai built
from datetime import datetime, timedelta, timezone

# Initialize Flask app
app = Flask(__name__)

# Load configuration
from config import config
config_name = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[config_name])


# Initialize extensions
db = SQLAlchemy(app) # Initialize database

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # redirects unauthorized users here

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize clinical scoring system
sepsis_scorer = SepsisScoring()
logger.info("Clinical scoring system initialized successfully")

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patients = db.relationship('Patient', backref='doctor', lazy=True)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sofa_tests = db.relationship('SofaTest', backref='patient', lazy=True, order_by='SofaTest.timestamp.desc()')

class SofaTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    test_type = db.Column(db.String(20), nullable=False) # 'qsofa', 'sofa', 'septic_shock', 'news2'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Test Results
    total_score = db.Column(db.Integer)
    sepsis_criteria_met = db.Column(db.Boolean)
    high_risk = db.Column(db.Boolean)
    septic_shock_present = db.Column(db.Boolean)
    mortality_risk = db.Column(db.String(50))
    interpretation = db.Column(db.Text)
    
    # Individual SOFA Scores (for SOFA tests)
    respiration_score = db.Column(db.Integer)
    coagulation_score = db.Column(db.Integer)
    liver_score = db.Column(db.Integer)
    cardiovascular_score = db.Column(db.Integer)
    cns_score = db.Column(db.Integer)
    renal_score = db.Column(db.Integer)
    delta_sofa = db.Column(db.Integer)
    baseline_sofa = db.Column(db.Integer)
    
    # qSOFA Criteria (for qSOFA tests)
    respiratory_rate_22_or_higher = db.Column(db.Boolean)
    altered_mentation = db.Column(db.Boolean)
    systolic_bp_100_or_lower = db.Column(db.Boolean)
    
    # Septic Shock Criteria (for septic shock tests)
    sepsis_present = db.Column(db.Boolean)
    persistent_hypotension_on_vasopressors = db.Column(db.Boolean)
    lactate_greater_than_2 = db.Column(db.Boolean)
    adequate_volume_resuscitation = db.Column(db.Boolean)
    
    # Input Parameters
    # Respiratory
    pao2_fio2 = db.Column(db.Float)
    respiratory_support = db.Column(db.Boolean)
    respiratory_rate = db.Column(db.Float)
    
    # Cardiovascular
    map_mmhg = db.Column(db.Float)
    systolic_bp = db.Column(db.Float)
    dopamine_dose = db.Column(db.Float)
    dobutamine_dose = db.Column(db.Float)
    epinephrine_dose = db.Column(db.Float)
    norepinephrine_dose = db.Column(db.Float)
    
    # Laboratory
    platelets = db.Column(db.Float)
    bilirubin = db.Column(db.Float)
    creatinine = db.Column(db.Float)
    lactate = db.Column(db.Float)
    urine_output = db.Column(db.Float)
    
    # Neurological
    gcs = db.Column(db.Integer)
    
    # Clinical Context
    suspected_infection = db.Column(db.Boolean)
    on_vasopressors = db.Column(db.Boolean)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def doctor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        name = request.form.get('name')
        password = request.form.get('password')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('register.html')
        
        user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password)
        )
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! You can now log in.', 'success')
        
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
@doctor_required
def dashboard():
    patients = Patient.query.filter_by(doctor_id=current_user.id).all()
    
    # Calculate total SOFA tests
    total_tests = 0
    sepsis_alerts = 0
    recent_tests = 0
    
    # Prepare patient data with latest NEWS2 risk
    patient_data = []
    for patient in patients:
        patient_tests = patient.sofa_tests
        total_tests += len(patient_tests)
        
        # Count sepsis alerts (SOFA tests with sepsis criteria met)
        for test in patient_tests:
            if test.test_type == 'sofa' and test.sepsis_criteria_met:
                sepsis_alerts += 1
            if test.test_type == 'qsofa' and test.high_risk:
                sepsis_alerts += 1
            if test.test_type == 'septic_shock' and test.septic_shock_present:
                sepsis_alerts += 1
            # Add NEWS2 alerts (if high risk)
            if test.test_type == 'news2' and test.high_risk:
                sepsis_alerts += 1
        
        # Count recent tests (last 24 hours) - Fixed for timezone compatibility
        recent_tests += len([t for t in patient_tests if (datetime.now(timezone.utc) - t.timestamp.replace(tzinfo=timezone.utc)).total_seconds() < 86400])
        
        # Get the latest NEWS2 test for this patient
        latest_news2 = SofaTest.query.filter_by(patient_id=patient.id, test_type='news2').order_by(SofaTest.timestamp.desc()).first()
        news2_risk = None
        if latest_news2:  # Ensure latest_news2 is not None
            # Safely determine risk band (handles None interpretation)
            interpretation = latest_news2.interpretation or ""  # Default to empty string if None
            if latest_news2.high_risk:
                if "High" in interpretation:
                    news2_risk = "High"
                else:
                    news2_risk = "Medium"  # high_risk True but not explicitly High means Medium
            else:
                news2_risk = "Low"
        
        patient_data.append({
            'patient': patient,
            'news2_risk': news2_risk
        })
    
    return render_template('dashboard.html', patient_data=patient_data, 
                           total_tests=total_tests, sepsis_alerts=sepsis_alerts, recent_tests=recent_tests)


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

@app.route('/patient/<int:patient_id>')
@doctor_required
def patient_detail(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    sofa_tests = patient.sofa_tests
    return render_template('patient_detail.html', patient=patient, sofa_tests=sofa_tests)

@app.route('/patient/<int:patient_id>/test/new', methods=['GET', 'POST'])
@doctor_required
def new_sofa_test(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        test_type = request.form.get('test_type')
        
        if test_type == 'qsofa':
            return redirect(url_for('qsofa_test', patient_id=patient_id))
        elif test_type == 'sofa':
            return redirect(url_for('sofa_test', patient_id=patient_id))
        elif test_type == 'septic_shock':
            return redirect(url_for('septic_shock_test', patient_id=patient_id))
        # Redirect to the new patient-specific NEWS2 test route
        elif test_type == 'news2':
            return redirect(url_for('news2_test', patient_id=patient_id))
    
    return render_template('new_sofa_test.html', patient=patient)

@app.route('/patient/<int:patient_id>/test/qsofa', methods=['GET', 'POST'])
@doctor_required
def qsofa_test(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        try:
            # Get form data
            respiratory_rate = float(request.form.get('respiratory_rate', 20))
            systolic_bp = float(request.form.get('systolic_bp', 120))
            gcs = int(request.form.get('gcs', 15))
            suspected_infection = request.form.get('suspected_infection') == 'yes'
            
            # Calculate qSOFA
            result = sepsis_scorer.calculate_qsofa(
                respiratory_rate=respiratory_rate,
                systolic_bp=systolic_bp,
                gcs=gcs,
                suspected_infection=suspected_infection
            )
            
            # Store in database
            sofa_test = SofaTest(
                patient_id=patient_id,
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
            
            db.session.add(sofa_test)
            db.session.commit()
            
            flash(f'qSOFA test completed! Score: {result["qsofa_score"]}, Risk: {"High" if result["high_risk_for_poor_outcomes"] else "Low"}', 'success')
            return redirect(url_for('patient_detail', patient_id=patient_id))
            
        except Exception as e:
            flash(f'Error calculating qSOFA: {str(e)}', 'error')
    
    return render_template('qsofa_test_form.html', patient=patient)

@app.route('/patient/<int:patient_id>/test/sofa', methods=['GET', 'POST'])
@doctor_required
def sofa_test(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        try:
            # Get form data
            pao2_fio2 = float(request.form.get('pao2_fio2', 300))
            platelets = float(request.form.get('platelets', 150))
            bilirubin = float(request.form.get('bilirubin', 1.0))
            map_mmhg = float(request.form.get('map_mmhg', 70))
            gcs = int(request.form.get('gcs', 15))
            creatinine = float(request.form.get('creatinine', 1.0))
            respiratory_support = request.form.get('respiratory_support') == 'yes'
            baseline_sofa = int(request.form.get('baseline_sofa', 0))
            
            # Optional vasopressor doses
            dopamine_dose = float(request.form.get('dopamine_dose', 0))
            dobutamine_dose = float(request.form.get('dobutamine_dose', 0))
            epinephrine_dose = float(request.form.get('epinephrine_dose', 0))
            norepinephrine_dose = float(request.form.get('norepinephrine_dose', 0))
            urine_output = float(request.form.get('urine_output', 0)) if request.form.get('urine_output') else None
            
            # Calculate SOFA
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
            
            # Store in database
            sofa_test = SofaTest(
            patient_id=patient_id,
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
            
            db.session.add(sofa_test)
            db.session.commit()
            
            flash(f'SOFA test completed! Total Score: {result["total_sofa"]}, Sepsis: {"Yes" if result["sepsis_criteria_met"] else "No"}', 'success')
            return redirect(url_for('patient_detail', patient_id=patient_id))
            
        except Exception as e:
            flash(f'Error calculating SOFA: {str(e)}', 'error')
    
    return render_template('sofa_test_form.html', patient=patient)

@app.route('/patient/<int:patient_id>/test/septic-shock', methods=['GET', 'POST'])
@doctor_required
def septic_shock_test(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()

    if request.method == 'POST':
        try:
            # Get form data
            map_mmhg = float(request.form.get('map_mmhg', 70))
            lactate = float(request.form.get('lactate', 1.0))
            on_vasopressors = request.form.get('on_vasopressors') == 'yes'
            adequate_volume_resus = request.form.get('adequate_volume_resus') == 'yes'
            sepsis_present = request.form.get('sepsis_present') == 'yes'
            
            # Calculate septic shock assessment
            result = sepsis_scorer.assess_septic_shock(
                map_mmhg=map_mmhg,
                lactate_mmol_l=lactate,
                on_vasopressors=on_vasopressors,
                adequate_volume_resus=adequate_volume_resus,
                sepsis_present=sepsis_present
            )
            
            # Store in database
            sofa_test = SofaTest(
            patient_id=patient_id,
                test_type='septic_shock',
                septic_shock_present=result['septic_shock_present'],
                mortality_risk=result['estimated_mortality_risk'],
                interpretation=result['interpretation'],
                map_mmhg=map_mmhg,
                lactate=lactate,
                on_vasopressors=on_vasopressors,
                sepsis_present=sepsis_present,
                persistent_hypotension_on_vasopressors=result['criteria']['persistent_hypotension_on_vasopressors'],
                lactate_greater_than_2=result['criteria']['lactate_greater_than_2'],
                adequate_volume_resuscitation=result['criteria']['adequate_volume_resuscitation']
            )
            
            db.session.add(sofa_test)
            db.session.commit()
            
            flash(f'Septic Shock Assessment completed! Result: {"Present" if result["septic_shock_present"] else "Not Present"}', 'success')
            return redirect(url_for('patient_detail', patient_id=patient_id))

        except Exception as e:
            flash(f'Error assessing septic shock: {str(e)}', 'error')

    return render_template('septic_shock_test_form.html', patient=patient)


# Patient-specific NEWS2 Test
@app.route('/patient/<int:patient_id>/test/news2', methods=['GET', 'POST'])
@doctor_required
def news2_test(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        try:
            # 1. Get form data for NEWS2. Must match the form inputs in news2_test_form.html
            respiratory_rate = int(request.form['respiratory_rate'])
            oxygen_saturation = int(request.form['oxygen_saturation'])
            supplemental_oxygen = request.form.get('supplemental_oxygen') == 'on'
            systolic_bp = int(request.form['systolic_bp'])
            heart_rate = int(request.form['heart_rate'])
            level_of_consciousness = request.form['level_of_consciousness']
            temperature = float(request.form['temperature'])

            # 2. Calculate NEWS2 score
            result = sepsis_scorer.calculate_news2(
                respiratory_rate, oxygen_saturation, supplemental_oxygen,
                systolic_bp, heart_rate, level_of_consciousness, temperature
            )
            
            # 3. Store result in database
            sofa_test = SofaTest(
                patient_id=patient_id,
                test_type='news2',
                total_score=result['total_score'],
                # Determine high_risk based on NEWS2 bands (>=5 or any score of 3)
                high_risk=(result.get('risk_band') == 'Medium' or result.get('risk_band') == 'High'),
                interpretation=result.get('recommended_response'),
                
                # Store input parameters in existing, relevant fields
                respiratory_rate=float(respiratory_rate),
                systolic_bp=float(systolic_bp),
                # Note: heart_rate, temperature, and SpO2 inputs are not saved as 
                # dedicated columns in your current SofaTest model.
            )
            
            db.session.add(sofa_test)
            db.session.commit()
            
            # 4. Render the result page with all data (instead of flashing and redirecting)
            return render_template('news2_result.html', 
                                   result=result, 
                                   patient=patient,
                                   respiratory_rate=respiratory_rate,
                                   oxygen_saturation=oxygen_saturation,
                                   supplemental_oxygen=supplemental_oxygen,
                                   systolic_bp=systolic_bp,
                                   heart_rate=heart_rate,
                                   level_of_consciousness=level_of_consciousness,
                                   temperature=temperature)
            
        except ValueError:
            flash('Error: Please ensure all inputs are valid numbers and selections.', 'error')
        except Exception as e:
            flash(f'Error calculating NEWS2: {str(e)}', 'error')
            
    # GET request: render the form
    return render_template('news2_test_form.html', patient=patient)

@app.route('/patient/<int:patient_id>/delete', methods=['POST'])
@doctor_required
def delete_patient(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    
    # Delete all SOFA tests for this patient first
    SofaTest.query.filter_by(patient_id=patient_id).delete()
    
    # Delete the patient
    db.session.delete(patient)
    db.session.commit()
    
    flash(f'Patient {patient.name} and all associated SOFA tests have been deleted successfully.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/patient/<int:patient_id>/test/<int:test_id>/delete', methods=['POST'])
@doctor_required
def delete_sofa_test(patient_id, test_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    sofa_test = SofaTest.query.filter_by(id=test_id, patient_id=patient.id).first_or_404()

    db.session.delete(sofa_test)
    db.session.commit()
    flash('SOFA test deleted successfully.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient.id))

@app.route('/api/patient/<int:patient_id>/sofa-tests')
@doctor_required
def patient_sofa_tests_api(patient_id):
    patient = Patient.query.filter_by(id=patient_id, doctor_id=current_user.id).first_or_404()
    sofa_tests = patient.sofa_tests
    
    data = {
        'timestamps': [t.timestamp.strftime('%Y-%m-%d %H:%M') for t in sofa_tests],
        'test_types': [t.test_type for t in sofa_tests],
        'total_scores': [t.total_score for t in sofa_tests],
        'sepsis_criteria_met': [t.sepsis_criteria_met for t in sofa_tests],
        'high_risk': [t.high_risk for t in sofa_tests],
        'septic_shock_present': [t.septic_shock_present for t in sofa_tests]
    }
    
    return jsonify(data)

# Clinical Scoring Calculator Routes
@app.route('/calculator/qsofa_calculator', methods=['GET', 'POST'])
@doctor_required
def qsofa_calculator():
    """qSOFA Calculator - Quick bedside assessment"""
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
            
            return render_template('qsofa_result.html', result=result, 
                                   respiratory_rate=respiratory_rate, systolic_bp=systolic_bp, 
                                   gcs=gcs, suspected_infection=suspected_infection)
        except Exception as e:
            flash(f'Error calculating qSOFA: {str(e)}', 'error')
    
    return render_template('qsofa_calculator.html')

@app.route('/calculator/sofa_calculator', methods=['GET', 'POST'])
@doctor_required
def sofa_calculator():
    """SOFA Calculator - Comprehensive organ dysfunction assessment"""
    if request.method == 'POST':
        try:
            # Get all SOFA parameters
            pao2_fio2 = float(request.form.get('pao2_fio2', 300))
            platelets = float(request.form.get('platelets', 150))
            bilirubin = float(request.form.get('bilirubin', 1.0))
            map_mmhg = float(request.form.get('map_mmhg', 70))
            gcs = int(request.form.get('gcs', 15))
            creatinine = float(request.form.get('creatinine', 1.0))
            respiratory_support = request.form.get('respiratory_support') == 'yes'
            baseline_sofa = int(request.form.get('baseline_sofa', 0))
            
            # Optional vasopressor doses
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
            
            return render_template('sofa_result.html', result=result,
                                   pao2_fio2=pao2_fio2, platelets=platelets, bilirubin=bilirubin,
                                   map_mmhg=map_mmhg, gcs=gcs, creatinine=creatinine,
                                   respiratory_support=respiratory_support, baseline_sofa=baseline_sofa)
        except Exception as e:
            flash(f'Error calculating SOFA: {str(e)}', 'error')
    
    return render_template('sofa_calculator.html')

@app.route('/calculator/septic_shock_calculator', methods=['GET', 'POST'])
@doctor_required
def septic_shock_calculator():
    """Septic Shock Assessment Calculator"""
    if request.method == 'POST':
        try:
            map_mmhg = float(request.form.get('map_mmhg', 70))
            lactate = float(request.form.get('lactate', 1.0))
            on_vasopressors = request.form.get('on_vasopressors') == 'yes'
            adequate_volume_resus = request.form.get('adequate_volume_resus') == 'yes'
            sepsis_present = request.form.get('sepsis_present') == 'yes'
            
            result = sepsis_scorer.assess_septic_shock(
                map_mmhg=map_mmhg,
                lactate_mmol_l=lactate,
                on_vasopressors=on_vasopressors,
                adequate_volume_resus=adequate_volume_resus,
                sepsis_present=sepsis_present
            )
            
            return render_template('septic_shock_result.html', result=result,
                                   map_mmhg=map_mmhg, lactate=lactate, on_vasopressors=on_vasopressors,
                                   adequate_volume_resus=adequate_volume_resus, sepsis_present=sepsis_present)
        except Exception as e:
            flash(f'Error assessing septic shock: {str(e)}', 'error')
    
    return render_template('septic_shock_calculator.html')


@app.route('/calculator/news2_calculator', methods=['GET', 'POST'])
@doctor_required
def news2_calculator():
    result = None
    if request.method == 'POST':
        try:
            respiratory_rate = int(request.form['respiratory_rate'])
            oxygen_saturation = int(request.form['oxygen_saturation'])
            supplemental_oxygen = request.form.get('supplemental_oxygen') == 'on'
            systolic_bp = int(request.form['systolic_bp'])
            heart_rate = int(request.form['heart_rate'])
            level_of_consciousness = request.form['level_of_consciousness']
            temperature = float(request.form['temperature'])

            result = sepsis_scorer.calculate_news2(
                respiratory_rate, oxygen_saturation, supplemental_oxygen,
                systolic_bp, heart_rate, level_of_consciousness, temperature
            )

        except Exception as e:
            flash(f'Error calculating NEWS2: {str(e)}', 'error')

    return render_template('news2_calculator.html', result=result)

# Clinical scoring system is now integrated directly into the test routes

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
