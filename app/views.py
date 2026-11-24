"""Pages that are not about one feature: landing page, welcome, dashboard, settings, error pages."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from .extensions import db
from .forecast import forecast
from .models import Access, Device, Meter, NotifyPref, User
from .utils import iso, login_required, notify, now, parse, prefs_for, row_ns, tr

bp = Blueprint("views", __name__)


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("views.dashboard"))
    return render_template("landing.html")


@bp.route("/welcome")
@login_required
def welcome():
    return render_template("welcome.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    u = g.user
    own = Meter.query.filter_by(owner_id=u.id).order_by(Meter.id).all()
    shared = [row_ns(m, role=role, owner_name=owner) for m, role, owner in
              db.session.query(Meter, Access.role, User.name).join(Access, Access.meter_id == Meter.id)
              .join(User, User.id == Meter.owner_id).filter(Access.phone == u.phone, Access.status == "active").all()]
    invites = [row_ns(a, nickname=nick, owner_name=owner) for a, nick, owner in
               db.session.query(Access, Meter.nickname, User.name).join(Meter, Meter.id == Access.meter_id)
               .join(User, User.id == Access.invited_by).filter(Access.phone == u.phone, Access.status == "pending").all()]
    fc = {m.id: forecast(m.id) for m in list(own) + list(shared)}
    return render_template("dashboard.html", own=own, shared=shared, invites=invites, fc=fc)


@bp.route("/settings")
@login_required
def settings():
    devs = Device.query.filter_by(user_id=g.user.id).order_by(Device.id.desc()).all()
    return render_template("settings.html", devs=devs, current=session.get("device"), prefs=prefs_for(g.user))


@bp.route("/settings/notify", methods=["POST"])
@login_required
def notify_settings():
    """Choose whether alerts and receipts come by email, by SMS, or both."""
    row = db.session.get(NotifyPref, g.user.id)
    if not row:
        row = NotifyPref(user_id=g.user.id)
        db.session.add(row)
    row.email_on = 1 if request.form.get("email") else 0
    row.sms_on = 1 if request.form.get("sms") else 0
    db.session.commit()
    flash(tr("added_ok", g.lang), "ok")
    return redirect(url_for("views.settings"))


@bp.route("/settings/test-alert", methods=["POST"])
@login_required
def test_alert():
    """Send one sample alert to the signed-in person, so email and SMS can be checked without waiting for a low balance."""
    last = session.get("last_test")
    if last and (now() - parse(last)).total_seconds() < 30:  # a small brake against SMS spam
        flash(tr("notify_test_wait", g.lang), "err")
    else:
        session["last_test"] = iso(now())
        sent = notify(g.user, tr("notify_test_subject", g.lang), tr("notify_test_text", g.lang))
        ok = any(sent.values())
        flash(tr("notify_test_ok" if ok else "notify_test_none", g.lang), "ok" if ok else "err")
    return redirect(url_for("views.settings"))


# Terms, Privacy and Contact: plain pages whose paragraphs live in the translation files (terms_1, terms_2 ...).
LEGAL_PARAGRAPHS = {"terms": 6, "privacy": 7, "contact": 2}


def _legal(page):
    return render_template("legal.html", page=page, count=LEGAL_PARAGRAPHS[page],
                           email=current_app.config["CONTACT_EMAIL"])


@bp.route("/terms")
def terms():
    return _legal("terms")


@bp.route("/privacy")
def privacy():
    return _legal("privacy")


@bp.route("/contact")
def contact():
    return _legal("contact")


@bp.app_errorhandler(403)
def forbidden(_):
    return render_template("error.html", msg=tr("err_forbidden", g.get("lang", "en"))), 403


@bp.app_errorhandler(404)
def notfound(_):
    return render_template("error.html", msg="404"), 404
