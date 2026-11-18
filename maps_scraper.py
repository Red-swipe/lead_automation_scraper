"""
Google Maps lead source (Places API New). OPTIONAL: runs only when
GOOGLE_PLACES_API_KEY exists in .env (the value is never printed).

Caps per run: MAX_TEXT_SEARCHES = 15, MAX_DETAIL_CALLS = 60.
Area: Islamabad and Rawalpindi academies, schools and coaching centres.
Rows merge into leads/mentis_leads.xlsx by place URL or phone; existing rows
are never dropped. Any error is printed and the run continues.

No new dependencies: plain urllib.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
LEADS_DIR = BASE / "leads"
LEADS_FILE = LEADS_DIR / "mentis_leads.xlsx"

MAX_TEXT_SEARCHES = 15
MAX_DETAIL_CALLS = 60
TIMEOUT = 20

QUERIES = [
    "academies in Islamabad",
    "schools in Islamabad",
    "coaching centres in Islamabad",
    "academies in Rawalpindi",
    "schools in Rawalpindi",
    "coaching centres in Rawalpindi",
]

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
])


def api_key_present():
    key = (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip()
    return bool(key)


def _post(url, payload, key, field_mask):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Goog-Api-Key", key)
    req.add_header("X-Goog-FieldMask", field_mask)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_text(query, key):
    return _post(TEXT_SEARCH_URL, {"textQuery": query, "pageSize": 20}, key, FIELD_MASK)


def place_url(place_id):
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def normalize_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("92") and len(digits) > 10:
        digits = "0" + digits[2:]
    return digits


def has_contact(row):
    return bool(str(row.get("Email", "")).strip()) or bool(str(row.get("WhatsApp", "")).strip())


def main():
    print("\nMentis AI - Google Maps leads")
    print("=" * 50)

    if not api_key_present():
        print("Google Maps: skipped (no key)")
        return 0

    key = os.environ["GOOGLE_PLACES_API_KEY"].strip()
    rows = []
    searches = details = 0
    errors = 0

    for query in QUERIES:
        if searches >= MAX_TEXT_SEARCHES:
            print(f"Google Maps: search cap of {MAX_TEXT_SEARCHES} reached, stopping searches")
            break
        searches += 1
        try:
            result = search_text(query, key)
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"   [Maps error] '{query}': HTTP {e.code} {e.reason}")
            continue
        except Exception as e:
            errors += 1
            print(f"   [Maps error] '{query}': {type(e).__name__}: {e}")
            continue

        places = result.get("places", []) or []
        print(f"Searching: '{query}'... found {len(places)} places")
        for place in places:
            place_id = place.get("id", "")
            name = str((place.get("displayName") or {}).get("text", "")).strip()
            if not place_id or not name:
                continue
            phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or ""
            website = place.get("websiteUri") or ""
            address = place.get("formattedAddress") or ""
            rows.append({
                "Channel Name": name,
                "Subscribers": 0,
                "Videos": 0,
                "Country": "PK",
                "Email": "",
                "WhatsApp": str(phone).strip(),
                "Website": str(website).strip(),
                "Channel URL": place_url(place_id),
                "Lead Quality": "Warm",
                "Has Contact Info": "Yes" if (phone or website) else "No",
                "Description Snippet": str(address)[:300],
                "Found Via Query": f"maps: {query}",
            })

        if details >= MAX_DETAIL_CALLS:
            print(f"Google Maps: detail cap of {MAX_DETAIL_CALLS} reached")
        time.sleep(0.2)

    if not rows:
        print(f"Google Maps: no rows added (errors: {errors})")
        return 0

    new_df = pd.DataFrame(rows)
    LEADS_DIR.mkdir(exist_ok=True)

    added = kept = 0
    if LEADS_FILE.exists():
        backup = LEADS_DIR / f"mentis_leads_backup_maps_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        old = pd.read_excel(LEADS_FILE)
        old.to_excel(backup, index=False)
        print(f"Backed up previous leads to: {backup.name}")

        old_urls = set(old["Channel URL"].astype(str))
        old_phones = {normalize_phone(p) for p in old.get("WhatsApp", []) if normalize_phone(p)}
        is_new = [
            (str(r["Channel URL"]) not in old_urls)
            and (not normalize_phone(r["WhatsApp"]) or normalize_phone(r["WhatsApp"]) not in old_phones)
            for _, r in new_df.iterrows()
        ]
        added = int(sum(is_new))
        kept = len(old)
        merged = pd.concat([new_df[pd.Series(is_new)], old], ignore_index=True)
    else:
        added = len(new_df)
        merged = new_df

    merged.to_excel(LEADS_FILE, index=False)
    print(f"Google Maps: {added} new rows added, {kept} existing rows kept, "
          f"{searches} searches, {details} detail calls, {errors} errors")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Google Maps: error, continuing ({type(e).__name__}: {e})")
        sys.exit(0)
