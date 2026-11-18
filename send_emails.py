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
DRY_RUN = "--dry-run" in sys.argv     # show decisions, send nothing
CURRENT_MODE = "LIVE" if (LIVE_REQUESTED and not TEST_MODE) else "TEST"


def parse_max_flag(argv):
    """--max N caps this run below the daily limit. Returns None if absent."""
    if "--max" not in argv:
        return None
    i = argv.index("--max")
    if i + 1 >= len(argv):
        print("--max needs a number, for example --max 3")
        return None
    try:
        value = int(argv[i + 1])
    except ValueError:
        print(f"--max value is not a number: {argv[i + 1]!r}, ignoring it")
        return None
    return max(1, value)
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


def _parse_line(line):
    """Parse a log line. New format: ts | name | status | mode=X | detail.
    Old lines (no mode field) are treated as TEST on purpose: the old sender
    had TEST_MODE = True and redirected every send to the owner's address,
    logging the lead email only as detail."""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    ts, name, status = parts[0], parts[1], parts[2]
    mode, detail = "TEST", ""
    if len(parts) >= 4:
        if parts[3].lower().startswith("mode="):
            mode = parts[3].split("=", 1)[1].strip().upper() or "TEST"
            detail = parts[4] if len(parts) >= 5 else ""
        else:
            detail = parts[3]
    return ts, name, status, mode, detail


def load_sent_log():
    """Only LIVE sends count as already contacted."""
    sent = {}
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = _parse_line(line)
            if not parsed:
                continue
            _ts, name, status, mode, detail = parsed
            if status.lower() in ("sent", "ok", "success") and mode == "LIVE":
                sent[name] = True
                if detail:
                    sent[detail.lower()] = True
    except FileNotFoundError:
        pass
    return sent


def count_sent_today():
    """Daily cap counts LIVE sends only."""
    today = time.strftime("%Y-%m-%d")
    count = 0
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            parsed = _parse_line(line.strip())
            if not parsed:
                continue
            ts, _name, status, mode, _detail = parsed
            if ts.startswith(today) and status.lower() == "sent" and mode == "LIVE":
                count += 1
    except FileNotFoundError:
        pass
    return count


def log_line(name, status, detail="", mode=None):
    mode = mode or CURRENT_MODE
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(" | ".join([ts, name, status, f"mode={mode}", detail]).strip() + "\n")


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
    print(f"Mode: {CURRENT_MODE}" + (" (dry run, nothing is sent)" if DRY_RUN else ""))
    if done:
        print(f"Resuming - {len(done)} LIVE-sent entries (skipping)")
    else:
        print("Resuming - no LIVE sends in log, nobody is treated as contacted")
    print(f"LIVE sends today: {count_sent_today()}/{DAILY_LIMIT}")

    sent_count = 0
    attempted = 0  # counts real sends and dry-run would-sends, for --max
    run_max = parse_max_flag(sys.argv)
    if run_max is not None:
        print(f"Run cap: at most {run_max} send(s) this run")
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
            log_line(name, "skipped", "already sent (LIVE)")
            print("   (already sent LIVE - skipping)")
            continue
        if name.lower() in dnc or email.lower() in dnc:
            log_line(name, "skipped", "on do not contact list")
            print("   [skipped - do not contact]")
            continue
        if count_sent_today() >= DAILY_LIMIT:
            log_line(name, "skipped", "daily limit reached")
            print(f"   [stopped - daily cap of {DAILY_LIMIT} LIVE sends reached]")
            break
        if run_max is not None and attempted >= run_max:
            log_line(name, "skipped", f"run cap of {run_max} reached")
            print(f"   [stopped - --max {run_max} reached for this run]")
            break

        body = message + "\n\nIf you would rather not hear from me, just reply STOP and I will not email again."
        subject = subject_for(name)
        recipient = email if live else TEST_TARGET

        if DRY_RUN:
            print(f"   [dry run] would send {CURRENT_MODE} to {recipient} | subject: {subject} | {len(body.split())} words")
            log_line(name, "dry-run", f"would send to {recipient}")
            attempted += 1
            continue

        try:
            send_email(recipient, subject, body)
            log_line(name, "sent", f"{email} (delivered to {recipient})")
            sent_count += 1
            attempted += 1
            print(f"   [sent {'LIVE' if live else 'TEST'}] {subject} -> {recipient}")
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
