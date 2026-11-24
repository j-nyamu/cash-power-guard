"""Unit tests for payments.py (test-mode checkout), alerts.py, notifications, the recommended purchase and outages.py."""
import io
import json
import unittest
from datetime import date, timedelta
from unittest import mock

from helpers import AppTestCase, db

from app.alerts import levels_for, run_alerts
from app.forecast import forecast, kwh_from_rwf, recommend, rwf_from_kwh
from app.models import Access, Activity, Alert, Entry, NotifyPref, OutageReport, Payment
from app.payments import settle
from app.utils import iso, notify, now, send_sms


def day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def reading(meter, user, n, kwh):
    db.session.add(Entry(meter_id=meter.id, user_id=user.id, kind="reading", day=day(n), kwh=kwh, created=iso(now())))
    db.session.commit()


# ---------------------------------------------------------------- the reverse tariff calculation
class RwfFromKwhTests(AppTestCase):
    def test_paying_the_result_buys_at_least_the_units(self):
        for kwh, used in [(5, 0), (27.93, 0), (80, 0), (10, 45), (200, 10), (1, 500)]:
            rwf = rwf_from_kwh(kwh, used)
            self.assertGreaterEqual(kwh_from_rwf(rwf, used), kwh, (kwh, used))
            self.assertLess(kwh_from_rwf(rwf - 2, used), kwh + 0.01, (kwh, used))  # and it is not wildly too much

    def test_starting_deeper_in_the_month_costs_more(self):
        self.assertGreater(rwf_from_kwh(20, 40), rwf_from_kwh(20, 0))

    def test_zero_units_cost_nothing(self):
        self.assertEqual(rwf_from_kwh(0), 0)


# ---------------------------------------------------------------- recommended purchase
class RecommendTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.c, self.u, self.m = self.signed_in()

    def test_no_recommendation_without_a_forecast(self):
        self.assertIsNone(recommend(self.m.id))

    def test_recommends_an_amount_for_the_days_asked(self):
        reading(self.m, self.u, 8, 20)
        reading(self.m, self.u, 4, 12)          # 2 kWh a day, about 4 kWh left now
        r = recommend(self.m.id, 10)
        self.assertFalse(r["enough"])
        self.assertEqual(r["days"], 10)
        self.assertAlmostEqual(r["kwh"], 16, delta=0.2)   # 10 days * 2 kWh - 4 kWh
        self.assertEqual(r["rwf"] % 100, 0)               # rounded up to a payable amount
        self.assertGreaterEqual(kwh_from_rwf(r["rwf"]), r["kwh"])

    def test_enough_when_balance_covers_the_days(self):
        reading(self.m, self.u, 8, 100)
        reading(self.m, self.u, 4, 92)          # 2 kWh a day, about 84 left
        r = recommend(self.m.id, 5)
        self.assertTrue(r["enough"])
        self.assertEqual(r["rwf"], 0)

    def test_meter_page_shows_it_and_survives_odd_days_values(self):
        reading(self.m, self.u, 8, 20)
        reading(self.m, self.u, 4, 12)
        self.assertIn(b"Recommended purchase", self.c.get(f"/meter/{self.m.id}?days=10").data)
        for bad in ("abc", "0", "-5", "9999"):
            self.assertEqual(self.c.get(f"/meter/{self.m.id}?days={bad}").status_code, 200, bad)


