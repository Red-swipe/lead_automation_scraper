import os
import re
import sys
import time
import pandas as pd
from pathlib import Path
from googleapiclient.discovery import build

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.environ.get("GOOGLE_API_KEY")

# ============================================================
# SEARCH KEYWORDS
# Targeting Pakistani tutors, academies, online educators
# ============================================================
SEARCH_QUERIES = [
    "Pakistan online academy tuition",
    "O levels tutor Pakistan",
    "A levels academy Pakistan",
    "IELTS coaching Pakistan",
    "online tutor Pakistan paid course",
    "matric tuition Pakistan",
    "FSc tutor Pakistan",
    "online academy Pakistan admission",
    "Pakistan coaching center",
    "tutor Pakistan WhatsApp batch",
    "MDCAT academy Pakistan",
    "ECAT preparation Pakistan online",
    "CSS preparation academy Pakistan",
    "Urdu medium tuition Pakistan",
    "Cambridge IGCSE tutor Pakistan",
    "SAT preparation Pakistan online",
    "Quran tutor online Pakistan",
    "online tutors Pakistan learn online",
    "English speaking course Pakistan",
    "spoken English classes Pakistan online",
]

MAX_RESULTS_PER_QUERY = 20  # YouTube allows max 50, keep at 20 to save quota

# ============================================================
# HELPER — extract emails from text
# ============================================================
def extract_emails(text):
    if not text:
        return ""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    return ", ".join(set(emails))

# ============================================================
# HELPER — extract WhatsApp numbers from text
# ============================================================
def extract_whatsapp(text):
    if not text:
        return ""
    # Matches Pakistani numbers: 03xx, +923xx, 923xx
    pattern = r'(?:\+92|92|0)3[0-9]{9}'
    numbers = re.findall(pattern, text)
    return ", ".join(set(numbers))

# ============================================================
# HELPER — extract website links from text
# ============================================================
def extract_website(text):
    if not text:
        return ""
    pattern = r'https?://[^\s\)\"\'<>]+'
    links = re.findall(pattern, text)
    # Filter out YouTube, Facebook, Instagram links
    external = [l for l in links if not any(x in l for x in [
        "youtube.com", "youtu.be", "facebook.com",
        "instagram.com", "twitter.com", "linktr.ee"
    ])]
    return external[0] if external else ""

# ============================================================
# HELPER — qualify lead based on subscriber count + keywords
# ============================================================
def qualify_lead(subscriber_count, description):
    if subscriber_count < 500:
        return "Cold"
    
    hot_keywords = ["admission", "enroll", "batch", "paid", "fee", "register", 
                    "course", "tuition", "WhatsApp", "join", "class"]
    desc_lower = (description or "").lower()
    keyword_hits = sum(1 for kw in hot_keywords if kw.lower() in desc_lower)
    
    if subscriber_count >= 5000 and keyword_hits >= 2:
        return "Hot"
    elif subscriber_count >= 1000 and keyword_hits >= 1:
        return "Warm"
    else:
        return "Cold"

