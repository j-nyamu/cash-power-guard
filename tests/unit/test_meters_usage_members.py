"""Unit tests for meters.py, usage.py, members.py and views.py: pages, entries, sharing, permissions."""
import unittest
from datetime import date, timedelta

from helpers import AppTestCase, db

from app.models import Access, Activity, Entry, Meter


def day(n):
    return (date.today() - timedelta(days=n)).isoformat()


class MeterTests(AppTestCase):
    def test_add_second_meter(self):
        c, u, _ = self.signed_in()
        self.post(c, "/meters/add", meter_no="0999 9999 999", nickname="Shop")
        self.assertEqual(Meter.query.filter_by(owner_id=u.id).count(), 2)
        self.assertEqual(Meter.query.filter_by(meter_no="09999999999").one().nickname, "Shop")

    def test_invalid_or_duplicate_meter_not_added(self):
        c, u, _ = self.signed_in()
        self.post(c, "/meters/add", meter_no="123")
        self.post(c, "/meters/add", meter_no="01234567890")
        self.assertEqual(Meter.query.count(), 1)

    def test_empty_nickname_defaults(self):
        c, _, _ = self.signed_in()
        self.post(c, "/meters/add", meter_no="09999999999", nickname="  ")
        self.assertEqual(Meter.query.filter_by(meter_no="09999999999").one().nickname, "Meter")

    def test_meter_page_owner_sees_people_and_log(self):
        c, _, m = self.signed_in()
        html = c.get(f"/meter/{m.id}").data
        self.assertIn(b"Activity log", html)

    def test_unknown_meter_404_and_other_owner_403(self):
        c, _, m = self.signed_in()
        b, _, _ = self.signed_in("Bob Mugisha", "0733555111", "09999999999")
        self.assertEqual(c.get("/meter/999").status_code, 404)
        self.assertEqual(b.get(f"/meter/{m.id}").status_code, 403)


class EntryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.c, self.u, self.m = self.signed_in()
        self.url = f"/meter/{self.m.id}/entry"

    def last(self):
        return Entry.query.order_by(Entry.id.desc()).first()

    def test_reading(self):
        self.post(self.c, self.url, kind="reading", day=day(1), balance="12.5")
        e = self.last()
        self.assertEqual((e.kind, e.kwh, e.day, e.rwf), ("reading", 12.5, day(1), None))

    def test_purchase_with_receipt_units_is_exact(self):
        self.post(self.c, self.url, kind="purchase", rwf="5000", kwh="25.3")
        e = self.last()
        self.assertEqual((e.kwh, e.estimated, e.rwf), (25.3, 0, 5000))

    def test_purchase_without_units_is_estimated_from_tariff(self):
        self.post(self.c, self.url, kind="purchase", rwf="5000")
        e = self.last()
        self.assertEqual(e.estimated, 1)
        # earlier purchases this month (none) -> starts in block 1 -> 27.93 kWh
        self.assertEqual(e.kwh, 27.93)

    def test_estimate_uses_calibration(self):
        self.post(self.c, self.url, kind="purchase", day=day(0), rwf="5000", kwh="25.0")
        self.post(self.c, self.url, kind="purchase", day=day(0), rwf="5000")
        self.assertLess(self.last().kwh, 5000 / (310 * 1.18))  # lower than the raw tariff

    def test_future_or_bad_date_becomes_today(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.post(self.c, self.url, kind="reading", day=tomorrow, balance="5")
        self.assertEqual(self.last().day, date.today().isoformat())
        self.post(self.c, self.url, kind="reading", day="not-a-date", balance="5")
        self.assertEqual(self.last().day, date.today().isoformat())

    def test_bad_values_are_refused(self):
        for data in (dict(kind="purchase", rwf="0"), dict(kind="purchase", rwf="-5"),
                     dict(kind="purchase", rwf="abc"), dict(kind="purchase", rwf="100", kwh="0"),
                     dict(kind="reading", balance="-1"), dict(kind="reading", balance="")):
            with self.subTest(data):
                r = self.post(self.c, self.url, **data)
                self.assertEqual(r.status_code, 302)
        self.assertEqual(Entry.query.count(), 0)

    def test_reading_of_zero_is_allowed(self):
        self.post(self.c, self.url, kind="reading", balance="0")
        self.assertEqual(Entry.query.count(), 1)

    def test_unknown_kind_400(self):
        self.assertEqual(self.post(self.c, self.url, kind="other").status_code, 400)

    def test_entries_are_logged(self):
        self.post(self.c, self.url, kind="purchase", rwf="5000", kwh="25")
        self.assertEqual(Activity.query.one().action, "added_purchase")

    def test_forecast_and_estimate_flag_on_page(self):
        self.post(self.c, self.url, kind="reading", day=day(10), balance="30")
        self.post(self.c, self.url, kind="reading", day=day(0), balance="20")
        self.post(self.c, self.url, kind="purchase", day=day(0), rwf="5000")
        html = self.c.get(f"/meter/{self.m.id}").data.decode()
        self.assertIn("Run-out forecast", html)
        self.assertIn("estimated", html)


class SharingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.alice, self.a, self.m = self.signed_in()
        self.bob, self.b, _ = self.signed_in("Bob Mugisha", "0733555111", "09999999999")

    def invite(self, phone="0733555111", role="view"):
        return self.post(self.alice, f"/meter/{self.m.id}/invite", phone=phone, role=role)

    def test_invite_is_pending_until_accepted(self):
        self.invite()
        a = Access.query.one()
        self.assertEqual((a.status, a.role), ("pending", "view"))
        self.assertEqual(self.bob.get(f"/meter/{self.m.id}").status_code, 403)
        self.assertIn(b"wants to share", self.bob.get("/dashboard").data)
        self.post(self.bob, f"/invite/{a.id}/accept")
        self.assertEqual(self.bob.get(f"/meter/{self.m.id}").status_code, 200)

    def test_decline_removes_invite(self):
        self.invite()
        self.post(self.bob, f"/invite/{Access.query.one().id}/decline")
        self.assertEqual(Access.query.count(), 0)
        self.assertEqual(Activity.query.order_by(Activity.id.desc()).first().action, "declined")

    def test_bad_choice_400_and_other_persons_invite_404(self):
        self.invite()
        aid = Access.query.one().id
        self.assertEqual(self.post(self.bob, f"/invite/{aid}/maybe").status_code, 400)
        carol, _, _ = self.signed_in("Carol Ineza", "0722000000", "05555555555")
        self.assertEqual(self.post(carol, f"/invite/{aid}/accept").status_code, 404)

    def test_view_role_cannot_add_and_cannot_manage(self):
        self.invite(role="view")
        self.post(self.bob, f"/invite/{Access.query.one().id}/accept")
        url = f"/meter/{self.m.id}"
        self.assertEqual(self.post(self.bob, url + "/entry", kind="reading", balance="1").status_code, 403)
        self.assertEqual(self.post(self.bob, url + "/invite", phone="0722000000").status_code, 403)
        self.assertEqual(self.post(self.bob, url + "/revoke/1").status_code, 403)
        self.assertNotIn(b"Activity log", self.bob.get(url).data)

    def test_add_role_can_add_entries(self):
        self.invite(role="add")
        self.post(self.bob, f"/invite/{Access.query.one().id}/accept")
        self.post(self.bob, f"/meter/{self.m.id}/entry", kind="reading", balance="7")
        self.assertEqual(Entry.query.one().user_id, self.b.id)

    def test_unknown_role_becomes_view(self):
        self.invite(role="admin")
        self.assertEqual(Access.query.one().role, "view")

    def test_self_bad_and_duplicate_invites_refused(self):
        self.invite(phone="0788123456")
        self.invite(phone="123")
        self.invite()
        self.invite()
        self.assertEqual(Access.query.count(), 1)

    def test_revoke(self):
        self.invite()
        aid = Access.query.one().id
        self.post(self.bob, f"/invite/{aid}/accept")
        self.post(self.alice, f"/meter/{self.m.id}/revoke/{aid}")
        self.assertEqual(self.bob.get(f"/meter/{self.m.id}").status_code, 403)

    def test_member_view_logged_once_per_10_minutes(self):
        self.invite()
        self.post(self.bob, f"/invite/{Access.query.one().id}/accept")
        for _ in range(3):
            self.bob.get(f"/meter/{self.m.id}")
        self.assertEqual(Activity.query.filter_by(action="viewed").count(), 1)
        self.assertIn(b"viewed the logs", self.alice.get(f"/meter/{self.m.id}").data)

    def test_invite_before_signup(self):
        self.invite(phone="0722000000", role="add")
        carol, _, _ = self.signed_in("Carol Ineza", "0722000000", "05555555555")
        self.assertIn(b"wants to share", carol.get("/dashboard").data)


class PageAndSecurityTests(AppTestCase):
    def test_public_pages_load(self):
        c = self.client()
        for url in ("/", "/login", "/register", "/forgot"):
            with self.subTest(url):
                self.assertEqual(c.get(url).status_code, 200)

    def test_logged_in_pages_load(self):
        c, _, m = self.signed_in()
        self.assertTrue(c.get("/").location.endswith("/dashboard"))
        for url in ("/dashboard", "/settings", "/welcome", f"/meter/{m.id}"):
            with self.subTest(url):
                self.assertEqual(c.get(url).status_code, 200)

    def test_post_without_csrf_rejected(self):
        c = self.client()
        self.assertEqual(c.post("/login", data={"phone": "0788123456", "password": "x"}).status_code, 400)
        self.assertEqual(c.post("/login", data={"csrf": "wrong", "phone": "x"}).status_code, 400)

    def test_language_switch(self):
        c = self.client()
        self.assertIn(b"Injira", c.get("/login?lang=rw").data)
        self.assertIn(b"Injira", c.get("/login").data)        # remembered in session
        self.assertIn(b"Log in", c.get("/login?lang=en").data)
        self.assertIn(b"Log in", self.client().get("/login?lang=fr").data)  # unsupported -> English

    def test_language_saved_on_account(self):
        c, u, _ = self.signed_in()
        c.get("/dashboard?lang=rw")
        db.session.refresh(u)
        self.assertEqual(u.lang, "rw")

    def test_language_kept_through_signup(self):
        c = self.client()
        c.get("/login?lang=rw")
        self.register(c)
        self.assertIn("Murakaza neza".encode(), c.get("/welcome").data)

    def test_static_files_served(self):
        c = self.client()
        for url in ("/static/style.css", "/static/app.js"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200)
            r.close()

    def test_session_cookie_flags(self):
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- starting balance and daily use
from app.forecast import forecast  # noqa: E402
from app.models import MeterSetting  # noqa: E402
from app.utils import notify, parse_start  # noqa: E402


class StartForecastTests(AppTestCase):
    def test_parse_start_accepts_empty_and_sensible_numbers(self):
        self.assertEqual(parse_start({}), (None, None))
        self.assertEqual(parse_start({"start_balance": "28,5", "start_use": "3"}), (28.5, 3.0))
        self.assertEqual(parse_start({"start_balance": "0", "start_use": ""}), (0.0, None))

    def test_parse_start_rejects_junk(self):
        for bad in ({"start_balance": "abc"}, {"start_balance": "-1"}, {"start_balance": "nan"}, {"start_balance": "inf"},
                    {"start_balance": "9999999"}, {"start_use": "0"}, {"start_use": "-2"}, {"start_use": "9999"}):
            with self.assertRaises(ValueError, msg=bad):
                parse_start(bad)

    def test_signing_up_with_a_start_gives_a_forecast_straight_away(self):
        c, u, m = self.signed_in()   # no start boxes: no forecast yet
        self.assertIsNone(forecast(m.id))
        c2 = self.client()
        r = self.register(c2, name="Bea Mutoni", phone="0733555111", meter="09876543210",
                          start_balance="30", start_use="3")
        self.assertEqual(r.status_code, 302, r.data[:300])
        m2 = Meter.query.filter_by(meter_no="09876543210").one()
        fc = forecast(m2.id)
        self.assertEqual((fc["balance"], fc["rate"], fc["days"], fc["guessed"]), (30.0, 3.0, 10.0, True))
        self.assertEqual(Entry.query.filter_by(meter_id=m2.id, kind="reading").one().kwh, 30)
        self.assertEqual(db.session.get(MeterSetting, m2.id).daily_use, 3)

    def test_bad_start_blocks_sign_up_and_keeps_what_was_typed(self):
        c = self.client()
        r = self.register(c, start_balance="abc", start_use="3")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"sensible numbers", r.data)
        self.assertEqual(Meter.query.count(), 0)

    def test_adding_a_meter_with_a_start(self):
        c, u, _ = self.signed_in()
        self.post(c, "/meters/add", meter_no="0999 9999 999", nickname="Shop", start_balance="12", start_use="2")
        m = Meter.query.filter_by(meter_no="09999999999").one()
        self.assertEqual(forecast(m.id)["days"], 6.0)
        # a bad start adds nothing at all
        self.post(c, "/meters/add", meter_no="0888 8888 888", nickname="Bad", start_balance="-3")
        self.assertIsNone(Meter.query.filter_by(meter_no="08888888888").first())

    def test_start_form_on_an_existing_meter(self):
        c, u, m = self.signed_in()
        page = c.get(f"/meter/{m.id}").data
        self.assertIn(b"Start your forecast", page)
        self.post(c, f"/meter/{m.id}/start", start_balance="20", start_use="4")
        fc = forecast(m.id)
        self.assertEqual((fc["days"], fc["guessed"]), (5.0, True))
        self.assertNotIn(b"Start your forecast", c.get(f"/meter/{m.id}").data)   # the form is gone once there is a forecast
        self.assertIn(b"Daily average", c.get(f"/meter/{m.id}").data)

    def test_start_form_needs_both_numbers_and_the_right_role(self):
        c, u, m = self.signed_in()
        self.post(c, f"/meter/{m.id}/start", start_balance="20")
        self.assertIsNone(forecast(m.id))
        vc, viewer, _ = self.signed_in("Bob Mutoni", "0733555111", "09876543210")   # a second person with his own account
        db.session.add(Access(meter_id=m.id, phone=viewer.phone, role="view", status="active", invited_by=u.id, created="x"))
        db.session.commit()
        self.assertEqual(self.post(vc, f"/meter/{m.id}/start", start_balance="20", start_use="2").status_code, 403)
        self.assertIsNone(forecast(m.id))

    def test_real_readings_replace_the_guess_and_narrow_the_range(self):
        c, u, m = self.signed_in()
        self.post(c, f"/meter/{m.id}/start", start_balance="30", start_use="3")
        guessed = forecast(m.id)
        for n, kwh in ((3, 30), (1, 24)):                     # two real readings: 3 kWh a day
            db.session.add(Entry(meter_id=m.id, user_id=u.id, kind="reading", day=day(n), kwh=kwh, created="x"))
        db.session.commit()
        real = forecast(m.id)
        self.assertEqual(real["rate"], 3.0)
        self.assertFalse(real["guessed"])
        self.assertLess(real["spread"], guessed["spread"])