# ---------------------------------------------------------------- test-mode payments
class PaymentTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.c, self.u, self.m = self.signed_in()
        self.buy = f"/meter/{self.m.id}/buy"

    def start(self, rwf="5000", client=None, url=None):
        r = self.post(client or self.c, url or self.buy, rwf=rwf)
        return r, Payment.query.order_by(Payment.id.desc()).first()

    def pay(self, p, **data):
        return self.post(self.c, f"/pay/{p.ref}/confirm", **data)

    def test_start_creates_a_pending_payment_with_a_quote(self):
        r, p = self.start()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith(f"/pay/{p.ref}"))
        self.assertEqual((p.status, p.rwf, p.meter_id, p.user_id), ("pending", 5000, self.m.id, self.u.id))
        self.assertEqual(p.kwh, 27.93)                     # same estimate as the "log a purchase" form
        self.assertEqual(Entry.query.count(), 0)           # nothing is credited before payment

    def test_amount_limits_and_junk_are_rejected(self):
        for bad in ("0", "-5", "50", "abc", "", "99999999"):
            self.start(bad)
        self.assertEqual(Payment.query.count(), 0)

    def test_checkout_page_says_test_mode(self):
        _, p = self.start()
        html = self.c.get(f"/pay/{p.ref}").data
        self.assertIn(b"TEST MODE", html)
        self.assertIn(p.ref.encode(), html)

    def test_mobile_money_success_credits_the_meter_once(self):
        _, p = self.start()
        r = self.pay(p, method="mtn", phone="0788123456", pin="1234")
        self.assertEqual(r.status_code, 302)
        p = db.session.get(Payment, p.id)
        db.session.refresh(p)
        self.assertEqual((p.status, p.method), ("paid", "mtn"))
        e = Entry.query.one()
        self.assertEqual((e.kind, e.rwf, e.kwh, e.estimated, e.meter_id), ("purchase", 5000, p.kwh, 1, self.m.id))
        self.assertEqual(p.entry_id, e.id)
        self.assertTrue(Activity.query.filter_by(meter_id=self.m.id, action="paid").count() == 1)
        # paying again (double click, back button) must not credit twice
        self.pay(p, method="mtn", phone="0788123456", pin="1234")
        self.assertEqual(Entry.query.count(), 1)

    def test_settle_only_works_once(self):
        _, p = self.start()
        self.assertTrue(settle(p, "mtn"))
        self.assertFalse(settle(p, "mtn"))
        self.assertEqual(Entry.query.count(), 1)

    def test_wrong_pin_fails_and_credits_nothing(self):
        _, p = self.start()
        self.pay(p, method="airtel", phone="0728123456", pin="0000")
        db.session.refresh(p)
        self.assertEqual((p.status, p.reason), ("failed", "wrong_pin"))
        self.assertEqual(Entry.query.count(), 0)
        self.assertIn(b"Wrong PIN", self.c.get(f"/pay/{p.ref}").data)

    def test_a_failed_payment_cannot_be_paid_later(self):
        _, p = self.start()
        self.pay(p, method="mtn", phone="0788123456", pin="0000")
        self.pay(p, method="mtn", phone="0788123456", pin="1234")
        db.session.refresh(p)
        self.assertEqual(p.status, "failed")
        self.assertEqual(Entry.query.count(), 0)

    def test_network_must_match_the_number(self):
        _, p = self.start()
        self.pay(p, method="mtn", phone="0728123456", pin="1234")   # an Airtel number on MTN
        db.session.refresh(p)
        self.assertEqual(p.status, "pending")                       # a form mistake: the person can fix it
        self.pay(p, method="mtn", phone="0788123456", pin="1234")
        db.session.refresh(p)
        self.assertEqual(p.status, "paid")

    def test_cards_only_test_numbers_work(self):
        _, ok = self.start()
        self.pay(ok, method="card", card="4242 4242 4242 4242")
        db.session.refresh(ok)
        self.assertEqual(ok.status, "paid")
        _, declined = self.start()
        self.pay(declined, method="card", card="4000000000000002")
        db.session.refresh(declined)
        self.assertEqual((declined.status, declined.reason), ("failed", "card_declined"))
        _, real = self.start()
        self.pay(real, method="card", card="5555 5555 5555 4444")    # looks like a real card: never accepted
        db.session.refresh(real)
        self.assertEqual(real.status, "pending")
        self.assertEqual(Entry.query.count(), 1)

    def test_unknown_method_is_rejected(self):
        _, p = self.start()
        self.pay(p, method="bitcoin")
        db.session.refresh(p)
        self.assertEqual(p.status, "pending")

    def test_cancel(self):
        _, p = self.start()
        r = self.post(self.c, f"/pay/{p.ref}/cancel")
        self.assertEqual(r.status_code, 302)
        db.session.refresh(p)
        self.assertEqual((p.status, p.reason), ("cancelled", "user"))
        self.pay(p, method="mtn", phone="0788123456", pin="1234")
        self.assertEqual(Entry.query.count(), 0)

    def test_old_pending_payment_expires(self):
        _, p = self.start()
        p.created = iso(now() - timedelta(minutes=31))
        db.session.commit()
        self.c.get(f"/pay/{p.ref}")
        db.session.refresh(p)
        self.assertEqual((p.status, p.reason), ("cancelled", "expired"))
        self.pay(p, method="mtn", phone="0788123456", pin="1234")
        self.assertEqual(Entry.query.count(), 0)

    def test_someone_else_cannot_see_or_pay_it(self):
        _, p = self.start()
        b, _, _ = self.signed_in("Bob Mugisha", "0733555111", "09999999999")
        self.assertEqual(b.get(f"/pay/{p.ref}").status_code, 404)
        self.assertEqual(self.post(b, f"/pay/{p.ref}/confirm", method="mtn", phone="0733555111", pin="1234").status_code, 404)
        self.assertEqual(self.post(b, f"/pay/{p.ref}/cancel").status_code, 404)
        self.assertEqual(Entry.query.count(), 0)

    def test_logged_out_goes_to_login(self):
        _, p = self.start()
        out = self.app.test_client()
        self.assertEqual(out.get(f"/pay/{p.ref}").status_code, 302)
        self.assertEqual(out.get(f"/meter/{self.m.id}/quote?rwf=1000").status_code, 302)

    def test_view_only_member_cannot_buy_but_contributor_can(self):
        b, bob, _ = self.signed_in("Bob Mugisha", "0733555111", "09999999999")
        db.session.add(Access(meter_id=self.m.id, phone=bob.phone, role="view", status="active",
                              invited_by=self.u.id, created=iso(now())))
        db.session.commit()
        self.assertEqual(self.post(b, self.buy, rwf="5000").status_code, 403)
        self.assertEqual(b.get(f"/meter/{self.m.id}/quote?rwf=1000").status_code, 403)
        Access.query.filter_by(phone=bob.phone).update({"role": "add"})
        db.session.commit()
        self.assertEqual(self.post(b, self.buy, rwf="5000").status_code, 302)

    def test_payment_history_privacy(self):
        _, mine = self.start()
        b, bob, _ = self.signed_in("Bob Mugisha", "0733555111", "09999999999")
        db.session.add(Access(meter_id=self.m.id, phone=bob.phone, role="add", status="active",
                              invited_by=self.u.id, created=iso(now())))
        db.session.commit()
        self.post(b, self.buy, rwf="2000")
        theirs = Payment.query.filter_by(user_id=bob.id).one()
        owner_page = self.c.get(f"/meter/{self.m.id}").data
        self.assertIn(mine.ref.encode(), owner_page)
        self.assertIn(theirs.ref.encode(), owner_page)          # the owner sees every payment on their meter
        bob_page = b.get(f"/meter/{self.m.id}").data
        self.assertIn(theirs.ref.encode(), bob_page)
        self.assertNotIn(mine.ref.encode(), bob_page)           # a contributor sees only their own

    def test_quote_api(self):
        r = self.c.get(f"/meter/{self.m.id}/quote?rwf=5000").get_json()
        self.assertEqual((r["ok"], r["kwh"]), (True, 27.93))
        for bad in ("0", "-1", "abc", "99999999"):
            self.assertFalse(self.c.get(f"/meter/{self.m.id}/quote?rwf={bad}").get_json()["ok"], bad)

    def test_receipt_goes_out_by_email_and_sms(self):
        with mock.patch("app.utils.send_email", return_value=True) as mail, mock.patch("app.utils.send_sms", return_value=True) as sms:
            _, p = self.start()
            self.pay(p, method="mtn", phone="0788123456", pin="1234")
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(sms.call_count, 1)
        self.assertIn(p.ref, mail.call_args[0][2])
        self.assertIn("TEST", mail.call_args[0][2])

    def test_a_broken_message_service_does_not_break_the_payment(self):
        with mock.patch("app.utils.send_email", side_effect=RuntimeError("smtp down")), \
                mock.patch("app.utils.send_sms", side_effect=RuntimeError("sms down")):
            _, p = self.start()
            self.pay(p, method="mtn", phone="0788123456", pin="1234")
        db.session.refresh(p)
        self.assertEqual(p.status, "paid")
        self.assertEqual(Entry.query.count(), 1)

    def test_csrf_is_required(self):
        _, p = self.start()
        r = self.c.post(f"/pay/{p.ref}/confirm", data=dict(method="mtn", phone="0788123456", pin="1234"))
        self.assertEqual(r.status_code, 400)


