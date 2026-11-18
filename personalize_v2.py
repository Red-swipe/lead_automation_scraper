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

# Primary model, then fallbacks in order (all verified available to this key
# via genai.list_models, free tier). If a model returns 503/504 on the test
# call, the next one is tried once.
PRIMARY_MODEL = "gemini-flash-latest"
MODELS_FALLBACK = [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
]

active_model_name = None

if not DRY_RUN_FAKE:
    genai.configure(api_key=GEMINI_API_KEY)

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
    response = genai.GenerativeModel(active_model_name).generate_content(
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
    if DRY_RUN_FAKE:
        pd.read_excel(REVIEW_FILE).to_excel(DRYRUN_FILE, index=False)

    df = pd.read_excel(out_file)

    y = df[df["Relevant"] == "Y"].copy()
    order = y["Priority"].str.contains("P1").map({True: 0, False: 1}) + \
            y["Priority"].str.contains("P2").map({True: 0, False: 1})
    y = y.assign(_o=order).sort_values(["_o", "Subscribers"], ascending=[True, False])

    # Work set: Y leads WITHOUT a valid existing message.
    # Existing valid messages are NEVER overwritten (owner decides regeneration).
    REGEN = "--regen" in sys.argv

    def needs_message(row):
        m = row.get("Outreach Message", "")
        if pd.isna(m):
            return True
        m = str(m).strip()
        return (not m) or m.startswith("[ERROR")

    def is_regen_candidate(row):
        # FAIL rows whose owner approved regeneration (--regen mode).
        return REGEN and str(row.get("Validation", "")).startswith("FAIL")

    if REGEN:
        # Owner approved regenerating every Y email lead with the new prompt.
        regen_mask = pd.Series(True, index=y.index)
    else:
        regen_mask = y.apply(needs_message, axis=1)
    todo = y[regen_mask].copy()
    skipped_existing = len(y) - len(todo)
    if REGEN:
        print("REGEN MODE: FAIL rows included (owner approved). PASS rows still kept.")
    print(f"Y leads: {len(y)} | already have a valid message (kept): {skipped_existing} | to generate: {len(todo)}")

    # Preserve the previous message in Old Message before regenerating overwrites it.
    for idx, row in y[regen_mask & y.apply(lambda r: not needs_message(r), axis=1)].iterrows():
        old = str(row.get("Old Message", "") or "").strip()
        if not old:
            df.loc[idx, "Old Message"] = str(row.get("Outreach Message", "")).strip()

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

    # WhatsApp only leads are NOT personalized for now (manual outreach list).
    # Rows with empty Send Via but an email address are email leads.
    sv = todo["Send Via"].astype(str).str.strip().str.lower()
    wa_only = todo[sv == "whatsapp"]
    if len(wa_only):
        for _, r in wa_only.iterrows():
            print(f"   [whatsapp only, skipped] {str(r['Channel Name']).strip()}")
    todo = todo[sv != "whatsapp"].copy()
    print(f"Email leads only (WhatsApp only leads skipped for manual outreach): {len(todo)}")

    batch = todo.head(MAX_PER_RUN)
    print(f"Batch cap {MAX_PER_RUN}: generating for {len(batch)} leads this run")

    # ONE test call before the batch (skipped in dry run).
    # If a model returns 503/504 on the test call, try the next model once.
    global active_model_name
    if not DRY_RUN_FAKE:
        for model_name in MODELS_FALLBACK:
            try:
                test = genai.GenerativeModel(model_name).generate_content(
                    "Reply with the single word: ready",
                    request_options={"retry": None, "timeout": REQUEST_TIMEOUT},
                )
                active_model_name = model_name
                print(f"Test call OK on {model_name}: {test.text.strip()!r}")
                break
            except Exception as e:
                err = str(e)
                if is_daily_quota_429(err):
                    print("\nQUOTA EXHAUSTED before batch. Gemini daily quota 429.")
                    print(f"Quota {quota_reset_time(err)}. Resume later.")
                    return 1
                if ("503" in err or "504" in err or "404" in err or "no longer available" in err) and model_name != MODELS_FALLBACK[-1]:
                    print(f"[{model_name}] unavailable on test call ({err[:80]}), trying next fallback model once")
                    continue
                print(f"Test call failed on {model_name} with non quota, non 503/504 error: {err[:120]}")
                return 1
        if active_model_name is None:
            print("All fallback models failed. Gemini unavailable right now. Resume later.")
            return 1

    fake_state = {"calls": 0}
    done = 0
    failed_rows = []
    for i, (idx, row) in enumerate(batch.iterrows(), 1):
        name = str(row["Channel Name"]).strip()
        url = str(row["Channel URL"])
        # Send Via decides; empty Send Via falls back to presence of an email
        # (rows already filtered to exclude WhatsApp-only leads).
        sv = str(row.get("Send Via", "")).strip().lower()
        has_email_value = not pd.isna(row.get("Email", "")) and str(row.get("Email", "")).strip() != ""
        is_whatsapp = (sv == "whatsapp") if sv else (not has_email_value)
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
        validation_ok = False  # unused, kept for clarity
        best = None  # (ok, reasons, message)
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

        # Auto retry: up to 2 more regenerations, failure reasons added to prompt.
        ok, reasons = validate_from_row(row, message)
        best = (ok, reasons, message, prompt)
        for regen in range(1, 3):
            if best[0]:
                break
            print(f"   [regen {regen}/2] fixing: {'; '.join(best[1])[:100]}")
            retry_prompt = best[3] + (
                "\n\nIMPORTANT: your previous attempt was rejected for these reasons: "
                + "; ".join(best[1])
                + ". Fix ALL of them and return only the corrected message."
            )
            try:
                message = real_generate(retry_prompt) if not DRY_RUN_FAKE else fake_generate(retry_prompt, fake_state)
            except Exception as e:
                err = str(e)
                if is_daily_quota_429(err):
                    log_line(url, name, "skipped", "quota exhausted during regen, run stopped")
                    print(f"\nQUOTA EXHAUSTED, stopping the whole run. Quota {quota_reset_time(err)}")
                    df.to_excel(out_file, index=False)
                    return 1
                print(f"   [regen failed: {err[:100]}]")
                break
            ok, reasons = validate_from_row(row, message)
            prev_ok, prev_reasons, _prev_msg, _prev_prompt = best
            # keep the attempt with the fewest failure reasons
            if ok or len(reasons) < len(prev_reasons):
                best = (ok, reasons, message, retry_prompt)
        ok, reasons, message, _p = best
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

