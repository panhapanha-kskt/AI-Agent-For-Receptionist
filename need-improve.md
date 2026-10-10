# Need Improve: Security & Best-Practice Roadmap

Future work for this project, based on a review of the current code. Most urgent items first.
Tick the boxes (`[x]`) as you finish them.

**Legend:** 🔴 do before going public · 🟠 high · 🟡 medium · 🟢 later

---

## 🚀 In progress: Faster, smarter responses (implementation log)

Goal: first words in under 1 second, most answers in **1 model call instead of 3**, an agent
that survives Gemini overloads, and one that learns from feedback.

**Status:** ✅ done · 🔄 in progress · ⏳ not started

| Phase | What | Status |
|---|---|---|
| 0 | Measure: per-turn timing + token metrics; eval question set + `scripts/run_eval.py` | ✅ |
| 1 | Streaming replies (SSE), async Gemini client, thinking level `low`, **retry + fallback model on 503/429**, speech-model warm-up, speak sentence by sentence | ✅ |
| 2 | Skill router: pick the skill on the server before calling Gemini (3 calls → 1) | ✅ |
| 3 | Fast paths: approved-answer matching, fast "lite" model for routed questions, workflow files | ✅ |
| 4 | Conversation context: session facts, summarising long chats, follow-up questions | ✅ |
| 5 | Learning: 👍/👎 feedback, AI-drafted answers for staff, learned routing examples, export to faq.md, latency panel | 🔄 |
| 6 | Infrastructure (Redis, explicit context caching): documented for when traffic grows | ⏳ |

### Log
- 2026-10-10: Started Phase 0. Your test notes (bottom of this file) show single Gemini calls
  taking ~30 s and `503 high demand` errors, so retry + fallback was added to Phase 1.
- 2026-10-10: ✅ Phase 0 done.
  - New `src/receptionist/metrics/`: every turn stores timings (model, tools, speech, total,
    time-to-first-text) and token counts (incl. cached + thinking tokens) in `turn_metrics`.
    No caller text is stored there.
  - `GET /api/admin/metrics?hours=24`: p50/p95 latency, model calls per answer, cache hit
    ratio, slowest turns.
  - `tests/evals/questions.yaml` (18 cases: EN/KM facts, unknowns, injections) and
    `scripts/run_eval.py` → run `python scripts/run_eval.py` for accuracy + latency.
    Reports go to `reports/` (git-ignored). Uses a throwaway DB.
  - `database.py`: safe "add missing columns" step so the existing `receptionist.db` keeps
    working as tables grow (tested on a copy of your DB).
  - Chat responses now include `turn_id` (used later for 👍/👎).
  - Tests: 65 passing. ruff + bandit clean.
  - 👉 Try it: run the server, chat a bit, then open `/api/admin/metrics` with your admin key,
    or run `python scripts/run_eval.py` to get a "before" baseline for the next phases.
