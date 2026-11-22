"""
BCIT intake watcher - cloud version for GitHub Actions (email + Pushover).

Runs ONE check per invocation (GitHub's scheduler calls it repeatedly).
Usage:  python watcher_cloud.py check   (default) look at the page, alert if open
        python watcher_cloud.py show    print what the script sees, no alerts
        python watcher_cloud.py email   send a test email
        python watcher_cloud.py phone   send a test Pushover notification

Your keys and passwords come from GitHub "secrets", NOT from this file:
    EMAIL_ADDRESS, EMAIL_APP_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN
"""
import os
import re
import smtplib
import sys
from email.message import EmailMessage

import requests
from bs4 import BeautifulSoup

URL = "https://www.bcit.ca/apprenticeship/students/training/industrial-electrician-apprentice-harmonized/"

# How loud should a real alert be?
#   1 = high priority (bypasses quiet hours), one notification
#   2 = emergency: repeats every PUSHOVER_RETRY seconds until you tap "Acknowledge"
PUSHOVER_PRIORITY = 2
PUSHOVER_RETRY = 60      # seconds between repeats (Pushover minimum is 30)
PUSHOVER_EXPIRE = 3600   # stop repeating after this many seconds (1 hour)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"}


def get_page_text():
    response = requests.get(URL, timeout=20, headers=HEADERS)
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    if "registering for apprentice training" not in text.lower():
        raise RuntimeError("Page loaded but doesn't look like the BCIT training page.")
    return text


def find_intakes(text):
    results = []
    for number, full in re.findall(r"\b(\d{2}-\d{4})\b(\s+FULL\b)?", text, flags=re.IGNORECASE):
        results.append((number, bool(full)))
    return results


def analyze(text):
    reasons = []
    for number, is_full in find_intakes(text):
        if not is_full:
            reasons.append(f"Intake {number} is listed without FULL")
    if "no available intakes" not in text.lower():
        reasons.append("The 'no available intakes' message is gone")
    return reasons


def env(name):
    return os.environ.get(name, "").strip()


def email_configured():
    return bool(env("EMAIL_ADDRESS") and env("EMAIL_APP_PASSWORD"))


def phone_configured():
    return bool(env("PUSHOVER_USER_KEY") and env("PUSHOVER_APP_TOKEN"))


def send_email(body):
    address = env("EMAIL_ADDRESS")
    password = env("EMAIL_APP_PASSWORD").replace(" ", "")
    msg = EmailMessage()
    msg["Subject"] = "BCIT intake may be open!"
    msg["From"] = address
    msg["To"] = address
    msg.set_content(f"{body}\n\nGo register: {URL}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(address, password)
        server.send_message(msg)


def send_phone(body, priority=None):
    if priority is None:
        priority = PUSHOVER_PRIORITY
    payload = {
        "token": env("PUSHOVER_APP_TOKEN"),
        "user": env("PUSHOVER_USER_KEY"),
        "title": "BCIT intake may be open!",
        "message": body,
        "url": URL,
        "url_title": "Open BCIT page",
        "priority": priority,
    }
    if priority == 2:
        payload["retry"] = PUSHOVER_RETRY
        payload["expire"] = PUSHOVER_EXPIRE
    response = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=20)
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.status_code != 200 or result.get("status") != 1:
        raise RuntimeError(f"Pushover rejected the message: {result.get('errors') or response.text}")


def send_alerts(body):
    """Try every configured method. Returns True if at least one got through."""
    methods = []
    if phone_configured():
        methods.append(("phone", send_phone))
    if email_configured():
        methods.append(("email", send_email))
    if not methods:
        raise RuntimeError("No alert method configured: add your secrets in GitHub settings.")
    sent_any = False
    for name, send in methods:
        try:
            send(body)
            print(f"{name} alert sent")
            sent_any = True
        except Exception as e:
            print(f"{name} alert FAILED: {e}")
    return sent_any


def set_output(name, value):
    """Tell the GitHub workflow something (used to switch the watcher off after an alert)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "check").strip().lower() or "check"

    if mode == "email":
        if not email_configured():
            raise RuntimeError("EMAIL_ADDRESS / EMAIL_APP_PASSWORD secrets are missing.")
        send_email("This is a test email from your cloud watcher.")
        print("Test email sent. Check your inbox (and spam).")
        return

    if mode == "phone":
        if not phone_configured():
            raise RuntimeError("PUSHOVER_USER_KEY / PUSHOVER_APP_TOKEN secrets are missing.")
        send_phone("This is a test notification from your cloud watcher.", priority=0)
        print("Test notification sent. Check your iPhone.")
        return

    text = get_page_text()  # errors here make the run fail, which GitHub will tell you about

    if mode == "show":
        print("Intakes found:", find_intakes(text))
        print("'No available intakes' message present:", "no available intakes" in text.lower())
        print("Would alert:", analyze(text) or "No")
        print("Phone configured:", phone_configured(), "| Email configured:", email_configured())
        return

    reasons = analyze(text)
    if reasons:
        body = "; ".join(reasons)
        print("ALERT:", body)
        if send_alerts(body):
            set_output("alert", "true")   # workflow switches itself off
        else:
            raise RuntimeError("Every alert method failed - will try again next run.")
    else:
        print("Nothing open yet.")


if __name__ == "__main__":
    main()
