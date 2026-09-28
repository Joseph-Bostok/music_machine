"""Password hashing, sessions, and login rate limiting.

Only the standard library is used: hashlib.scrypt is a memory-hard
password hash (expensive to brute-force on GPUs), and `secrets` provides
cryptographically strong session tokens.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

SESSION_COOKIE = "session"
SESSION_TTL = 30 * 24 * 3600  # seconds: stay signed in for 30 days
MIN_PASSWORD_LENGTH = 10

# scrypt cost parameters: N=2^14, r=8 uses 16 MiB of memory and takes
# ~50 ms per hash, slow enough to hurt an attacker but fine for a login.
_N, _R, _P = 2 ** 14, 8, 1


def _b64(raw):
    return base64.b64encode(raw).decode()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    # Store the parameters with the hash so they can be raised later
    # without invalidating existing passwords.
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                               n=int(n), r=int(r), p=int(p))
    # Constant-time comparison: doesn't leak how many bytes matched.
    return hmac.compare_digest(candidate, base64.b64decode(digest))


# Verified against when the email doesn't exist, so "no such user" takes as
# long as "wrong password" and response timing can't reveal valid emails.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def authenticate(conn, email, password):
    """Return the user row if the credentials are valid, else None."""
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip(),)).fetchone()
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user["password_hash"]) else None


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                 (_token_hash(token), user_id, int(time.time()) + SESSION_TTL))
    conn.commit()
    return token


def user_for_token(conn, token):
    if not token:
        return None
    return conn.execute(
        """SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_token_hash(token), int(time.time())),
    ).fetchone()


def delete_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()


class LoginLimiter:
    """Allow at most `limit` failed logins per email within `window` seconds.

    Kept in memory: it resets when the server restarts, which is acceptable
    for slowing down password guessing against a handful of accounts.
    """

    def __init__(self, limit=5, window=15 * 60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.failures = defaultdict(deque)

    def _recent(self, key):
        q = self.failures[key]
        while q and self.clock() - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key):
        return len(self._recent(key.lower())) >= self.limit

    def record_failure(self, key):
        self._recent(key.lower()).append(self.clock())

    def reset(self, key):
        self.failures.pop(key.lower(), None)
