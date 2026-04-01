import hashlib
import hmac
import os
import re
import base64

from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

# ── Master secret (loaded from .env, never hardcoded) ─────────────────────────
MASTER_SECRET: str = os.getenv("SCB_MASTER_SECRET", "")
if not MASTER_SECRET:
    raise EnvironmentError(
        "SCB_MASTER_SECRET is not set. "
        "Run: python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and add it to your .env file."
    )

# PII_PATTERNS = [
#     (re.compile(r'\b(\+91[\-\s]?)?\d{10}\b'),          '[PHONE]'),
#     (re.compile(r'[\w.\-+]+@[\w.\-]+\.\w{2,}'),        '[EMAIL]'),
#     (re.compile(r'\b\d{2}[\/\-]\d{2}[\/\-]\d{4}\b'),  '[DOB]'),
#     (re.compile(r'\b\d{4}[\/\-]\d{2}[\/\-]\d{2}\b'),  '[DOB]'),
#     (re.compile(
#         r'\b\d+[,\s]+[\w\s]{2,40}(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|'
#         r'Nagar|Colony|Layout|Cross|Main|Block|Phase|Sector|Plot)\b',
#         re.IGNORECASE
#     ), '[ADDRESS]'),
# ]

def derive_key(patient_id: str) -> Fernet:
    """
    Derives a deterministic, unique Fernet key for a given patient_id
    using PBKDF2-HMAC-SHA256 with the master secret as the password.

    Same patient_id + master secret → same key every time.
    Different patients → different keys.
    Compromising one key does NOT expose other patients.
    """
    if not patient_id or not isinstance(patient_id, str):
        raise ValueError("patient_id must be a non-empty string.")

    raw = hashlib.pbkdf2_hmac(
        hash_name='sha256',
        password=MASTER_SECRET.encode('utf-8'),
        salt=patient_id.lower().encode('utf-8'),
        iterations=100_000,   # NIST recommended minimum
        dklen=32,
    )
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


# def anonymise_text(text: str) -> str:
#     if not isinstance(text, str):
#         raise TypeError(f"Expected str, got {type(text).__name__}.")
#     if not text:
#         return text
#
#     result = text
#     for pattern, replacement in PII_PATTERNS:
#         result = pattern.sub(replacement, result)
#     return result


def encrypt_patient_data(plain_text: str, patient_id: str) -> str:
    if not isinstance(plain_text, str):
        raise TypeError(f"Expected str, got {type(plain_text).__name__}.")
    if not plain_text:
        return ""

    cipher = derive_key(patient_id)
    return cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_patient_data(encrypted_text: str, patient_id: str) -> str:
    if not isinstance(encrypted_text, str):
        raise TypeError(f"Expected str, got {type(encrypted_text).__name__}.")
    if not encrypted_text:
        return ""

    try:
        cipher = derive_key(patient_id)
        return cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(
            f"Decryption failed for patient {patient_id}: invalid token or wrong key."
        ) from e

def hash_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError(f"Expected str, got {type(text).__name__}.")
    if not text:
        raise ValueError("Cannot hash an empty string.")

    # HMAC-SHA256: hash(master_secret + name) — not just hash(name)
    return hmac.new(
        MASTER_SECRET.encode('utf-8'),
        text.lower().encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def generate_and_save_key() -> bytes:
    """Utility — generate a new Fernet key for initial setup."""
    return Fernet.generate_key()