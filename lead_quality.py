"""
Build leads/mentis_leads_review.xlsx: a NEW copy of the lead data with
Relevant (Y/N), Relevance Reason and Review Flag columns.

Rules:
- NEVER modifies mentis_leads.xlsx or mentis_leads_with_messages.xlsx.
- Relevant Y/N values here are PROPOSALS shown to the owner; the owner
  approves or edits them before any personalization run uses this file.
- Dedupe by email address: only the first (largest channel) gets outreach.
- Routing: any lead with both Email and WhatsApp gets Send Via = Email.
- Existing valid messages are carried over keyed by Channel URL, so the
  17 already-written messages are never regenerated.
"""
import sys
import pandas as pd
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEADS_DIR = Path(__file__).resolve().parent / "leads"
SOURCE_FILE = LEADS_DIR / "mentis_leads.xlsx"
MESSAGES_FILE = LEADS_DIR / "mentis_leads_with_messages.xlsx"
OUTPUT_FILE = LEADS_DIR / "mentis_leads_review.xlsx"
# ---------------------------------------------------------------
# Owner-proposed relevance verdicts (Channel Name -> [Y/N, reason])
# Marked N by owner instruction: Captain Halal, TheeTube, Mr How,
# AK Designer Art (handled below, not in this dict).
# ---------------------------------------------------------------
PROPOSALS = {
    "IELTS Advantage": ["Y", "IELTS course business, sells paid courses, ideal client for inquiry automation"],
    "Asad Yaqub IELTS": ["Y", "Pakistani IELTS educator with large audience and admissions/batch inquiries"],
    "Education Updates": ["N", "Jobs/scholarships news portal, not an education business selling courses"],
    "Naveed Jamal": ["Y", "Career counselor and education consultant, direct fit for student inquiry automation"],
    "Perfect Info": ["Y", "Educational career guidance channel for Pakistani students"],
    "English with Bilal": ["Y", "English teaching channel, Pakistani, paid class audience"],
    "Al-Noor Online Academy": ["Y", "Online Islamic education academy with admission flow"],
    "Aleena Rais Live": ["Y", "Large English communication educator, keeps the shared hello@aleenarais.com contact"],
    "Sugandha Sharma": ["N", "Indian career/job content channel, outside target market"],
    "Raftar": ["N", "Storytelling and social change platform, not education services"],
    "Learn With Shalima": ["Y", "Spoken English and personality development trainer"],
    "Startup Pakistan Podcast": ["N", "Startup/entrepreneur podcast, already on skip list"],
    "Tech Babo Vai": ["N", "Bangla tech review channel, not education"],
    "Sheeza Rana": ["Y", "Online educator for digital skills (owner to review: possible duplicate of Aleena Rais)"],
    "Universities Page Pakistan": ["Y", "Study abroad consultancy, education services business"],
    "Dr Farooq Buzdar Baloch (Hindi & Urdu Videos)": ["N", "Vague research/self-help content, no clear education business"],
    "Career With Riwas": ["Y", "Career coaching educator for students"],
    "Dr. ASA Medic": ["Y", "MDCAT guidance educator, founder of an academy"],
    "DAILY PAKISTAN JOBS": ["N", "Jobs listings channel, already on skip list"],
    "Career Selection with Raza Shah": ["Y", "Student counseling and study abroad guidance service"],
    "ExpHub - Prashant Kirad": ["N", "Indian JEE/NEET educator, outside target market"],
    "HBA Services": ["N", "Freelancing/earn-online content, not an education business"],
    "90s Mentor": ["N", "Make money online content, not a tutoring/academy business"],
    "Tech Frack": ["N", "Tech tips and gadget reviews, not education"],
    "Umar Tazkeer": ["N", "Marketing agency owner, not an education business"],
    "Bukhari Speaks": ["N", "General Urdu motivational channel, no education service"],
    "Edusquadz": ["N", "Indian edtech company itself (Indore), wrong target profile"],
    "RISHAV TECH": ["N", "Tech channel, not education"],
    "Anam Khan Siddique": ["N", "Freelancing and blogging income content, not education services"],
    "Mashooq Younis": ["N", "Tech unboxing and tips channel"],
    "Dr Hamza Ashraf": ["Y", "MDCAT and FSc guidance educator, Pakistani"],
    "Ayan Assassin": ["N", "General career/life advice, no education business"],
    "Urdu Dictation": ["N", "Personal Urdu literacy channel, no paid education service"],
    "Sheikh Muhammad Ali": ["Y", "MDCAT guidance content by a medical student, education niche"],
    "Rabia Khan": ["Y", "Study abroad and scholarship guidance educator"],
    "Life with rarity": ["N", "Lifestyle channel, not education"],
    "Physiogic": ["N", "Physiotherapy clinic content, not a tutoring business"],
    "Sawera Begum Official": ["N", "Vague free learning platform, no clear education service"],
    "Tech Ayesha Mashhood": ["N", "Online earning tips channel, not education"],
    "CSS with Syed Younis Rashdi": ["Y", "CSS exam preparation educator, Pakistani"],
    "LACAS Home Tutors": ["Y", "Home tutoring service with website, textbook ideal client"],
    "Champions Science Academy": ["Y", "O/A level and entry test preparation academy"],
    "Muhammad Hasnaat": ["Y", "Free learning portal for Pakistani intermediate/commerce students, WhatsApp contact"],
    "DAWOOD.K VLOGS": ["N", "Vlogs on social and financial issues, not education"],
    "TechBoy Gulfam Ali": ["N", "SEO and content writing consultant, not an education business"],
    "Zain Malik Gymnast": ["N", "Gymnastics/fitness coach, not academic education"],
    "Dr Ali Hamza": ["N", "Medical student vlogs, no coaching or education service"],
    "Tauqir Academy": ["Y", "Urdu medium academy selling model papers and video lectures, WhatsApp contact"],
    "Urdu Arabic Education اردو عربی ایجوکیشن": ["N", "Vague Urdu/Arabic language content channel, no education service"],
    "Dr. Nadia Nadeem": ["Y", "Runs BeMed online premed/MDCAT coaching"],
    "Torchbearer CSS Academy - TBA Karachi": ["Y", "CSS/PMS preparation academy opening a Karachi campus"],
    "Dogar Publishers": ["Y", "Leading exam preparation and academic book publisher"],
    "Rafiageeks": ["N", "Tech and freelancing tips channel, 42 subscribers, not education"],
    # --- New channels from the 2026-11-17 scrape (applied by approved rule) ---
    "irtisam's biology": ["Y", "Pakistani biology tutor for matric/FSc/BSc, WhatsApp contact, hot lead"],
    "IQRA Quranics": ["Y", "Online Quran academy selling courses (Qaida, Tajweed), WhatsApp contact"],
    "Pak Dafence ISSB Coaching Centre": ["Y", "ISSB coaching centre in Lahore, education business"],
    "Education and Pedagogy by Saleh Muhammad": ["Y", "Education content channel for students and teachers, WhatsApp contact"],
    "Catalyst Academics": ["Y", "A-levels/medical teaching with contact email, small but a tutor"],
    "English  With Saify": ["Y", "Pakistani English teaching channel, tutor fit"],
    "Hafiz Aftab Alam": ["Y", "O/A level and FSc tutor with parent guidance, WhatsApp contact"],
    "Dear Sir": ["N", "Indian edutainment mega-channel, outside target market"],
    "Nidhi Mishra": ["N", "Indian consultant training teachers to use AI, not a Pakistani education business"],
    "Manish Digital Academy": ["N", "Indian AI tools/productivity content, not education services"],
    "Dr. Farooq Baloch": ["N", "Same channel as Dr Farooq Buzdar Baloch (already N), vague self-help content"],
}

