"""Helpers used across the app: form validation, role checks, formatting, tokens."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, request
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import db
from models import ZERO, User, money

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------------------
# Password rules from the brief: more than 8 characters, an uppercase letter,
# a lowercase letter and a special character.
# ---------------------------------------------------------------------------
PASSWORD_RULES = [
    ("length", "More than 8 characters", lambda p: len(p) > 8),
    ("upper", "An uppercase letter", lambda p: re.search(r"[A-Z]", p) is not None),
    ("lower", "A lowercase letter", lambda p: re.search(r"[a-z]", p) is not None),
    ("special", "A special character (like @ # $ !)", lambda p: re.search(r"[^A-Za-z0-9\s]", p) is not None),
]


def password_problems(password: str) -> list[str]:
    return [label for _key, label, check in PASSWORD_RULES if not check(password or "")]


# ---------------------------------------------------------------------------
# Form validation
# ---------------------------------------------------------------------------
class Form:
    """Reads submitted values, validates them and remembers errors for the template.

    Usage in a route:
        form = Form(request.form if request.method == "POST" else None, initial={...})
        name = form.text("name", "Name")
        if form.submitted and form.ok: ...save...
    Templates read form.values[...] and form.errors[...].
    """

    def __init__(self, data=None, initial: dict | None = None):
        self.data = data
        self.values: dict = {}
        for key, value in (initial or {}).items():
            self.values[key] = _to_form_value(value)
        if data is not None:
            for key in data.keys():
                items = data.getlist(key) if hasattr(data, "getlist") else [data.get(key)]
                self.values[key] = items[0] if len(items) == 1 else items
        self.errors: dict[str, str] = {}

    @property
    def submitted(self) -> bool:
        return self.data is not None

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, name: str, message: str) -> None:
        self.errors.setdefault(name, message)

    def raw(self, name: str) -> str:
        if self.data is None:
            return ""
        return (self.data.get(name) or "").strip()

    # -- field types -------------------------------------------------------
    def text(self, name, label, required=True, max_len=255, min_len=0):
        value = self.raw(name)
        if not value:
            if required:
                self.error(name, f"{label} is required.")
            return None
        if len(value) > max_len:
            self.error(name, f"{label} must be {max_len} characters or fewer.")
        elif len(value) < min_len:
            self.error(name, f"{label} must be at least {min_len} characters.")
        return value

    def email(self, name="email", label="Email", required=True):
        value = self.raw(name).lower()
        if not value:
            if required:
                self.error(name, f"{label} is required.")
            return None
        if len(value) > 255 or not EMAIL_RE.match(value):
            self.error(name, "Enter a valid email address, like name@company.com.")
            return None
        return value

    def phone(self, name="phone", label="Phone", required=False):
        value = self.raw(name)
        if not value:
            if required:
                self.error(name, f"{label} is required.")
            return None
        if not re.fullmatch(r"[0-9+\-\s()]{7,20}", value):
            self.error(name, "Enter a phone number with 7 to 20 digits.")
        return value

    def password(self, name="password", label="Password"):
        value = self.data.get(name, "") if self.data is not None else ""
        if not value:
            self.error(name, f"{label} is required.")
            return None
        problems = password_problems(value)
        if problems:
            self.error(name, "Password needs: " + ", ".join(p.lower() for p in problems) + ".")
            return None
        return value

    def decimal(self, name, label, required=True, min_value=None, max_value=None, default=None):
        raw = self.raw(name).replace(",", "")
        if raw == "":
            if required:
                self.error(name, f"{label} is required.")
            return default
        try:
            value = Decimal(raw)
        except InvalidOperation:
            self.error(name, f"{label} must be a number.")
            return default
        if not value.is_finite():
            self.error(name, f"{label} must be a number.")
            return default
        if min_value is not None and value < Decimal(str(min_value)):
            self.error(name, f"{label} must be at least {min_value}.")
        if max_value is not None and value > Decimal(str(max_value)):
            self.error(name, f"{label} must be at most {max_value}.")
        return money(value)

    def integer(self, name, label, required=True, min_value=None, max_value=None, default=None):
        raw = self.raw(name)
        if raw == "":
            if required:
                self.error(name, f"{label} is required.")
            return default
        try:
            value = int(raw)
        except ValueError:
            self.error(name, f"{label} must be a whole number.")
            return default
        if min_value is not None and value < min_value:
            self.error(name, f"{label} must be at least {min_value}.")
        if max_value is not None and value > max_value:
            self.error(name, f"{label} must be at most {max_value}.")
        return value

    def date(self, name, label, required=True):
        raw = self.raw(name)
        if not raw:
            if required:
                self.error(name, f"{label} is required.")
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self.error(name, f"{label} must be a valid date.")
            return None

    def choice(self, name, label, choices, required=True):
        raw = self.raw(name)
        if not raw:
            if required:
                self.error(name, f"Choose a {label.lower()}.")
            return None
        keys = {str(k): k for k in choices}
        if raw not in keys:
            self.error(name, f"Choose a valid {label.lower()}.")
            return None
        return keys[raw]

    def record(self, name, label, model, required=True, where=None):
        """Turn a submitted id into a database row (or an error)."""
        raw = self.raw(name)
        if not raw:
            if required:
                self.error(name, f"Choose a {label.lower()}.")
            return None
        try:
            obj = db.session.get(model, int(raw))
        except (TypeError, ValueError):
            obj = None
        if obj is None or (where is not None and not where(obj)):
            self.error(name, f"Choose a valid {label.lower()}.")
            return None
        return obj

    def boolean(self, name) -> bool:
        if self.data is None:
            return bool(self.values.get(name))
        return self.data.get(name) in ("on", "1", "true", "yes")


def _to_form_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return f"{money(value):f}"
    return value


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def roles_required(*roles):
    """Allow only users whose role is in `roles`; everyone else gets 403."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


