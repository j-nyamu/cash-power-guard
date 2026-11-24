"""Outage reports: anyone can say where the power is off, and when it is back. No account needed."""
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .extensions import db
from .models import OutageReport
from .utils import iso, now, parse, row_ns, tr

bp = Blueprint("outages", __name__)

DISTRICTS = [
    "Gasabo", "Kicukiro", "Nyarugenge",                                                                    # Kigali
    "Bugesera", "Gatsibo", "Kayonza", "Kirehe", "Ngoma", "Nyagatare", "Rwamagana",                         # East
    "Burera", "Gakenke", "Gicumbi", "Musanze", "Rulindo",                                                  # North
    "Gisagara", "Huye", "Kamonyi", "Muhanga", "Nyamagabe", "Nyanza", "Nyaruguru", "Ruhango",               # South
    "Karongi", "Ngororero", "Nyabihu", "Nyamasheke", "Rubavu", "Rusizi", "Rutsiro",                       # West
]
CAT = timedelta(hours=2)  # Rwanda time (UTC+2, no daylight saving)


def _recent():
    since = iso(now() - timedelta(hours=24))
    rows = OutageReport.query.filter(OutageReport.created >= since).order_by(OutageReport.id.desc()).limit(15).all()
    return [row_ns(r, when=(parse(r.created) + CAT).strftime("%d/%m %H:%M")) for r in rows]


def _report(kind):
    if request.method == "POST":
        district = request.form.get("district", "")
        last = session.get("last_report")
        if district not in DISTRICTS:
            flash(tr("out_err_district", g.lang), "err")
        elif last and (now() - parse(last)).total_seconds() < 60:  # a small brake against spam
            flash(tr("out_err_wait", g.lang), "err")
        else:
            db.session.add(OutageReport(kind=kind, district=district, area=request.form.get("area", "").strip()[:120],
                                        note=request.form.get("note", "").strip()[:255],
                                        user_id=g.user.id if g.user else None, created=iso(now())))
            db.session.commit()
            session["last_report"] = iso(now())
            flash(tr("out_thanks", g.lang), "ok")
            return redirect(url_for(f"outages.report_{kind}"))
    return render_template("outage.html", kind=kind, districts=DISTRICTS, recent=_recent())


@bp.route("/report-outage", methods=["GET", "POST"])
def report_outage():
    return _report("outage")


@bp.route("/report-restored", methods=["GET", "POST"])
def report_restored():
    return _report("restored")
