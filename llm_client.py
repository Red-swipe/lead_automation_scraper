"""Provider chain for Gemini and Groq. No credential values are logged."""
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

def _key(name):
    return (os.getenv(name) or "").strip().strip("\"'").strip()

GEMINI_KEY = _key("GEMINI_API_KEY")
GROQ_KEY = _key("GROQ_API_KEY")
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"]
GROQ_PREFERENCES = [
    "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
    "llama3-70b-8192", "llama3-8b-8192",
    "qwen/qwen3.8-27b", "openai/gpt-oss-120b",
    "openai/gpt-oss-20b", "allam-2-7b",
]
_groq_models = None
_active = None
_dead = {}
_swapped_back = False

def _redact(value):
    text = str(value)
    for secret in (GEMINI_KEY, GROQ_KEY):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text

class ProviderUnavailable(Exception):
    pass

def strip_preamble(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:text|plaintext)?\s*|\s*```$", "", text, flags=re.I).strip()
    text = re.sub(r"^(?:subject\s*:\s*[^\n]+\n+|here(?:'s| is) (?:the )?(?:email|message)\s*:?\s*|(?:email|message|body|final answer)\s*:\s*)", "", text, flags=re.I)
    text = text.strip().strip('"“”')
    text = re.sub(r"\n\s*(?:Best regards|Regards|Sincerely|Thanks|Thank you),?\s*\n.*$", "", text, flags=re.I | re.S).strip()
    return text

def _config_primary():
    try:
        value = json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("primary_provider", "gemini")
        return "groq" if str(value).lower() == "groq" else "gemini"
    except Exception:
        return "gemini"

def _classify(exc):
    s = str(exc).lower()
    if any(x in s for x in ("429", "resource_exhausted", "quota", "rate_limit")):
        return "quota exhausted"
    if any(x in s for x in ("401", "403", "invalid api key", "api key not valid", "expired key", "unauthorized", "forbidden")):
        return "invalid or expired key"
    return _redact(f"{type(exc).__name__}: {s[:100]}")

def _gemini(prompt):
    if not GEMINI_KEY:
        raise ProviderUnavailable("Gemini key missing")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_KEY)
    last = None
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                result = genai.GenerativeModel(model).generate_content(prompt, request_options={"retry": None, "timeout": 60})
                return strip_preamble(result.text), f"gemini:{model}"
            except Exception as exc:
                last = exc
                reason = _classify(exc)
                if reason in ("quota exhausted", "invalid or expired key"):
                    raise ProviderUnavailable(reason) from None
                if "404" in str(exc) or "not found" in str(exc).lower():
                    break
                if attempt < 2:
                    time.sleep(2 ** attempt)
    raise ProviderUnavailable(_classify(last) if last else "no Gemini models available")

def _get_groq_models():
    global _groq_models
    if _groq_models is not None:
        return _groq_models
    if not GROQ_KEY:
        raise ProviderUnavailable("Groq key missing")
    last = None
    for attempt in range(3):
        try:
            r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=60)
            if r.status_code in (401, 403):
                raise ProviderUnavailable("invalid or expired key")
            if r.status_code == 429:
                raise ProviderUnavailable("quota exhausted")
            r.raise_for_status()
            break
        except ProviderUnavailable:
            raise
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:
        raise ProviderUnavailable(_classify(last) if last else "Groq model discovery failed")
    available = {m.get("id") for m in r.json().get("data", [])}
    preferred = [m for m in GROQ_PREFERENCES if m in available]
    preferred += sorted(m for m in available if m and
                        not any(x in m.lower() for x in ("guard", "whisper", "orpheus"))
                        and m not in preferred)
    if not preferred:
        raise ProviderUnavailable("no usable Groq chat models")
    _groq_models = preferred
    return preferred

def _groq(prompt):
    last = None
    for model in _get_groq_models():
        for attempt in range(3):
            try:
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.6, "max_tokens": 350}, timeout=60)
                if r.status_code in (401, 403, 429):
                    raise ProviderUnavailable("invalid or expired key" if r.status_code in (401, 403) else "quota exhausted")
                r.raise_for_status()
                return strip_preamble(r.json()["choices"][0]["message"]["content"]), f"groq:{model}"
            except ProviderUnavailable:
                raise
            except Exception as exc:
                last = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
    raise ProviderUnavailable(_classify(last) if last else "no Groq models available")

def generate(prompt):
    """Generate once through provider chain and return (message, provider:model)."""
    global _active, _swapped_back
    primary = _active or _config_primary()
    order = [primary, "groq" if primary == "gemini" else "gemini"]
    reasons = []
    for provider in order:
        if _dead.get(provider):
            reasons.append(f"{provider}: {_dead[provider]}")
            continue
        try:
            text, used = (_gemini if provider == "gemini" else _groq)(prompt)
            if _active != provider:
                if _active:
                    print(f"{_active.title()} unavailable, switching to {provider.title()}")
                else:
                    print(f"Active provider: {provider.title()}")
                _active = provider
            return text, used
        except Exception as exc:
            reason = _redact(exc)[:140]
            _dead[provider] = reason
            reasons.append(f"{provider}: {reason}")
            if provider == "groq" and not _swapped_back and not _dead.get("gemini"):
                _swapped_back = True
                continue
    message = "Both providers unavailable: " + "; ".join(reasons or [f"{k}: {_dead[k]}" for k in ("gemini", "groq") if _dead.get(k)])
    raise ProviderUnavailable(message)

def reset_for_tests():
    global _active, _swapped_back, _groq_models
    _active = None; _swapped_back = False; _groq_models = None; _dead.clear()

def test_provider(provider):
    """Run one explicit provider smoke call without changing chain state."""
    fn = _gemini if provider.lower() == "gemini" else _groq
    return fn("Reply with the single word ready")