class LegalPageAndTestAlertTests(AppTestCase):
    def test_terms_privacy_and_contact_pages_open_for_anyone_in_both_languages(self):
        c = self.client()
        for path, word in (("/terms", b"Terms of use"), ("/privacy", b"Privacy policy"), ("/contact", b"Contact us")):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(word, r.data)
        self.assertIn(b"Amabwiriza", c.get("/terms?lang=rw").data)
        self.assertIn(b"not been set up", c.get("/contact").data)       # no address configured yet

    def test_contact_page_shows_the_configured_address(self):
        self.app.config["CONTACT_EMAIL"] = "team@example.rw"
        self.assertIn(b"team@example.rw", self.app.test_client().get("/contact").data)

    def test_sign_up_page_links_to_the_terms(self):
        self.assertIn(b"/terms", self.app.test_client().get("/register").data)

    def test_test_alert_goes_to_the_signed_in_person_only_and_is_rate_limited(self):
        from unittest import mock
        c, u, _ = self.signed_in()
        with mock.patch("app.utils.send_email", return_value=True) as mail, mock.patch("app.utils.send_sms", return_value=True) as sms:
            self.post(c, "/settings/test-alert")
            self.assertEqual((mail.call_count, sms.call_count), (1, 1))
            self.assertIn("test alert", mail.call_args[0][1].lower())
            self.post(c, "/settings/test-alert")          # straight away again: refused
            self.assertEqual((mail.call_count, sms.call_count), (1, 1))

    def test_test_alert_needs_login(self):
        r = self.app.test_client().post("/settings/test-alert")
        self.assertIn(r.status_code, (302, 400, 401, 403))
