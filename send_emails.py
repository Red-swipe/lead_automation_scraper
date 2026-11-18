"""
Mentis AI Email Sender (safe Gmail sending).

Owner rules:
- TEST_MODE stays True by default; live sending additionally requires the
  --live flag AND typing YES at the confirmation prompt.
- Gmail SMTP (smtp.gmail.com, port 465 SSL) with an app password from .env.
  The password is never printed.
- Sender name/address unchanged.
- Hard daily cap of 10 sent emails. Random 2 to 5 minute delay between sends.
- Plain text only, no links, no attachments, no tracking.
- Deterministic subject from prompt_builder.subject_for, sign off already in
  the message, STOP opt out line appended, do not contact list honored.
- Run stops on any SMTP error or bounce; everything is logged.
- WhatsApp only leads are listed separately for manual outreach and never
  sent by this sender.
"""
import os
import sys
import time
import random
import smtplib
import ssl
import pandas as pd
from email.mime.text import MIMEText

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from dotenv import load_dotenv
from prompt_builder import subject_for

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")
LEADS_DIR = SCRIPT_DIR / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
LOG_FILE = LEADS_DIR / "send_log.txt"
DO_NOT_CONTACT = LEADS_DIR / "do_not_contact.txt"
WHATSAPP_ONLY_FILE = LEADS_DIR / "whatsapp_only_leads.txt"

DAILY_LIMIT = 10
MIN_DELAY_SECONDS = 120   # 2 minutes
MAX_DELAY_SECONDS = 300   # 5 minutes

TEST_MODE = True          # default True; never flip to live except via --live + YES
LIVE_REQUESTED = "--live" in sys.argv
TEST_ONE = "--test-one" in sys.argv   # send a single TEST email, then stop
TEST_TARGET = "menttis.ai@gmail.com"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_LOGIN = (os.environ.get("GMAIL_SMTP_LOGIN") or os.environ.get("SMTP_LOGIN") or "").strip()
# Gmail app passwords are 16 chars, often copied with spaces; strip them.
SMTP_PASSWORD = (os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").replace(" ", "").strip()

SENDER_NAME = "Asjad from Mentis AI"
SENDER_EMAIL = "menttis.ai@gmail.com"


def load_do_not_contact():
    dnc = set()
    if DO_NOT_CONTACT.exists():
        for line in DO_NOT_CONTACT.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                dnc.add(line)
    return dnc


def load_sent_log():
    sent = {}
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 3 and parts[2].strip().lower() in ("sent", "ok", "success"):
                sent[parts[1].strip()] = True
    except FileNotFoundError:
        pass
    return sent


def count_sent_today():
    today = time.strftime("%Y-%m-%d")
    count = 0
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(today) and "| sent |" in line.lower():
                count += 1
    except FileNotFoundError:
        pass
    return count


def log_line(name, status, detail=""):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(" | ".join([ts, name, status, detail]).strip() + "\n")


def send_email(to_email, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
        server.login(SMTP_LOGIN, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())


def main():
    print("\nMentis AI - Email Sender (Gmail SMTP)")
    print("=" * 60)

    live = LIVE_REQUESTED and not TEST_MODE
    if LIVE_REQUESTED and TEST_MODE:
        print("\nERROR: --live passed but TEST_MODE is True in the file.")
        print("Flip TEST_MODE to False only together with --live, then run again.")
        return 1
    if live:
        answer = input("You are about to send REAL emails. Type YES to confirm: ")
        if answer.strip() != "YES":
            print("Cancelled. No emails sent.")
            return 1
    if not live:
        print(f"TEST MODE: all emails go to {TEST_TARGET}. No lead receives anything.")

    if not REVIEW_FILE.exists():
        print(f"\nERROR: {REVIEW_FILE.name} not found.")
        return 1
    df = pd.read_excel(REVIEW_FILE)

    def has_value(v):
        return not pd.isna(v) and str(v).strip() != ""

    email_leads = df[
        (df["Relevant"].astype(str).str.strip().str.upper() == "Y")
        & (df["Email"].apply(has_value))
        & (df["Outreach Message"].fillna("").astype(str).str.strip() != "")
        & (~df["Outreach Message"].fillna("").astype(str).str.startswith("[ERROR"))
    ].copy()

    wa_only = df[
        (df["Relevant"].astype(str).str.strip().str.upper() == "Y")
        & (~df["Email"].apply(has_value))
        & (df["WhatsApp"].apply(has_value))
    ].copy()
    if len(wa_only):
        with open(WHATSAPP_ONLY_FILE, "w", encoding="utf-8") as f:
            f.write("WhatsApp only leads, manual outreach (never emailed)\n")
            f.write("=" * 50 + "\n")
            for _, r in wa_only.iterrows():
                f.write(f"{str(r['Channel Name']).strip()} | {str(r.get('WhatsApp', '')).strip()}\n")
        print(f"WhatsApp only leads listed in {WHATSAPP_ONLY_FILE.name} ({len(wa_only)} leads) - not emailed.")

    print(f"Email leads ready: {len(email_leads)}")

    done = load_sent_log()
    dnc = load_do_not_contact()
    if done:
        print(f"Resuming - {len(done)} already sent (skipping)")

    sent_count = 0
    for i, (idx, row) in enumerate(email_leads.iterrows(), 1):
        name = str(row["Channel Name"]).strip()
        email = str(row["Email"]).strip()
        message = str(row["Outreach Message"]).strip()
        validation = str(row.get("Validation", "") or "")

        print(f"\n[{i}/{len(email_leads)}] {name} -> {email}")

        if validation.startswith("FAIL"):
            log_line(name, "skipped", "message failed validation")
            print("   [skipped - message failed validation, not sending]")
            continue
        if name in done or email.lower() in done:
            print("   (already sent - skipping)")
            continue
        if name.lower() in dnc or email.lower() in dnc:
            log_line(name, "skipped", "on do not contact list")
            print("   [skipped - do not contact]")
            continue
        if count_sent_today() >= DAILY_LIMIT:
            log_line(name, "skipped", "daily limit reached")
            print(f"   [stopped - daily cap of {DAILY_LIMIT} reached]")
            break

        body = message + "\n\nIf you would rather not hear from me, just reply STOP and I will not email again."
        subject = subject_for(name)
        recipient = email if live else TEST_TARGET
        try:
            send_email(recipient, subject, body)
            log_line(name, "sent", email)
            sent_count += 1
            print(f"   [sent {'LIVE' if live else 'TEST'}] {subject}")
            if TEST_ONE:
                print("   TEST ONE: 1 email sent, stopping run as requested.")
                break
        except smtplib.SMTPException as e:
            log_line(name, "failed", f"{email} | {e}")
            print(f"   [SMTP error, stopping run: {e}]")
            break
        except Exception as e:
            log_line(name, "failed", f"{email} | {e}")
            print(f"   [error, stopping run: {e}]")
            break

        if i < len(email_leads) and not TEST_ONE:
            delay = random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
            print(f"   waiting {delay}s before next send (2 to 5 min)")
            time.sleep(delay)

    print("\n" + "=" * 60)
    print(f"DONE - {sent_count} sent this run. Log: {LOG_FILE}")
    print("No links, no attachments, plain text only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
