"""Database tables. The same tables are written as plain SQL in the sql/ folder.

Times are stored as ISO text (UTC) so the file works the same on SQLite and MySQL.
"""
from sqlalchemy import CheckConstraint, UniqueConstraint

from .extensions import db


class Base(db.Model):
    __abstract__ = True

    def __getitem__(self, key):  # lets templates write user["name"] as well as user.name
        return getattr(self, key)


class User(Base):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(12), unique=True)           # shown to the person: CPG-000001
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(10), nullable=False, unique=True)  # one account per phone number
    email = db.Column(db.String(255), nullable=False)
    acct_type = db.Column(db.String(20), nullable=False)  # household / business / landlord
    pw_hash = db.Column(db.String(255), nullable=False)
    lang = db.Column(db.String(2), default="en")
    failed = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.String(32))
    created = db.Column(db.String(32), nullable=False)


class Meter(Base):
    __tablename__ = "meters"
    id = db.Column(db.Integer, primary_key=True)
    meter_no = db.Column(db.String(11), nullable=False, unique=True)  # one owner per meter number
    nickname = db.Column(db.String(30), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created = db.Column(db.String(32), nullable=False)


class Access(Base):
    """People the owner lets look at (or add to) a meter. Keyed by phone so an invite works before sign-up."""
    __tablename__ = "access"
    __table_args__ = (UniqueConstraint("meter_id", "phone"),
                      CheckConstraint("role IN ('view','add')"),
                      CheckConstraint("status IN ('pending','active')"))
    id = db.Column(db.Integer, primary_key=True)
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), nullable=False)
    phone = db.Column(db.String(10), nullable=False)
    role = db.Column(db.String(4), nullable=False)
    status = db.Column(db.String(8), nullable=False)
    invited_by = db.Column(db.Integer, nullable=False)
    created = db.Column(db.String(32), nullable=False)


class Entry(Base):
    """A purchase (money and units) or a meter reading (units left)."""
    __tablename__ = "entries"
    __table_args__ = (CheckConstraint("kind IN ('purchase','reading')"),)
    id = db.Column(db.Integer, primary_key=True)
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(8), nullable=False)
    day = db.Column(db.String(10), nullable=False)
    rwf = db.Column(db.Integer)
    kwh = db.Column(db.Float, nullable=False)
    estimated = db.Column(db.Integer, default=0)  # 1 = units were calculated, 0 = copied from the receipt
    created = db.Column(db.String(32), nullable=False)


class Otp(Base):
    """One-time codes. Only a hash is kept, never the code."""
    __tablename__ = "otps"
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(10), nullable=False)
    purpose = db.Column(db.String(10), nullable=False)  # signup / login / reset
    code_hash = db.Column(db.String(64), nullable=False)
    expires = db.Column(db.String(32), nullable=False)
    tries = db.Column(db.Integer, default=0)
    used = db.Column(db.Integer, default=0)
    created = db.Column(db.String(32), nullable=False)


class Device(Base):
    """'Remember this device': one row per browser. The browser keeps a random token, we keep its hash."""
    __tablename__ = "devices"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    label = db.Column(db.String(80))
    expires = db.Column(db.String(32), nullable=False)
    created = db.Column(db.String(32), nullable=False)


class Payment(Base):
    """A test-mode payment. Created when the person asks to buy, settled by the sandbox checkout page."""
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("status IN ('pending','paid','failed','cancelled')"),)
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(20), nullable=False, unique=True)   # shown to the person: CPG-1A2B3C4D5E6F
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rwf = db.Column(db.Integer, nullable=False)
    kwh = db.Column(db.Float, nullable=False)        # units quoted when the payment was started (an estimate)
    method = db.Column(db.String(10))                # mtn / airtel / card, set at checkout
    status = db.Column(db.String(10), nullable=False, default="pending")
    reason = db.Column(db.String(30))                # why it did not go through: wrong_pin, card_declined, expired, user
    entry_id = db.Column(db.Integer)                 # the purchase Entry created when it was paid
    created = db.Column(db.String(32), nullable=False)
    paid_at = db.Column(db.String(32))


class Alert(Base):
    """A low-balance alert already sent for a meter. Removed when the balance recovers so it can fire again."""
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("meter_id", "level"),)
    id = db.Column(db.Integer, primary_key=True)
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), nullable=False)
    level = db.Column(db.String(3), nullable=False)  # d3 = about 3 days left, d1 = about 1 day, out = ran out
    sent = db.Column(db.String(32), nullable=False)


class NotifyPref(Base):
    """Whether a person wants alerts and receipts by email and by SMS. No row means both are on."""
    __tablename__ = "notify_prefs"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    email_on = db.Column(db.Integer, nullable=False, default=1)
    sms_on = db.Column(db.Integer, nullable=False, default=1)


class OutageReport(Base):
    """Someone says the power is off (or back) in a district. Public and anonymous; user_id is kept if they were logged in."""
    __tablename__ = "outage_reports"
    __table_args__ = (CheckConstraint("kind IN ('outage','restored')"),)
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(8), nullable=False)
    district = db.Column(db.String(30), nullable=False)
    area = db.Column(db.String(120))
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer)
    created = db.Column(db.String(32), nullable=False)


class MeterSetting(Base):
    """What the owner told us about a meter. daily_use = kWh a day they say they use, so a first forecast
    (and low-balance alerts) can work before there are two meter readings."""
    __tablename__ = "meter_settings"
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), primary_key=True)
    daily_use = db.Column(db.Float, nullable=False)


class Activity(Base):
    """The log the owner sees: who viewed, added, was invited, left."""
    __tablename__ = "activity"
    id = db.Column(db.Integer, primary_key=True)
    meter_id = db.Column(db.Integer, db.ForeignKey("meters.id"), nullable=False)
    user_id = db.Column(db.Integer)
    action = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(255))
    ts = db.Column(db.String(32), nullable=False)
