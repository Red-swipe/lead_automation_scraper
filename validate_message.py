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

# A cell that starts with a label instead of the address ("Email-", "Contact:").
# The label is stripped mechanically; the address itself is never guessed.
LABEL_PREFIX_RE = re.compile(r"^\s*(email|mail|contact)\s*[-:]\s*", re.I)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _cell_text(value):
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def label_prefix(value):
    """The label prefix found at the start of an email cell, or ''."""
    m = LABEL_PREFIX_RE.match(_cell_text(value))
    return m.group(0).strip() if m else ""


def clean_email(value):
    """Strip a label prefix only. Never rewrites or guesses the address."""
    return LABEL_PREFIX_RE.sub("", _cell_text(value)).strip()


def normalize_terms(text):
    """House casing for exam names, applied to generated output."""
    text = re.sub(r"\bmdcat\b", "MDCAT", text, flags=re.I)
    text = re.sub(r"\bielts\b", "IELTS", text, flags=re.I)
    return text


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


# --- grammar and structure checks (owner reported these slipping through) ---
ENSURE_STOPS_RE = re.compile(r"\bensure[sd]?\b[^.!?]{0,140}?\bstops?\b", re.I)

_ABBREVIATIONS = ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.", "Sr.", "Jr.",
                  "vs.", "e.g.", "i.e.", "No.")

FACT_STOPWORDS = {
    "this", "that", "these", "those", "there", "their", "them", "they", "with",
    "your", "yours", "ours", "about", "after", "again", "also", "been", "being",
    "both", "does", "doing", "each", "from", "have", "having", "here", "into",
    "just", "like", "more", "most", "much", "must", "only", "other", "over",
    "same", "some", "such", "than", "then", "very", "want", "were", "what",
    "when", "where", "which", "while", "will", "would", "you", "welcome",
    "channel", "video", "videos", "subscribe", "youtube", "please", "contact",
    "email", "whatsapp", "instagram", "facebook", "follow", "links", "through",
    "every", "best", "good", "great", "make", "makes", "need", "join", "free",
    "info", "information", "official", "page",
}

VALUE_MARKERS = (
    "build", "assistant", "answer", "repl", "remind", "instant", "save",
    "automat", "workflow", "collect", "follow up", "help", "faster", "reduce",
    "free up", "no inquiry waits", "so no", "so that", "quick",
)


def _sentences(text):
    body = _strip_signoff(text)
    # Protect common abbreviations so "Dr." does not start a new sentence.
    protected = body
    for i, ab in enumerate(_ABBREVIATIONS):
        protected = protected.replace(ab, f"@@AB{i}@@")
    parts = re.split(r"(?<=[.!?])\s+", protected.strip())
    out = []
    for part in parts:
        for i, ab in enumerate(_ABBREVIATIONS):
            part = part.replace(f"@@AB{i}@@", ab)
        if part.strip():
            out.append(part.strip())
    return out


def _fact_tokens(channel_name, description):
    """Content words actually present in the description, minus the channel
    name and generic filler. Used to require a real fact in sentence one."""
    name_words = set(re.findall(r"[a-z]+", str(channel_name).lower()))
    tokens = []
    for word in re.findall(r"[A-Za-z]{4,}", str(description or "")):
        low = word.lower()
        if low in name_words or low in FACT_STOPWORDS:
            continue
        tokens.append(low)
    return tokens


def _fact_in(fact, text):
    """Word match tolerant of a trailing plural s (courses / course)."""
    if fact in text:
        return True
    if fact.endswith("s") and fact[:-1] in text:
        return True
    return False


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

    sents = _sentences(text)

    # 1. The "ensure ... stops" pattern (and its variants) reads wrong.
    for sentence in sents:
        m = ENSURE_STOPS_RE.search(sentence)
        if m:
            reasons.append(f"grammar: 'ensure ... stops' reads wrong ({m.group(0)[:70]!r})")
            break

    # 2. Sentence one must carry a concrete fact from the description, not
    #    just the channel name. Skipped when the description has no usable
    #    fact at all (e.g. a non English description), since nothing can be
    #    required that is not there.
    facts = _fact_tokens(channel_name, description)
    if sents and facts:
        first_sentence = sents[0].lower()
        if not any(_fact_in(f, first_sentence) for f in facts):
            reasons.append("first sentence has no specific fact from the description")

    # 3. One sentence of value must sit immediately before the CTA.
    if len(sents) >= 3:
        value_line = sents[-2].lower()
        if not any(marker in value_line for marker in VALUE_MARKERS):
            reasons.append("no one sentence value line before the CTA")

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
    # Same rule as personalize_v2: Send Via decides, empty falls back to
    # whether an email address exists (rows without email are WhatsApp only).
    sv = str(row.get("Send Via", "")).strip().lower()
    email_value = row.get("Email", "")
    has_email = str(email_value).strip().lower() not in ("", "nan", "none")
    is_whatsapp = (sv == "whatsapp") if sv else (not has_email)
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
