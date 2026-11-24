"""Test-mode payments: buy cash power on a sandbox checkout page.

No real money moves and no real token is made. The page behaves like a payment provider's test mode:
a mobile money PIN or a test card number decides whether the payment succeeds, so every outcome can be shown.

A real provider such as Flutterwave would replace only the checkout step: create a hosted payment link, send the
person there, and when they come back ask the provider if the payment really went through before calling settle().
Everything else (the Payment row, settle(), the receipt, the history) stays the same.
"""
import re
import secrets
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import update

from .extensions import db
from .forecast import bought_this_month_before, calibration, kwh_from_rwf
from .models import Entry, Meter, Payment, User
from .utils import get_meter, iso, log_act, login_required, normalize_phone, notify, now, parse, tr

bp = Blueprint("payments", __name__)

# Sandbox rules. They are also printed on the checkout page, so anyone can try every outcome.
TEST_PIN = "1234"
TEST_CARDS = {"4242424242424242": "ok", "4000000000000002": "declined"}
METHODS = ("mtn", "airtel", "card")
PREFIXES = {"mtn": ("078", "079"), "airtel": ("072", "073")}
RETRY = {"invalid_phone", "invalid_card"}  # a form mistake: let the person fix it, do not fail the payment


def quote(mid, rwf):
    """Units `rwf` would buy today on this meter. Same estimate as the 'log a purchase' form."""
    used = bought_this_month_before(mid, date.today().isoformat())
    return round(kwh_from_rwf(rwf, used) * calibration(mid)[0], 2)


def sandbox_charge(method, form):
    """Pretend to charge. Returns (ok, reason). Nothing from the form is stored or logged.

    Real card numbers are never accepted: only the test cards above.
    """
    if method == "card":
        number = re.sub(r"\D", "", form.get("card", ""))
        if number not in TEST_CARDS:
            return False, "invalid_card"
        return (True, "") if TEST_CARDS[number] == "ok" else (False, "card_declined")
    phone = normalize_phone(form.get("phone"))
    if not phone or not phone.startswith(PREFIXES[method]):
        return False, "invalid_phone"
    if not secrets.compare_digest(form.get("pin", ""), TEST_PIN):
        return False, "wrong_pin"
    return True, ""


def _own(ref):
    """The person's own payment. Other people's references look like they do not exist."""
    p = Payment.query.filter_by(ref=ref).first()
    if not p or p.user_id != g.user.id:
        abort(404)
    return p


def _expire(p):
    if p.status == "pending" and now() - parse(p.created) > timedelta(minutes=current_app.config["PAY_TTL_MIN"]):
        p.status, p.reason = "cancelled", "expired"
        db.session.commit()


def settle(p, method):
    """Mark a payment paid and add the purchase to the meter's log.

    Safe to call twice: the update below only matches a payment that is still pending, so only one call credits the meter.
    """
    claimed = db.session.execute(update(Payment).where(Payment.id == p.id, Payment.status == "pending")
                                 .values(status="paid", method=method, paid_at=iso(now())))
    if claimed.rowcount != 1:
        db.session.rollback()
        return False
    db.session.refresh(p)
    entry = Entry(meter_id=p.meter_id, user_id=p.user_id, kind="purchase", day=date.today().isoformat(),
                  rwf=p.rwf, kwh=p.kwh, estimated=1, created=iso(now()))
    db.session.add(entry)
    db.session.flush()
    p.entry_id = entry.id
    log_act(p.meter_id, "paid", f"{p.kwh:g} kWh, {p.rwf} RWF")
    db.session.commit()
    _receipt(p)
    return True


def _receipt(p):
    user, meter = db.session.get(User, p.user_id), db.session.get(Meter, p.meter_id)
    lang = user.lang or "en"
    text = (tr("pay_mail_body", lang).replace("{ref}", p.ref).replace("{rwf}", f"{p.rwf:,}")
            .replace("{meter}", meter.nickname).replace("{kwh}", f"{p.kwh:g}"))
    notify(user, tr("pay_mail_subject", lang), text)


# ---------------------------------------------------------------- routes
@bp.route("/meter/<int:mid>/buy", methods=["POST"])
@login_required
def start(mid):
    """Create a pending payment and go to the checkout page."""
    m, role = get_meter(mid)
    if role not in ("owner", "add"):
        abort(403)
    cfg = current_app.config
    try:
        rwf = int(request.form.get("rwf", "0"))
    except ValueError:
        rwf = 0
    if not cfg["PAY_MIN_RWF"] <= rwf <= cfg["PAY_MAX_RWF"]:
        msg = tr("pay_err_amount", g.lang).replace("{min}", f"{cfg['PAY_MIN_RWF']:,}").replace("{max}", f"{cfg['PAY_MAX_RWF']:,}")
        flash(msg, "err")
        return redirect(url_for("meters.meter_page", mid=mid))
    p = Payment(ref="CPG-" + secrets.token_hex(6).upper(), meter_id=mid, user_id=g.user.id, rwf=rwf,
                kwh=quote(mid, rwf), status="pending", created=iso(now()))
    db.session.add(p)
    db.session.commit()
    return redirect(url_for("payments.checkout", ref=p.ref))


@bp.route("/meter/<int:mid>/quote")
@login_required
def quote_api(mid):
    """Live estimate for the buy form: how many kWh an amount buys."""
    m, role = get_meter(mid)
    if role not in ("owner", "add"):
        abort(403)
    try:
        rwf = int(request.args.get("rwf", "0"))
    except ValueError:
        rwf = 0
    if rwf <= 0 or rwf > current_app.config["PAY_MAX_RWF"]:
        return jsonify(ok=False)
    return jsonify(ok=True, kwh=quote(mid, rwf))


@bp.route("/pay/<ref>")
@login_required
def checkout(ref):
    """The checkout page while pending; the receipt or the reason once it is settled."""
    p = _own(ref)
    _expire(p)
    return render_template("pay.html", p=p, m=db.session.get(Meter, p.meter_id), pin=TEST_PIN)


@bp.route("/pay/<ref>/confirm", methods=["POST"])
@login_required
def confirm(ref):
    p = _own(ref)
    _expire(p)
    if p.status != "pending":
        return redirect(url_for("payments.checkout", ref=ref))
    method = request.form.get("method")
    if method not in METHODS:
        flash(tr("pay_err_method", g.lang), "err")
        return redirect(url_for("payments.checkout", ref=ref))
    ok, reason = sandbox_charge(method, request.form)
    if reason in RETRY:
        flash(tr("pay_err_phone" if reason == "invalid_phone" else "pay_err_card", g.lang), "err")
    elif ok:
        settle(p, method)
    else:
        p.status, p.method, p.reason = "failed", method, reason
        log_act(p.meter_id, "pay_failed", f"{p.rwf} RWF")
        db.session.commit()
    return redirect(url_for("payments.checkout", ref=ref))


@bp.route("/pay/<ref>/cancel", methods=["POST"])
@login_required
def cancel(ref):
    p = _own(ref)
    if p.status == "pending":
        p.status, p.reason = "cancelled", "user"
        db.session.commit()
        flash(tr("pay_cancelled", g.lang), "ok")
    return redirect(url_for("meters.meter_page", mid=p.meter_id))
