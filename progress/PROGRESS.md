# Progress Report: Faster, Smarter Receptionist

> **Status: PAUSED (2026-10-10).** Phases 0–5 are coded and tested (122 tests pass).
> Phase 6 and the documentation updates still need doing. The last section,
> "How to continue", lists the exact next steps.
> Nothing has been committed to git. You commit it yourself (see section 7).

The plan this work follows is `C:\Users\ASUS\.claude\plans\i-wanna-build-an-goofy-noodle.md`.
`need-improve.md` also has a short progress table.

---

## 1. The goal

You asked for answers that feel as fast as ChatGPT, Gemini or Claude, with the receptionist
learning safely from callers. Before this work:

- Each question took **3 Gemini calls, one after another** (pick a skill → read a file → answer).
- Nothing was shown until the whole answer was ready (no streaming).
- The first voice message waited for the Whisper model to load.
- The conversation was wiped after 20 turns.
- The only learning was staff approving unanswered questions.

### Measured result (real Gemini, same fee question)

| | Before | After |
|---|---|---|
| Gemini calls | 3 | **1** |
| Time to first words | 15.7 s | **1.4 s** |
| Full answer | 16.5 s | **1.7 s** |

The free-tier Gemini key is slow and unpredictable: the same request took between 2.3 s and
18.7 s. It also often returns **429 (quota)** errors. A paid key will be faster and more
private.

---

## 2. Progress by phase

| Phase | What it does | Status |
|---|---|---|
| 0. Measure | Per-answer timings and token counts saved in the database; evaluation questions and script | ✅ Done |
| 1. Quick wins | Streamed replies (SSE), speech sentence by sentence, async Gemini client, low thinking level, Whisper loaded at startup, timeouts and retries | ✅ Done |
| 2. Skill router | Picks the skill **before** calling Gemini and puts its content in the request, so 3 calls become 1 | ✅ Done |
| 3. Fast paths | Approved answers matched quickly; fast "lite" model for simple answers; `workflow.yaml` for multi-step tasks; fallback model when one is overloaded | ✅ Done |
| 4. Conversation memory | Older turns are summarised instead of wiped; follow-up questions keep the same skill | ✅ Done |
| 5. Learning loop | 👍/👎 buttons, learned routing examples, "Draft with AI" for staff, "Move to skill faq.md", speed panel in admin | ✅ Code done and checked live. Remaining: run the eval and update docs |
| 6. Infrastructure | Redis, several workers, Gemini context caching, server region | ⏳ Not started. Only needs writing up in `need-improve.md` (not needed at your current traffic) |

---

## 3. How it works now (simple picture)

```
Caller question
   │
   ├─► Router (on our server, no AI call)
   │     1. Clear keyword match?  ─────────┐
   │     2. Embedding match (Gemini)? ─────┤──► skill found → put SKILL.md + small files
   │     3. Keyword (IDF) match? ──────────┤      into the question → 1 call, fast "lite" model
   │     4. Short follow-up? → last skill ─┘
   │     5. Approved answer matches? ──────────► 1 call, fast model rephrases it
   │     6. Nothing matched ───────────────────► old tool loop (main model loads skills itself)
   │
   ├─► Gemini streams the answer → browser shows words as they arrive and speaks each sentence
   │
   └─► Saved: timings (turn_metrics), turn for 👍/👎, routing example if the router missed
```

How it copes with failures:
- If the fast model errors, the main model takes over.
- If the main model is overloaded, `gemini-3.6-flash` takes over. This only happens before any
  text has been sent.
- After a 429 error it stops trying that model for 60 seconds, and 429s are not retried.

---

## 4. Every file changed

### 4.1 New files (`??` in git status)

| File | Purpose |
|---|---|
| `src/receptionist/metrics/__init__.py` | Package marker |
| `src/receptionist/metrics/models.py` | `TurnMetric` table: path, skill, model, number of calls, route/ttft/llm/tool/stt/tts/total ms, tokens |
| `src/receptionist/metrics/service.py` | `TurnStats` timer, `record()` (never crashes a request), `summary()` with p50/p95, errors, slowest answers |
| `src/receptionist/router/__init__.py` | Package marker |
| `src/receptionist/router/embeddings.py` | `GeminiEmbedder` (`gemini-embedding-2`), cosine similarity, query and document text formats |
| `src/receptionist/router/lexical.py` | Tokenizer (English stemming, stop words, Khmer bigrams) and `LexicalIndex` (IDF keyword scoring) |
| `src/receptionist/router/service.py` | `SkillRouter`: shortcut, then embeddings, then keywords, then follow-up; index rebuilt when skills or examples change; LRU cache of 512 queries |
| `src/receptionist/router/knowledge.py` | `KnowledgeMatcher`: finds an approved answer that matches the question |
| `src/receptionist/agent/memory.py` | Prompts for summaries and AI drafts; `transcript()` with redaction |
| `src/receptionist/admin/export.py` | `export_to_faq()`: writes an approved Q&A into `skills/<skill>/faq.md` safely |
| `skills/admissions/workflow.yaml` | Steps for booking a campus tour |
| `scripts/run_eval.py` | Asks the evaluation questions to the real model and writes a report to `reports/` |
| `tests/evals/questions.yaml` | 18 evaluation questions in English and Khmer (facts, unknown topics, injection attempts) |
| `tests/test_metrics.py` | Tests for the timings |
| `tests/test_stream.py` | Tests for streaming (SSE) |
| `tests/test_router.py` | Tests for the router (English, Khmer, below threshold, follow-up) |
| `tests/test_fast_paths.py` | Tests for the fast model, fallback, circuit breaker and approved-answer path |
| `tests/test_memory.py` | Tests for summaries and long conversations |
| `tests/test_learning.py` | Tests for 👍/👎, routing examples, AI drafts and export to `faq.md` |

