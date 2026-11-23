"""Meters: add a meter, open its page (balance, forecast, history, people and log for the owner)."""
import re
from datetime import date

from flask import Blueprint, current_app, flash, g, redirect, render_template, url_for, request
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .forecast import calibration, forecast, recommend
from .models import Access, Activity, Entry, Meter, Payment, User
from .utils import (get_meter, iso, log_act, login_required, now, parse, row_ns, tr, valid_meter)

bp = Blueprint("meters", __name__)


@bp.route("/meters/add", methods=["POST"])
@login_required
def add_meter():
    meter = re.sub(r"\s", "", request.form.get("meter_no", ""))
    nick = request.form.get("nickname", "").strip()[:30] or "Meter"
    if not valid_meter(meter):
        flash(tr("err_meter_invalid", g.lang), "err")
    else:
        try:
            db.session.add(Meter(meter_no=meter, nickname=nick, owner_id=g.user.id, created=iso(now())))
            db.session.commit()
            flash(tr("added_ok", g.lang), "ok")
        except IntegrityError:
            db.session.rollback()
            flash(tr("err_meter_exists", g.lang), "err")
    return redirect(url_for("views.dashboard"))


@bp.route("/meter/<int:mid>")
@login_required
def meter_page(mid):
    m, role = get_meter(mid)
    if role != "owner":  # record views, at most once per 10 minutes per person
        last = (Activity.query.filter_by(meter_id=mid, user_id=g.user.id, action="viewed")
                .order_by(Activity.id.desc()).first())
        if not last or (now() - parse(last.ts)).total_seconds() > 600:
            log_act(mid, "viewed")
            db.session.commit()
    entries = [row_ns(e, who=who) for e, who in
               db.session.query(Entry, User.name).join(User, User.id == Entry.user_id)
               .filter(Entry.meter_id == mid).order_by(Entry.day.desc(), Entry.id.desc()).all()]
    people = acts = None
    if role == "owner":
        people = [row_ns(a, name=name) for a, name in
                  db.session.query(Access, User.name).outerjoin(User, User.phone == Access.phone)
                  .filter(Access.meter_id == mid).order_by(Access.id).all()]
        acts = [row_ns(a, name=name) for a, name in
                db.session.query(Activity, User.name).outerjoin(User, User.id == Activity.user_id)
                .filter(Activity.meter_id == mid).order_by(Activity.id.desc()).limit(25).all()]
    pays = None
    if role in ("owner", "add"):  # payments are money: the owner sees all of them, a contributor only their own
        q = Payment.query.filter_by(meter_id=mid)
        if role != "owner":
            q = q.filter_by(user_id=g.user.id)
        pays = q.order_by(Payment.id.desc()).limit(10).all()
    days = request.args.get("days", type=int)  # "cover me for N days"; empty = the rest of this month
    rec = recommend(mid, min(max(days, 1), 90) if days else None)
    cfg = current_app.config
    return render_template("meter.html", m=m, role=role, entries=entries, fc=forecast(mid), calib=calibration(mid),
                           people=people, acts=acts, today=date.today().isoformat(), rec=rec, pays=pays,
                           pay_min=cfg["PAY_MIN_RWF"], pay_max=cfg["PAY_MAX_RWF"])
