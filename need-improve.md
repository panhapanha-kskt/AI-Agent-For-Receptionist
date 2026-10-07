# Need Improve: Security & Best-Practice Roadmap

Future work for this project, based on a review of the current code. Most urgent items first.
Tick the boxes (`[x]`) as you finish them.

**Legend:** 🔴 do before going public · 🟠 high · 🟡 medium · 🟢 later

---

## 🔴 1. Cost and overload attacks
**Problem:** `/api/chat` is anonymous, and every message calls Claude Opus, which costs money.
The only protection is a per-IP rate limit, so a bot rotating IP addresses could run up a large
bill. Voice is worse: `src/receptionist/voice/stt.py` runs Whisper with no limit on how many run
at once, so a few long uploads at the same time can use up all the CPU.

- [ ] Set a monthly spend limit in the Anthropic console
- [ ] Add a server-side daily cap on Claude calls, with a friendly "busy" reply when it is reached
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
**Problem:** callers' words are sent to Anthropic. `src/receptionist/security/redact.py` only
masks emails and phone numbers, and data is kept forever.

- [ ] Check local data-protection law and Anthropic's commercial terms and data-retention options
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
- [ ] Rotate `ANTHROPIC_API_KEY` and admin credentials on a schedule (e.g. every 90 days)

---

## 🟡 9. Server-issued sessions
**Problem:** the browser chooses its own `session_id`.

- [ ] The server issues a signed `HttpOnly` session cookie
- [ ] Cap `take_message` and `log_unanswered` calls per session, so nobody can flood staff

## 🟡 10. Remove the `TypeError` shortcut
**Problem:** `src/receptionist/chat/router.py` catches `TypeError` to handle a missing API key,
which could hide real bugs.

- [ ] Check credentials once at startup and fail fast with a clear message
- [ ] Remove `TypeError` from the `except` clause

## 🟡 11. Automated evals (accuracy + prompt injection)
**Problem:** the test sheet and attack table in `USEIT.md` (section 6) are manual.

- [ ] Script that runs the question set against the real model and checks the expected facts
- [ ] Prompt-injection set in English **and Khmer**, typed and spoken
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
3. **9-10**, then **11** (evals). Evals are what prove the agent stays correct as it improves.
4. Everything else as the project grows.

See also: `USEIT.md` (how to use and test the system) and `README.md` (overview and setup).
