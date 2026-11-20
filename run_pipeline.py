"""
Mentis AI one-click pipeline (6 stages).

Safe mode (run_mentis.bat): real scraping + real Gemini, sends at most ONE
TEST email to the owner's own address.
Live mode (run_mentis_live.bat): passes --live; real emails require typing
"SEND <n>" at the confirmation prompt.

Flags: --skip-scrape, --fake (fake generator, no Gemini), --dry-run (no sending).
Each stage is guarded: an error prints the traceback and the next safe stage
continues. No .env values are ever printed. Existing data is never deleted.
"""
import sys
import traceback
import subprocess
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(__file__).resolve().parent
LEADS_DIR = BASE / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"

from send_emails import (skip_reason, load_do_not_contact,
                         load_already_contacted, load_live_sent, load_config,
                         send_sort_key)
from validate_message import validate_from_row

LIVE = "--live" in sys.argv
FAKE = "--fake" in sys.argv
DRY_RUN = "--dry-run" in sys.argv
SKIP_SCRAPE = "--skip-scrape" in sys.argv


def banner(text):
    print("\n" + "=" * 62)
    print(text)
    print("=" * 62)


def run_script(name, args=None):
    # -u keeps child output in order with this process's own prints.
    cmd = [sys.executable, "-u", str(BASE / name)] + (args or [])
    return subprocess.run(cmd, cwd=str(BASE), check=False)


def stage(stage_fn):
    """Run a stage, printing the traceback on error and continuing."""
    try:
        return stage_fn()
    except Exception:
        print("\n[STAGE ERROR] this stage failed, continuing with saved data:")
        traceback.print_exc()
        return None


def load_review():
    return pd.read_excel(REVIEW_FILE)


# ---------------- stages ----------------
def stage_find_leads():
    banner("[1/6] FINDING LEADS")
    if SKIP_SCRAPE:
        print("Scrape skipped (--skip-scrape). Using existing lead data.")
        return
    print("--- YouTube ---")
    rc = run_script("scraper.py")
    if rc.returncode != 0:
        print(f"YouTube scraper exited with code {rc.returncode}, continuing.")
    print("\n--- Google Maps ---")
    rc = run_script("maps_scraper.py")
    if rc.returncode != 0:
        print(f"Google Maps stage exited with code {rc.returncode}, continuing.")


def stage_filter():
    banner("[2/6] FILTERING (relevance)")
    rc = run_script("lead_quality.py")
    if rc.returncode != 0:
        print(f"Relevance stage exited with code {rc.returncode}, continuing.")
    if REVIEW_FILE.exists():
        df = load_review()
        rel = df["Relevant"].astype(str).str.strip()
        print(f"\nY/N/? counts: Y={int((rel == 'Y').sum())} "
              f"N={int((rel == 'N').sum())} ?={int((rel == '?').sum())}")
        unknown = df[rel == "?"]
        if len(unknown):
            print("New '?' leads (no verdict, never messaged):")
            for _, r in unknown.iterrows():
                print(f"   {str(r['Channel Name']).strip()}")


def stage_websites():
    banner("[3/6] CHECKING WEBSITES (Y leads with a URL)")
    rc = run_script("website_check.py")
    if rc.returncode != 0:
        print(f"Website stage exited with code {rc.returncode}, continuing.")


def stage_write_messages():
    banner("[4/6] WRITING MESSAGES")
    args = ["--fake", "--fresh"] if FAKE else []
    rc = run_script("personalize_v2.py", args)
    if rc.returncode != 0:
        print("\nGemini quota used up, continuing with messages that are ready.")


def stage_review():
    banner("[5/6] REVIEW")
    df = load_review()
    dnc = load_do_not_contact()
    already = load_already_contacted()
    live_sent = load_live_sent()
    cfg = load_config()

    print(f"{'Name':<34} {'Email':<34} {'Priority':<4} {'Subs':>9}  Validation")
    print("-" * 100)
    ready = []
    for _, row in df.iterrows():
        if str(row.get("Relevant", "")).strip().upper() != "Y":
            continue
        if skip_reason(row, dnc, already, live_sent) is not None:
            continue
        name = str(row["Channel Name"]).strip()
        email = str(row.get("Email", "") or "").strip()
        ok, reasons = validate_from_row(row, str(row["Outreach Message"]).strip())
        prio = str(row.get("Priority", "")).strip()[:2]
        try:
            subs = int(row.get("Subscribers", 0) or 0)
        except (TypeError, ValueError):
            subs = 0
        print(f"{name[:34]:<34} {email[:34]:<34} {prio:<4} {subs:>9,}  {'PASS' if ok else 'FAIL'}")
        ready.append(row)
    # Send order: academies, then tutors, then channels over 100k.
    ready.sort(key=send_sort_key)
    ready.sort(key=lambda r: (priority_key(r.get("Priority", "")),
                              -(float(r.get("Subscribers", 0) or 0))))

    print(f"\nReady messages ({len(ready)}), in full:")
    for i, row in enumerate(ready, 1):
        name = str(row["Channel Name"]).strip()
        print("\n" + "-" * 62)
        print(f"{i}. {name} | {str(row.get('Email', '')).strip()} | {str(row.get('Priority', '')).strip()}")
        print(str(row["Outreach Message"]).strip())

    print("\nSkipped leads and why:")
    skipped = 0
    for _, row in df.iterrows():
        if str(row.get("Relevant", "")).strip().upper() != "Y":
            continue
        reason = skip_reason(row, dnc, already, live_sent)
        if reason:
            skipped += 1
            print(f"   {str(row['Channel Name']).strip()[:40]:<42} | {reason}")
    if not skipped:
        print("   (none)")
    print(f"\nBatch for sending: {len(ready)} email(s) | daily cap {cfg.get('daily_cap', 3)} "
          f"| delay {cfg.get('min_delay_seconds', 120)}-{cfg.get('max_delay_seconds', 300)}s")


def stage_send():
    banner("[6/6] SENDING")
    args = []
    if LIVE:
        args.append("--live")
    if DRY_RUN:
        args.append("--dry-run")
    rc = run_script("send_emails.py", args)
    if rc.returncode != 0:
        print("Sending stopped (see the message above). Nothing else was sent.")


def main():
    banner("MENTIS AI PIPELINE - " + ("LIVE" if LIVE else "SAFE") +
           (" | fake generator" if FAKE else "") + (" | dry run" if DRY_RUN else ""))
    if LIVE:
        print("LIVE MODE: real emails require typing SEND <n> at the prompt.")
    else:
        print("SAFE MODE: real Gemini, at most 1 TEST email to your own address.")

    for fn in (stage_find_leads, stage_filter, stage_websites,
               stage_write_messages, stage_review, stage_send):
        stage(fn)

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
