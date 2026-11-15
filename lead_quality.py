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
    typo_mask = df["Email"].fillna("").astype(str).str.contains(r"@(gmil|gamil|gmaul|gmal)\.", case=False, regex=True)
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
    try:
        prev = pd.read_excel(MESSAGES_FILE)
        msg_by_url = {}
        for _, r in prev.iterrows():
            m = "" if pd.isna(r.get("Outreach Message", "")) else str(r["Outreach Message"]).strip()
            if m and not m.startswith("[ERROR"):
                msg_by_url[str(r.get("Channel URL", ""))] = (m, str(r.get("Send Via", "")))
        matched = df["Channel URL"].astype(str).map(lambda u: u in msg_by_url)
        df["Outreach Message"] = [msg_by_url.get(u, ("", ""))[0] for u in df["Channel URL"].astype(str)]
        df["Send Via"] = [msg_by_url.get(u, ("", ""))[1] for u in df["Channel URL"].astype(str)]
        print(f"Carried over {int(matched.sum())} existing valid messages (keyed by Channel URL)")
    except FileNotFoundError:
        df["Outreach Message"] = ""
        df["Send Via"] = ""

    # --- Routing fix: both email and WhatsApp -> Send Via = Email ------
    has_em = df["Email"].fillna("").astype(str).str.strip() != ""
    has_wa = df["WhatsApp"].fillna("").astype(str).str.strip() != ""
    both = has_em & has_wa
    df.loc[both, "Send Via"] = "Email"
    print(f"Routing fix: {int(both.sum())} leads with both email and WhatsApp set to Send Via = Email")
    for i in df[both].index:
        print(f"   {df.loc[i, 'Channel Name'].strip()} -> Email")

    df.to_excel(OUTPUT_FILE, index=False)

    # --- Summary -------------------------------------------------------
    print(f"\nSaved: {OUTPUT_FILE.name} ({len(df)} rows, source file untouched)")
    y = df[(df["Relevant"] == "Y") & (has_em)]
    print(f"Relevant Y with email: {len(y)}")
    print(f"Relevant N: {int((df['Relevant'] == 'N').sum())}")
    flagged = df[df["Review Flag"] != ""]
    print(f"Review flags: {len(flagged)}")
    for _, r in flagged.iterrows():
        print(f"   {str(r['Channel Name']).strip()} | {r['Review Flag']}")


if __name__ == "__main__":
    main()
    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass
