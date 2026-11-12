import sys
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEADS_FILE = r"C:\Users\DELL\OneDrive\Desktop\lead automation\leads\mentis_leads_with_messages.xlsx"

df = pd.read_excel(LEADS_FILE)

def has_value(v):
    return not pd.isna(v) and str(v).strip() != ""

sel = df[
    (df["Send Via"].astype(str).str.strip().str.lower() == "email")
    & (df["Email"].apply(has_value))
].copy()

print(f"Recipients to email: {len(sel)}")
print("=" * 110)

for i, (idx, r) in enumerate(sel.iterrows(), 1):
    name = str(r.get("Channel Name", ""))
    email = str(r.get("Email", "")).strip()
    via = str(r.get("Send Via", "")).strip()
    msg = str(r.get("Outreach Message", ""))[:100]
    print(f"{i:>2}. {name[:35]:<35} | {email:<40} | {via:<10} | {msg}")

print("=" * 110)
print("No emails were sent (dry run).")