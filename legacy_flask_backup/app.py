from flask import Flask, jsonify, render_template, redirect, url_for, request
from config import Config
from database.models import db, User, Exam, Question, Submission
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from database.models import db, User, Exam, Question, Submission, Violation
from datetime import datetime, timedelta
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.platypus import Table
from flask import send_file
import io


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

# ------------------ HOME ------------------

@app.route("/")
def home():
    return redirect(url_for("login"))

# ------------------ REGISTER ------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        role = request.form["role"]

        existing_user = User.query.filter_by(username=username).first()

        if existing_user:
            return "Username already exists! Try different username."

        user = User(username=username, password=password, role=role)
        db.session.add(user)
        db.session.commit()

        return redirect(url_for("login"))

    return render_template("register.html")

# ------------------ LOGIN ------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)

            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            else:
                return redirect(url_for("student_dashboard"))

        return "Invalid credentials"

    return render_template("login.html")

# ------------------ ADMIN DASHBOARD ------------------

@app.route("/admin")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        return "Unauthorized"
    return render_template("admin_dashboard.html")
@app.route("/violation_stats")
@login_required
def violation_stats():

    from sqlalchemy import func

    stats = db.session.query(
        Violation.violation_type,
        func.count(Violation.id)
    ).group_by(Violation.violation_type).all()

    result = {v_type: count for v_type, count in stats}

    from flask import jsonify
    return jsonify(result)

# ------------------ CREATE EXAM ------------------

@app.route("/create_exam", methods=["GET", "POST"])
@login_required
def create_exam():
    if current_user.role != "admin":
        return "Unauthorized"

    if request.method == "POST":
        title = request.form["title"]
        duration = request.form["duration"]

        exam = Exam(title=title, duration=duration, created_by=current_user.id)
        db.session.add(exam)
        db.session.commit()

        return redirect(url_for("add_question", exam_id=exam.id))

    return render_template("create_exam.html")

# ------------------ ADD QUESTION ------------------

@app.route("/add_question/<int:exam_id>", methods=["GET", "POST"])
@login_required
def add_question(exam_id):
    if current_user.role != "admin":
        return "Unauthorized"

    if request.method == "POST":
        question_text = request.form["question"]
        option_a = request.form["option_a"]
        option_b = request.form["option_b"]
        option_c = request.form["option_c"]
        option_d = request.form["option_d"]
        correct_answer = request.form["correct_answer"]

        question = Question(
            exam_id=exam_id,
            question=question_text,
            option_a=option_a,
            option_b=option_b,
            option_c=option_c,
            option_d=option_d,
            correct_answer=correct_answer
        )

        db.session.add(question)
        db.session.commit()

        return redirect(url_for("add_question", exam_id=exam_id))

    return render_template("add_question.html", exam_id=exam_id)

# ------------------ STUDENT DASHBOARD ------------------

@app.route("/student")
@login_required
def student_dashboard():
    if current_user.role != "student":
        return "Unauthorized"

    exams = Exam.query.all()
    return render_template("student_dashboard.html", exams=exams)

# ------------------ START EXAM ------------------

@app.route("/start_exam/<int:exam_id>", methods=["GET", "POST"])
@login_required
def start_exam(exam_id):

    if current_user.role != "student":
        return "Unauthorized"

    exam = Exam.query.get_or_404(exam_id)
    questions = Question.query.filter_by(exam_id=exam_id).all()

    # 🔥 CREATE submission immediately if not exists
    submission = Submission.query.filter_by(
        student_id=current_user.id,
        exam_id=exam_id,
        completed=False
    ).first()

    if not submission:
        submission = Submission(
            student_id=current_user.id,
            exam_id=exam_id,
            started_at=datetime.now(),
            completed=False
        )
        db.session.add(submission)
        db.session.commit()

    if request.method == "POST":

        score = 0
        for question in questions:
            selected = request.form.get(str(question.id))
            if selected == question.correct_answer:
                score += 1

        submission.score = score
        submission.completed = True
        db.session.commit()

        return render_template("result.html",
                               score=score,
                               total=len(questions))

    return render_template("exam_page.html",
                           exam=exam,
                           questions=questions)

