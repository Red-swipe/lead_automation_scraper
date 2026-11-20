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
- Never overwrites an existing valid Outreach Message from the current
  PROMPT_VERSION. Rows are eligible only when the message is empty, fails
  validation, or was produced by a different prompt version; --force-names is
  the only deliberate override. Replaced text is appended to Old Message.
- Atomic saves (temp file then replace), backup before every run, and a
  post-run assertion that the current-version PASS count did not decrease
  (restore from backup and abort if it did).
- Runs validate_message on every generated message; FAIL rows are marked
  for regeneration but never silently deleted.
- Per-lead success/failed/skipped log at leads/personalize_log.txt.
- DRY RUN (--fake) uses a fake generator so the resume, cap and quota-stop
  logic can be tested without calling Gemini.
"""
import os
import re
import shutil
import sys
import time
import pandas as pd
from pathlib import Path

import google.generativeai as genai
import google.api_core.exceptions

from prompt_builder import build_prompt, subject_for, PROMPT_VERSION
from validate_message import validate_from_row, normalize_terms, label_prefix, clean_email

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

LEADS_DIR = Path(__file__).resolve().parent / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
DRYRUN_FILE = LEADS_DIR / "mentis_leads_review_dryrun.xlsx"
BACKUP_LATEST = LEADS_DIR / "mentis_leads_review_backup_latest.xlsx"
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
        # Fake runs keep the log untouched; the caller prints the result line.
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
    """Deterministic fake generator for dry run (no API calls).
    With --fake-quota it simulates a daily quota 429 after 3 calls so the
    stop behavior can be tested; plain --fake never raises."""
    state["calls"] += 1
    if state.get("quota_after") and state["calls"] > state["quota_after"]:
        raise google.api_core.exceptions.ResourceExhausted(
            "429 You exceeded your current quota. Quota will reset at 12:00:00 UTC.")
    return "Fake test message for " + prompt.split("Name:")[1].split("\n")[0].strip()


# ---------------- safety helpers ----------------
def has_email(row):
    """Email leads only: WhatsApp-only rows are never generated for, and a
    cell that starts with a label ("Email-", "Contact:") is not a clean
    address until it is fixed."""
    sv = str(row.get("Send Via", "")).strip().lower()
    if sv == "whatsapp":
        return False
    email = clean_email(row.get("Email", ""))
    return email != "" and label_prefix(row.get("Email", "")) == ""


def is_message_empty(row):
    m = row.get("Outreach Message", "")
    if pd.isna(m):
        return True
    m = str(m).strip()
    return (not m) or m.startswith("[ERROR")


def message_passes(row):
    if is_message_empty(row):
        return False
    ok, _reasons = validate_from_row(row, str(row["Outreach Message"]).strip())
    return ok


def row_version(row):
    return str(row.get("Prompt Version", "") or "").strip()


def needs_generation(row):
    """Eligible when: Y + email + (no message / FAIL / missing or stale version)."""
    if not has_email(row):
        return False
    if is_message_empty(row):
        return True
    if not message_passes(row):
        return True
    return row_version(row) != PROMPT_VERSION


def save_atomic(df, path):
    """Write to a temp file then replace, so a crash cannot truncate data."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp.xlsx")
    df.to_excel(tmp, index=False)
    os.replace(tmp, path)


def backup_latest():
    """Copy the review file before every run. Never deletes anything."""
    if REVIEW_FILE.exists():
        shutil.copy2(REVIEW_FILE, BACKUP_LATEST)
        return BACKUP_LATEST
    return None


def current_version_pass_count(df):
    """Rows whose message PASSES with the current prompt version."""
    count = 0
    for _, row in df[df["Relevant"].astype(str) == "Y"].iterrows():
        if row_version(row) == PROMPT_VERSION and message_passes(row):
            count += 1
    return count


def preserve_old_message(df, idx):
    """Copy the message about to be replaced into Old Message. Appends with a
    timestamp separator when Old Message already holds text. Nothing is lost."""
    existing = str(df.at[idx, "Outreach Message"] or "").strip()
    if not existing:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M")
    block = f"[{stamp}]\n{existing}"
    old = str(df.at[idx, "Old Message"] or "").strip()
    df.at[idx, "Old Message"] = f"{old}\n\n{block}" if old else block


