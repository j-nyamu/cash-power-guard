"""Pages that are not about one feature: landing page, welcome, dashboard, settings, error pages."""
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .extensions import db
from .forecast import forecast
from .models import Access, Device, Meter, NotifyPref, User
from .utils import login_required, prefs_for, row_ns, tr

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


@bp.app_errorhandler(403)
def forbidden(_):
    return render_template("error.html", msg=tr("err_forbidden", g.get("lang", "en"))), 403


@bp.app_errorhandler(404)
def notfound(_):
    return render_template("error.html", msg="404"), 404
