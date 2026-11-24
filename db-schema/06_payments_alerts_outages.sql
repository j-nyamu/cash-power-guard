-- Mirrors app/models.py: Payment, Alert, NotifyPref, OutageReport, MeterSetting.
-- Depends on: 01_auth.sql, 02_meters.sql
-- The Flask app creates these tables by itself (db.create_all). Run this file only when you build the database by hand.
USE cashpower;

CREATE TABLE payments (                            -- a test-mode payment: created when the person asks to buy, settled by the sandbox checkout
  id         INT NOT NULL AUTO_INCREMENT,
  ref        VARCHAR(20) NOT NULL,                 -- shown to the person, e.g. CPG-1A2B3C4D5E6F
  meter_id   INT NOT NULL,
  user_id    INT NOT NULL,
  rwf        INT NOT NULL,
  kwh        FLOAT NOT NULL,                       -- units quoted when the payment started (an estimate)
  method     VARCHAR(10) NULL,                     -- mtn / airtel / card
  status     VARCHAR(10) NOT NULL DEFAULT 'pending',
  reason     VARCHAR(30) NULL,                     -- wrong_pin, card_declined, expired, user
  entry_id   INT NULL,                             -- the purchase entry created when it was paid
  created    VARCHAR(32) NOT NULL,
  paid_at    VARCHAR(32) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_payments_ref (ref),
  CONSTRAINT fk_payments_meter FOREIGN KEY (meter_id) REFERENCES meters (id),
  CONSTRAINT fk_payments_user  FOREIGN KEY (user_id)  REFERENCES users (id),
  CONSTRAINT ck_payments_status CHECK (status IN ('pending','paid','failed','cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE alerts (                              -- a low-balance alert already sent; removed when the balance recovers so it can fire again
  id        INT NOT NULL AUTO_INCREMENT,
  meter_id  INT NOT NULL,
  level     VARCHAR(3) NOT NULL,                   -- d3 = about 3 days left, d1 = about 1 day, out = ran out
  sent      VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_alerts_meter_level (meter_id, level),
  CONSTRAINT fk_alerts_meter FOREIGN KEY (meter_id) REFERENCES meters (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE notify_prefs (                        -- email / SMS choices; no row means both are on
  user_id   INT NOT NULL,
  email_on  INT NOT NULL DEFAULT 1,
  sms_on    INT NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id),
  CONSTRAINT fk_notify_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE outage_reports (                      -- public, anonymous reports that the power is off (or back); user_id kept if they were logged in
  id        INT NOT NULL AUTO_INCREMENT,
  kind      VARCHAR(8) NOT NULL,                   -- outage / restored
  district  VARCHAR(30) NOT NULL,
  area      VARCHAR(120) NULL,
  note      VARCHAR(255) NULL,
  user_id   INT NULL,
  created   VARCHAR(32) NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT ck_outage_kind CHECK (kind IN ('outage','restored'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE meter_settings (                      -- what the owner said they use (kWh a day): a first guess until real readings exist
  meter_id   INT NOT NULL,
  daily_use  FLOAT NOT NULL,
  PRIMARY KEY (meter_id),
  CONSTRAINT fk_meter_settings_meter FOREIGN KEY (meter_id) REFERENCES meters (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
