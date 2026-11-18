"""
Mentis AI Email Sender (safe Gmail sending).

Owner rules:
- TEST_MODE stays True. The default path (no --live) sends exactly ONE test
  email to the owner's own address and logs mode=TEST.
- Live sending requires the --live flag AND typing "SEND <n>" at the prompt,
  where n is the real number of emails in the batch. Anything else cancels.
- Gmail SMTP (smtp.gmail.com:465 SSL), app password from .env, never printed.
- config.json: daily_cap (only mode=LIVE sends today count), delay range.
- Send order P1, P2, P3. Skips: email typo flag, invalid email, FAIL message,
  do_not_contact.txt, already_contacted.txt, WhatsApp-only, LIVE-sent before.
- Plain text only, no links, no attachments. Deterministic subject, sign off,
  STOP line, sender name and address unchanged.
- Stops the whole run on any SMTP error. Prints a summary and reminders.
"""
import json
import os
import re
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
from validate_message import validate_from_row

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")
LEADS_DIR = SCRIPT_DIR / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
LOG_FILE = LEADS_DIR / "send_log.txt"
DO_NOT_CONTACT = LEADS_DIR / "do_not_contact.txt"
ALREADY_CONTACTED = LEADS_DIR / "already_contacted.txt"
WHATSAPP_ONLY_FILE = LEADS_DIR / "whatsapp_only_leads.txt"
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_CONFIG = {"daily_cap": 3, "min_delay_seconds": 120, "max_delay_seconds": 300}

TEST_MODE = True          # default path is always TEST; --live is the only live path
LIVE_REQUESTED = "--live" in sys.argv
DRY_RUN = "--dry-run" in sys.argv
TEST_TARGET = "menttis.ai@gmail.com"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_LOGIN = (os.environ.get("GMAIL_SMTP_LOGIN") or os.environ.get("SMTP_LOGIN") or "").strip()
SMTP_PASSWORD = (os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").replace(" ", "").strip()

SENDER_NAME = "Asjad from Mentis AI"
SENDER_EMAIL = "menttis.ai@gmail.com"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"config.json could not be read ({e}), using defaults")
    return cfg


def load_list(path):
    items = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                items.add(line)
    return items


def load_do_not_contact():
    return load_list(DO_NOT_CONTACT)


def load_already_contacted():
    return load_list(ALREADY_CONTACTED)


def _parse_line(line):
    """Parse a log line. Format: ts | name | status | mode=X | detail.
    Old lines without a mode field are treated as TEST (the old sender always
    redirected sends to the owner's address and logged the lead email only)."""
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


def load_live_sent():
    """Names and emails with a LIVE 'sent' entry."""
    sent = set()
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            parsed = _parse_line(line.strip())
            if not parsed:
                continue
            _ts, name, status, mode, detail = parsed
            if status.lower() == "sent" and mode == "LIVE":
                sent.add(name.lower())
                if detail:
                    sent.add(detail.lower())
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
    mode = mode or ("LIVE" if LIVE_REQUESTED and not DRY_RUN else "TEST")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(" | ".join([ts, name, status, f"mode={mode}", detail]).strip() + "\n")


def has_value(v):
    return not pd.isna(v) and str(v).strip() != ""


def priority_key(value):
    text = str(value or "")
    for key, rank in PRIORITY_ORDER.items():
        if key in text:
            return rank
    return 3


