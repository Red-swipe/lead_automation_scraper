import sys
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

LEADS_DIR = Path(__file__).resolve().parent / "leads"
LEADS_FILE = LEADS_DIR / "mentis_leads.xlsx"  # fresh scraper output
LEADS_WITH_MESSAGES = LEADS_DIR / "mentis_leads_with_messages.xlsx"  # output of personalize.py

def has_value(v):
    return not pd.isna(v) and str(v).strip() != ""

if not LEADS_WITH_MESSAGES.exists():
    print(f"ERROR: {LEADS_WITH_MESSAGES.name} not found.")
    print("Run personalize.py first to generate messages for the latest leads.")
    sys.exit(1)

# Staleness check: compare against the latest scraper output
fresh = pd.read_excel(LEADS_FILE)
df = pd.read_excel(LEADS_WITH_MESSAGES)
fresh_channels = set(fresh["Channel Name"].astype(str))
old_channels = set(df["Channel Name"].astype(str))
stale = fresh_channels - old_channels
if stale:
    print(f"[STALE] {LEADS_WITH_MESSAGES.name} is missing {len(stale)} of {len(fresh_channels)}")
    print("channels from the latest scraper output. Run personalize.py to refresh.")
    print(f"Example stale channels: {sorted(stale)[:5]}")
    print()

sel = df[
    (df["Send Via"].astype(str).str.strip().str.lower() == "email")
    & (df["Email"].apply(has_value))
].copy()

with_msg = sel[sel["Outreach Message"].apply(has_value)]
skipped_no_msg = len(sel) - len(with_msg)
skipped_wa = len(df) - len(sel)

print(f"Excel file in use: {LEADS_WITH_MESSAGES.name}")
print(f"Total leads in file: {len(df)} | With messages: {len(with_msg)}")
if skipped_no_msg:
    print(f"Skipped: {skipped_no_msg} email leads have no message (run personalize.py)")
if skipped_wa:
    print(f"Skipped: {skipped_wa} leads are WhatsApp-only (not sent by email)")
print("=" * 110)

for i, (idx, r) in enumerate(sel.iterrows(), 1):
    name = str(r.get("Channel Name", ""))
    email = str(r.get("Email", "")).strip()
    via = str(r.get("Send Via", "")).strip()
    msg = str(r.get("Outreach Message", ""))[:100]
    print(f"{i:>2}. {name[:35]:<35} | {email:<40} | {via:<10} | {msg}")

print("=" * 110)
print("No emails were sent (dry run).")