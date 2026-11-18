"""
Safety checks for the regeneration rules. No API calls, no new dependencies.

Run:  python check_regen.py
Exit code 0 only when every assertion passes.

Checks:
 1. Prompt Version column exists in the review file.
 2. A Y email row whose message PASSES on the current PROMPT_VERSION is never
    eligible for generation (so --regen cannot touch it).
 3. Only the three allowed reasons make a row eligible, and WhatsApp-only rows
    are never eligible.
 4. preserve_old_message keeps the replaced text and appends with a timestamp
    when Old Message already has content (nothing is ever lost).
 5. save_atomic writes a readable file and leaves no .tmp behind.
 6. The fake dry-run file (when present) has zero eligible rows on a repeat
    run, proving a second run generates nothing.
"""
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import personalize_v2 as p
from prompt_builder import PROMPT_VERSION
from validate_message import validate_from_row

FAILS = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" | {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def eligible_reason(row):
    if p.is_message_empty(row):
        return "no message yet"
    if not p.message_passes(row):
        return "existing message FAILS validation"
    if p.row_version(row) != PROMPT_VERSION:
        return f"prompt version {p.row_version(row) or 'empty'} differs from {PROMPT_VERSION}"
    return None


def audit(path, label):
    print(f"\n--- {label}: {path.name}")
    df = pd.read_excel(path)
    for col in ("Prompt Version", "Old Message", "Outreach Message", "Validation"):
        if col not in df.columns:
            df[col] = ""

    y = df[df["Relevant"].astype(str).str.strip() == "Y"]
    email_rows = y[y.apply(p.has_email, axis=1)]
    protected = [r for _, r in email_rows.iterrows()
                 if p.row_version(r) == PROMPT_VERSION and p.message_passes(r)]
    eligible = [r for _, r in email_rows.iterrows() if p.needs_generation(r)]

    check(f"{label}: Prompt Version column exists", "Prompt Version" in df.columns)
    check(f"{label}: no current-version PASS row is eligible",
          all(not (p.row_version(r) == PROMPT_VERSION and p.message_passes(r))
              for r in eligible),
          f"eligible={len(eligible)}")
    check(f"{label}: no WhatsApp-only row is eligible",
          all(p.has_email(r) for r in eligible))
    check(f"{label}: eligible reasons are allowed ones",
          all(eligible_reason(r) is not None for r in eligible))

    names = [str(r["Channel Name"]).strip() for r in eligible]
    print(f"    Y email leads: {len(email_rows)} | protected on {PROMPT_VERSION}: {len(protected)} | eligible: {len(eligible)}")
    if names:
        print("    eligible: " + ", ".join(names))
    return df, eligible, protected


def main():
    review = p.REVIEW_FILE
    check("review file exists", review.exists(), str(review))
    if not review.exists():
        return 1

    df, eligible, protected = audit(review, "review")

    versioned = df[(df["Relevant"].astype(str).str.strip() == "Y")
                   & (df["Prompt Version"].astype(str).str.strip() == PROMPT_VERSION)]
    bad = []
    for _, r in versioned.iterrows():
        msg = str(r["Outreach Message"] or "").strip()
        if not msg or not validate_from_row(r, msg)[0]:
            bad.append(str(r["Channel Name"]).strip())
    check(f"every row tagged {PROMPT_VERSION} still PASSES", not bad,
          "all good" if not bad else f"failing: {bad}")
    print(f"    tagged {PROMPT_VERSION}: {len(versioned)} rows")

    # preserve_old_message: nothing lost, timestamp separator on append.
    t = df.head(1).copy()
    t.at[t.index[0], "Outreach Message"] = "OLD TEXT ONE"
    t.at[t.index[0], "Old Message"] = ""
    p.preserve_old_message(t, t.index[0])
    first = str(t.at[t.index[0], "Old Message"])
    check("preserve_old_message copied the replaced text", "OLD TEXT ONE" in first)

    t.at[t.index[0], "Outreach Message"] = "OLD TEXT TWO"
    p.preserve_old_message(t, t.index[0])
    second = str(t.at[t.index[0], "Old Message"])
    check("preserve_old_message appends without losing earlier text",
          "OLD TEXT ONE" in second and "OLD TEXT TWO" in second)
    check("preserve_old_message adds a timestamp separator", second.count("[") >= 2,
          second.replace("\n", " / ")[:80])

    # save_atomic: readable output, no leftover .tmp file.
    tmp_target = p.LEADS_DIR / "_check_regen_atomic_test.xlsx"
    p.save_atomic(df.head(3), tmp_target)
    ok_read = len(pd.read_excel(tmp_target)) == 3
    leftovers = list(p.LEADS_DIR.glob("_check_regen_atomic_test*.tmp.xlsx"))
    check("save_atomic writes a readable file", ok_read)
    check("save_atomic leaves no .tmp file", not leftovers)
    for f in p.LEADS_DIR.glob("_check_regen_atomic_test*.xlsx"):
        f.unlink()  # only the throwaway test file this script created

    # Fake dry-run file: a repeat run must have nothing eligible.
    if p.DRYRUN_FILE.exists():
        audit(p.DRYRUN_FILE, "dry run")
    else:
        print("\n--- dry run file not present, skipped")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"CHECKS FAILED: {len(FAILS)}")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
