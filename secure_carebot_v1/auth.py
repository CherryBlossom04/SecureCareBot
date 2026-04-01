import os
import random
import smtplib
import string
import uuid
import logging
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from pymongo import MongoClient

load_dotenv()

# ── Logging & Config ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("auth")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

JWT_SECRET = os.getenv("JWT_SECRET", "atvZ08phXX-JDAfEwKy4Eim_g1vbXuiydiFN10CRmmM=")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = "securecarebot"
COLLECTION = "users"

OTP_EXPIRE_MINUTES = 10
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# ── Database & State ──────────────────────────────────────────────────────────
_mongo_client = MongoClient(MONGO_URL)
db = _mongo_client[DB_NAME][COLLECTION]

# In-memory deny-list (Restarting server clears this; use Redis for Production)
_denied_tokens: set[str] = set()

# ── RBAC Map ──────────────────────────────────────────────────────────────────
ROLE_PERMISSIONS = {
    "admin": ["view_patients", "edit_patients", "delete_patients", "view_reports", "manage_users"],
    "doctor": ["view_patients", "edit_patients", "view_reports"],
    "nurse": ["view_patients", "view_reports"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def _hash_payload(data: str) -> str:
    return bcrypt.hashpw(data.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _create_jwt(username: str, role: str, permissions: list[str], jti: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "permissions": permissions,
        "jti": jti,
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _send_email(to: str, subject: str, otp: str, name: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning(f"--- [DEV MODE] OTP for {to}: {otp} ---")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"SecureCareBot <{SMTP_USER}>"
        msg["To"] = to

        html = f"<html><body><h2>SecureCareBot</h2><p>Hi {name}, your code is: <b>{otp}</b></p></body></html>"
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to, msg.as_string())
    except Exception as e:
        log.warning(f"--- [DEV MODE] Email failed, OTP for {to}: {otp} (Error: {e}) ---")


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class OtpRequest(BaseModel):
    username: str
    otp: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    permissions: list[str]
    name: str  # BUG 4 FIX: was missing, Login.tsx needs it


class ForgotPasswordRequest(BaseModel):
    email: str


class VerifyResetOtpRequest(BaseModel):
    email: str
    otp: str


class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str


# ── API Logic ─────────────────────────────────────────────────────────────────

app = FastAPI(title="SecureCare Auth")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/login/step1")
async def login_step1(body: LoginRequest):
    user = db.find_one({"username": body.username})
    if not user:
        raise HTTPException(401, "Invalid username or password.")

    # Timezone-safe lockout check
    locked_until = user.get("locked_until")
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if _now() < locked_until:
            remaining = max(1, int((locked_until - _now()).total_seconds() / 60) + 1)
            raise HTTPException(429, f"Account locked. Try again in {remaining} minute(s).")

    if not _verify_password(body.password, user["password_hash"]):
        # BUG 1+2 FIX: consistent field name + actual lockout enforcement
        new_count = user.get("failed_login_attempts", 0) + 1
        update: dict = {"failed_login_attempts": new_count}
        if new_count >= MAX_FAILED_ATTEMPTS:
            update["locked_until"] = _now() + timedelta(minutes=LOCKOUT_MINUTES)
            db.update_one({"_id": user["_id"]}, {"$set": update})
            raise HTTPException(429, f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes.")
        db.update_one({"_id": user["_id"]}, {"$set": update})
        raise HTTPException(401, "Invalid username or password.")

    otp = "".join(random.choices(string.digits, k=6))
    db.update_one({"_id": user["_id"]}, {"$set": {
        "otp_hash": _hash_payload(otp),
        "otp_expires_at": _now() + timedelta(minutes=OTP_EXPIRE_MINUTES),  # BUG 3 FIX
        "failed_login_attempts": 0,
        "locked_until": None,
    }})

    _send_email(user["email"], "Your Login OTP", otp, user.get("name", "User"))
    return {"message": "OTP sent to email."}


@app.post("/auth/login/step2", response_model=TokenResponse)
async def login_step2(body: OtpRequest):
    user = db.find_one({"username": body.username})
    if not user or not user.get("otp_hash"):
        raise HTTPException(400, "No pending OTP. Please start login again.")

    # BUG 3 FIX: use consistent field name; safely handle naive/aware datetime
    raw_exp = user.get("otp_expires_at") or user.get("otp_exp")
    if raw_exp is None:
        raise HTTPException(400, "OTP record is corrupt. Please start login again.")
    otp_exp = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
    if _now() > otp_exp:
        raise HTTPException(400, "OTP expired. Please start login again.")

    if not bcrypt.checkpw(body.otp.encode(), user["otp_hash"].encode()):
        raise HTTPException(401, "Invalid OTP.")

    jti = str(uuid.uuid4())
    role = user.get("role", "doctor")
    token = _create_jwt(user["username"], role, ROLE_PERMISSIONS.get(role, []), jti)

    db.update_one({"_id": user["_id"]}, {"$set": {"otp_hash": None, "last_login": _now()}})
    return {
        "access_token": token,
        "role": role,
        "permissions": ROLE_PERMISSIONS.get(role, []),
        "name": user.get("name", user["username"]),  # BUG 4 FIX
    }


# ── Forgot-password flow ───────────────────────────────────────────────────────
# BUG 2 FIX (Login.tsx): these three endpoints were missing entirely, causing
# every forgot-password action to 404 and show a generic error.

@app.post("/auth/forgot-password/request")
async def forgot_password_request(body: ForgotPasswordRequest):
    """Step 1 – send reset OTP to the registered email address."""
    user = db.find_one({"email": body.email})
    # Always return success to prevent email enumeration
    generic = {"message": "If that email is registered, an OTP has been sent."}
    if not user:
        return generic

    otp = "".join(random.choices(string.digits, k=6))
    db.update_one({"_id": user["_id"]}, {"$set": {
        "reset_otp_hash": _hash_payload(otp),
        "reset_otp_expires_at": _now() + timedelta(minutes=OTP_EXPIRE_MINUTES),
    }})

    _send_email(user["email"], "SecureCareBot – Password Reset OTP", otp, user.get("name", "User"))
    dev_note = f" (DEV – OTP: {otp})" if not SMTP_USER else ""
    return {"message": f"If that email is registered, an OTP has been sent.{dev_note}"}


@app.post("/auth/forgot-password/verify-otp")
async def forgot_password_verify_otp(body: VerifyResetOtpRequest):
    """Step 2 – verify the reset OTP before showing the new-password form."""
    user = db.find_one({"email": body.email})
    _bad = HTTPException(400, "Invalid or expired OTP.")
    if not user or not user.get("reset_otp_hash"):
        raise _bad

    raw_exp = user.get("reset_otp_expires_at")
    if raw_exp is None:
        raise _bad
    exp = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
    if _now() > exp:
        raise _bad

    if not bcrypt.checkpw(body.otp.encode(), user["reset_otp_hash"].encode()):
        raise _bad

    # OTP is valid — leave it in the DB so /reset can re-verify it
    return {"message": "OTP verified. You may now set a new password."}


@app.post("/auth/forgot-password/reset")
async def forgot_password_reset(body: ResetPasswordRequest):
    """Step 3 – re-verify OTP and set the new password atomically."""
    user = db.find_one({"email": body.email})
    _bad = HTTPException(400, "Invalid or expired OTP.")
    if not user or not user.get("reset_otp_hash"):
        raise _bad

    raw_exp = user.get("reset_otp_expires_at")
    if raw_exp is None:
        raise _bad
    exp = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
    if _now() > exp:
        raise _bad

    if not bcrypt.checkpw(body.otp.encode(), user["reset_otp_hash"].encode()):
        raise _bad

    if len(body.new_password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")

    new_hash = _hash_payload(body.new_password)
    db.update_one({"_id": user["_id"]}, {"$set": {
        "password_hash": new_hash,
        "reset_otp_hash": None,
        "reset_otp_expires_at": None,
        "failed_login_attempts": 0,
        "locked_until": None,
    }})
    return {"message": "Password reset successfully. You may now log in."}


@app.get("/auth/health")
async def health():
    return {"status": "running"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)