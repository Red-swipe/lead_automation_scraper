import sys
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

XLSX = r"C:\Users\DELL\OneDrive\Desktop\lead automation\leads\mentis_leads_with_messages.xlsx"

df = pd.read_excel(XLSX, dtype=str).fillna("")

before = df[df["Channel Name"].str.contains("DAWOOD.K VLOGS", case=False, na=False)][["Channel Name", "Email"]].to_string(index=False)
print("BEFORE:\n" + before + "\n")

mask = df["Channel Name"].str.contains("DAWOOD.K VLOGS", case=False, na=False)
print(f"Rows matched: {int(mask.sum())}")
df.loc[mask, "Email"] = df.loc[mask, "Email"].str.replace("gmil.com", "gmail.com", case=False)

after = df[df["Channel Name"].str.contains("DAWOOD.K VLOGS", case=False, na=False)][["Channel Name", "Email"]].to_string(index=False)
print("AFTER:\n" + after + "\n")

df.to_excel(XLSX, index=False)
print("Saved.")
