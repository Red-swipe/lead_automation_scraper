import os
import sys
import time
import pandas as pd
import google.generativeai as genai
import google.api_core.exceptions

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEADS_FILE = r"C:\Users\DELL\OneDrive\Desktop\lead automation\leads\mentis_leads.xlsx"
OUTPUT_FILE = r"C:\Users\DELL\OneDrive\Desktop\lead automation\leads\mentis_leads_with_messages.xlsx"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-flash-latest")

MAX_ATTEMPTS = 6
QUOTA_BASE_WAIT = 15
QUOTA_WAIT_CAP = 60

def generate_message(channel_name, subscribers, niche, description, has_email, has_whatsapp):
    if has_whatsapp:
        channel_type = "WhatsApp"
        length_note = "3-4 sentences max, very casual, like a real WhatsApp message"
    else:
        channel_type = "email"
        length_note = "4-5 sentences, professional but warm, not corporate"

    prompt = f"""You are writing outreach for Mentis AI, a Pakistani AI automation company.

Lead details:
- Channel/Academy Name: {channel_name}
- Subscribers: {subscribers:,}
- Niche: {niche}
- Description snippet: {description[:300]}
- Contact channel: {channel_type}

Write a {channel_type} message that:
1. Opens with ONE specific observation about their channel or work (not generic praise)
2. Mentions ONE real problem educators/tutors at their scale face - pick the most relevant:
   - Under 5,000 subs: struggling to manage student inquiries manually via WhatsApp
   - 5,000-50,000 subs: losing leads because they cant reply fast enough to admission questions
   - Above 50,000 subs: spending too much time on repetitive student queries instead of content
3. Introduces Mentis AI in ONE casual sentence as a solution (we build AI systems that handle this automatically)
4. Ends with a soft low-pressure CTA like "would it be worth a 10-minute chat?" or "want me to show you how it works?"

Rules:
- Sound like a real human not a marketing email
- No bullet points
- No "I hope this message finds you well"
- No "My name is..." opening
- Write in English
- dont use hyphens
- {length_note}

Return ONLY the message text. Nothing else."""

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            last_error = str(e)
            is_quota = isinstance(e, google.api_core.exceptions.ResourceExhausted) or "429" in last_error
            if not is_quota or attempt == MAX_ATTEMPTS:
                return f"[ERROR: {last_error}]"
            delay = min(QUOTA_WAIT_CAP, QUOTA_BASE_WAIT * (2 ** (attempt - 1)))
            print(f"\n   [Quota exceeded 429] attempt {attempt}/{MAX_ATTEMPTS} - retrying in {delay}s...")
            time.sleep(delay)
    return f"[ERROR: quota retries exhausted - {last_error}]"

def main():
    print("\nMentis AI - Message Personalizer (Gemini)")
    print("=" * 50)

    try:
        df = pd.read_excel(LEADS_FILE)
    except FileNotFoundError:
        print(f"\nERROR: File not found at {LEADS_FILE}")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    print(f"Loaded {len(df)} total leads")

    contactable = df[df["Has Contact Info"] == "Yes"].copy()
    print(f"Leads with contact info: {len(contactable)}")

    if len(contactable) == 0:
        print("\nNo contactable leads found.")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    hot_warm = contactable[contactable["Lead Quality"].isin(["Hot", "Warm"])].copy()
    cold = contactable[contactable["Lead Quality"] == "Cold"].copy()
    to_process = pd.concat([hot_warm, cold]).reset_index(drop=True)

    print(f"Hot/Warm with contact: {len(hot_warm)}")
    print(f"Cold with contact: {len(cold)}")
    print(f"\nGenerating messages for {len(to_process)} leads...")
    print("=" * 50)

    to_process["Outreach Message"] = ""
    to_process["Send Via"] = ""

    done_messages = {}
    try:
        prev = pd.read_excel(OUTPUT_FILE)
        for _, r in prev.iterrows():
            m_val = r.get("Outreach Message", "")
            m = "" if pd.isna(m_val) else str(m_val).strip()
            if m and not m.startswith("[ERROR"):
                done_messages[str(r.get("Channel Name", ""))] = m
    except Exception:
        pass
    if done_messages:
        print(f"Resuming - {len(done_messages)} leads already have messages")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    for i, row in to_process.iterrows():
        channel_name = str(row.get("Channel Name", ""))
        subscribers = int(row.get("Subscribers", 0))
        niche = str(row.get("Found Via Query", "education"))
        description = str(row.get("Description Snippet", ""))
        email_val = row.get("Email", "")
        wa_val = row.get("WhatsApp", "")
        has_email = (not pd.isna(email_val)) and str(email_val).strip() != ""
        has_whatsapp = (not pd.isna(wa_val)) and str(wa_val).strip() != ""
        channel = "WhatsApp" if has_whatsapp else "Email"

        print(f"\n[{i+1}/{len(to_process)}] {channel_name} | {subscribers:,} subs | {row.get('Lead Quality')}")

        if channel_name in done_messages:
            message = done_messages[channel_name]
            print("   (already has a message - resuming)")
        else:
            message = generate_message(channel_name, subscribers, niche, description, has_email, has_whatsapp)
            done_messages[channel_name] = message

        preview = message[:120].replace("\n", " ")
        print(f"   {preview}...")

        to_process.loc[i, "Outreach Message"] = message
        to_process.loc[i, "Send Via"] = channel
        try:
            to_process.to_excel(OUTPUT_FILE, index=False)
            print(f"   [saved - {i+1}/{len(to_process)}]")
        except Exception as e:
            print(f"   [save warning: {e}]")

        time.sleep(4)

    print("\n" + "=" * 50)
    print(f"DONE - Messages written for {len(to_process)} leads")
    print(f"SAVED to: {OUTPUT_FILE}")
    print("=" * 50)

    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass

if __name__ == "__main__":
    main()
