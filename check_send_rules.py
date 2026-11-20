"""No SMTP send checks for eligibility, dedupe, order, and LIVE cap accounting."""
from datetime import datetime
import tempfile
from pathlib import Path
import pandas as pd
import send_emails as s

FAILS = []
def check(label, ok):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok: FAILS.append(label)

def row(name, email, desc="English lessons", subs=1000, flag="", manual="", msg=None):
    return {"Channel Name": name, "Email": email, "Relevant": "Y", "Description Snippet": desc,
            "Subscribers": subs, "Priority": "P1", "Review Flag": flag, "Manual Clear": manual,
            "Send Via": "Email", "Outreach Message": msg or
            f"{name} teaches English lessons. Mentis AI builds AI assistants for student inquiries on WhatsApp, plus fee reminders and websites. WhatsApp auto replies can answer repeated student questions. Would a short chat be useful?\n\nAsjad from Mentis AI"}

def main():
    good = row("Tutor One", "one@gmail.com", "teaches English lessons")
    academy = row("Bright Academy", "academy@bright.edu", "teaches mathematics classes", 5000)
    rows = [good, row("WhatsApp Only", "", "teaches English"),
            row("Fail", "fail@gmail.com", msg="This message fails validation."),
            row("Flagged", "flag@gmail.com", flag="manual check"),
            row("Dup Address", "one@gmail.com"),
            row("Tutor One", "second@gmail.com"),
            row("Dup Domain", "another@bright.edu"),
            row("DNC", "dnc@gmail.com"), row("Already", "already@gmail.com"),
            row("Live sent", "live@gmail.com"), academy]
    frame = pd.DataFrame(rows)
    batch = s.build_batch(frame, dnc={"dnc@gmail.com"}, already={"already@gmail.com"}, live_sent={"Live sent", "live@gmail.com"})
    names = [r["Channel Name"] for r in batch]
    check("excludes WhatsApp, FAIL, flag, duplicate address/domain, DNC, already, LIVE sent", set(names) == {"Bright Academy", "Tutor One"})
    check("structured academy orders before tutor", names == ["Bright Academy", "Tutor One"])
    old_log = s.LOG_FILE
    try:
        with tempfile.TemporaryDirectory() as folder:
            s.LOG_FILE = Path(folder) / "send_log.txt"
            today = datetime.now().strftime("%Y-%m-%d")
            s.LOG_FILE.write_text(f"{today} 10:00:00 | A | sent | mode=TEST | x\n{today} 10:01:00 | B | sent | mode=LIVE | y\n", encoding="utf-8")
            check("daily cap counts LIVE only", s.count_sent_today() == 1)
    finally:
        s.LOG_FILE = old_log
    print("ALL CHECKS PASSED" if not FAILS else f"CHECKS FAILED: {len(FAILS)}")
    return int(bool(FAILS))

if __name__ == "__main__": raise SystemExit(main())
