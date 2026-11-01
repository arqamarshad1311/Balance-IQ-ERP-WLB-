"""
Load demo people + assessments so the HR dashboard has something to show.

    cd backend
    python seed.py

Safe to re-run (existing usernames are skipped). Demo logins:
    hr.priya   / Welcome@123    (HR)
    <username> / Welcome@123    (employees - see list below)
"""
import os
import random
from datetime import timedelta

import auth
from database import init_db, SessionLocal, User, Assessment, utcnow
from model import model, FEATURES

DEMO_PASSWORD = "Welcome@123"

PEOPLE = [
    # username,        full name,        role,       department,   designation
    ("hr.priya",     "Priya Sharma",    "hr",       "HR",          "HR Manager"),
    ("amit.verma",   "Amit Verma",      "employee", "Engineering", "Software Engineer"),
    ("neha.singh",   "Neha Singh",      "employee", "Engineering", "Senior Engineer"),
    ("rohit.das",    "Rohit Das",       "employee", "Engineering", "QA Engineer"),
    ("sana.khan",    "Sana Khan",       "employee", "Sales",       "Account Executive"),
    ("vikram.rao",   "Vikram Rao",      "employee", "Sales",       "Sales Manager"),
    ("meera.nair",   "Meera Nair",      "employee", "Finance",     "Analyst"),
    ("karan.mehta",  "Karan Mehta",     "employee", "Finance",     "Accountant"),
    ("isha.gupta",   "Isha Gupta",      "employee", "Support",     "Support Lead"),
    ("dev.patel",    "Dev Patel",       "employee", "Support",     "Support Agent"),
    ("anita.joshi",  "Anita Joshi",     "employee", "Operations",  "Operations Exec"),
    ("farhan.ali",   "Farhan Ali",      "employee", "Operations",  "Logistics Coordinator"),
]

# rough "how stretched is this person" level per department (0 = relaxed, 1 = overloaded)
LOAD = {"HR": 0.25, "Engineering": 0.7, "Sales": 0.8, "Finance": 0.45, "Support": 0.65, "Operations": 0.55}


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def make_features(load, rng):
    work = clip(rng.gauss(36 + 24 * load, 5), 20, 80)
    stress = clip(1.5 + 7 * load + rng.gauss(0, 1.1), 1, 10)
    sleep = clip(8.6 - 2.2 * load - 0.05 * (work - 45) + rng.gauss(0, 0.5), 3, 10)
    return {
        "work_hours": round(work), "commute": round(clip(rng.gauss(1.1, 0.5), 0, 4) * 4) / 4,
        "sleep": round(sleep * 2) / 2, "stress": round(stress),
        "family_time": round(clip(3.6 - 2.4 * load + rng.gauss(0, 0.5), 0, 6) * 2) / 2,
        "social_time": round(clip(11 - 8 * load + rng.gauss(0, 2), 0, 20)),
        "satisfaction": round(clip(8.5 - 5 * load + rng.gauss(0, 1), 1, 10)),
        "mental_health": round(clip(8.5 - 4.5 * load + rng.gauss(0, 1), 1, 10)),
    }


def seed_demo(password: str = DEMO_PASSWORD) -> int:
    """Create demo people + assessments (skips usernames that already exist). Returns count created."""
    init_db()
    rng = random.Random(7)
    db = SessionLocal()
    created = 0
    try:
        for username, name, role, dept, desig in PEOPLE:
            if db.query(User).filter(User.username == username).first():
                continue
            u = User(username=username, full_name=name, role=role, department=dept, designation=desig,
                     email=f"{username}@example.com", password_hash=auth.hash_password(password))
            db.add(u)
            db.flush()
            created += 1
            base_load = LOAD[dept] + rng.uniform(-0.2, 0.2)
            for weeks_ago in sorted(rng.sample(range(0, 8), rng.randint(2, 5)), reverse=True):
                feats = make_features(clip(base_load + rng.uniform(-0.1, 0.1), 0, 1), rng)
                out = model.predict(feats)
                when = utcnow() - timedelta(weeks=weeks_ago, hours=rng.randint(0, 40))
                db.add(Assessment(user_id=u.id, created_at=when, **feats, risk_label=out["label"],
                                  risk_score=out["score"], top_factor=out["top_factor"]))
        db.commit()
    finally:
        db.close()
    return created


def main():
    password = os.getenv("WLB_DEMO_PASSWORD", DEMO_PASSWORD)
    if auth.is_production() and os.getenv("WLB_ALLOW_SEED") != "1":
        raise SystemExit("Refusing to create demo accounts in production. Set WLB_ALLOW_SEED=1 (and a strong "
                         "WLB_DEMO_PASSWORD) if you really want them - they are for demos only.")
    created = seed_demo(password)
    print(f"Seeded {created} new people. Demo password for all of them: {password}")


if __name__ == "__main__":
    main()