def cell_text(value):
    """Cell text, with NaN/None treated as empty (pandas turns blank
    cells into float NaN, and str(nan) would look like a real value)."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def skip_reason(row, dnc, already, live_sent):
    """Why a Y row is not sendable, or None when it is."""
    name = cell_text(row.get("Channel Name"))
    email = cell_text(row.get("Email"))
    message = cell_text(row.get("Outreach Message"))
    flag = cell_text(row.get("Review Flag")).upper()

    if not email:
        return "WhatsApp only (no email)"
    if "EMAIL TYPO" in flag:
        return "email typo flag"
    if not EMAIL_RE.match(email):
        return "email format invalid"
    if not message or message.startswith("[ERROR"):
        return "quota pending (no message yet)"
    ok, reasons = validate_from_row(row, message)
    if not ok:
        return f"message FAIL ({'; '.join(reasons)[:60]})"
    lname, lemail = name.lower(), email.lower()
    if lname in dnc or lemail in dnc:
        return "do not contact"
    if lname in already or lemail in already:
        return "already contacted"
    if lname in live_sent or lemail in live_sent:
        return "already sent (LIVE)"
    return None


def build_batch(df, dnc=None, already=None, live_sent=None):
    """Ordered list of sendable rows: Y + valid email + PASS + not skipped."""
    dnc = load_do_not_contact() if dnc is None else dnc
    already = load_already_contacted() if already is None else already
    live_sent = load_live_sent() if live_sent is None else live_sent

    rows = []
    for _, row in df.iterrows():
        if str(row.get("Relevant", "")).strip().upper() != "Y":
            continue
        if skip_reason(row, dnc, already, live_sent) is not None:
            continue
        rows.append(row)

    rows.sort(key=lambda r: (priority_key(r.get("Priority", "")),
                             -(float(r.get("Subscribers", 0) or 0))))
    return rows


def send_email(to_email, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
        server.login(SMTP_LOGIN, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())


def countdown(seconds):
    for remaining in range(seconds, 0, -1):
        mins, secs = divmod(remaining, 60)
        print(f"\r   Next email in {mins:02d}:{secs:02d}", end="", flush=True)
        time.sleep(1)
    print("\r   Next email in 00:00", flush=True)


def write_whatsapp_only(df):
    wa = df[(df["Relevant"].astype(str).str.strip().str.upper() == "Y")
            & (~df["Email"].apply(has_value))
            & (df["WhatsApp"].apply(has_value))]
    if len(wa):
        with open(WHATSAPP_ONLY_FILE, "w", encoding="utf-8") as f:
            f.write("WhatsApp only leads, manual outreach (never emailed)\n")
            f.write("=" * 50 + "\n")
            for _, r in wa.iterrows():
                f.write(f"{str(r['Channel Name']).strip()} | {str(r.get('WhatsApp', '')).strip()}\n")
        print(f"WhatsApp only leads listed in {WHATSAPP_ONLY_FILE.name} ({len(wa)}) - not emailed.")
    return len(wa)


def main():
    cfg = load_config()
    daily_cap = int(cfg.get("daily_cap", 3))
    min_delay = int(cfg.get("min_delay_seconds", 120))
    max_delay = int(cfg.get("max_delay_seconds", 300))

    live = LIVE_REQUESTED and not DRY_RUN
    print("\nMentis AI - Email Sender (Gmail SMTP)")
    print("=" * 60)
    print(f"Mode: {'LIVE (real leads)' if live else ('DRY RUN (nothing sent)' if DRY_RUN else 'TEST (your own address only)')}")
    print(f"Config: daily_cap={daily_cap}, delay {min_delay}-{max_delay}s")

    if not REVIEW_FILE.exists():
        print(f"\nERROR: {REVIEW_FILE.name} not found.")
        return 1
    df = pd.read_excel(REVIEW_FILE)
    write_whatsapp_only(df)

    batch = build_batch(df)
    sent_today = count_sent_today()
    remaining_cap = max(0, daily_cap - sent_today)
    print(f"LIVE sends today: {sent_today}/{daily_cap} | eligible emails: {len(batch)}")

    if DRY_RUN:
        print("\nBatch (dry run, nothing will be sent):")
        for i, row in enumerate(batch, 1):
            name = str(row["Channel Name"]).strip()
            print(f"  {i}. {name} -> {str(row['Email']).strip()} | {subject_for(name)}")
        try:
            answer = input(f'Type SEND {len(batch)} to confirm (dry run will still not send): ')
        except EOFError:
            answer = ""
        if answer.strip() != f"SEND {len(batch)}":
            print("\nNothing sent.")
            return 0
        print(f"\n[dry run] Confirmation accepted for {len(batch)} email(s). Nothing actually sent.")
        return 0

    if not batch:
        print("\nNo eligible emails to send right now.")
        return 0

    if live and remaining_cap <= 0:
        print(f"\nDaily cap reached ({sent_today}/{daily_cap} LIVE sends today). Nothing sent.")
        return 0

    if live:
        sendable = batch[:remaining_cap]
        print(f"\nLIVE batch ({len(sendable)} email(s), ordered P1 then P2 then P3):")
        for i, row in enumerate(sendable, 1):
            name = str(row["Channel Name"]).strip()
            print(f"  {i}. {name} | {str(row['Email']).strip()} | {subject_for(name)}")
        expected = f"SEND {len(sendable)}"
        try:
            answer = input(f'\nType "{expected}" to send {len(sendable)} real email(s): ')
        except EOFError:
            answer = ""
        if answer.strip() != expected:
            print("\nNothing sent.")
            return 0
        mode = "LIVE"
    else:
        # Safe default path: exactly ONE TEST email to the owner's address.
        sendable = batch[:1]
        mode = "TEST"
        print(f"\nTEST: sending 1 email to {TEST_TARGET} (no lead receives anything).")

    sent_count = 0
    for i, row in enumerate(sendable, 1):
        name = str(row["Channel Name"]).strip()
        email = str(row["Email"]).strip()
        message = str(row["Outreach Message"]).strip()
        recipient = email if live else TEST_TARGET
        subject = subject_for(name)
        body = message + "\n\nIf you would rather not hear from me, just reply STOP and I will not email again."

        try:
            send_email(recipient, subject, body)
            log_line(name, "sent", f"{email} (delivered to {recipient})", mode=mode)
            sent_count += 1
            print(f"   [{mode}] {name} | {subject} -> {recipient}")
        except smtplib.SMTPException as e:
            log_line(name, "failed", f"{email} | {e}", mode=mode)
            print(f"\nSMTP error, stopping the run: {e}")
            print("Fix the SMTP/credential issue before sending again.")
            return 1
        except Exception as e:
            log_line(name, "failed", f"{email} | {e}", mode=mode)
            print(f"\nError, stopping the run: {type(e).__name__}: {e}")
            return 1

        if mode == "LIVE" and i < len(sendable):
            delay = random.randint(min_delay, max_delay)
            countdown(delay)

    print("\n" + "=" * 60)
    print(f"DONE - {sent_count} email(s) sent this run in {mode} mode.")
    print("Summary:")
    print(f"  sent this run: {sent_count}")
    print(f"  LIVE sends today: {count_sent_today()}/{daily_cap}")
    print(f"  log: {LOG_FILE}")
    print("\nReminders:")
    print("  - Check Gmail for bounces and failed deliveries.")
    print("  - Add anyone who replies STOP to leads/do_not_contact.txt.")
    print("  - Wait a few days before raising the cap in config.json.")
    print("No links, no attachments, plain text only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