# ------------------ LOG VIOLATION ------------------


@app.route("/log_violation", methods=["POST"])
@login_required
def log_violation():

    data = request.json
    violation_type = data.get("violation")
    exam_id = data.get("exam_id")

    now = datetime.now()

    violation = Violation(
        student_id=current_user.id,
        exam_id=exam_id,
        violation_type=violation_type,
        timestamp=now
    )

    db.session.add(violation)
    db.session.commit()

    # SMART INSIGHT: check last 30 seconds
    thirty_seconds_ago = now - timedelta(seconds=30)

    recent_violations = Violation.query.filter(
        Violation.student_id == current_user.id,
        Violation.exam_id == exam_id,
        Violation.timestamp >= thirty_seconds_ago
    ).count()

    if recent_violations >= 2:
        return {"status": "high_risk"}

    return {"status": "logged"}
@app.route("/check_lock/<int:exam_id>")
@login_required
def check_lock(exam_id):

    submission = Submission.query.filter_by(
        student_id=current_user.id,
        exam_id=exam_id
    ).first()

    if submission and submission.locked:
        return jsonify({"locked": True})

    return jsonify({"locked": False})
# ------------------ MONITOR VIOLATIONS ------------------

@app.route("/monitor")
@login_required
def monitor():
    if current_user.role != "admin":
        return "Unauthorized"

    violations = Violation.query.all()
    return render_template("monitor.html", violations=violations)

@app.route("/force_disqualify/<int:student_id>")
@login_required
def force_disqualify(student_id):

    if current_user.role != "admin":
        return "Unauthorized"

    submission = Submission.query.filter_by(student_id=student_id).first()

    if submission:
        submission.score = 0
        db.session.commit()

    return redirect(url_for("monitor"))

@app.route("/live_monitor")
@login_required
def live_monitor():

    from sqlalchemy import func

    data = db.session.query(
        Submission.student_id,
        User.username,
        Submission.exam_id,
        Submission.completed,
        func.count(Violation.id)
    ).join(User, User.id == Submission.student_id)\
     .outerjoin(Violation,
        (Violation.student_id == Submission.student_id) &
        (Violation.exam_id == Submission.exam_id)
     )\
     .group_by(
        Submission.student_id,
        User.username,
        Submission.exam_id,
        Submission.completed
     ).all()

    result = []

    for student_id, username, exam_id, completed, count in data:

        count = count or 0

        if completed:
            status = "Completed"
        elif count >= 3:
            status = "Disqualified"
        elif count >= 2:
            status = "High Risk"
        else:
            status = "Active"

        result.append({
            "student_id": student_id,
            "student": username,
            "exam": exam_id,
            "violations": count,
            "status": status
        })

    return jsonify(result)



@app.route("/lock_student/<int:student_id>")
@login_required
def lock_student(student_id):

    if current_user.role != "admin":
        return jsonify({"error": "Unauthorized"})

    submission = Submission.query.filter_by(student_id=student_id).first()

    if submission:
        submission.locked = True
        db.session.commit()

    return jsonify({"status": "locked"})

@app.route("/export_report")
@login_required
def export_report():

    if current_user.role != "admin":
        return "Unauthorized"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    elements = []

    styles = getSampleStyleSheet()

    elements.append(Paragraph("Exam Report", styles['Heading1']))
    elements.append(Spacer(1, 12))

    data = [["Student ID", "Exam ID", "Violations"]]

    violations = Violation.query.all()

    for v in violations:
        data.append([v.student_id, v.exam_id, v.violation_type])

    table = Table(data)
    elements.append(table)

    doc.build(elements)

    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="exam_report.pdf", mimetype='application/pdf')

# ------------------ LOGOUT ------------------

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)