def parse_names(argv, flag):
    """Read a comma separated name list after a flag, lowercased."""
    if flag not in argv:
        return set()
    i = argv.index(flag)
    if i + 1 >= len(argv):
        print(f"{flag} needs a comma separated list of names")
        return set()
    return {n.strip().lower() for n in argv[i + 1].split(",") if n.strip()}


def parse_force_names(argv):
    """--force-names "A,B" is the ONLY way to regenerate a protected message."""
    return parse_names(argv, "--force-names")


def max_per_run(argv, default=MAX_PER_RUN):
    """--max N caps how many leads are generated in this run."""
    if "--max" in argv:
        i = argv.index("--max")
        if i + 1 < len(argv):
            try:
                return max(1, int(argv[i + 1]))
            except ValueError:
                print(f"--max needs a number, using default {default}")
    return default

# ---------------- run ----------------
def main():
    global DRY_RUN_FAKE
    print("\nMentis AI Personalizer v2" + (" (DRY RUN, fake generator)" if DRY_RUN_FAKE else ""))
    print("=" * 60)

    # Back up the review file before every run (never deleted).
    backup = backup_latest()
    if backup:
        print(f"Backup before run: {backup.name}")

    # Dry run reads the real review file but writes ONLY to a separate
    # dry-run copy. It is seeded from the review file when missing or when
    # --fresh is passed, so a repeated fake run sees the previous output.
    out_file = DRYRUN_FILE if DRY_RUN_FAKE else REVIEW_FILE
    if DRY_RUN_FAKE and (("--fresh" in sys.argv) or not DRYRUN_FILE.exists()):
        pd.read_excel(REVIEW_FILE).to_excel(DRYRUN_FILE, index=False)

    df = pd.read_excel(out_file)
    for col in ("Validation", "Subject", "Old Message", "Prompt Version"):
        if col not in df.columns:
            df[col] = ""
    df["Prompt Version"] = df["Prompt Version"].fillna("").astype(str)

    pass_before = current_version_pass_count(df)

    REGEN = "--regen" in sys.argv
    FORCE = parse_force_names(sys.argv)
    ONLY = parse_names(sys.argv, "--only-names")
    if FORCE:
        print(f"FORCED regeneration for: {', '.join(sorted(FORCE))}")
    if ONLY:
        print(f"ONLY THESE LEADS in scope: {', '.join(sorted(ONLY))}")
    if REGEN:
        print(f"REGEN MODE: only eligible rows are regenerated. PASS rows on {PROMPT_VERSION} are protected.")

    y = df[df["Relevant"] == "Y"].copy()
    order = y["Priority"].str.contains("P1").map({True: 0, False: 1}) + \
            y["Priority"].str.contains("P2").map({True: 0, False: 1})
    y = y.assign(_o=order).sort_values(["_o", "Subscribers"], ascending=[True, False])

    def is_force(idx, row):
        return str(row["Channel Name"]).strip().lower() in FORCE

    # A row is eligible only when Y + email + (empty/ERROR message OR FAIL
    # validation OR missing/stale Prompt Version). A current-version PASS is
    # never eligible, even with --regen; --force-names is the only override.
    eligible_rows = []
    protected_rows = []
    whatsapp_rows = []
    out_of_scope = 0
    for idx, row in y.iterrows():
        name = str(row["Channel Name"]).strip()
        if ONLY and name.lower() not in ONLY:
            out_of_scope += 1
            continue
        if not has_email(row):
            whatsapp_rows.append(name)
            continue
        if is_force(idx, row) or needs_generation(row):
            eligible_rows.append(idx)
        else:
            protected_rows.append(name)

    if whatsapp_rows:
        for name in whatsapp_rows:
            print(f"   [whatsapp only, skipped] {name}")
    if ONLY:
        print(f"   [out of scope this run] {out_of_scope} other Y leads (not in --only-names)")
    print(f"Y leads: {len(y)} | eligible for generation: {len(eligible_rows)} | "
          f"protected (current {PROMPT_VERSION} PASS): {len(protected_rows)}")

    # Show why each eligible row qualifies.
    for idx in eligible_rows:
        row = df.loc[idx]
        name = str(row["Channel Name"]).strip()
        if is_force(idx, row):
            why = "forced by --force-names"
        elif is_message_empty(row):
            why = "no message yet"
        elif not message_passes(row):
            why = "existing message FAILS validation"
        else:
            why = f"prompt version {row_version(row) or 'empty'} differs from {PROMPT_VERSION}"
        print(f"   [eligible] {name} | {why}")

    todo = df.loc[eligible_rows].copy()

    batch = todo.head(max_per_run(sys.argv))
    print(f"Batch: generating for {len(batch)} of {len(todo)} eligible leads this run")

    def finalize(code=0):
        """Atomic save, then assert the current-version PASS count did not drop."""
        if DRY_RUN_FAKE:
            save_atomic(df, out_file)
        else:
            save_atomic(df, out_file)
        pass_after = current_version_pass_count(df)
        print(f"\nPASS with {PROMPT_VERSION} before: {pass_before} | after: {pass_after}")
        if pass_after < pass_before:
            print("ABORT: current-version PASS count decreased.")
            if backup:
                shutil.copy2(backup, REVIEW_FILE)
                print(f"Restored review file from {backup.name}")
            return 1
        return code

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
                    return finalize(1)
                if ("503" in err or "504" in err or "404" in err or "no longer available" in err) and model_name != MODELS_FALLBACK[-1]:
                    print(f"[{model_name}] unavailable on test call ({err[:80]}), trying next fallback model once")
                    continue
                print(f"Test call failed on {model_name} with non quota, non 503/504 error: {err[:120]}")
                return finalize(1)
        if active_model_name is None:
            print("All fallback models failed. Gemini unavailable right now. Resume later.")
            return finalize(1)

    fake_state = {"calls": 0}
    if "--fake-quota" in sys.argv:
        fake_state["quota_after"] = 3
    done = 0
    failed_rows = []
    for i, (idx, row) in enumerate(batch.iterrows(), 1):
        name = str(row["Channel Name"]).strip()
        url = str(row["Channel URL"])
        # Every batch row has an email (eligibility rule), so this is always email.
        is_whatsapp = not has_email(row)
        lead = {
            "channel_name": name,
            "subscribers": row.get("Subscribers", 0) or 0,
            "niche": str(row.get("Found Via Query", "education")),
            "description": str(row.get("Description Snippet", "")),
            "has_email": not is_whatsapp,
            "website_notes": str(row.get("Website Notes", "")),
        }
        print(f"\nWriting message for {name} ({i}/{len(batch)})...", end=" ", flush=True)

        prompt = build_prompt(lead)
        message = None
        best = None  # (ok, reasons, message, prompt)
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
                    return finalize(1)
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

        message = normalize_terms(message)

        # Auto retry: up to 2 more regenerations, failure reasons added to prompt.
        ok, reasons = validate_from_row(row, message)
        best = (ok, reasons, message, prompt)
        for regen in range(1, 3):
            if best[0]:
                break
            print(f"\n   [regen {regen}/2] fixing: {'; '.join(best[1])[:100]}")
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
                    return finalize(1)
                print(f"   [regen failed: {err[:100]}]")
                break
            message = normalize_terms(message)
            ok, reasons = validate_from_row(row, message)
            prev_ok, prev_reasons, _prev_msg, _prev_prompt = best
            # keep the attempt with the fewest failure reasons
            if ok or len(reasons) < len(prev_reasons):
                best = (ok, reasons, message, retry_prompt)
        ok, reasons, message, _p = best
        # Preserve the message being replaced BEFORE overwriting it.
        preserve_old_message(df, idx)
        df.loc[idx, "Outreach Message"] = message
        df.loc[idx, "Validation"] = "PASS" if ok else "FAIL: " + "; ".join(reasons)
        df.loc[idx, "Subject"] = subject_for(name)
        df.loc[idx, "Prompt Version"] = PROMPT_VERSION
        log_line(url, name, "success" if ok else "success-with-validation-FAIL", "; ".join(reasons)[:120])
        print(f"done {'PASS' if ok else 'FAIL: ' + '; '.join(reasons)} (prompt {PROMPT_VERSION})")
        save_atomic(df, out_file)   # incremental save (dry run saves to its own copy)
        done += 1

    print("\n" + "=" * 60)
    print(f"DONE - generated {done} messages this run, {len(failed_rows)} failed")
    print(f"Remaining eligible Y email leads: {max(0, len(todo) - done)}")
    print(f"Log: {LOG_FILE}")
    print("No emails were sent. Old messages were never touched.")
    return finalize(0)

if __name__ == "__main__":
    sys.exit(main())

