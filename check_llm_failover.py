"""Stubbed provider chain checks. No API requests are made."""
import contextlib
import io
import llm_client as c

FAILS = []
def check(label, ok):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok: FAILS.append(label)

def main():
    old_gemini, old_groq = c._gemini, c._groq
    try:
        calls = []
        def quota(_):
            calls.append("gemini"); raise c.ProviderUnavailable("quota exhausted")
        def groq(_):
            calls.append("groq"); return "ready", "groq:test-model"
        c._gemini, c._groq = quota, groq
        c.reset_for_tests()
        output = io.StringIO()
        with contextlib.redirect_stdout(output): text, used = c.generate("x")
        check("primary quota switches to Groq", calls == ["gemini", "groq"] and used == "groq:test-model" and text == "ready")
        c.reset_for_tests(); calls.clear()
        def invalid(_):
            calls.append("gemini"); raise c.ProviderUnavailable("invalid or expired key")
        c._gemini = invalid
        with contextlib.redirect_stdout(io.StringIO()): _, used = c.generate("x")
        check("invalid key switches providers", calls == ["gemini", "groq"] and used.startswith("groq:"))
        c.reset_for_tests()
        c._gemini = lambda _: (_ for _ in ()).throw(c.ProviderUnavailable("quota exhausted"))
        c._groq = lambda _: (_ for _ in ()).throw(c.ProviderUnavailable("invalid or expired key"))
        try:
            c.generate("x"); clean = False
        except c.ProviderUnavailable as exc:
            clean = str(exc).startswith("Both providers unavailable:") and "gemini:" in str(exc) and "groq:" in str(exc)
        check("both dead raises clean reason", clean)
        check("Groq preamble stripping", c.strip_preamble("Here is the email:\n\nHello there.\n\nAsjad from Mentis AI") == "Hello there.\n\nAsjad from Mentis AI")
        check("provider is recorded per generated message", used.startswith("groq:"))
        c.reset_for_tests()
        probe = c.GEMINI_KEY or "GEMINI_SECRET_SENTINEL"
        c._gemini = lambda _: (_ for _ in ()).throw(RuntimeError("upstream error " + probe))
        c._groq = lambda _: (_ for _ in ()).throw(c.ProviderUnavailable("quota exhausted"))
        safe_output = io.StringIO()
        with contextlib.redirect_stdout(safe_output):
            try:
                c.generate("x")
            except c.ProviderUnavailable as exc:
                safe_error = str(exc)
        secrets = [x for x in (c.GEMINI_KEY, c.GROQ_KEY) if x]
        check("no credential value appears in test output or provider reason",
              all(x not in safe_output.getvalue() and x not in safe_error for x in secrets))
    finally:
        c._gemini, c._groq = old_gemini, old_groq
        c.reset_for_tests()
    print("ALL CHECKS PASSED" if not FAILS else f"CHECKS FAILED: {len(FAILS)}")
    return int(bool(FAILS))

if __name__ == "__main__": raise SystemExit(main())
