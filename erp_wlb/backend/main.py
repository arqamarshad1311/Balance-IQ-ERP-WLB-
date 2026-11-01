"""
BalanceIQ ERP - Work-Life Balance risk prediction behind a login system.

Roles
  employee : take assessments, see own results and history
  hr       : everything an employee can + people directory (employees only),
             organisation overview, all assessments
  admin    : everything HR can + manage HR/admin accounts and roles

Run locally (inside backend/): uvicorn main:app --reload      -> http://localhost:8000/
Run as a website:              see README.md (Render / Docker / VPS) and .env.example
"""
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal, Optional
import os
import re
import time

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
from database import init_db, get_db, SessionLocal, User, Assessment, utcnow
from model import model, FEATURES, FEATURE_LABELS, suggestions

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ROLES = ("admin", "hr", "employee")
RISK_LABELS = ("Low Risk", "Medium Risk", "High Risk")


# ------------------------------ startup -----------------------------------
PRODUCTION = auth.is_production()
# cookies are Secure (HTTPS only) in production or whenever WLB_COOKIE_SECURE=1
COOKIE_SECURE = PRODUCTION or os.getenv("WLB_COOKIE_SECURE", "0") == "1"


def ensure_default_admin():
    """First run: create the 'admin' account (must be changed at first login).

    Password comes from WLB_ADMIN_PASSWORD. If it is not set: local development
    uses Admin@123; production generates a random one and prints it once to the log.
    """
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            pw = os.getenv("WLB_ADMIN_PASSWORD")
            shown = False
            if pw:
                problem = auth.validate_password_strength(pw)
                if problem:
                    raise RuntimeError(f"WLB_ADMIN_PASSWORD rejected: {problem}")
            elif PRODUCTION:
                pw, shown = auth.generate_temp_password(), True
            else:
                pw, shown = "Admin@123", True
            db.add(User(
                username="admin", full_name="System Administrator", role="admin",
                department="Administration", designation="Administrator",
                password_hash=auth.hash_password(pw), must_change_password=True,
            ))
            db.commit()
            if shown:
                print("\n  >> Created admin account: admin / %s (change it after login)\n" % pw)
            else:
                print("\n  >> Created admin account 'admin' using WLB_ADMIN_PASSWORD (change it after login)\n")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_default_admin()
    if os.getenv("WLB_SEED_DEMO") == "1":          # optional: demo people for a showcase site
        from seed import seed_demo
        n = seed_demo(os.getenv("WLB_DEMO_PASSWORD", "Welcome@123"))
        if n:
            print(f"  >> Seeded {n} demo people")
    yield


# the interactive API docs are only exposed outside production
app = FastAPI(
    title="BalanceIQ ERP - Work-Life Risk Analyzer", lifespan=lifespan,
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None, openapi_url=None if PRODUCTION else "/openapi.json",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")          # never cache personal data
    elif PRODUCTION:                                       # /docs (which needs a CDN) is off in production
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                     "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if COOKIE_SECURE:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    """Used by the host's health check; also confirms the database is reachable."""
    db.execute(select(1))
    return {"status": "ok"}


# ------------------------------ helpers -----------------------------------
def iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def user_out(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email,
        "role": u.role, "department": u.department, "designation": u.designation,
        "is_active": u.is_active, "must_change_password": u.must_change_password,
        "created_at": iso(u.created_at), "last_login": iso(u.last_login),
    }


def assessment_out(a: Assessment) -> dict:
    d = {f: getattr(a, f) for f in FEATURES}
    d.update(id=a.id, created_at=iso(a.created_at), risk_label=a.risk_label,
             risk_score=a.risk_score, top_factor=a.top_factor)
    return d


def set_session_cookie(response: Response, user: User):
    response.set_cookie(
        auth.COOKIE_NAME, auth.make_token(user.id, user.password_hash),
        max_age=auth.TOKEN_TTL, httponly=True, samesite="lax", path="/", secure=COOKIE_SECURE,
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(auth.COOKIE_NAME)
    payload = auth.read_token(token) if token else None
    user = db.get(User, payload["uid"]) if payload else None
    if user is None or not user.is_active or not auth.token_matches_user(payload, user):
        raise HTTPException(401, "Not authenticated")
    return user


def require_roles(*roles):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission to do that.")
        return user
    return dep


staff_only = require_roles("admin", "hr")


# ------------------------------ schemas -----------------------------------
class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=60)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(..., max_length=200)


class AssessmentIn(BaseModel):
    work_hours: float = Field(..., ge=20, le=80)
    commute: float = Field(..., ge=0, le=4)
    sleep: float = Field(..., ge=3, le=10)
    stress: float = Field(..., ge=1, le=10)
    family_time: float = Field(..., ge=0, le=6)
    social_time: float = Field(..., ge=0, le=20)
    satisfaction: float = Field(..., ge=1, le=10)
    mental_health: float = Field(..., ge=1, le=10)


class EmployeeCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    full_name: str = Field(..., min_length=2, max_length=100)
    email: Optional[str] = Field(None, max_length=120)
    department: str = Field("General", min_length=1, max_length=60)
    designation: str = Field("", max_length=80)
    role: Literal["admin", "hr", "employee"] = "employee"
    password: Optional[str] = Field(None, max_length=200)    # blank => generated


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[str] = Field(None, max_length=120)
    department: Optional[str] = Field(None, min_length=1, max_length=60)
    designation: Optional[str] = Field(None, max_length=80)
    role: Optional[Literal["admin", "hr", "employee"]] = None
    is_active: Optional[bool] = None


class ResetPasswordIn(BaseModel):
    new_password: Optional[str] = Field(None, max_length=200)   # blank => generated


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    full_name: str = Field(..., min_length=2, max_length=100)
    email: Optional[str] = Field(None, max_length=120)
    department: str = Field("General", min_length=1, max_length=60)
    designation: str = Field("", max_length=80)
    password: str = Field(..., min_length=8, max_length=200)
    # NOTE: deliberately NO `role` field. Self-registered accounts are always "employee".


# ------------------------------ auth routes -------------------------------
@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    uname = payload.username.strip().lower()
    key = f"{uname}|{request.client.host if request.client else '?'}"
    auth.check_lockout(key)

    user = db.query(User).filter(func.lower(User.username) == uname).first()
    if not user:
        auth.burn_time()
    if not user or not user.is_active or not auth.verify_password(payload.password, user.password_hash):
        auth.register_failure(key)
        raise HTTPException(401, "Invalid username or password.")

    auth.clear_failures(key)
    user.last_login = utcnow()
    db.commit()
    set_session_cookie(response, user)
    return user_out(user)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@app.post("/api/auth/change-password")
def change_password(payload: ChangePasswordIn, response: Response,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    problem = auth.validate_password_strength(payload.new_password)
    if problem:
        raise HTTPException(400, problem)
    if payload.new_password == payload.current_password:
        raise HTTPException(400, "New password must be different from the current one.")
    user.password_hash = auth.hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    set_session_cookie(response, user)          # keep THIS browser signed in
    return user_out(user)


# ------------------------------ self-registration -------------------------
ALLOW_REGISTRATION = os.getenv("WLB_ALLOW_REGISTRATION", "1") != "0"
REGISTER_LIMIT = int(os.getenv("WLB_REGISTER_LIMIT", "5"))      # signups per IP per hour
_reg_attempts: dict = {}                                        # ip -> [count, window_start]


def _rate_limit_register(request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    for k in [k for k, v in _reg_attempts.items() if now - v[1] > 3600]:   # forget old windows
        del _reg_attempts[k]
    rec = _reg_attempts.get(ip)
    if not rec:
        _reg_attempts[ip] = [1, now]
        return
    rec[0] += 1
    if rec[0] > REGISTER_LIMIT:
        raise HTTPException(429, "Too many signups from this network. Try again later.")


@app.post("/api/register", status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Public self-registration. Always creates a plain employee - never HR or admin."""
    if not ALLOW_REGISTRATION:
        raise HTTPException(403, "Self-registration is turned off. Ask your administrator for an account.")
    _rate_limit_register(request)

    uname = payload.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", uname):
        raise HTTPException(400, "Username may only contain letters, digits, '.', '_' and '-'.")
    if db.query(User).filter(func.lower(User.username) == uname.lower()).first():
        raise HTTPException(409, "That username is already taken.")
    if payload.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(400, "Email address looks invalid.")
    problem = auth.validate_password_strength(payload.password)
    if problem:
        raise HTTPException(400, problem)

    u = User(
        username=uname, full_name=payload.full_name.strip(), email=payload.email or None,
        department=payload.department.strip() or "General", designation=payload.designation.strip(),
        role="employee",                         # hard-coded: the client cannot choose a role
        password_hash=auth.hash_password(payload.password), must_change_password=False,
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:                       # two people picked the same username at once
        db.rollback()
        raise HTTPException(409, "That username is already taken.")
    db.refresh(u)
    return {"ok": True, "username": u.username, "role": u.role}


# ------------------------------ prediction --------------------------------
def describe(features: dict) -> dict:
    """Run the model + rules on a feature dict -> everything the UI shows."""
    result = model.predict(features)
    return {**result, "tips": suggestions(features), "inputs": features}


@app.post("/api/predict")
def predict(payload: AssessmentIn, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    features = payload.model_dump()
    out = describe(features)
    row = Assessment(user_id=user.id, **features, risk_label=out["label"],
                     risk_score=out["score"], top_factor=out["top_factor"])
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "created_at": iso(row.created_at), **out}


@app.get("/api/my/assessments")
def my_assessments(limit: int = 50, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    limit = max(1, min(limit, 500))
    rows = (db.query(Assessment).filter(Assessment.user_id == user.id)
            .order_by(Assessment.id.desc()).limit(limit).all())
    return [assessment_out(r) for r in rows]


@app.get("/api/my/latest")
def my_latest(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = (db.query(Assessment).filter(Assessment.user_id == user.id)
           .order_by(Assessment.id.desc()).first())
    if not row:
        return None
    feats = {f: getattr(row, f) for f in FEATURES}
    return {"id": row.id, "created_at": iso(row.created_at), **describe(feats)}


# ------------------------------ HR / admin --------------------------------
@app.get("/api/hr/overview")
def hr_overview(user: User = Depends(staff_only), db: Session = Depends(get_db)):
    people = db.query(User).filter(User.is_active == True).all()          # noqa: E712
    latest_ids = select(func.max(Assessment.id)).group_by(Assessment.user_id)
    latest = (db.query(Assessment, User).join(User, User.id == Assessment.user_id)
              .filter(Assessment.id.in_(latest_ids), User.is_active == True).all())  # noqa: E712

    dist = Counter()
    by_dept = defaultdict(lambda: {"Low Risk": 0, "Medium Risk": 0, "High Risk": 0, "total": 0})
    drivers = Counter()
    feat_sum = Counter()
    score_sum = 0.0
    at_risk = []
    for a, u in latest:
        dist[a.risk_label] += 1
        d = by_dept[u.department]
        d[a.risk_label] += 1
        d["total"] += 1
        drivers[a.top_factor] += 1
        score_sum += a.risk_score
        for f in FEATURES:
            feat_sum[f] += getattr(a, f)
        if a.risk_label == "High Risk":
            at_risk.append({"user_id": u.id, "name": u.full_name, "department": u.department,
                            "designation": u.designation, "risk_score": a.risk_score,
                            "top_factor": a.top_factor, "created_at": iso(a.created_at)})
    n = len(latest)
    at_risk.sort(key=lambda r: r["risk_score"])

    # weekly average balance score for the last 8 weeks
    since = utcnow() - timedelta(days=56)
    weekly = defaultdict(lambda: [0.0, 0])
    for a in db.query(Assessment).filter(Assessment.created_at >= since).all():
        monday = (a.created_at - timedelta(days=a.created_at.weekday())).date().isoformat()
        weekly[monday][0] += a.risk_score
        weekly[monday][1] += 1
    trend = [{"week": w, "avg_score": round(s / c, 3), "count": c} for w, (s, c) in sorted(weekly.items())]

    return {
        "total_people": len(people),
        "assessed": n,
        "coverage_pct": round(100 * n / len(people), 1) if people else 0,
        "distribution": {k: dist.get(k, 0) for k in RISK_LABELS},
        "avg_balance_score": round(score_sum / n, 3) if n else None,
        "avg_inputs": {f: round(feat_sum[f] / n, 2) for f in FEATURES} if n else {},
        "by_department": [{"department": k, **v} for k, v in sorted(by_dept.items())],
        "top_drivers": [{"factor": k, "label": FEATURE_LABELS.get(k, k), "count": c}
                        for k, c in drivers.most_common()],
        "at_risk": at_risk[:10],
        "weekly_trend": trend,
    }


@app.get("/api/hr/assessments")
def hr_assessments(department: Optional[str] = None, risk: Optional[str] = None,
                   q: Optional[str] = None, limit: int = 200,
                   user: User = Depends(staff_only), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 1000))
    query = db.query(Assessment, User).join(User, User.id == Assessment.user_id)
    if department:
        query = query.filter(User.department == department)
    if risk in RISK_LABELS:
        query = query.filter(Assessment.risk_label == risk)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(User.full_name).like(like) | func.lower(User.username).like(like))
    rows = query.order_by(Assessment.id.desc()).limit(limit).all()
    return [{**assessment_out(a), "user_id": u.id, "name": u.full_name, "username": u.username,
             "department": u.department} for a, u in rows]


@app.get("/api/departments")
def departments(user: User = Depends(staff_only), db: Session = Depends(get_db)):
    return sorted(d for (d,) in db.query(User.department).distinct().all() if d)


# --- people directory ---
def can_manage(actor: User, target: User) -> bool:
    """Admins manage everyone; HR manages plain employees only."""
    return actor.role == "admin" or target.role == "employee"


@app.get("/api/employees")
def list_employees(q: Optional[str] = None, department: Optional[str] = None,
                   user: User = Depends(staff_only), db: Session = Depends(get_db)):
    query = db.query(User)
    if department:
        query = query.filter(User.department == department)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(User.full_name).like(like) | func.lower(User.username).like(like))
    people = query.order_by(User.full_name).all()

    latest_ids = select(func.max(Assessment.id)).group_by(Assessment.user_id)
    latest = {a.user_id: a for a in db.query(Assessment).filter(Assessment.id.in_(latest_ids)).all()}
    out = []
    for p in people:
        a = latest.get(p.id)
        out.append({**user_out(p), "manageable": can_manage(user, p) and p.id != user.id,
                    "latest_risk": a.risk_label if a else None,
                    "latest_score": a.risk_score if a else None,
                    "latest_at": iso(a.created_at) if a else None})
    return out


@app.post("/api/employees", status_code=201)
def create_employee(payload: EmployeeCreate, actor: User = Depends(staff_only),
                    db: Session = Depends(get_db)):
    if actor.role != "admin" and payload.role != "employee":
        raise HTTPException(403, "Only an admin can create HR or admin accounts.")
    uname = payload.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", uname):
        raise HTTPException(400, "Username may only contain letters, digits, '.', '_' and '-'.")
    if db.query(User).filter(func.lower(User.username) == uname.lower()).first():
        raise HTTPException(409, "That username is already taken.")
    if payload.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(400, "Email address looks invalid.")

    generated = None
    password = (payload.password or "").strip()
    if not password:
        password = generated = auth.generate_temp_password()
    else:
        problem = auth.validate_password_strength(password)
        if problem:
            raise HTTPException(400, problem)

    u = User(username=uname, full_name=payload.full_name.strip(), email=payload.email or None,
             department=payload.department.strip(), designation=payload.designation.strip(),
             role=payload.role, password_hash=auth.hash_password(password), must_change_password=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return {**user_out(u), "temp_password": generated}


@app.patch("/api/employees/{user_id}")
def update_employee(user_id: int, payload: EmployeeUpdate, actor: User = Depends(staff_only),
                    db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Person not found.")
    if not can_manage(actor, target):
        raise HTTPException(403, "HR can only manage employee accounts.")
    data = payload.model_dump(exclude_unset=True)

    if "role" in data and data["role"] != target.role:
        if actor.role != "admin":
            raise HTTPException(403, "Only an admin can change roles.")
        if target.id == actor.id:
            raise HTTPException(400, "You cannot change your own role.")
    if data.get("is_active") is False and target.id == actor.id:
        raise HTTPException(400, "You cannot deactivate your own account.")
    if data.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", data["email"]):
        raise HTTPException(400, "Email address looks invalid.")

    # never leave the system without an active admin
    losing_admin = target.role == "admin" and (data.get("role", "admin") != "admin" or data.get("is_active") is False)
    if losing_admin:
        others = db.query(User).filter(User.role == "admin", User.is_active == True, User.id != target.id).count()  # noqa: E712
        if others == 0:
            raise HTTPException(400, "At least one active admin must remain.")

    for k, v in data.items():
        setattr(target, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(target)
    return user_out(target)


@app.post("/api/employees/{user_id}/reset-password")
def reset_password(user_id: int, payload: ResetPasswordIn, actor: User = Depends(staff_only),
                   db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Person not found.")
    if target.id == actor.id:
        raise HTTPException(400, "Use 'Change password' on your profile for your own account.")
    if not can_manage(actor, target):
        raise HTTPException(403, "HR can only manage employee accounts.")

    generated = None
    password = (payload.new_password or "").strip()
    if not password:
        password = generated = auth.generate_temp_password()
    else:
        problem = auth.validate_password_strength(password)
        if problem:
            raise HTTPException(400, problem)
    target.password_hash = auth.hash_password(password)       # also signs them out everywhere
    target.must_change_password = True
    db.commit()
    return {"ok": True, "temp_password": generated or password}


# ------------------------------ frontend ----------------------------------
# API routes above are matched first; everything else is static files.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
