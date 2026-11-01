# BalanceIQ ERP - Work-Life Balance prediction (website edition)

Login-based web app: employees take an 8-question assessment and get a work-life-balance
risk prediction; HR/admin get an organisation overview, people directory and CSV export.
Backend: FastAPI + SQLAlchemy. Frontend: plain HTML/JS served by the same app (one service, one URL).

Functionality is unchanged from the local version. This edition adds what a public site needs:
PostgreSQL support, HTTPS-only cookies, secrets from environment variables, a `/health` check,
security headers, and one-click deploy files.

```
erp_wlb/
  backend/    main.py auth.py database.py model.py seed.py requirements.txt
  frontend/   login.html app.html app.js login.js style.css index.html
  render.yaml Dockerfile .env.example .gitignore
```

## Run locally (unchanged)

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows   (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python seed.py                   # optional demo people (password Welcome@123)
uvicorn main:app --reload        # http://localhost:8000/
```
Local first login: `admin` / `Admin@123` (you must change it). Local mode uses SQLite (`backend/erp.db`).

## Put it on the internet - Render (free, ~10 minutes)

1. Push this folder to a **private** GitHub repo (the `.gitignore` already keeps `venv`, `erp.db` and `.secret_key` out).
2. Render dashboard -> **New +** -> **Blueprint** -> pick the repo. `render.yaml` creates the
   PostgreSQL database and the web service and wires them together.
3. When asked, type a value for **WLB_ADMIN_PASSWORD** (8+ characters, letters and digits).
4. Wait for the first deploy, open `https://<your-service>.onrender.com`, sign in as `admin`, and change the password.
5. Create real accounts under **People**.

Tables and the admin account are created automatically on first start - no shell access needed.
Want sample people on a showcase site? Add env var `WLB_SEED_DEMO=1` (demo password: `WLB_DEMO_PASSWORD`, default `Welcome@123`). Don't use that for real data.

Free-tier notes: the service sleeps after ~15 min idle (first visit then takes ~30-60 s) and Render's free
Postgres database expires after a limited period, so upgrade or export before relying on it long term.

## Other hosts

**Docker** (any VPS, Fly.io, Railway, ...):
```bash
docker build -t balanceiq .
docker run -p 8000:8000 --env-file .env balanceiq     # copy .env.example to .env first
```
Put Nginx/Caddy (HTTPS) in front. **VPS without Docker:** run
`uvicorn main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="*"` under systemd behind Nginx with Let's Encrypt.
Use `--forwarded-allow-ips="*"` only when the app is reachable solely through your proxy.

## Environment variables

| Variable | Purpose |
|---|---|
| `WLB_ENV=production` | Enables Secure cookies, HSTS, CSP, hides `/docs`, requires a real secret key |
| `WLB_SECRET_KEY` | Session-signing key, 32+ random characters (required in production) |
| `WLB_DATABASE_URL` | `postgresql://...` (also accepts `postgres://...`). Unset = local SQLite |
| `WLB_ADMIN_PASSWORD` | Password for the first `admin` account (production without it = random one printed once in the log) |
| `WLB_COOKIE_SECURE=1` | Force Secure cookies when not using `WLB_ENV=production` |
| `WLB_SEED_DEMO`, `WLB_DEMO_PASSWORD` | Optional demo data on startup |
| `WLB_ALLOW_REGISTRATION=0` | Turn off public self-registration (default: on) |
| `WLB_REGISTER_LIMIT` | Max signups per IP per hour (default 5) |

## Self-registration

The login page has a **Create an account** link (`/register.html`). Anyone can sign up, but only as an
**employee**: the API has no role field, and the server hard-codes `role="employee"`. HR and admin accounts
can only be created by an admin under **People -> Add person**. Signups are limited per IP
(`WLB_REGISTER_LIMIT`, in memory, resets on restart). Set `WLB_ALLOW_REGISTRATION=0` to close signups.
Note that open signup means anyone who finds the URL can create an account and submit assessments, and HR will see them in reports.

## Roles

| Capability | Employee | HR | Admin |
|---|:--:|:--:|:--:|
| Sign in, take assessments, own results/history, change password | yes | yes | yes |
| Org overview, all assessments, CSV export | - | yes | yes |
| Add / edit / deactivate / reset password of **employees** | - | yes | yes |
| Manage HR and admin accounts, change roles | - | - | yes |

## API (all under /api, cookie session)
```
POST /auth/login  /auth/logout  /auth/change-password     GET /auth/me
POST /register    (public, employee accounts only)
POST /predict     GET /my/assessments   GET /my/latest
GET  /hr/overview /hr/assessments /departments             (hr, admin)
GET  /employees   POST /employees   PATCH /employees/{id}
POST /employees/{id}/reset-password                        (hr, admin)
GET  /health      (no login; used by the host)
```
Interactive docs at `/docs` outside production.

## Security notes
- Passwords: PBKDF2-SHA256, 200k rounds, per-user salt. Sessions: signed HttpOnly cookie, 8 h, SameSite=Lax, Secure on HTTPS.
- 5 failed logins (same username + IP) lock sign-in for 5 minutes. This is in memory, so it resets on restart and is per-process (fine for one worker; use Redis/a WAF if you scale out).
- Roles are enforced on the server for every endpoint. You cannot deactivate/demote yourself; the last active admin cannot be removed.
- Never commit `.env` or `.secret_key`. If a key was ever shared or committed, set a new `WLB_SECRET_KEY` (this signs everyone out).
- Privacy: HR can see named results. Tell employees this (the result card says so). Back up the database.

## Notes
- The prediction model is the synthetic-data Logistic Regression from `model.py`, trained when the server starts (about a second).
  To use your own trained model, replace `model.predict()`; the app only needs `label`, `score`, `proba`, `contributions`, `top_factor`.