### 4.2 Modified files (`M` in git status)

| File | What changed |
|---|---|
| `src/receptionist/agent/client.py` | **Biggest change.** Async `ReceptionistAgent` with `stream()`/`reply()`, the router before the model, fast/main/fallback models, circuit breaker, `SessionState` with summary, routing examples learned, `draft_answer()` |
| `src/receptionist/agent/prompts.py` | Rules for `<school_reference>` blocks, workflows, and "load at most one or two skills" |
| `src/receptionist/agent/tools.py` | Small changes to support the new flow |
| `src/receptionist/chat/router.py` | Async endpoints, new `POST /api/chat/stream` (SSE), `POST /api/transcribe`, `POST /api/feedback` |
| `src/receptionist/chat/schemas.py` | `ChatRequest.lang`, `ChatResponse.turn_id`, `TranscriptResponse`, `FeedbackRequest` |
| `src/receptionist/config.py` | New settings (see section 6) |
| `src/receptionist/database.py` | Registers the new tables; `_add_missing_columns()` upgrades an existing DB safely |
| `src/receptionist/learning/models.py` | `UnansweredQuestion` gets `source`, `previous_answer` and `draft_answer`; new `Feedback` and `RoutingExample` tables |
| `src/receptionist/learning/service.py` | Feedback (a 👎 goes to the review queue), routing examples (deduplicated, max 50 per skill), drafts, counts |
| `src/receptionist/admin/router.py` | New endpoints: `/questions/{id}/draft`, `/knowledge/{id}/export`, `/feedback`, `/routing-examples`; `/metrics` now includes speed numbers |
| `src/receptionist/admin/schemas.py` | `FeedbackOut`, `RoutingExampleOut`, `ExportOut`, extra `QuestionOut` fields |
| `src/receptionist/skills/loader.py` | `bundle()` (SKILL.md + small files), `example_questions()`, `version` counter |
| `src/receptionist/voice/stt.py` | `warm_up()` loads Whisper at startup |
| `src/receptionist/main.py` | Startup warm-up (speech model and router index) |
| `web/js/app.js` | Reads the stream, shows text live, speaks sentence by sentence, 👍/👎 buttons, voice goes through `/api/transcribe` |
| `web/js/admin.js` | Speed panel, AI draft button, 👎 list, learned examples (with delete), "Move to skill faq.md" |
| `web/admin.html` | New sections for the items above |
| `web/style.css` | Classes `.feedback`, `.stats`, `.tag`, `.prev` |
| `skills/admissions/SKILL.md`, `skills/campus-and-contact/SKILL.md`, `skills/class-schedule/SKILL.md`, `skills/fees-and-payments/SKILL.md` | Better descriptions with Khmer keywords, so routing is more accurate |
| `skills/general-faq/faq.md` | Removed the campus-visit FAQ (now handled by the admissions workflow) |
| `tests/conftest.py` | `FakeGemini` supports async streaming and embeddings; tests never use the real API |
| `tests/test_api.py`, `tests/test_tools.py` | Updated for the async agent |
| `.env.example` | Documents all the new settings |
| `.gitignore` | Adds `reports/` |
| `pyproject.toml` | Dependency and tool settings |
| `need-improve.md` | Progress table and log (always rewritten as a whole file) |
| `README.md`, `USEIT.md` | Changed in earlier sessions (Gemini port). **Not yet updated for these new features** (still to do) |

---

## 5. New API endpoints

| Method and path | Who | What |
|---|---|---|
| `POST /api/chat/stream` | Public | Streams the answer as Server-Sent Events: `delta`, then `done` (or `error`) |
| `POST /api/transcribe` | Public | Voice → text only. The browser then calls the stream endpoint |
| `POST /api/feedback` | Public | `{session_id, turn_id, rating: "up"/"down", comment?}`. A 👎 goes to the review queue |
| `POST /api/admin/questions/{id}/draft` | Admin | AI writes a draft answer from the skills |
| `POST /api/admin/knowledge/{id}/export` | Admin | Moves an approved answer into `skills/<skill>/faq.md` |
| `GET /api/admin/feedback?rating=down` | Admin | List of ratings |
| `GET /api/admin/routing-examples` | Admin | Examples learned from questions |
| `DELETE /api/admin/routing-examples/{id}` | Admin | Removes a wrong example |
| `GET /api/admin/metrics?hours=24` | Admin | p50/p95 speed, calls per answer, errors, 👍/👎 counts |

