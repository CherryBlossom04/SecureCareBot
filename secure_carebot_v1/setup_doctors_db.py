import sys
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
import bcrypt

# ── Config ────────────────────────────────────────────────────────────────────
MONGO_URL = "mongodb://localhost:27017/"
DB_NAME = "securecarebot"
COLLECTION = "users"

# ── RBAC roles ────────────────────────────────────────────────────────────────
# Each role maps to the set of permissions it holds.
ROLES = {
    "admin": [
        "view_patients",
        "edit_patients",
        "delete_patients",
        "view_reports",
        "manage_users",
        "view_analytics",
    ],
    "senior_doctor": [
        "view_patients",
        "edit_patients",
        "view_reports",
        "view_analytics",
    ],
    "doctor": [
        "view_patients",
        "edit_patients",
        "view_reports",
    ],
    "nurse": [
        "view_patients",
        "view_reports",
    ],
    "receptionist": [
        "view_patients",
    ],
}

DOCTORS = [
    {
        "name": "Dr. Arjun Sharma",
        "username": "arjun.sharma",
        "email": "arjun.sharma@securecarebot.com",
        "password": "Admin@1234",
        "role": "admin",
        "department": "Administration",
        "phone": "+91-9876543210",
        "active": True,
    },
    {
        "name": "Sowmya",
        "username": "sowmyaarun",
        "email": "sowmya635207@gmail.com",
        "password": "123456",
        "role": "doctor",
        "department": "Cardiology",
        "phone": "91+8870255572",
        "active": True,
    },
{
        "name": "Swathi",
        "username": "swathi",
        "email": "swathi.22ads@sonatech.ac.in",
        "password": "123456",
        "role": "doctor",
        "department": "Administration",
        "phone": "91+8870255572",
        "active": True,
    },
    {
        "name": "Dr. Priya Nair",
        "username": "priya.nair",
        "email": "priya.nair@securecarebot.com",
        "password": "Senior@5678",
        "role": "senior_doctor",
        "department": "Cardiology",
        "phone": "+91-9876543211",
        "active": True,
    },
    {
        "name": "Dr. Rahul Mehta",
        "username": "rahul.mehta",
        "email": "rahul.mehta@securecarebot.com",
        "password": "Doctor@9012",
        "role": "doctor",
        "department": "Neurology",
        "phone": "+91-9876543212",
        "active": True,
    },
    {
        "name": "Nurse Kavya Reddy",
        "username": "kavya.reddy",
        "email": "kavya.reddy@securecarebot.com",
        "password": "Nurse@3456",
        "role": "nurse",
        "department": "ICU",
        "phone": "+91-9876543213",
        "active": True,
    },
    {
        "name": "Ravi Kumar",
        "username": "ravi.kumar",
        "email": "ravi.kumar@securecarebot.com",
        "password": "Recept@7890",
        "role": "receptionist",
        "department": "Front Desk",
        "phone": "+91-9876543214",
        "active": True,
    },
]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def seed():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    col = db[COLLECTION]

    # Unique indexes
    col.create_index([("username", ASCENDING)], unique=True)
    col.create_index([("email", ASCENDING)], unique=True)

    inserted = 0
    skipped = 0
    for doc in DOCTORS:
        doc_copy = doc.copy()
        password = doc_copy.pop("password")
        record = {
            **doc_copy,
            "password_hash": hash_password(password),
            "permissions": ROLES[doc["role"]],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "failed_login_attempts": 0,
            "locked_until": None,
            "last_login": None,
            "otp_hash": None,
            "otp_expires_at": None,
            "reset_otp_hash": None,
            "reset_otp_expires_at": None,
        }
        try:
            col.insert_one(record)
            inserted += 1
            print(f"  ✅  Inserted  {record['username']}  [{record['role']}]")
        except DuplicateKeyError:
            skipped += 1
            print(f"  ⚠️   Skipped   {record['username']}  (already exists)")

    print(f"\nDone — {inserted} inserted, {skipped} skipped.")
    client.close()


if __name__ == "__main__":
    print("=" * 55)
    print("  SecureCareBot — Doctors DB Setup")
    print("=" * 55)
    try:
        seed()
    except Exception as exc:
        print(f"\n❌  Error: {exc}", file=sys.stderr)
        sys.exit(1)