staff_required = roles_required("admin", "staff")
admin_required = roles_required("admin")
customer_required = roles_required("customer")


def is_safe_next(target: str | None) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/") and not target.startswith("//")


def home_endpoint(user: User) -> str:
    return "portal.home" if user.is_customer else "main.dashboard"


# ---------------------------------------------------------------------------
# Signed tokens for password reset and invites (no extra table needed).
# The token embeds part of the current password hash, so it stops working
# once the password changes: every link works only once.
# ---------------------------------------------------------------------------
TOKEN_SALTS = {"reset": "password-reset", "invite": "account-invite"}


def make_token(user: User, purpose: str = "reset") -> str:
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALTS[purpose])
    return serializer.dumps({"uid": user.id, "fp": user.password_hash[-16:]})


def read_token(token: str, purpose: str = "reset") -> User | None:
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALTS[purpose])
    max_age = current_app.config["INVITE_TOKEN_MAX_AGE" if purpose == "invite" else "RESET_TOKEN_MAX_AGE"]
    try:
        data = serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or not user.is_active or user.password_hash[-16:] != data.get("fp"):
        return None
    return user


# ---------------------------------------------------------------------------
# Formatting (registered as Jinja filters in app.py)
# ---------------------------------------------------------------------------
def format_money(value, currency: str | None = None) -> str:
    """Indian digit grouping: 1234567.5 -> ₹12,34,567.50"""
    symbol = current_app.config.get("CURRENCY", "₹") if currency is None else currency
    amount = money(value if value is not None else ZERO)
    sign = "-" if amount < 0 else ""
    whole, fraction = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{symbol}{whole}.{fraction}"


def format_date(value, fmt: str = "%d %b %Y") -> str:
    if not value:
        return "—"
    return value.strftime(fmt)


def format_datetime(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d %b %Y, %I:%M %p")


def page_args(**overrides) -> dict:
    """Current query-string args with some values replaced (for pagination/filter links)."""
    args = request.args.to_dict()
    args.update({k: v for k, v in overrides.items()})
    return {k: v for k, v in args.items() if v not in (None, "")}


def current_page() -> int:
    try:
        return max(int(request.args.get("page", 1)), 1)
    except ValueError:
        return 1
