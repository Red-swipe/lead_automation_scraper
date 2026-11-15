"""
Mentis AI one-click pipeline.

Stages: find leads -> filter relevance -> write messages -> review screen ->
sending (dry run by default; live requires --live AND typing YES).

Never sends unless --live is passed AND the owner types YES at the review
screen. Never touches .env content or deletes lead data.
"""
import subprocess
import sys
import time
import pandas as pd
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
LEADS_DIR = BASE / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
LEADS_FILE = LEADS_DIR / "mentis_leads.xlsx"
SEND_LOG = LEADS_DIR / "send_log.txt"
DO_NOT_CONTACT = LEADS_DIR / "do_not_contact.txt"

LIVE = "--live" in sys.argv


def banner(text):
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)


def stage_find_leads():
    banner("STAGE 1: FINDING LEADS (YouTube)")
    subprocess.run([sys.executable, str(BASE / "scraper.py")], check=False)


def stage_filter_relevance():
    banner("STAGE 2: FILTERING RELEVANCE")
    subprocess.run([sys.executable, str(BASE / "lead_quality.py")], check=False)
    subprocess.run([sys.executable, str(BASE / "website_check.py")], check=False)
    if REVIEW_FILE.exists():
        df = pd.read_excel(REVIEW_FILE)
        counts = df["Relevant"].value_counts().to_dict()
        print(f"\nRelevance: Y={counts.get('Y', 0)} N={counts.get('N', 0)}")


def stage_write_messages():
    banner("STAGE 3: WRITING MESSAGES")
    result = subprocess.run([sys.executable, str(BASE / "personalize_v2.py")] +
                            (["--fake"] if not LIVE else []), check=False)
    if result.returncode != 0:
        print("\nMessage writing stopped early (see above). Continuing to review.")


def already_contacted():
    contacted = set()
    for f in (SEND_LOG, DO_NOT_CONTACT):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                parts = line.split("|")
                if len(parts) >= 2:
                    contacted.add(parts[1].strip().lower())
    return contacted


def stage_review():
    banner("STAGE 4: REVIEW")
    df = pd.read_excel(REVIEW_FILE)
    y = df[df["Relevant"] == "Y"].copy()
    em = y["Email"].fillna("").astype(str).str.strip() != ""
    email_leads = y[em]
    contacted = already_contacted()

    print(f"Y leads: {len(y)} | with email: {len(email_leads)} | "
          f"WhatsApp only: {len(y) - len(email_leads)} | "
          f"already contacted / do not contact: skipped at send time")

    print("\nBatch preview:")
    for _, r in email_leads.iterrows():
        name = str(r["Channel Name"]).strip()
        msg = str(r.get("Outreach Message", "") or "").strip()
        status = "no message yet" if not msg else ("FAIL" if str(r.get("Validation", "")).startswith("FAIL") else "PASS")
        print(f"  {name[:40]:<40} | {status}")

    print("\nSample messages (up to 3, in full):")
    shown = 0
    for _, r in email_leads.iterrows():
        msg = str(r.get("Outreach Message", "") or "").strip()
        if msg and not msg.startswith("[ERROR") and shown < 3:
            print("-" * 60)
            print(f"{str(r['Channel Name']).strip()}:")
            print(msg)
            shown += 1

    wa_only = y[~em]
    if len(wa_only):
        print("\nWhatsApp only leads (cannot be emailed, listed separately):")
        for _, r in wa_only.iterrows():
            print(f"  {str(r['Channel Name']).strip()} | {str(r.get('WhatsApp', '')).strip()}")

    print("\n" + "=" * 60)
    if LIVE:
        answer = input("Type YES to send REAL emails to this batch (anything else cancels): ")
        if answer.strip() != "YES":
            print("Cancelled. No emails sent.")
            return False
        subprocess.run([sys.executable, str(BASE / "send_emails.py"), "--live"], check=False)
    else:
        print("DRY RUN: no emails will be sent.")
        answer = input("Type YES to run the sending stage in TEST MODE (all mail to your own address): ")
        if answer.strip() == "YES":
            subprocess.run([sys.executable, str(BASE / "send_emails.py")], check=False)
        else:
            print("Skipping sending stage. Nothing was sent.")
    return True


def main():
    banner("MENTIS AI PIPELINE" + (" (LIVE)" if LIVE else " (DRY RUN)"))
    if LIVE:
        print("WARNING: --live flag passed. Real emails require typing YES at the review screen.")
    stage_find_leads()
    stage_filter_relevance()
    stage_write_messages()
    stage_review()
    banner("PIPELINE COMPLETE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user. Progress is saved.")
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