# ---------------------------------------------------------------- messages: SMS provider and notify()
class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, *a):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class MessageTests(AppTestCase):
    def test_sms_without_a_key_only_logs(self):
        with mock.patch("urllib.request.urlopen") as op:
            self.assertFalse(send_sms("0788123456", "hi"))
            op.assert_not_called()

    def test_sms_goes_to_africas_talking_with_an_international_number(self):
        self.app.config.update(AT_API_KEY="k", AT_USERNAME="sandbox")
        ok = {"SMSMessageData": {"Recipients": [{"statusCode": 101, "number": "+250788123456"}]}}
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(ok)) as op:
            self.assertTrue(send_sms("0788123456", "hello"))
        req = op.call_args[0][0]
        self.assertIn("api.sandbox.africastalking.com", req.full_url)
        self.assertEqual(req.get_header("Apikey"), "k")
        self.assertIn(b"%2B250788123456", req.data)

    def test_sms_rejected_or_network_error_returns_false(self):
        self.app.config.update(AT_API_KEY="k")
        bad = {"SMSMessageData": {"Recipients": [{"statusCode": 403}]}}
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(bad)):
            self.assertFalse(send_sms("0788123456", "x"))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertFalse(send_sms("0788123456", "x"))

    def test_notify_follows_the_person_s_choices(self):
        u = self.make_user()
        with mock.patch("app.utils.send_email", return_value=True) as mail, mock.patch("app.utils.send_sms", return_value=True) as sms:
            self.assertEqual(notify(u, "s", "t"), {"email": True, "sms": True})   # no saved choice = both on
            db.session.add(NotifyPref(user_id=u.id, email_on=0, sms_on=1))
            db.session.commit()
            self.assertEqual(notify(u, "s", "t"), {"sms": True})
            NotifyPref.query.update({"email_on": 1, "sms_on": 0})
            db.session.commit()
            self.assertEqual(notify(u, "s", "t"), {"email": True})
        self.assertEqual((mail.call_count, sms.call_count), (2, 2))

    def test_settings_page_saves_the_choices(self):
        c, u, _ = self.signed_in()
        self.post(c, "/settings/notify", email="1")          # SMS box left unticked
        row = db.session.get(NotifyPref, u.id)
        self.assertEqual((row.email_on, row.sms_on), (1, 0))
        self.assertIn(b"Alerts and receipts", c.get("/settings").data)
        self.post(c, "/settings/notify")
        db.session.refresh(row)
        self.assertEqual((row.email_on, row.sms_on), (0, 0))