# ============================================================
# MAIN SCRAPER
# ============================================================
def scrape_youtube_leads():
    print("\nMentis AI - YouTube Lead Scraper")
    print("=" * 50)
    
    youtube = build("youtube", "v3", developerKey=API_KEY)
    
    all_leads = []
    seen_channel_ids = set()  # avoid duplicates
    skipped_no_contact = 0
    
    for query in SEARCH_QUERIES:
        print(f"\nSearching: '{query}'")
        
        try:
            # Step 1: Search for videos matching the query
            search_response = youtube.search().list(
                q=query,
                part="snippet",
                type="video",
                maxResults=MAX_RESULTS_PER_QUERY,
                relevanceLanguage="en",
                regionCode="PK"
            ).execute()
            
            # Extract unique channel IDs from search results
            channel_ids = []
            for item in search_response.get("items", []):
                cid = item["snippet"]["channelId"]
                if cid not in seen_channel_ids:
                    channel_ids.append(cid)
                    seen_channel_ids.add(cid)
            
            if not channel_ids:
                print(f"   No results found.")
                continue
            
            # Step 2: Get full channel details (description, subscriber count, etc.)
            channels_response = youtube.channels().list(
                part="snippet,statistics,brandingSettings",
                id=",".join(channel_ids)
            ).execute()
            
            for channel in channels_response.get("items", []):
                snippet = channel.get("snippet", {})
                stats = channel.get("statistics", {})
                
                channel_name = snippet.get("title", "")
                description = snippet.get("description", "")
                country = snippet.get("country", "")
                channel_url = f"https://www.youtube.com/channel/{channel['id']}"
                
                # Subscriber count (YouTube hides exact count sometimes)
                try:
                    subscribers = int(stats.get("subscriberCount", 0))
                except ValueError:
                    subscribers = 0
                
                video_count = stats.get("videoCount", 0)
                
                # Extract contact info
                email = extract_emails(description)
                whatsapp = extract_whatsapp(description)
                website = extract_website(description)
                
                # Skip leads with no way to contact them
                if not email and not whatsapp:
                    skipped_no_contact += 1
                    print(f"   [SKIP] {channel_name} | {subscribers:,} subs | no email or WhatsApp")
                    continue

                # Qualify the lead
                quality = qualify_lead(subscribers, description)
                
                lead = {
                    "Channel Name": channel_name,
                    "Subscribers": subscribers,
                    "Videos": video_count,
                    "Country": country,
                    "Email": email,
                    "WhatsApp": whatsapp,
                    "Website": website,
                    "Channel URL": channel_url,
                    "Lead Quality": quality,
                    "Has Contact Info": "Yes" if (email or whatsapp) else "No",
                    "Description Snippet": description[:300].replace("\n", " "),
                    "Found Via Query": query,
                }
                
                all_leads.append(lead)
                
                contact_indicator = "[EMAIL]" if email else ("[WA]" if whatsapp else "[NONE]")
                print(f"   {contact_indicator} {channel_name} | {subscribers:,} subs | {quality} | {email or whatsapp or 'no contact'}")
            
            # Respect API rate limits
            time.sleep(0.5)
            
        except Exception as e:
            print(f"   [ERROR] on query '{query}': {e}")
            continue
    
    # ============================================================
    # SAVE RESULTS
    # ============================================================
    if not all_leads:
        print("\nNo leads found. Check your API key.")
        return
    
    df = pd.DataFrame(all_leads)
    
    # Sort: Hot leads first, then by subscriber count
    quality_order = {"Hot": 0, "Warm": 1, "Cold": 2}
    df["Quality Sort"] = df["Lead Quality"].map(quality_order)
    df = df.sort_values(["Quality Sort", "Subscribers"], ascending=[True, False])
    df = df.drop(columns=["Quality Sort"])
    
    # Save to Excel
    LEADS_DIR = Path(__file__).resolve().parent / "leads"
    LEADS_DIR.mkdir(exist_ok=True)
    output_file = LEADS_DIR / "mentis_leads.xlsx"

    # Merge by Channel URL instead of overwriting: never drop existing rows.
    # Back up the current file first (timestamped, never deleted).
    if output_file.exists():
        backup = LEADS_DIR / f"mentis_leads_backup_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        pd.read_excel(output_file).to_excel(backup, index=False)
        print(f"Backed up previous leads to: {backup.name}")
        old = pd.read_excel(output_file)
        new_urls = set(df["Channel URL"].astype(str))
        kept = old[~old["Channel URL"].astype(str).isin(new_urls)]
        if len(kept):
            df = pd.concat([df, kept], ignore_index=True)
            print(f"Merged: kept {len(kept)} previous leads not found this run")
    df.to_excel(output_file, index=False)
    
    # Summary
    print("\n" + "=" * 50)
    print(f"DONE - {len(df)} leads scraped")
    print(f"HOT:  {len(df[df['Lead Quality'] == 'Hot'])}")
    print(f"WARM: {len(df[df['Lead Quality'] == 'Warm'])}")
    print(f"COLD: {len(df[df['Lead Quality'] == 'Cold'])}")
    print(f"EMAIL:    {len(df[df['Email'] != ''])}")
    print(f"WHATSAPP: {len(df[df['WhatsApp'] != ''])}")
    print(f"SKIPPED (no contact info): {skipped_no_contact}")
    print(f"\nSAVED to: {output_file}")
    print("=" * 50)

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    scrape_youtube_leads()
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