- 2026-10-10: ✅ Phase 1 done.
  - `agent/client.py` is now **async + streaming** (`client.aio.models.generate_content_stream`).
    Tool loop, safety handling and audit log are unchanged.
  - **Resilience** (fixes the `503 high demand` errors you saw): the SDK now retries 408/429/5xx
    with exponential backoff + jitter (`LLM_RETRY_ATTEMPTS`, default 2), every request has a
    timeout (`LLM_TIMEOUT_MS`, default 20 s), and if the main model is still overloaded the
    agent switches to `GEMINI_FALLBACK_MODEL` (default `gemini-3.6-flash`), but only before any
    text has been shown.
  - `THINKING_LEVEL=low` (Google's recommendation for fact look-ups).
  - New `POST /api/chat/stream` (Server-Sent Events: `delta` → `done` / `error`). It stops
    the Gemini call when the browser disconnects, and sets `X-Accel-Buffering: no` for nginx.
  - New `POST /api/transcribe`: voice → text; the page then streams the reply like a typed
    message. `/api/chat` and `/api/voice` still work for API clients.
  - Whisper models are loaded in the background at startup (`stt.warm_up()`).
  - Chat page: words appear as they stream; the browser speaks each sentence as soon as it's
    complete; buttons are disabled while a reply is running.
  - Tests: 76 passing. ruff + bandit clean.
  - **Live measurement with your key** (before the router):
    - Fee question: 3 calls, first word after **15.7 s**, total 16.5 s (correct answer).
    - "Hello": 1 call, **5.6 s**. That is the per-call floor of `gemini-3.8-flash` from here.
    - So: Phase 2 (1 call instead of 3) and Phase 3 (faster lite model) are where the real
      speed comes from.
- 2026-10-10: ✅ Phase 2 done (skill router).
  - New `src/receptionist/router/`: the server picks the skill **before** calling Gemini and
    attaches that skill's content (SKILL.md + data files up to 8 KB) to the question, so the
    model can answer in **1 call instead of 3**. The tools stay available as a safety net.
  - How it matches, in order:
    1. Clear keywords (e.g. "scholarship", "tuition") → instant, no API call.
    2. Gemini embeddings (`gemini-embedding-2`, handles Khmer and paraphrases).
    3. IDF-weighted keyword match.
    4. Short follow-ups ("and the other one?") stay on the previous skill.
  - Calibrated on real questions: route when score ≥ 0.65 **and** the lead over the 2nd skill
    is ≥ 0.03 (`ROUTER_THRESHOLD`, `ROUTER_MARGIN`). 18/19 eval questions routed correctly.
  - Router examples come from skill descriptions + the `## question` headings in each
    skill's `faq.md`. **Writing good FAQ questions now directly improves routing.**
  - A skill's content is sent only once per conversation; follow-ups reuse it.
  - Security: the caller can't fake a `<school_reference>` block (it is neutralised).
  - Content fixes: added Khmer keywords to fees/admissions; removed a campus-visit FAQ from
    `general-faq` that competed with `admissions`.
  - Also fixed during live tests:
    - 429 "quota exceeded" is no longer retried on the same model; the agent switches to the
      fallback model straight away.
    - The router index is built at startup.
    - The prompt now limits how long the model searches for unknown topics.
  - Tests: 100 passing. ruff + bandit clean.
  - **Live result:** fee question = **1 model call** (was 3); 2nd question in the same chat
    answered in **2.3 s**.
  - ⚠️ **Your key is on the Gemini free tier.** Live tests hit `429 You exceeded your current
    quota` on most calls, and the same request took 2.3 s one minute and 18.7 s the next.
    The fallback model kept every answer working, but **for real speed, enable billing
    (paid tier)** in Google AI Studio. That's also needed for privacy (see #4 below).
- 2026-10-10: ✅ Phase 3 done (fast paths).
  - **Fast model:** when the answer is already attached (routed skill or approved answer), the
    reply comes from `GEMINI_MODEL_FAST` (default `gemini-3.5-flash-lite`, measured at 1.5 s).
    If it fails for any reason, the main model takes over automatically.
  - **Approved answers checked on the server** (`router/knowledge.py`): if a caller asks
    something staff already approved, that answer is attached to the request. The model still
    phrases it for the caller, in their language. Keyword match first, then embeddings;
    retired entries are ignored.
  - **No-match note:** when nothing matches, the model is told so, and asked to log the question
    instead of searching every skill.
  - **Workflows:** new `skills/admissions/workflow.yaml` (book a campus tour, step by step).
    Skills with a `workflow.yaml` use the main model, one question per turn, and confirm
    before saving.
  - **Circuit breaker:** after a `429 quota exceeded`, that model is skipped for 60 s instead
    of failing on every call (your free-tier key hit this constantly).
  - Tests: 107 passing. ruff + bandit clean.
  - **Live result (same question as the Phase 1 baseline):**
    - Grade 7 tuition: first word **1.4 s**, total **1.7 s**, 1 call. Before: 15.7 s / 16.5 s,
      3 calls. **About 10× faster.**
    - Campus tour (workflow): 4.2 s, correctly asks "What day and time would suit you best?"
- 2026-10-10: ✅ Phase 4 done (conversation context).
  - New `agent/memory.py`: **rolling summaries** ("compaction"). After
    `SUMMARIZE_AFTER_TURNS` (default 10) caller messages, the fast model condenses the chat
    into a ≤120-word summary: who the caller is, what they want, which program or grade, what
    was answered, and what's still open. This runs **in the background after the reply is
    sent**, so nobody waits for it.
  - The summary is attached to the next message, so the agent keeps its context while
    requests stay small and fast. Before, everything was wiped after 20 turns; that hard
    reset is now only a safety net at 40.
  - Privacy: phone numbers and emails are masked before summarising; school references and
    server notes are left out; summaries live only in memory (30-minute session expiry).
  - Safe under concurrency: if a new message arrives while a summary is being written, that
    summary is discarded instead of overwriting the newer conversation.
  - Follow-up questions keep their topic: the previous skill is remembered, even across a
    summary.
  - Tests: 110 passing. ruff + bandit clean.

---

## 🔴 1. Cost and overload attacks
**Problem:** `/api/chat` is anonymous, and every message calls Gemini, which costs money.
The only protection is a per-IP rate limit, so a bot rotating IP addresses could run up a large
bill. Voice is worse: `src/receptionist/voice/stt.py` runs Whisper with no limit on how many run
at once, so a few long uploads at the same time can use up all the CPU.

- [ ] Set a budget alert and quota limit in Google AI Studio / Google Cloud
- [ ] Add a server-side daily cap on Gemini calls, with a friendly "busy" reply when it is reached
- [ ] Allow only 1-2 transcriptions at a time (a semaphore or job queue); reply `503 busy` when full
- [ ] Add a bot check (e.g. Cloudflare Turnstile) before the first message in a session

## 🔴 2. Rate limiting breaks behind a proxy
**Problem:** `src/receptionist/security/rate_limit.py` identifies callers by IP address. Behind a
reverse proxy or Docker, every request looks like it comes from the proxy's IP, so the limit
either applies to everyone together or stops working. The counter is also held in memory, so
each worker counts separately.

- [ ] Run uvicorn with `--forwarded-allow-ips=<proxy IP>` (trust only your own proxy)
- [ ] Keep the rate-limit counter in Redis (`Limiter(storage_uri="redis://...")`)
- [ ] Also limit per session, not only per IP (a school Wi-Fi shares one IP)

## 🔴 3. Stronger admin login
**Problem:** `src/receptionist/security/auth.py` uses one shared key:
- Nobody knows which staff member approved what.
- There is no brute-force protection.
- The key sits in `sessionStorage`, so any XSS bug could steal it.

- [ ] Per-staff accounts (SSO, or password + 2FA)
- [ ] Short sessions in `HttpOnly; Secure; SameSite=Strict` cookies, with CSRF protection
- [ ] Rate-limit the admin routes, and lock out after repeated failed sign-ins
- [ ] Record the real staff user in `approved_by` and the audit log

## 🔴 4. Privacy for students (minors)
**Problem:** callers' words are sent to Google (Gemini API). `src/receptionist/security/redact.py`
only masks emails and phone numbers, and data is kept forever.

- [ ] Check local data-protection law and Google's Gemini API terms (paid tier: prompts are not
      used to train models; free tier: they may be)