# ---------------------------------------------------------------- low-balance alerts
class AlertTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.c, self.u, self.m = self.signed_in()
        p = mock.patch("app.utils.send_email", return_value=True)
        s = mock.patch("app.utils.send_sms", return_value=True)
        self.mail, self.sms = p.start(), s.start()
        self.addCleanup(p.stop)
        self.addCleanup(s.stop)

    def low(self, left_after_4_days):
        """Two readings that give 2.25 kWh a day (or so) and the stated balance 4 days ago."""
        reading(self.m, self.u, 8, 20)
        reading(self.m, self.u, 4, left_after_4_days)

    def test_levels(self):
        self.assertEqual(levels_for(dict(days=9, balance=18)), [])
        self.assertEqual(levels_for(dict(days=3, balance=6)), ["d3"])
        self.assertEqual(levels_for(dict(days=0.9, balance=2)), ["d3", "d1"])
        self.assertEqual(levels_for(dict(days=0, balance=0)), ["d3", "d1", "out"])

    def test_no_alert_when_there_is_plenty(self):
        reading(self.m, self.u, 8, 100)
        reading(self.m, self.u, 4, 92)
        self.assertEqual(run_alerts(), (0, 0))
        self.mail.assert_not_called()

    def test_no_alert_without_a_forecast(self):
        self.assertEqual(run_alerts(), (0, 0))

    def test_low_balance_sends_once_by_email_and_sms(self):
        self.low(12)                                    # 2 kWh a day, about 4 kWh (2 days) left
        self.assertEqual(forecast(self.m.id)["days"], 2.0)
        self.assertEqual(run_alerts(), (1, 1))
        self.assertEqual((self.mail.call_count, self.sms.call_count), (1, 1))
        self.assertIn("Home", self.mail.call_args[0][2])
        self.assertEqual({a.level for a in Alert.query.all()}, {"d3"})
        self.assertEqual(run_alerts(), (0, 0))          # the same alert is never sent twice
        self.assertEqual(self.mail.call_count, 1)

    def test_worse_level_sends_one_more_message_not_two(self):
        self.low(12)
        run_alerts()
        Entry.query.filter_by(kind="reading", day=day(4)).delete()
        db.session.commit()
        reading(self.m, self.u, 4, 10)                  # now it has run out
        self.assertEqual(forecast(self.m.id)["balance"], 0)
        self.assertEqual(run_alerts(), (1, 1))
        self.assertEqual(self.mail.call_count, 2)
        self.assertEqual({a.level for a in Alert.query.all()}, {"d3", "d1", "out"})
        self.assertIn("run out", self.mail.call_args[0][2])

    def test_alert_can_fire_again_after_the_balance_recovers(self):
        self.low(12)
        run_alerts()
        db.session.add(Entry(meter_id=self.m.id, user_id=self.u.id, kind="purchase", day=date.today().isoformat(),
                             rwf=20000, kwh=100, created=iso(now())))
        db.session.commit()
        self.assertEqual(run_alerts(), (0, 0))
        self.assertEqual(Alert.query.count(), 0)        # recovered: rows cleared
        Entry.query.filter_by(kind="purchase").delete()
        db.session.commit()
        self.assertEqual(run_alerts(), (1, 1))          # low again: warned again
        self.assertEqual(self.mail.call_count, 2)

    def test_failed_delivery_is_retried_next_run(self):
        self.low(12)
        self.mail.return_value = False
        self.sms.return_value = False
        self.assertEqual(run_alerts(), (1, 0))
        self.assertEqual(Alert.query.count(), 0)        # not recorded as sent
        self.mail.return_value = True
        self.assertEqual(run_alerts(), (1, 1))

    def test_shared_members_get_it_and_respect_their_choices(self):
        bob = self.make_user("0733555111", "Bob")
        db.session.add(Access(meter_id=self.m.id, phone=bob.phone, role="view", status="active",
                              invited_by=self.u.id, created=iso(now())))
        db.session.add(Access(meter_id=self.m.id, phone="0722000111", role="view", status="pending",
                              invited_by=self.u.id, created=iso(now())))       # not accepted yet: no alert
        db.session.add(NotifyPref(user_id=bob.id, email_on=0, sms_on=1))
        db.session.commit()
        self.low(12)
        self.assertEqual(run_alerts(), (2, 2))
        self.assertEqual(self.mail.call_count, 1)       # the owner only
        self.assertEqual(self.sms.call_count, 2)        # the owner and Bob

    def test_message_is_in_the_person_s_language(self):
        self.u.lang = "rw"
        db.session.commit()
        self.low(12)
        run_alerts()
        self.assertIn("iminsi", self.mail.call_args[0][2])

    def test_one_broken_provider_does_not_stop_the_run(self):
        self.low(12)
        self.mail.side_effect = RuntimeError("smtp down")
        self.assertEqual(run_alerts(), (1, 1))          # SMS still got through


