"""All settings in one place. Every value can be overridden with an environment variable (CPG_...)."""
import os
import secrets

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.path.join(BASE, "instance")


def _secret():
    """Use CPG_SECRET if given, otherwise keep one random key in instance/secret_key so logins survive a restart."""
    if os.environ.get("CPG_SECRET"):
        return os.environ["CPG_SECRET"]
    os.makedirs(INSTANCE, exist_ok=True)
    path = os.path.join(INSTANCE, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


def _db_uri():
    database_url = os.environ.get("CPG_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if database_url:  # MySQL or PostgreSQL connection URL
        # Render provides postgres:// or postgresql:// URLs; SQLAlchemy needs a driver.
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
        return database_url
    path = os.environ.get("CPG_DB") or os.path.join(INSTANCE, "cpg.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return "sqlite:///" + path


SMTP_HOST = os.environ.get("CPG_SMTP_HOST", "")


class Config:
    SECRET_KEY = _secret()
    SQLALCHEMY_DATABASE_URI = _db_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # One-time codes (OTP)
    OTP_CHANNEL = os.environ.get("CPG_OTP_CHANNEL", "email")  # "email" or "sms"
    DEMO_OTP = os.environ.get("CPG_DEMO_OTP", "0" if SMTP_HOST else "1") == "1"  # show the code on screen
    OTP_REQUIRED = os.environ.get("CPG_OTP_REQUIRED", "0") == "1"  # code at sign-up/login (off for now)
    OTP_TTL_MIN = 5
    OTP_MAX_TRIES = 5
    OTP_RESEND_SEC = 60

    # Sign-in protection
    LOCK_AFTER = 5       # wrong passwords before the account is locked
    LOCK_MIN = 15        # minutes locked
    DEVICE_DAYS = 30     # "remember this device"
    DEVICE_COOKIE = "cpg_device"

    # Email (any SMTP server: Gmail app password, Brevo, Resend ...)
    SMTP_HOST = SMTP_HOST
    SMTP_PORT = int(os.environ.get("CPG_SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("CPG_SMTP_USER", "")
    SMTP_PASS = os.environ.get("CPG_SMTP_PASS", "")
    SMTP_FROM = os.environ.get("CPG_SMTP_FROM") or os.environ.get("CPG_SMTP_USER", "no-reply@localhost")

    # Listed REG prices exclude VAT; 18% VAT is assumed (not yet confirmed with a real receipt).
    VAT_PCT = float(os.environ.get("CPG_VAT_PCT", "18"))

    # Payments. With no Flutterwave key the app uses its own demo checkout (no real money, no real token).
    # With a Flutterwave key the customer pays on Flutterwave's hosted page (MTN, Airtel or card).
    # Test keys (FLWSECK_TEST-...) always work. A live key is ignored unless CPG_ALLOW_LIVE_PAYMENTS=1, because
    # this app records purchases but does not itself issue the REG/EUCL electricity token.
    FLW_SECRET_KEY = os.environ.get("CPG_FLW_SECRET_KEY", "")
    FLW_WEBHOOK_HASH = os.environ.get("CPG_FLW_WEBHOOK_HASH", "")   # the "secret hash" set in Flutterwave > Settings > Webhooks
    FLW_BASE = "https://api.flutterwave.com/v3"
    ALLOW_LIVE_PAYMENTS = os.environ.get("CPG_ALLOW_LIVE_PAYMENTS", "0") == "1"
    PUBLIC_URL = os.environ.get("CPG_PUBLIC_URL", "")               # e.g. https://yourname.pythonanywhere.com (else the request's own address)
    PAY_MIN_RWF = 100
    PAY_MAX_RWF = 500000
    PAY_TTL_MIN = 30     # minutes a checkout page stays open

    # SMS through Africa's Talking. Username "sandbox" uses their free simulator (nothing is really delivered).
    # With no API key the app only writes the message to the log.
    AT_USERNAME = os.environ.get("CPG_AT_USERNAME", "sandbox")
    AT_API_KEY = os.environ.get("CPG_AT_API_KEY", "")
    AT_SENDER = os.environ.get("CPG_AT_SENDER", "")   # optional approved sender ID

    # Shown on the Contact page. Leave empty and the page says that no address is set up yet.
    CONTACT_EMAIL = os.environ.get("CPG_CONTACT_EMAIL", "")
    # Check for low balances while the app is running: minutes between checks, 0 = off (use send_alerts.py instead)
    ALERT_EVERY_MIN = float(os.environ.get("CPG_ALERT_EVERY_MIN", "0"))