---

## 6. New `.env` settings (all optional; these are the defaults)

```
GEMINI_MODEL_FAST=gemini-3.5-flash-lite
GEMINI_FALLBACK_MODEL=gemini-3.6-flash
THINKING_LEVEL=low
LLM_TIMEOUT_MS=20000
LLM_RETRY_ATTEMPTS=2
ROUTER_ENABLED=true
ROUTER_EMBEDDINGS=true
EMBEDDING_MODEL=gemini-embedding-2
ROUTER_THRESHOLD=0.65
ROUTER_MARGIN=0.03
ROUTER_LEXICAL_THRESHOLD=0.35
ROUTER_BUNDLE_MAX_BYTES=8000
KNOWLEDGE_THRESHOLD=0.85
KNOWLEDGE_LEXICAL_THRESHOLD=0.6
MAX_HISTORY_TURNS=40
SUMMARIZE_AFTER_TURNS=10
```

If the router picks wrong skills, raise `ROUTER_THRESHOLD`. If it misses too often, lower it.

---

## 7. Commands

Run all of these from the project folder (`C:\Programming-CTF\AI-Agent For Receptionist`)
in Git Bash. In PowerShell, use `.venv\Scripts\python` instead.

### Run the app
```bash
.venv/Scripts/python -m uvicorn receptionist.main:app --reload
# Chat:  http://127.0.0.1:8000/
# Admin: http://127.0.0.1:8000/admin   (needs ADMIN_API_KEY from .env)
```

### Checks I ran during the work (all passed at the pause: 122 tests)
```bash
.venv/Scripts/python -m pytest -q                       # tests (Gemini is faked, no key needed)
.venv/Scripts/python -m ruff check src tests scripts     # code style / lint
.venv/Scripts/python -m bandit -c pyproject.toml -r src  # security scan
```

### Evaluation with the real Gemini model (uses your quota)
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/run_eval.py --delay 3
# Report goes to reports/ (ignored by git). PYTHONIOENCODING is needed to print Khmer on Windows.
# --delay = seconds between questions; increase it if you get 429 errors on the free tier.
```
The script was recently changed to async. It passes lint and a syntax check, but **it has not
been run yet**. I was about to run it when the work was paused.

### Quick manual checks
```bash
# Streamed answer (watch the words arrive):
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"How much is grade 7 tuition?"}'

# Speed numbers (admin):
curl http://127.0.0.1:8000/api/admin/metrics -H "X-Admin-Key: <your ADMIN_API_KEY>"
```

### Saving the work to GitHub (you do this by hand; I don't run git)
```bash
git status                      # check that .env is NOT in the list
git add .
git status                      # check again before committing
git commit -m "Faster responses: streaming, skill router, fast model, memory, learning loop"
git push
```
`.env` is in `.gitignore`. Never commit it. Your Gemini key and admin key were pasted
into chat earlier, so please **rotate both** if you haven't already.

---

## 8. Problems hit and how they were fixed (for reference)

| Problem | Fix |
|---|---|
| Free-tier 429 quota errors | Don't retry 429s; switch to the fallback model straight away; 60-second circuit breaker |
| First question was slow because the router index was built then | The index is built at server startup |
| Unknown questions caused many model calls | Prompt rule "load at most 1–2 skills" plus a "no match" note |
| SSE endpoint crashed together with the rate limiter | Return `EventSourceResponse(...)` directly |
| Keyword router matched noise words | Switched to IDF-weighted scoring |
| Tests could have changed the real `skills/` folder | Export tests use a temporary copy |
| Old databases lack the new columns | `_add_missing_columns()` adds them on startup |

### Known limitations
- Free-tier key: slow and often hits quota. Upgrade to a paid key for real use.
- "Move to skill faq.md" fails (409 message) if `skills/` is read-only, e.g. in Docker with a
  read-only mount.
- Server-side MMS text-to-speech still makes the whole audio at once. Browser speech is
  sentence by sentence already.

---

## 9. How to continue (next steps in order)

1. **Run the tests** to confirm everything is still green:
   `.venv/Scripts/python -m pytest -q` (expect 122 passed).
2. **Run the eval** (optional, uses quota):
   `PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/run_eval.py --delay 3`, then compare
   accuracy and speed with the numbers in section 1.
3. **Update `need-improve.md`** (whole-file rewrite): mark Phase 5 ✅ and add a log line.
4. **Phase 6**: write up the infrastructure items in `need-improve.md`:
   - Redis for sessions, the rate limiter and the cache, with several uvicorn workers
   - explicit Gemini context caching (`client.caches.create`) once the skills get large
   - hosting in a region close to Gemini

   It isn't needed for one school's traffic.
5. **Update `USEIT.md` and `README.md`** with:
   - streaming, the 👍/👎 buttons and `/api/transcribe`
   - the new admin features: Draft with AI, Move to faq.md, the speed panel and routing examples
   - the eval script
   - the new `.env` settings
   - the free-tier note
6. Run pytest, ruff and bandit again, then **commit and push by hand** (section 7).

To resume with Claude, say: *"Continue from progress/PROGRESS.md, step 2."*
