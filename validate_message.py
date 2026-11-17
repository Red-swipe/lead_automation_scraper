"""
Message validator for Mentis AI outreach.

Checks each generated message and prints PASS or FAIL with reasons.
Checks:
  - word limit (90 email, 50 WhatsApp)
  - no hyphens (any dash character)
  - no links
  - no bullet points
  - no banned phrases ("I hope this finds you well", "My name is")
  - contains the lead's channel name
  - no numbers or proper nouns that are not in the lead data

Usage from other scripts:
    from validate_message import validate_message
    ok, reasons = validate_message(message, channel_name, description,
                                   subscribers, is_whatsapp)
"""
import re

BANNED_PHRASES = [
    "i hope this finds you well",
    "i hope this message finds you well",
    "i hope this email finds you well",
    "my name is",
]

WHITELIST_PROPER = {"assalam", "alaikum", "assalamoalaikum", "i", "mentis", "ai",
                    "asjad", "from", "pakistan", "pakistani", "mdcat", "ecat",
                    "css", "ielts", "sat", "fsc", "igcse", "english",
                    "whatsapp", "instagram", "facebook", "youtube", "google"}

# Numbers that are part of our own CTA or service wording, not claims
# about the lead (a "10 minute chat", replies "24/7"). Anything else must
# appear in the lead data.
NUMERIC_WHITELIST = {"10", "24", "7"}

DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
BULLET_CHARS = "•‣⁃∙·*"


SIGNOFF_LINE = "asjad from mentis ai"


def _strip_signoff(text):
    """Remove the trailing sign off line so it is not counted in words."""
    lines = [l.strip() for l in text.strip().splitlines()]
    while lines and lines[-1].lower() in (SIGNOFF_LINE, SIGNOFF_LINE + "?", SIGNOFF_LINE + "."):
        lines.pop()
    return "\n".join(lines)


def _word_limit_ok(message, is_whatsapp):
    limit = 50 if is_whatsapp else 80
    countable = _strip_signoff(message)
    return len(countable.split()) <= limit, limit


def _extract_data_words(channel_name, description, niche):
    blob = f"{channel_name} {description} {niche}".lower()
    return blob


def validate_message(message, channel_name, description="", niche="",
                     subscribers=0, is_whatsapp=False):
    """Returns (ok: bool, reasons: list[str])."""
    reasons = []
    text = message.strip()

    if not text or text.startswith("[ERROR"):
        return False, ["empty or error placeholder message"]

    ok, limit = _word_limit_ok(text, is_whatsapp)
    if not ok:
        reasons.append(f"too long: {len(text.split())} words (limit {limit})")

    for ch in DASH_CHARS:
        if ch in text:
            reasons.append("contains hyphen or dash")
            break

    if re.search(r"(https?://|www\.|\.com\b|\.pk\b|\.net\b|\.org\b)", text, re.I):
        reasons.append("contains a link")

    for ch in BULLET_CHARS:
        if ch in text:
            reasons.append(f"contains bullet character {ch!r}")
            break
    if re.search(r"^\s*\d+[\.\)]\s", text, re.M):
        reasons.append("looks like a numbered list")

    lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            reasons.append(f"banned phrase: {phrase!r}")

    # Channel name must appear: full name, first word, or first two words.
    blob = _extract_data_words(channel_name, description, niche)
    lower = text.lower()
    name_lower = channel_name.lower().strip()
    name_words = [w for w in re.findall(r"\w+", name_lower) if w]
    candidates = {name_lower}
    if name_words and len(name_words[0]) >= 4:
        candidates.add(name_words[0])
    if len(name_words) >= 2 and len(" ".join(name_words[:2])) >= 5:
        candidates.add(" ".join(name_words[:2]))
    name_present = any(c in lower for c in candidates)
    if not name_present:
        reasons.append("does not mention the channel name")

    # Numbers must exist in lead data (subscribers count too)
    data_blob = _extract_data_words(channel_name, description, niche)
    allowed_numbers = {str(subscribers), f"{subscribers:,}"} | NUMERIC_WHITELIST
    try:
        subs_int = int(subscribers)
    except (ValueError, TypeError):
        subs_int = 0
    for num in re.findall(r"\d[\d,\.]*", text):
        n = num.replace(",", "").rstrip(".")
        if n in allowed_numbers:
            continue
        if n in data_blob.replace(",", ""):
            continue
        # "700k" style in the data matches 700,000
        if n.isdigit() and int(n) % 1000 == 0 and f"{int(n) // 1000}k" in data_blob:
            continue
        # rounded subscriber counts are fine ("nearly 5 million" for 5.14M)
        try:
            f = float(n)
        except ValueError:
            reasons.append(f"number not in lead data: {num!r}")
            continue
        close_to_subs = any(
            abs(subs_int - f * scale) <= max(1000, 0.1 * subs_int)
            for scale in (1, 1000, 1_000_000)
        )
        if not close_to_subs:
            reasons.append(f"number not in lead data: {num!r}")

    # Proper nouns: capitalized words not found in data and not whitelisted.
    # Skip the first word of every sentence (sentence starts are not
    # proper noun evidence), and handle camelCase like WhatsApp.
    whitelist = WHITELIST_PROPER | {w.lower() for w in re.findall(r"[A-Za-z]+", blob)}
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        words = re.findall(r"[A-Za-z]+", sentence)
        for wi, w in enumerate(words):
            if wi == 0:
                continue  # sentence start
            if w[0].isupper() and w[1:].islower():
                wl = w.lower()
                if wl in whitelist:
                    continue
                # camelCase split: WhatsApp -> whats + app
                parts = re.findall(r"[A-Z][a-z]*", w)
                parts = [p.lower() for p in parts if len(p) > 1]
                if parts and (all(p in whitelist for p in parts)
                              or "".join(parts) in whitelist):
                    continue
                # contractions: I'm -> I
                base = w.split("'")[0].lower()
                if base in whitelist:
                    continue
                reasons.append(f"proper noun not in lead data: {w!r}")

    # De-duplicate reasons while keeping order
    seen = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    return (len(seen) == 0), seen


def validate_from_row(row, message):
    is_whatsapp = str(row.get("Send Via", "")).strip().lower() != "email"
    return validate_message(
        message,
        str(row.get("Channel Name", "")),
        str(row.get("Description Snippet", "")),
        str(row.get("Found Via Query", "")),
        row.get("Subscribers", 0) or 0,
        is_whatsapp,
    )


if __name__ == "__main__":
    import sys
    import pandas as pd
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    REVIEW_FILE = Path(__file__).resolve().parent / "leads" / "mentis_leads_review.xlsx"
    df = pd.read_excel(REVIEW_FILE)
    msgs = df["Outreach Message"].fillna("").astype(str).str.strip()
    for i, row in df[msgs != ""].iterrows():
        ok, reasons = validate_from_row(row, msgs[i])
        status = "PASS" if ok else "FAIL"
        print(f"{status} | {str(row['Channel Name']).strip()}"
              + ("" if ok else " | " + "; ".join(reasons)))
