"""App settings. Values can be overridden with environment variables or a .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_DB = (BASE_DIR / "instance" / "subscriptions.db").as_posix()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")

    # SQLite database file lives in instance/ (ignored by git, rebuilt by seed.py)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    WTF_CSRF_TIME_LIMIT = None  # CSRF token valid for the whole session

    # Company shown on invoices and emails
    COMPANY_NAME = "Brightloop Software"
    COMPANY_ADDRESS = "2nd Floor, Techno Park Road, Kannur, Kerala 670001"
    COMPANY_EMAIL = "billing@brightloop.example"
    COMPANY_GSTIN = "32ABCDE1234F1Z5"
    CURRENCY = "₹"

    ITEMS_PER_PAGE = 20

    # Used for links in emails sent from command-line jobs (outside a web request)
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000")

    # Create demo data automatically the first time the app starts
    AUTO_SEED = os.environ.get("AUTO_SEED", "1") == "1"

    # Password reset links expire after 1 hour, staff invites after 7 days
    RESET_TOKEN_MAX_AGE = 60 * 60
    INVITE_TOKEN_MAX_AGE = 7 * 24 * 60 * 60

    # Optional real email (e.g. Gmail with an App Password). If MAIL_SERVER is
    # empty, emails are saved to the in-app Email outbox instead of being sent.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "") or MAIL_USERNAME or "billing@brightloop.example"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    MAIL_SERVER = ""
    AUTO_SEED = False
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"  # fast hashing keeps the test suite quick
