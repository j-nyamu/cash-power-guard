"""Cash Power Guard: builds the Flask app and connects the parts.

    auth.py      accounts, OTP, login, remember device
    meters.py    meters and the meter page
    members.py   inviting people to see a meter
    usage.py     purchases and readings
    forecast.py  the calculation (units bought, days left, how much to buy)
    payments.py  payments: Flutterwave hosted checkout, or the built-in demo checkout
    alerts.py    low-balance alerts by email and SMS (run by send_alerts.py)
    outages.py   outage and "power restored" reports
    views.py     landing page, dashboard, settings
"""
import secrets
import threading
import time

from flask import Flask, abort, g, request, session

from config import Config

from .extensions import db


def create_app(overrides=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config.update(overrides or {})

    db.init_app(app)

    from . import auth, members, meters, models, outages, payments, usage, views  # noqa: F401  (models: so the tables are known)
    from .utils import LANGS, fmt_phone, csrf_token, tr

    for part in (views, auth, meters, usage, members, payments, outages):
        app.register_blueprint(part.bp)

    with app.app_context():
        db.create_all()

    every = float(app.config.get("ALERT_EVERY_MIN") or 0)
    if every > 0:
        threading.Thread(target=_alert_loop, args=(app, every * 60), daemon=True, name="alerts").start()

    @app.before_request
    def before():
        if request.method == "POST" and request.endpoint != "payments.flw_webhook":   # the webhook is checked by its own secret
            if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
                abort(400)
        q = request.args.get("lang")
        if q in LANGS:
            session["lang"] = q
        g.user = auth.load_user()
        g.lang = session.get("lang") or (g.user.lang if g.user else "en")
        if q in LANGS and g.user and g.user.lang != q:
            g.user.lang = q
            db.session.commit()

    @app.context_processor
    def inject():
        lang = g.get("lang", "en")
        return dict(t=lambda k: tr(k, lang), lang=lang, langs=LANGS, user=g.get("user"),
                    csrf=csrf_token, fmt_phone=fmt_phone, vat=app.config["VAT_PCT"],
                    otp_required=app.config["OTP_REQUIRED"])

    return app


def _alert_loop(app, seconds):
    """Background check: send due low-balance alerts every `seconds` while the app runs (CPG_ALERT_EVERY_MIN)."""
    from .alerts import run_alerts
    time.sleep(15)  # let the app finish starting
    while True:
        try:
            with app.app_context():
                tried, reached = run_alerts()
                if tried:
                    app.logger.info("alerts: %s of %s message(s) delivered", reached, tried)
        except Exception:  # never let a failed check stop the loop
            app.logger.exception("alert check failed")
        time.sleep(seconds)