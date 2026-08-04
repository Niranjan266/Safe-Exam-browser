"""
Seed the database with demo accounts and a sample exam.

Usage:
    python seed.py
    # or
    flask --app app seed
"""
from datetime import datetime

from safeexam.extensions import db
from safeexam.models import (
    User, Exam, Question, SchoolClass, ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT,
)

DEMO_QUESTIONS = [
    ("Which data structure uses FIFO ordering?",
     "Stack", "Queue", "Tree", "Graph", "B"),
    ("What is the time complexity of binary search on a sorted array?",
     "O(n)", "O(n log n)", "O(log n)", "O(1)", "C"),
    ("In Python, which keyword defines a function?",
     "func", "def", "function", "lambda", "B"),
    ("Which HTTP status code means 'Not Found'?",
     "200", "301", "404", "500", "C"),
    ("Which SQL clause filters rows after grouping?",
     "WHERE", "HAVING", "ORDER BY", "GROUP BY", "B"),
]


def run_seed(app=None):
    if app is None:
        from safeexam import create_app

        app = create_app()

    with app.app_context():
        db.create_all()

        created = []

        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", full_name="Lead Proctor",
                         email="admin@safeexam.local", role=ROLE_ADMIN)
            admin.set_password("admin123")
            db.session.add(admin)
            created.append("admin / admin123 (admin)")

        teacher = User.query.filter_by(username="teacher").first()
        if not teacher:
            teacher = User(username="teacher", full_name="Demo Teacher",
                           email="teacher@safeexam.local", role=ROLE_TEACHER)
            teacher.set_password("teacher123")
            db.session.add(teacher)
            created.append("teacher / teacher123 (teacher)")

        student = User.query.filter_by(username="student").first()
        if not student:
            student = User(username="student", full_name="Demo Student",
                           email="student@safeexam.local", role=ROLE_STUDENT)
            student.set_password("student123")
            db.session.add(student)
            created.append("student / student123 (student)")

        # A few extra candidates so user management / analytics look realistic.
        for uname, fname in [("alice", "Alice Turner"), ("ben", "Ben Carter"), ("chloe", "Chloe Diaz")]:
            if not User.query.filter_by(username=uname).first():
                u = User(username=uname, full_name=fname,
                         email=f"{uname}@safeexam.local", role=ROLE_STUDENT)
                u.set_password("password123")
                db.session.add(u)

        db.session.commit()

        demo_class = SchoolClass.query.filter_by(subject_code="FA-301").first()
        if not demo_class:
            demo_class = SchoolClass(
                name="BCom III Year",
                subject_name="Financial Accounting",
                subject_code="FA-301",
                section="A",
                academic_year="2026-27",
                description="Demo class created by the seed script.",
                teacher_id=teacher.id,
            )
            db.session.add(demo_class)
            db.session.commit()
            created.append("Class: BCom III Year — Financial Accounting (FA-301)")

        if not Exam.query.filter_by(title="Sample Aptitude Test").first():
            exam = Exam(
                title="Sample Aptitude Test",
                description="A short demo exam covering basic CS concepts.",
                duration=10,
                created_by=admin.id,
                class_id=demo_class.id,
                is_published=True,
                shuffle_questions=False,
            )
            db.session.add(exam)
            db.session.commit()

            for i, (text, a, b, c, d, correct) in enumerate(DEMO_QUESTIONS, start=1):
                db.session.add(
                    Question(
                        exam_id=exam.id,
                        text=text,
                        option_a=a,
                        option_b=b,
                        option_c=c,
                        option_d=d,
                        correct_answer=correct,
                        order=i,
                    )
                )
            db.session.commit()
            created.append("Sample Aptitude Test (5 questions, published)")

        if created:
            print("Seeded:")
            for line in created:
                print("  -", line)
        else:
            print("Nothing to seed — demo data already present.")


if __name__ == "__main__":
    run_seed()
