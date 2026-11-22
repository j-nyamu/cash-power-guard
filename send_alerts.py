"""Send low-balance alerts by email and SMS. Run it once a day:

    python send_alerts.py

On Windows, Task Scheduler can run it every morning. On a server, use cron.
The same alert is never sent twice until the balance has recovered. Without SMTP or SMS settings the messages are only
printed here, so you can see what would be sent (see the Configuration table in README.md).
"""
import logging

from app import create_app
from app.alerts import run_alerts

if __name__ == "__main__":
    app = create_app()
    app.logger.setLevel(logging.INFO)  # show each message that is sent or only logged
    with app.app_context():
        tried, reached = run_alerts()
    print(f"{reached} of {tried} message(s) delivered")
