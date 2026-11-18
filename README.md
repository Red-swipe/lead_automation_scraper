# Mentis AI Lead Pipeline

Low volume, manually reviewed outreach for Pakistani education businesses.
Six stages, all visible live in the terminal.

## How to run

| File | What it does |
|---|---|
| `run_mentis.bat` | **Safe mode.** Real YouTube scraping, real Gemini. Sends at most **1 TEST email to your own address**. |
| `run_mentis_live.bat` | **Live mode.** Same pipeline, can email real leads. Shows the exact batch and requires typing `SEND <n>`. |

Double click either file. Optional flags can be appended in a terminal:

```
run_mentis.bat --skip-scrape          reuse existing lead data
run_mentis.bat --skip-scrape --fake   fake message generator (no Gemini calls)
run_mentis_live.bat --skip-scrape --fake --dry-run   full live flow, nothing sent
```

Stages: **1 Finding leads** (YouTube, plus Google Maps only if
`GOOGLE_PLACES_API_KEY` exists) → **2 Filtering** (Y/N/?) → **3 Website checks**
(Y leads with a URL, 10s timeout) → **4 Writing messages** (Gemini, one test
call, quota stop) → **5 Review** (table, every ready message in full, skipped
leads with reasons) → **6 Sending** (safe: 1 TEST email; live: confirm with
`SEND <n>`).

## Files

| Path | Purpose |
|---|---|
| `leads/mentis_leads.xlsx` | Raw leads (merged by Channel URL, backed up before every scrape). |
| `leads/mentis_leads_review.xlsx` | Working file: verdicts, priorities, messages, validation, prompt version. |
| `leads/mentis_leads_review_backup_latest.xlsx` | Safety backup taken before every message run. |
| `leads/whatsapp_only_leads.txt` | WhatsApp-only leads for manual outreach (never emailed). |
| `leads/do_not_contact.txt` | Names/emails to never email (opt-outs). |
| `leads/already_contacted.txt` | Names/emails you already contacted by other means. |
| `leads/send_log.txt` | Every send/skip with `mode=TEST` or `mode=LIVE`. |
| `leads/personalize_log.txt` | Message generation log. |
| `config.json` | Sending limits (see below). |
| `check_regen.py` | Safety self-test for the regeneration rules. |

## config.json

```json
{ "daily_cap": 3, "min_delay_seconds": 120, "max_delay_seconds": 300 }
```

The cap counts **only `mode=LIVE` sends today**. Raise it manually after a few
days of good results. Delays are randomised between min and max and a
countdown is printed before each send.

## Adding to the lists

`leads/do_not_contact.txt` — one entry per line, a channel name **or** an email
address. Anyone who replies STOP goes here. Lines starting with `#` are ignored.

`leads/already_contacted.txt` — same format. Use it for people you contacted
outside this pipeline; they will never be emailed by it.

## Safety notes

- `.env` holds the Gmail app password and API keys and is never read aloud,
  printed, or committed.
- Messages with a FAIL validation, an email typo flag, an invalid address, or
  no message yet are never sent.
- The sender stops the whole run on any SMTP error.
- After every send run: check Gmail for bounces, add STOP replies to
  `do_not_contact.txt`, and wait a few days before raising the cap.
