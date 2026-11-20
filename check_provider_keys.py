"""One real smoke call per configured LLM provider, printing no response or key."""
import llm_client

def main():
    failures = 0
    for provider in ("gemini", "groq"):
        try:
            _text, used = llm_client.test_provider(provider)
            print(f"{provider}: success ({used})")
        except Exception as exc:
            failures += 1
            print(f"{provider}: {type(exc).__name__}")
    return int(failures > 0)

if __name__ == "__main__":
    raise SystemExit(main())
