import os
import sys
import time
import smtplib
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LEADS_DIR = SCRIPT_DIR / "leads"
LEADS_FILE = LEADS_DIR / "mentis_leads_with_messages.xlsx"
LOG_FILE = LEADS_DIR / "send_log.txt"
SEND_DELAY_SECONDS = 30
DAILY_LIMIT = 20

TEST_MODE = True
TEST_TARGET = "menttis.ai@gmail.com"

SKIP_CHANNELS = ["Startup Pakistan Podcast", "DAILY PAKISTAN JOBS"]

SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_LOGIN = os.environ.get("SMTP_LOGIN")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

SENDER_NAME = "Asjad from Mentis AI"
SENDER_EMAIL = "menttis.ai@gmail.com"

def load_sent_log():
    sent = {}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    channel = parts[1].strip()
                    status = parts[2].strip()
                    if status.lower() in ("sent", "ok", "success"):
                        sent[channel] = True
    except FileNotFoundError:
        pass
    return sent

def count_sent_today():
    today = time.strftime("%Y-%m-%d")
    count = 0
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(today) and "| sent |" in line.lower():
                    count += 1
    except FileNotFoundError:
        pass
    return count

def log_line(channel, status, detail=""):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    parts = [ts, channel, status, detail]
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(" | ".join(parts).strip() + "\n")

def send_email(to_email, subject, body):
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_LOGIN, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())

def main():
    print("\nMentis AI - Email Sender")
    print("=" * 50)

    if TEST_MODE:
        print(f"TEST MODE ON - all emails will be sent to {TEST_TARGET}")
    elif not SMTP_LOGIN or not SMTP_PASSWORD:
        print("\nERROR: Set SMTP_LOGIN and SMTP_PASSWORD for Brevo SMTP.")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    try:
        df = pd.read_excel(LEADS_FILE)
    except FileNotFoundError:
        print(f"\nERROR: File not found at {LEADS_FILE}")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    print(f"Loaded {len(df)} total leads")

    def has_value(v):
        return not pd.isna(v) and str(v).strip() != ""

    email_leads = df[
        (df["Send Via"].astype(str).str.strip().str.lower() == "email")
        & (df["Email"].apply(has_value))
    ].copy()

    print(f"Leads to email: {len(email_leads)}")

    if len(email_leads) == 0:
        print("\nNo email leads found.")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    done = load_sent_log()
    if done:
        print(f"Resuming - {len(done)} leads already sent")

    for i, (df_idx, row) in enumerate(email_leads.iterrows(), 1):
        channel = str(row.get("Channel Name", ""))
        email = str(row.get("Email", "")).strip()
        message = str(row.get("Outreach Message", "")).strip()

        subs = row.get("Subscribers", 0)
        if isinstance(subs, (int, float)) and not pd.isna(subs):
            subs = int(subs)
        else:
            subs = 0

        if subs < 10000:
            subject = "quick question about your student inquiries"
        elif subs <= 100000:
            subject = f"how {channel} could automate student responses"
        else:
            subject = f"scaling {channel} without more admin work"

        print(f"\n[{i}/{len(email_leads)}] {channel} -> {email}")

        if channel in SKIP_CHANNELS:
            log_line(channel, "skipped", "channel in SKIP_CHANNELS")
            print("   [skipped - channel in SKIP_CHANNELS]")
            continue

        if channel in done:
            print("   (already sent - skipping)")
            continue

        sent_today = count_sent_today()
        if sent_today >= DAILY_LIMIT:
            log_line(channel, "skipped", "daily limit reached")
            print(f"   [stopped - daily limit of {DAILY_LIMIT} sent emails reached]")
            break

        if not message or message.startswith("[ERROR"):
            detail = "no message" if not message else "invalid message"
            log_line(channel, "skipped", detail)
            print(f"   [skipped - {detail}]")
            continue

        body = message + "\n\nIf you would rather not hear from me, just reply STOP and I will not email again."
        try:
            recipient = TEST_TARGET if TEST_MODE else email
            send_email(recipient, subject, body)
            log_line(channel, "sent", email)
            if TEST_MODE:
                print(f"   [sent] Test mode: served to {TEST_TARGET}")
            else:
                print(f"   [sent] to {email}")
        except Exception as e:
            log_line(channel, "failed", f"{email} | {e}")
            print(f"   [failed: {e}]")

        time.sleep(SEND_DELAY_SECONDS)

    print("\n" + "=" * 50)
    print(f"DONE - Email batch complete. Log: {LOG_FILE}")
    print("=" * 50)

    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass

if __name__ == "__main__":
    main()