NOT_RELEVANT = {
    "Captain Halal": "Owner-marked irrelevant (not education)",
    "TheeTube": "Owner-marked irrelevant (not education)",
    "Mr How": "Owner-marked irrelevant (not education)",
    "AK Designer Art": "Owner-marked irrelevant (not education)",
}


def main():
    df = pd.read_excel(SOURCE_FILE)
    df["Relevant"] = ""
    df["Relevance Reason"] = ""
    df["Review Flag"] = ""

    for i, row in df.iterrows():
        name = str(row["Channel Name"]).strip()
        if name in NOT_RELEVANT:
            df.loc[i, "Relevant"] = "N"
            df.loc[i, "Relevance Reason"] = NOT_RELEVANT[name]
        elif name in PROPOSALS:
            df.loc[i, "Relevant"] = PROPOSALS[name][0]
            df.loc[i, "Relevance Reason"] = PROPOSALS[name][1]
        else:
            df.loc[i, "Relevant"] = "?"
            df.loc[i, "Relevance Reason"] = "No proposal yet"

    # --- Review flags -------------------------------------------------
    # DAWOOD.K VLOGS: gmil.com typo -> manual review, never auto-corrected
    typo_mask = df["Email"].fillna("").astype(str).str.contains(r"@(?:gmil|gamil|gmaul|gmal)\.", case=False, regex=True)
    for i in df[typo_mask].index:
        df.loc[i, "Review Flag"] = "EMAIL TYPO: looks like gmail.com misspelled, manual review required"

    # Dedupe by email: same address -> keep first row (larger channel listed first), flag the rest
    em = df["Email"].fillna("").astype(str).str.strip()
    contactable = df[em != ""]
    dup_mask = contactable.duplicated(subset=["Email"], keep="first")
    for i in contactable[dup_mask].index:
        if df.loc[i, "Review Flag"]:
            df.loc[i, "Review Flag"] += "; DUPLICATE EMAIL with earlier row, only one gets outreach"
        else:
            df.loc[i, "Review Flag"] = "DUPLICATE EMAIL with earlier row, only one gets outreach"
        df.loc[i, "Relevant"] = "N"
        df.loc[i, "Relevance Reason"] = "Duplicate email address, deduped (one email per address)"

    # --- Carry over existing messages keyed by Channel URL ------------
    msg_by_url = {}
    raw_by_url = {}
    version_by_url = {}
    sources = []
    if OUTPUT_FILE.exists():
        sources.append((OUTPUT_FILE, "review (newest)"))
    if MESSAGES_FILE.exists():
        sources.append((MESSAGES_FILE, "messages (older)"))
    for f, label in reversed(sources):
        try:
            prev = pd.read_excel(f)
            for _, r in prev.iterrows():
                m = "" if pd.isna(r.get("Outreach Message", "")) else str(r["Outreach Message"]).strip()
                u = str(r.get("Channel URL", ""))
                if m and not m.startswith("[ERROR"):
                    msg_by_url[u] = (m, str(r.get("Send Via", "")))
                    v = str(r.get("Prompt Version", "") or "").strip()
                    if v and v.lower() != "nan":
                        version_by_url[u] = v
                if m:
                    raw_by_url[u] = m
        except Exception as e:
            print(f"Note: could not read {f.name}: {e}")
    matched = df["Channel URL"].astype(str).map(lambda u: u in msg_by_url)
    # Old Message preserves EVERY original value (valid or error) and is
    # never overwritten later. Outreach Message starts from valid ones only.
    df["Old Message"] = df["Channel URL"].astype(str).map(raw_by_url).fillna("")
    df["Outreach Message"] = [msg_by_url.get(u, ("", ""))[0] for u in df["Channel URL"].astype(str)]
    df["Send Via"] = [msg_by_url.get(u, ("", ""))[1] for u in df["Channel URL"].astype(str)]
    # The prompt version that produced each carried message must survive the
    # rebuild, otherwise approved messages would look stale and become eligible.
    df["Prompt Version"] = df["Channel URL"].astype(str).map(version_by_url).fillna("")
    print(f"Carried over {int(matched.sum())} existing valid messages (keyed by Channel URL)")
    print(f"Preserved {int((df['Old Message'].fillna('').astype(str).str.strip() != '').sum())} old messages (valid and error) in Old Message column")

    # --- Routing fix: both email and WhatsApp -> Send Via = Email ------
    has_em = df["Email"].fillna("").astype(str).str.strip() != ""
    has_wa = df["WhatsApp"].fillna("").astype(str).str.strip() != ""
    both = has_em & has_wa
    df.loc[both, "Send Via"] = "Email"
    print(f"Routing fix: {int(both.sum())} leads with both email and WhatsApp set to Send Via = Email")
    for i in df[both].index:
        print(f"   {df.loc[i, 'Channel Name'].strip()} -> Email")

    # --- Priority: small and mid academies/tutors first, >1M subs last ---
    def priority(subs):
        try:
            subs = int(subs)
        except (ValueError, TypeError):
            subs = 0
        if subs < 50000:
            return "P1 (small/mid, contact first)"
        if subs < 1000000:
            return "P2 (mid/large)"
        return "P3 (over 1M subs, last)"
    df["Priority"] = df["Subscribers"].map(priority)

    df.to_excel(OUTPUT_FILE, index=False)

    # --- Summary -------------------------------------------------------
    print(f"\nSaved: {OUTPUT_FILE.name} ({len(df)} rows, source file untouched)")
    y = df[(df["Relevant"] == "Y") & (has_em)]
    print(f"Relevant Y with email: {len(y)}")
    print(f"Relevant N: {int((df['Relevant'] == 'N').sum())}")
    print(f"Relevance counts: Y={int((df['Relevant'] == 'Y').sum())} "
          f"N={int((df['Relevant'] == 'N').sum())} "
          f"?={int((df['Relevant'] == '?').sum())}")
    unknown = df[df["Relevant"] == "?"]
    if len(unknown):
        print("Unknown '?' leads (never messaged, need your verdict):")
        for _, r in unknown.iterrows():
            print(f"   {str(r['Channel Name']).strip()}")
    flagged = df[df["Review Flag"] != ""]
    print(f"Review flags: {len(flagged)}")
    for _, r in flagged.iterrows():
        print(f"   {str(r['Channel Name']).strip()} | {r['Review Flag']}")


if __name__ == "__main__":
    # No input() here: this script runs as a child of run_pipeline.py, which
    # owns the pause at the end of the run.
    main()