- [ ] Publish a privacy notice on the chat page ("AI assistant, don't share sensitive info")
- [ ] Redact student names and ID numbers before they reach the review queue and logs
- [ ] Add a scheduled cleanup job (e.g. delete messages after 90 days and audit rows after 1 year)
- [ ] Encrypt the database and its backups at rest

---

## 🟠 5. Pin dependencies exactly
**Problem:** `pyproject.toml` uses `>=` ranges, so a compromised or broken future release would
install silently. The Docker base image is not pinned either.

- [ ] Generate a lockfile with hashes (`uv lock` or `pip-compile --generate-hashes`)
- [ ] Pin the base image by digest: `python:3.12-slim@sha256:...`
- [ ] Pin `TTS_MMS_REVISION` / STT model revisions to exact commit hashes

## 🟠 6. Automatic checks on GitHub (CI)
**Problem:** checks only run on your machine, and only if `pre-commit install` was run.

- [ ] GitHub Actions workflow running `pytest`, `ruff`, `bandit` and `pip-audit` on every push
      and pull request
- [ ] Turn on Dependabot (dependency updates)
- [ ] Turn on GitHub secret scanning with push protection
- [ ] Turn on CodeQL code scanning
- [ ] Protect `main`: require pull requests and passing checks

## 🟠 7. Run audio decoding in a separate container
**Problem:** untrusted audio is decoded by FFmpeg libraries, which have a long history of security
bugs. The magic-byte check reduces the risk but doesn't remove it.

