"""Low-balance alerts by email and SMS. Run once a day with `python send_alerts.py`, or set CPG_ALERT_EVERY_MIN to check while the app runs.

Three levels: about 3 days left (d3), about 1 day left (d1) and ran out (out). Each level is sent once per meter.
When the balance recovers (a top-up or a new reading) its row is removed, so the alert can fire again next time.
The owner and everyone the meter is shared with get the same message, in their own language.
"""
from .extensions import db
from .forecast import forecast, recommend
from .models import Access, Alert, Meter, User
from .utils import iso, notify, now, tr

LEVELS = ("d3", "d1", "out")  # least to most serious


def levels_for(fc):
    """Which alert levels apply to this forecast, least serious first."""
    due = []
    if fc["days"] <= 3:
        due.append("d3")
    if fc["days"] <= 1:
        due.append("d1")
    if fc["balance"] <= 0:
        due.append("out")
    return due


def people_for(meter):
    owner = db.session.get(User, meter.owner_id)
    phones = [a.phone for a in Access.query.filter_by(meter_id=meter.id, status="active")]
    members = User.query.filter(User.phone.in_(phones)).all() if phones else []
    return [owner] + members


def alert_text(level, meter, fc, rec, lang):
    """(subject, text) for one person, in their language."""
    text = (tr("alert_" + level, lang).replace("{meter}", meter.nickname).replace("{days}", str(max(round(fc["days"]), 1)))
            .replace("{date}", fc["buy_by"].strftime("%d/%m/%Y")))
    if rec and rec["rwf"]:
        text += " " + tr("alert_suggest", lang).replace("{rwf}", f"{rec['rwf']:,}")
    return tr("alert_subject", lang).replace("{meter}", meter.nickname), text


def run_alerts():
    """Check every meter and send the alerts that are due.

    Returns (messages_tried, people_reached). An alert is only recorded as sent when at least one person was reached,
    so if the email or SMS service is down the same alert is tried again on the next run.
    """
    tried = reached = 0
    for m in Meter.query.order_by(Meter.id).all():
        fc = forecast(m.id)
        if not fc:
            continue
        due = levels_for(fc)
        have = {a.level: a for a in Alert.query.filter_by(meter_id=m.id)}
        for level, row in have.items():
            if level not in due:
                db.session.delete(row)  # balance recovered: this alert may fire again later
        new = [lvl for lvl in due if lvl not in have]
        if new:
            worst, rec, got = new[-1], recommend(m.id), 0
            for u in people_for(m):
                subject, text = alert_text(worst, m, fc, rec, u.lang or "en")
                tried += 1
                if any(notify(u, subject, text).values()):
                    got += 1
            reached += got
            if got:
                for level in new:
                    db.session.add(Alert(meter_id=m.id, level=level, sent=iso(now())))
        db.session.commit()
    return tried, reached