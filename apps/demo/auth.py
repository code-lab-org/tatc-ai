import hashlib
import hmac
import os
import secrets
import time

_PBKDF_ROUNDS = 120_000
_SECRET = os.environ.get("DEMO_SECRET", "dev-demo-secret-change-me").encode()

ROLES = ("user", "contributor", "developer", "admin")


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF_ROUNDS).hex()


class UserStore:
    def __init__(self):
        self._users = {}

    def add(self, role: str, user_id):
        email = f"codelab_{role}_{user_id}@lab.com"
        password = f"{role}_{user_id}"
        salt = secrets.token_bytes(16)
        self._users[email] = {
            "role": role,
            "user_id": user_id,
            "salt": salt,
            "hash": _hash(password, salt),
        }
        return email, password

    def verify(self, email: str, password: str):
        rec = self._users.get(email)
        if not rec:
            return None
        if not hmac.compare_digest(rec["hash"], _hash(password, rec["salt"])):
            return None
        return {"email": email, "role": rec["role"], "user_id": rec["user_id"]}

    def emails(self):
        return sorted(self._users)


def seed(user_ids=(1, 2)):
    store = UserStore()
    creds = []
    for role in ROLES:
        for uid in user_ids:
            email, pw = store.add(role, uid)
            creds.append({"email": email, "password": pw, "role": role})
    return store, creds


def issue_token(email: str, role: str, ttl: int = 3600) -> str:
    exp = str(int(time.time()) + ttl)
    body = f"{email}|{role}|{exp}"
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{sig}"


def verify_token(token: str):
    parts = token.split("|")
    if len(parts) != 4:
        return None
    email, role, exp, sig = parts
    body = f"{email}|{role}|{exp}"
    expected = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if int(exp) < int(time.time()):
        return None
    return {"email": email, "role": role}