# ---------------------------------------------------------------- outage reports
class OutageTests(AppTestCase):
    def test_pages_are_public(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/report-outage").status_code, 200)
        self.assertEqual(c.get("/report-restored").status_code, 200)
        self.assertIn(b"Report a power outage", c.get("/report-outage").data)

    def test_report_is_saved_and_listed(self):
        c = self.client()
        r = self.post(c, "/report-outage", district="Gasabo", area="near the market", note="since 9am")
        self.assertEqual(r.status_code, 302)
        row = OutageReport.query.one()
        self.assertEqual((row.kind, row.district, row.user_id), ("outage", "Gasabo", None))
        html = c.get("/report-outage").data
        self.assertIn(b"near the market", html)
        self.assertIn(b"Power off", html)

    def test_restored_report_and_login_is_remembered(self):
        c, u, _ = self.signed_in()
        self.post(c, "/report-restored", district="Huye")
        row = OutageReport.query.one()
        self.assertEqual((row.kind, row.user_id), ("restored", u.id))

    def test_unknown_district_is_rejected(self):
        c = self.client()
        self.post(c, "/report-outage", district="Atlantis")
        self.post(c, "/report-outage", district="")
        self.assertEqual(OutageReport.query.count(), 0)

    def test_second_report_within_a_minute_is_refused(self):
        c = self.client()
        self.post(c, "/report-outage", district="Gasabo")
        self.post(c, "/report-outage", district="Huye")
        self.assertEqual(OutageReport.query.count(), 1)

    def test_text_is_escaped_and_trimmed(self):
        c = self.client()
        self.post(c, "/report-outage", district="Gasabo", area="<script>alert(1)</script>", note="x" * 1000)
        self.assertNotIn(b"<script>alert(1)", c.get("/report-outage").data)
        self.assertLessEqual(len(OutageReport.query.one().note), 255)

    def test_old_reports_drop_off_the_list(self):
        c = self.client()
        self.post(c, "/report-outage", district="Gasabo", area="old one")
        OutageReport.query.update({"created": iso(now() - timedelta(hours=25))})
        db.session.commit()
        self.assertNotIn(b"old one", c.get("/report-outage").data)

    def test_csrf_is_required(self):
        c = self.client()
        self.assertEqual(c.post("/report-outage", data=dict(district="Gasabo")).status_code, 400)


if __name__ == "__main__":
    unittest.main()