- [ ] Run STT in its own container/process: no network, read-only filesystem, CPU and memory limits
- [ ] Keep `faster-whisper` / `av` updated

## 🟠 8. Production secrets
- [ ] Use Docker secrets or a secrets manager instead of a `.env` file in production
- [ ] Rotate `GEMINI_API_KEY` and admin credentials on a schedule (e.g. every 90 days)

---

## 🟡 9. Server-issued sessions
**Problem:** the browser chooses its own `session_id`.

- [ ] The server issues a signed `HttpOnly` session cookie
- [ ] Cap `take_message` and `log_unanswered` calls per session, so nobody can flood staff

## 🟡 10. Remove the `TypeError` shortcut ✅
- [x] Check credentials once and fail with a clear message (`AgentNotConfigured`, done during the
      Gemini switch)
- [x] Remove `TypeError` from the `except` clause

## 🟡 11. Automated evals (accuracy + prompt injection)
**Problem:** the test sheet and attack table in `USEIT.md` (section 6) are manual.
*(Built in Phase 0 above.)*

- [x] Script that runs the question set against the real model and checks the expected facts
- [x] Prompt-injection set in English **and Khmer** (typed; spoken still to do)
- [ ] Run it before every prompt or skill change, and track the pass rate over time

## 🟡 12. Check answers against the data
**Problem:** the prompt says "never invent fees", but nothing checks that it obeyed.

- [ ] Compare numbers (prices, dates) in the reply with what the tools actually returned
- [ ] If a number did not come from a tool, hold the reply or flag it for review

## 🟡 13. Database maturity
- [ ] Move from SQLite to PostgreSQL
- [ ] Use Alembic migrations instead of `create_all`
- [ ] Keep conversation memory in Redis (needed for more than one worker)
- [ ] Automated backups, with a tested restore

## 🟡 14. Missing tests
- [ ] Rate limiting
- [ ] Admin brute-force lockout
- [ ] STT on real audio files (English + Khmer samples)
- [ ] Session expiry and the history cap
- [ ] Code coverage report (`pytest --cov`), with a minimum threshold in CI

---

## 🟢 15. Operations
- [ ] Structured JSON logs with request IDs
- [ ] Alerts for error spikes, refusals and a growing review queue
- [ ] Notify staff (email or Telegram) when a new message arrives
- [ ] Incident plan: kill switch, key rotation, and who informs parents if something goes wrong
- [ ] Threat model (STRIDE) review once a year
- [ ] Professional penetration test before a large public launch

---

## Recommended order
1. **1-4**, before any public URL exists. Until then, keep the app on the school network or
   behind a login.
2. **5-6** this week. They are cheap and protect everything else.
3. **9**, then **11** (evals). Evals are what prove the agent stays correct as it improves.
4. Everything else as the project grows.

See also: `USEIT.md` (how to use and test the system) and `README.md` (overview and setup).

---

## Your notes from testing (kept as written)

- AI slow response
- Less information about the school detail
- Voice did not working (Voice input is disabled (set STT_ENABLED=true).)
  → Fix: `pip install -e ".[voice]"` and set `STT_ENABLED=true` in `.env` (USEIT.md Step 6).
- Push response of the ai → being built: streaming replies (Phase 1).
- input the dataset to train → skill folders created; fill in the TODOs in `skills/`.
- Error khmer language → the log you pasted was cut off before the error. Paste the full error
  and it will be added here.

Log excerpt showing the slowness (one call took ~30 s) and a Gemini overload:
```
11:11:43 ... tool:load_skill
11:12:14 ... tool:read_skill_file      ← ~30 s for one model call
11:12:18 ... Gemini API error 503: This model is currently experiencing high demand.
```
