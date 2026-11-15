"""
Light website check for leads that have a URL in their channel data.

- Uses the already-installed requests library, GET with a 10 second timeout.
- Writes results into new columns Website URL and Website Notes in the
  review file (mentis_leads_review.xlsx). Original files are never touched.
- Leads with no URL in the data get "no website found in channel data".
  This is NOT a claim that they have no website.
- A WhatsApp channel link is not a website, so it is recorded as such
  and not fetched.

No messages are sent, nothing is called except the URLs found in the data.
"""
import sys
import re
import time
import json
import requests
import pandas as pd
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEADS_DIR = Path(__file__).resolve().parent / "leads"
REVIEW_FILE = LEADS_DIR / "mentis_leads_review.xlsx"

TIMEOUT = 10
NOT_A_WEBSITE_PREFIXES = ("https://whatsapp.com", "https://chat.whatsapp.com",
                          "https://wa.me", "https://t.me")


def check_site(url):
    notes = []
    https_ok = url.lower().startswith("https://")
    notes.append("https" if https_ok else "no https (http only)")

    start = time.time()
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 (site check)"})
    except requests.exceptions.Timeout:
        notes.append("no response within 10s (slow or down)")
        return notes
    except requests.exceptions.RequestException as e:
        notes.append(f"unreachable ({type(e).__name__})")
        return notes

    elapsed = round(time.time() - start, 1)
    notes.append(f"status {resp.status_code}, {elapsed}s")
    if elapsed > 3:
        notes.append(f"load time over 3 seconds (slow: {elapsed}s)")

    html = resp.text or ""
    notes.append(f"page size {len(html)} chars")

    if re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I):
        notes.append("mobile viewport: yes")
    else:
        notes.append("mobile viewport: NO (site may look bad on phones)")

    years = re.findall(r"(?:&copy;|©|\(c\)|copyright)\D{0,15}(20\d{2})", html, re.I)
    if years:
        latest = max(int(y) for y in years)
        notes.append(f"footer copyright year: {latest}" + (" (outdated)" if latest < time.localtime().tm_year - 1 else ""))
    else:
        notes.append("no copyright year found")

    return notes


def main():
    df = pd.read_excel(REVIEW_FILE)
    df["Website URL"] = ""
    df["Website Notes"] = ""

    checked = 0
    for i, row in df.iterrows():
        raw = row.get("Website", "")
        url = "" if pd.isna(raw) else str(raw).strip()
        name = str(row["Channel Name"]).strip()

        if not url:
            df.loc[i, "Website Notes"] = "no website found in channel data"
            continue

        if url.lower().startswith(NOT_A_WEBSITE_PREFIXES):
            df.loc[i, "Website URL"] = ""
            df.loc[i, "Website Notes"] = f"no website found in channel data (link was {url.split('/')[2] if '://' in url else url})"
            continue

        df.loc[i, "Website URL"] = url
        notes = "; ".join(check_site(url))
        df.loc[i, "Website Notes"] = notes
        checked += 1
        print(f"[CHECKED] {name}: {notes}")

    df.to_excel(REVIEW_FILE, index=False)
    print(f"\nDone. {checked} site(s) fetched, results written to {REVIEW_FILE.name}")
    print("No files deleted, no messages sent.")


if __name__ == "__main__":
    main()
