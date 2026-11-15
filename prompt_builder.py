"""
Prompt builder for Mentis AI outreach.

Selects ONE primary product (and at most one secondary) from products.json
based on lead type and facts, and builds the generation prompt.

NO API CALLS happen in this module. personalize.py will import
build_prompt() once the owner approves the wording.
"""
import json
from pathlib import Path

PRODUCTS_FILE = Path(__file__).resolve().parent / "products.json"

def load_products():
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["products"]

def classify_lead(lead):
    """Return (lead_type, primary, secondary) using only facts from the lead row.

    lead_type: academy | tutor | online educator | consultancy | publisher
    """
    desc = (lead.get("description") or "").lower()
    name = (lead.get("channel_name") or "").lower()
    subs = lead.get("subscribers", 0) or 0
    text = desc + " " + name

    # Lead type, matched on words actually present in the data
    if any(k in text for k in ["consultant", "counselor", "study abroad", "visa", "scholarship"]):
        lead_type = "consultancy"
    elif any(k in text for k in ["publishers", "publisher"]):
        lead_type = "publisher"
    elif any(k in text for k in ["academy", "coaching", "campus", "tutors", "classes", "institute", "portal"]):
        lead_type = "academy"
    else:
        lead_type = "online educator" if subs >= 100000 else "tutor"

    # Product choice: match the lead type to what the product actually helps with
    if lead_type == "academy":
        primary = "WhatsApp Auto Replies" if "whatsapp" in text else "Fee Reminders"
        secondary = "Student Databases"
    elif lead_type == "consultancy":
        primary = "Instagram Auto Replies" if "instagram" in text else "WhatsApp Auto Replies"
        secondary = "AI Automations"
    elif lead_type == "publisher":
        primary = "Websites"
        secondary = "Social Media Design Packages"
    elif lead_type == "tutor":
        primary = "WhatsApp Auto Replies"
        secondary = "Student Databases"
    else:  # online educator
        primary = "AI Automations"
        secondary = "Websites"
    return lead_type, primary, secondary

def website_clause(website_notes):
    """One factual sentence about the website, or empty string.

    Rules:
    - Mention the site ONLY if a measured problem exists in Website Notes.
    - If no website was found in channel data, offer (not criticize) once.
    - If nothing was measured, say nothing.
    """
    notes = (website_notes or "").lower()
    if "unreachable" in notes or "no response within 10s" in notes:
        return "Your site seemed unreachable when I checked it earlier today, so it may need a quick look."
    if "no https" in notes:
        return "One small thing I noticed: your site opens over http, which browsers now mark as not secure."
    if "mobile viewport: no" in notes:
        return "I checked your site on the idea of phone visitors and it looks like it does not adapt to phone screens yet."
    if "copyright year" in notes and "outdated" in notes:
        return "I also noticed the footer on your site still shows an old copyright year, which is a two minute fix."
    if "no website found in channel data" in notes:
        return "I could not find a website in your channel details, so if you do not have one, that is exactly what we build."
    return ""

def build_prompt(lead):
    """lead keys: channel_name, subscribers, niche, description, has_email,
    website_notes, lead_type (optional override)."""
    products = {p["name"]: p for p in load_products()}
    lead_type, primary, secondary = classify_lead(lead)
    primary_desc = products[primary]["description"]
    secondary_desc = products[secondary]["description"]
    primary_price = (products[primary].get("price") or "").strip()
    price_line = (
        f"Price for {primary} is {primary_price}. You may state this price once, plainly."
        if primary_price
        else "PRICES ARE NOT SET. Do NOT mention any price, cost, package rate, or say anything is cheaper or affordable. Stay completely quiet on price."
    )
    website_line = website_clause(lead.get("website_notes"))

    channel_type = "email (max 90 words)" if lead.get("has_email") else "WhatsApp (max 50 words)"
    length_note = (
        "4 to 5 short sentences" if lead.get("has_email") else "3 short sentences, casual"
    )

    prompt = f"""You are writing a short outreach message for Mentis AI, a Pakistani company that builds AI automations, agents and websites for education businesses (schools, academies, coaching centres, test prep tutors, online teachers).

Lead facts (the ONLY facts you may use):
- Name: {lead['channel_name']}
- Subscribers: {lead.get('subscribers', 0):,}
- How we found them: {lead.get('niche', 'education')}
- Description from their channel: {str(lead.get('description', ''))[:300]}
- Lead type: {lead_type}

Write a {channel_type} message that:
1. Opens with ONE specific observation strictly drawn from the description above. If the description is too vague for a specific observation, use an honest opening like "I came across your channel while looking at {lead.get('niche', 'education')}".
2. Explains, in value terms (what saves them time or wins admissions, not what the product is called), how {primary} would help a {lead_type} at their scale: {primary_desc}
3. Optionally adds ONE sentence that {secondary_desc.lower()[:-1]} would also help, framed as "we also do".
4. Ends with a soft, low pressure question like "worth a short chat?".
5. Website sentence to include if provided below: {website_line or '(none, say nothing about their site)'}

Hard rules:
- Never invent problems, results, clients, numbers, videos, achievements or tools. Use only the facts above.
- {price_line}
- Plain text only. No links, no bullet points, no lists, no emojis, no hyphens anywhere in the text.
- Never write "I hope this message finds you well" and never open with "My name is".
- No exaggerated claims, no excessive flattery.
- {length_note}.
- Write in English. Return ONLY the message text, nothing else."""

    return prompt
