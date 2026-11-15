"""
Mentis AI personalizer v2 (SAFE: TEST and dry-run first, no emails sent here).

Owner-approved behavior:
- Only Relevant == Y leads, ordered P1, then P2, then P3.
- One TEST call before the batch. If it returns a daily quota 429, stop,
  print the reset time, do not retry.
- Daily quota 429 at any point: stop the whole run immediately, save
  progress, print reset time. Never retried.
- Only 5xx errors get exponential backoff, max 3 attempts, 60s timeout.
- request_options={"retry": None, "timeout": 60} verified in
  google-generativeai 0.8.6 (retry=None disables hidden SDK retries).
- Resume keyed on Channel URL, never channel name.
- Never overwrites an existing valid Outreach Message (old messages live in
  the Old Message column and the owner decides on regeneration separately).
- Runs validate_message on every generated message; FAIL rows are marked
  for regeneration but never silently deleted.
- Per-lead success/failed/skipped log at leads/personalize_log.txt.
- DRY RUN (--fake) uses a fake generator so the resume, cap and quota-stop
  logic can be tested without calling Gemini.
"""
import os
import re
import sys
import time
import pandas as pd
from pathlib import Path

import google.generativeai as genai
import google.api_core.exceptions

from prompt_builder import build_prompt, subject_for
from validate_message import validate_from_row

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

LEADS_DIR = Path(__file__).resolve().parent / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
DRYRUN_FILE = LEADS_DIR / "mentis_leads_review_dryrun.xlsx"
LOG_FILE = LEADS_DIR / "personalize_log.txt"

MAX_PER_RUN = 10          # cap per run, configurable
BACKOFF_BASE = 5          # seconds, 5xx only
MAX_5XX_ATTEMPTS = 3
REQUEST_TIMEOUT = 60

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DRY_RUN_FAKE = "--fake" in sys.argv

if not DRY_RUN_FAKE:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-flash-latest")

# ---------------- logging ----------------
def log_line(url, name, status, detail=""):
    if DRY_RUN_FAKE:
        print(f"   [dry-run log] {name} | {status} | {detail}")
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {url} | {name} | {status} | {detail}\n")

# ---------------- error classification ----------------
def quota_reset_time(err_text):
    m = re.search(r"reset\D{0,30}([\d:;\-]+)\s*(UTC)?", str(err_text), re.I)
    return (m.group(1) + (m.group(2) or "")) if m else "reset time not stated in error"

def is_daily_quota_429(err_text):
    t = str(err_text).lower()
    return "429" in t and ("quota" in t or "resourceexhausted" in t or "resource_exhausted" in t)

def is_5xx(err_text):
    t = str(err_text)
    return any(code in t for code in ("500", "503", "504", "Deadline", "Internal", "Unavailable"))

# ---------------- generation ----------------
def real_generate(prompt):
    response = model.generate_content(
        prompt,
        request_options={"retry": None, "timeout": REQUEST_TIMEOUT},
    )
    return response.text.strip()

def fake_generate(prompt, state):
    """Deterministic fake generator for dry run. Simulates the real logic:
    first two calls OK, then raises quota 429 to prove the stop behavior."""
    state["calls"] += 1
    if state["calls"] > 3:
        raise google.api_core.exceptions.ResourceExhausted(
            "429 You exceeded your current quota. Quota will reset at 12:00:00 UTC.")
    return "Fake test message for " + prompt.split("Name:")[1].split("\n")[0].strip()

# ---------------- run ----------------
def main():
    global DRY_RUN_FAKE
    print("\nMentis AI Personalizer v2" + (" (DRY RUN, fake generator)" if DRY_RUN_FAKE else ""))
    print("=" * 60)

    # Dry run reads the real review file but writes ONLY to a separate
    # dry-run copy, so resume behavior can be tested without touching
    # the real data.
    out_file = DRYRUN_FILE if DRY_RUN_FAKE else REVIEW_FILE
    if DRY_RUN_FAKE and not DRYRUN_FILE.exists():
        pd.read_excel(REVIEW_FILE).to_excel(DRYRUN_FILE, index=False)

    df = pd.read_excel(out_file)

    y = df[df["Relevant"] == "Y"].copy()
    order = y["Priority"].str.contains("P1").map({True: 0, False: 1}) + \
            y["Priority"].str.contains("P2").map({True: 0, False: 1})
    y = y.assign(_o=order).sort_values(["_o", "Subscribers"], ascending=[True, False])

    # Work set: Y leads WITHOUT a valid existing message.
    # Existing valid messages are NEVER overwritten (owner decides regeneration).
    def needs_message(row):
        m = row.get("Outreach Message", "")
        if pd.isna(m):
            return True
        m = str(m).strip()
        return (not m) or m.startswith("[ERROR")

    todo = y[y.apply(needs_message, axis=1)].copy()
    skipped_existing = len(y) - len(todo)
    print(f"Y leads: {len(y)} | already have a valid message (kept): {skipped_existing} | to generate: {len(todo)}")

    # Mark existing old messages against the validator (messages NOT changed).
    if "Validation" not in df.columns:
        df["Validation"] = ""
    if "Subject" not in df.columns:
        df["Subject"] = ""
    for idx, row in y[~y.apply(needs_message, axis=1)].iterrows():
        ok, reasons = validate_from_row(row, str(row["Outreach Message"]).strip())
        df.loc[idx, "Validation"] = "PASS" if ok else "FAIL: " + "; ".join(reasons)
    if not DRY_RUN_FAKE:
        df.to_excel(out_file, index=False)
    for idx, row in y[~y.apply(needs_message, axis=1)].iterrows():
        v = df.loc[idx, "Validation"]
        if str(v).startswith("FAIL"):
            print(f"   [regeneration candidate] {str(row['Channel Name']).strip()} | {v}")

    batch = todo.head(MAX_PER_RUN)
    print(f"Batch cap {MAX_PER_RUN}: generating for {len(batch)} leads this run")

    # ONE test call before the batch (skipped in dry run)
    if not DRY_RUN_FAKE:
        for attempt in range(1, MAX_5XX_ATTEMPTS + 1):
            try:
                test = model.generate_content(
                    "Reply with the single word: ready",
                    request_options={"retry": None, "timeout": REQUEST_TIMEOUT},
                )
                print(f"Test call OK: {test.text.strip()!r}")
                break
            except Exception as e:
                err = str(e)
                if is_daily_quota_429(err):
                    print("\nQUOTA EXHAUSTED before batch. Gemini daily quota 429.")
                    print(f"Quota {quota_reset_time(err)}. Resume later.")
                    return 1
                if is_5xx(err) and attempt < MAX_5XX_ATTEMPTS:
                    delay = BACKOFF_BASE * (2 ** (attempt - 1))
                    print(f"[5xx] test call attempt {attempt}/{MAX_5XX_ATTEMPTS} failed, backing off {delay}s")
                    time.sleep(delay)
                    continue
                print(f"Test call failed with non quota error: {e}")
                return 1

    fake_state = {"calls": 0}
    done = 0
    failed_rows = []
    for i, (idx, row) in enumerate(batch.iterrows(), 1):
        name = str(row["Channel Name"]).strip()
        url = str(row["Channel URL"])
        is_whatsapp = str(row.get("Send Via", "")).strip().lower() != "email"
        lead = {
            "channel_name": name,
            "subscribers": row.get("Subscribers", 0) or 0,
            "niche": str(row.get("Found Via Query", "education")),
            "description": str(row.get("Description Snippet", "")),
            "has_email": not is_whatsapp,
            "website_notes": str(row.get("Website Notes", "")),
        }
        print(f"\n[{i}/{len(batch)}] {name} ({'WhatsApp' if is_whatsapp else 'email'})")

        prompt = build_prompt(lead)
        message = None
        for attempt in range(1, MAX_5XX_ATTEMPTS + 1):
            try:
                if DRY_RUN_FAKE:
                    message = fake_generate(prompt, fake_state)
                else:
                    message = real_generate(prompt)
                break
            except Exception as e:
                err = str(e)
                if is_daily_quota_429(err):
                    log_line(url, name, "skipped", "quota exhausted, run stopped")
                    print(f"\nQUOTA EXHAUSTED, stopping the whole run. Quota {quota_reset_time(err)}")
                    print("Progress saved. Resume later.")
                    df.to_excel(out_file, index=False)
                    return 1
                if is_5xx(err) and attempt < MAX_5XX_ATTEMPTS:
                    delay = BACKOFF_BASE * (2 ** (attempt - 1))
                    print(f"   [5xx] attempt {attempt}/{MAX_5XX_ATTEMPTS} failed, backing off {delay}s")
                    time.sleep(delay)
                    continue
                log_line(url, name, "failed", err[:120])
                print(f"   [failed] {err[:120]}")
                failed_rows.append((url, name, err[:80]))
                break

        if message is None:
            continue

        ok, reasons = validate_from_row(row, message)
        df.loc[idx, "Outreach Message"] = message
        df.loc[idx, "Validation"] = "PASS" if ok else "FAIL: " + "; ".join(reasons)
        df.loc[idx, "Subject"] = subject_for(name)
        log_line(url, name, "success" if ok else "success-with-validation-FAIL", "; ".join(reasons)[:120])
        print(f"   validator: {'PASS' if ok else 'FAIL: ' + '; '.join(reasons)}")
        df.to_excel(out_file, index=False)   # incremental save (dry run saves to its own copy)
        done += 1

    print("\n" + "=" * 60)
    print(f"DONE - generated {done} messages this run, {len(failed_rows)} failed")
    print(f"Remaining Y leads without a message: {len(todo) - done}")
    print(f"Log: {LOG_FILE}")
    print("No emails were sent. Old messages were never touched.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

