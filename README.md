# AI Voice Receptionist (Academy School, English + Khmer)

An AI receptionist that answers voice and text messages about the school, in English and Khmer.
It is built on Google Gemini (google-genai SDK) and FastAPI, with self-hosted speech-to-text (faster-whisper).

```
Voice / text ─► validate ─► STT (faster-whisper) ─► Gemini agent + tools ─► TTS ─► reply
                                                        │
                       skills/ (SKILL.md)  ◄── reads ───┤
                       approved knowledge  ◄── reads ───┤
                       staff messages      ◄── writes ──┤
                       review queue        ◄── writes ──┘
                                │
                Admin page: staff approve answers ─► new knowledge (the agent gets smarter)
```

## Quick start (Windows / PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # add ,voice for speech-to-text
copy .env.example .env           # then fill in GEMINI_API_KEY and ADMIN_API_KEY
uvicorn receptionist.main:app --reload
```

- Chat page: http://localhost:8000/
- Staff admin page: http://localhost:8000/admin (sign in with `ADMIN_API_KEY`)
- API docs (development only): http://localhost:8000/docs

Generate an admin key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## How the agent gets smarter

**1. Skills** (`skills/<name>/SKILL.md`): one folder per topic, following the Agent Skills
*progressive disclosure* pattern.
- The system prompt holds only each skill's `name` + `description`.
- The agent calls `load_skill` to read the full SKILL.md when a question matches.
- It calls `read_skill_file` for details such as `fees.yaml` only when needed.

To add a skill, copy `skills/_template`, rename the folder, edit it, then click
**Reload skills** on the admin page. Folders starting with `_` are ignored.

**2. Human-approved learning loop**
1. A caller asks something no skill or approved answer covers.
2. The agent says it doesn't know, calls `log_unanswered`, and offers to take a message.
3. Staff see the question (grouped, with a count of how often it is asked) on the admin page,
   write the answer, and pick a skill.
4. The approved answer becomes a versioned knowledge entry. The next caller gets it.

Callers can **never** change what the agent knows. Only staff can, which prevents
knowledge poisoning through prompt injection.

## Voice

**Speech-to-text:** `pip install -e ".[voice]"`, then set `STT_ENABLED=true`.
- English and language detection use `STT_MODEL_EN` (default `small`; try `large-v3-turbo` for
  better accuracy if the server can handle it).
- Stock Whisper is weak at Khmer. Use a Khmer fine-tuned Whisper converted to CTranslate2:
  ```powershell
  pip install "ctranslate2" "transformers[torch]"
  ct2-transformers-converter --model <hf-khmer-whisper-model> --output_dir models/whisper-km --quantization int8 --copy_files tokenizer.json preprocessor_config.json
  ```
  Then set `STT_MODEL_KM=./models/whisper-km`. Candidates to test on real recordings from your
  school: `BuzzASR/khmer` (large-v3 based), `phonsobon/Whisper-Small-Khmer-v3`.
  Pick the model by measuring accuracy on 20-50 real voice messages.

**Text-to-speech** (`TTS_PROVIDER`):
- `browser` (default): the web page speaks the reply. It's free, but whether a Khmer voice is
  available depends on the device.
- `mms`: self-hosted Meta MMS (`pip install -e ".[tts-mms]"`). **Licence CC-BY-NC 4.0, so
  non-commercial use only.** For a commercial school, add a provider in `voice/tts.py` for a
  licensed service such as Google Cloud TTS (`km-KH`).

## Project layout

```
src/receptionist/
  main.py         app factory, middleware, routes
  config.py       settings from .env
  agent/          Gemini client + tool loop, prompts, tools, guardrails
  skills/         SKILL.md registry (progressive disclosure, no path access)
  knowledge/      approved Q&A entries (versioned) + search (works for Khmer)
  learning/       review queue for unanswered questions
  messages/       messages for staff (human handoff)
  chat/           public /api/chat and /api/voice endpoints
  voice/          upload validation, STT (faster-whisper), TTS
  admin/          staff endpoints (X-Admin-Key)
  security/       auth, rate limit, security headers, audit log, PII redaction
skills/           skill content (editable by staff)
web/              chat page + admin page (no inline JS; strict CSP)
tests/            pytest suite (Gemini is faked; no API key needed)
```

## Security measures

| Risk | Mitigation |
|---|---|
| Prompt injection (OWASP LLM01) | Caller text only ever goes in user turns. The system prompt is fixed and tells the model to ignore embedded instructions. Tools are allowlisted and least-privilege. |
| Excessive agency (LLM06) | The agent can only read skills and knowledge, save a staff message, and log a question. It cannot edit knowledge. |
| Tool abuse | `strict: true` schemas with enums for skill names and offices. Every handler validates again (types, lengths). Skill files are looked up by registered name only, so no path traversal. |
| Knowledge poisoning | Only staff approve new knowledge. Entries are versioned and every action is audit-logged. |
| Malicious uploads | Content-type allowlist, size cap enforced while reading, magic-byte check, duration cap, nothing written to disk. |
| Insecure output (LLM05) | The front end uses `textContent` only. The CSP blocks inline scripts. Model output is never executed. |
| Secrets | `.env` is git-ignored, keys are held as `SecretStr`, `detect-secrets` runs in pre-commit, and API errors are never passed to clients. |
| Abuse / cost | Per-IP rate limits, input length caps, a tool-round cap per turn, and a session history cap. |
| Admin access | Constant-time key comparison. Admin is disabled when no key is set. |
| Privacy (minors) | Minimal data collection, PII redacted in logs and the review queue, and the user is told it's an AI. |
| Supply chain | `pip-audit`, `bandit`, `ruff` and pinned Hugging Face revisions. |

## Development checks

```powershell
pytest
ruff check src tests
bandit -c pyproject.toml -r src
python -m pip_audit
pre-commit install   # run checks on every commit
```

## Production checklist
- [ ] Real school data in `skills/`: replace every "EXAMPLE DATA" file.
- [ ] `ENVIRONMENT=production` (disables /docs, enables HSTS), with HTTPS through a reverse proxy.
- [ ] Strong `ADMIN_API_KEY`. Consider SSO or per-staff accounts as staff numbers grow.
- [ ] Move conversation memory to Redis if you run more than one worker.
- [ ] Use PostgreSQL + Alembic migrations instead of SQLite `create_all`.
- [ ] Set a data-retention policy and periodically delete old messages and audit rows.
- [ ] Test with real English and Khmer voice samples, including prompt-injection attempts.
