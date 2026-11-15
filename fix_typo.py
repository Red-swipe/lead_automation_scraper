"""
Read-only typo checker: reports suspicious email domains and suggests
corrections. NEVER writes anything back to any file. Corrections are
applied by the owner manually or by explicit instruction in chat.
"""
import sys
import pandas as pd
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEADS_DIR = Path(__file__).resolve().parent / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"

# Common gmail.com misspellings seen or likely in scraped data
TYPO_DOMAINS = {
    "gmil.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gmaul.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.om": "gmail.com",
    "gmai.com": "gmail.com",
    "gmaill.com": "gmail.com",
}


def has_value(v):
    return not pd.isna(v) and str(v).strip() != ""


def main():
    if not REVIEW_FILE.exists():
        print(f"ERROR: {REVIEW_FILE.name} not found. Run lead_quality.py first.")
        return

    df = pd.read_excel(REVIEW_FILE, dtype=str).fillna("")

    suggestions = 0
    for i, row in df.iterrows():
        email = row.get("Email", "").strip()
        if not email or "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1].lower()
        # Exact domain match only, so gmail.co does not match inside gmail.com
        if domain in TYPO_DOMAINS:
            good = TYPO_DOMAINS[domain]
            fixed = email.rsplit("@", 1)[0] + "@" + good
            suggestions += 1
            print(f"[FLAG] {row.get('Channel Name', '').strip()}")
            print(f"       current:  {email}")
            print(f"       suggest:  {fixed}  (manual review, NOT changed)")
            break

    if suggestions == 0:
        print("No typo domain patterns found.")

    print(f"\nNo files were modified (flag-only mode). {suggestions} suggestion(s).")


if __name__ == "__main__":
    main()
