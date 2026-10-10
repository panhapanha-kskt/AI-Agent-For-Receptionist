# Plan: Faster, Smarter Receptionist Responses

> Copy of the original plan (from C:\Users\ASUS\.claude\plans\i-wanna-build-an-goofy-noodle.md).
> **Status (2026-10-10):** Phases 0-5 ✅ done · Phase 6 ⏳ not started.
> For what was actually built, the files changed and the next steps, see [PROGRESS.md](PROGRESS.md).
> Line numbers in the "file-by-file map" refer to the code *before* the work started.


## Context
The agent works, but every answer is slow, and it only "learns" when staff approve an
unanswered question. You want answers that feel as fast as ChatGPT, Gemini or Claude apps:
- known questions and workflows are answered straight from `skills/`;
- the agent uses the caller's context within a conversation;
- the system learns from what callers ask and how good the answers were.

### Why it is slow today (from the current code)
In `src/receptionist/agent/client.py`, a typical question costs **three model calls, one after
another**:

```
call 1: model picks a skill   → load_skill
call 2: model needs details   → read_skill_file (e.g. fees.yaml)
call 3: model writes answer
```

On top of that:
- The reply is only sent once all three calls are finished. Nothing is streamed.
- Speech-to-text loads the Whisper model on the first voice message, so that message waits
  for it.
- Spoken audio (TTS) only starts after the full text is ready.
- Nothing is cached, not even for a question asked 100 times a day.

**Target:** the first words appear in under 1 second, and a typical answer finishes in about
1.5-2.5 s. Today each answer needs three model calls plus waiting for the whole reply. Quality
must not drop; the eval in Phase 0 proves it.

---

## Phase 0: Measure first (needed to prove every later gain)
- Add timing for every stage to each request: `stt_ms, route_ms, llm_ms[round], tool_ms, tts_ms,
  total_ms`, plus token counts from `response.usage_metadata`. Store one row per turn in a new
  `turn_metrics` table, and log it.
- Turn USEIT.md section 6.2 into `tests/evals/questions.yaml`: 40+ questions in English and
  Khmer, each with the expected skill and expected facts. Write `scripts/run_eval.py` to score
  accuracy, the routing hit rate and p50/p95 latency.
- Files: new `src/receptionist/metrics/` (model + service), `agent/client.py` (timers),
  `scripts/run_eval.py`.

## Phase 1: Quick wins (no change in how the agent behaves)
1. **Stream the reply.** Add a `POST /api/chat/stream` endpoint (Server-Sent Events) built on
   `client.aio.models.generate_content_stream`. `web/js/app.js` shows the words as they arrive.
   This makes the biggest difference to how fast it *feels*.
2. **Speak sentence by sentence.** Start text-to-speech on the first complete sentence instead
   of waiting for the whole answer.
3. **Use the async client** (`client.aio`) with one shared connection, and make the chat
   endpoints `async`.
4. **Pick the thinking level per question.** Use `ThinkingConfig(thinking_level=LOW)` for FAQ
   questions and `MEDIUM` only for multi-step workflows. Thinking tokens are the hidden latency.
5. **Keep the cached prefix stable.** The system prompt and tool list are already deterministic.
   Put anything that changes per question *after* them, so Gemini's implicit caching can reuse
   the prefix. Check `cached_content_token_count` in the Phase 0 metrics.
6. **Load the speech model at startup.** Load the Whisper model(s) when the server starts, not
   on the first voice message.
- Files: `chat/router.py`, `chat/schemas.py`, `agent/client.py`, `voice/stt.py`, `voice/tts.py`,
  `web/js/app.js`, `main.py` (warm-up in `lifespan`).

## Phase 2: Skill router (3 model calls → usually 1). The biggest real speed-up
Choose the skill **on our server, before calling Gemini**, then put its content into the first
request.

```
question ─► [Router] ──match (score ≥ threshold)──► insert SKILL.md + small data files
              │                                       into the request ─► 1 Gemini call
              └──no clear match──► today's tool loop (load_skill / read_skill_file)
```

- **How the router matches:** it compares the question with the skill descriptions plus
  routing examples, using Gemini embeddings (`client.models.embed_content`). Each skill's
  embeddings are computed once, when skills load or reload.
- **Fallback:** the existing similarity function in `knowledge/service.py` keeps the router
  working without the embeddings API.
- **What gets inserted:** the matched skill's `SKILL.md`, plus its data files if they are under
  roughly 8 KB. Use the existing `SkillRegistry.load()` / `read_file()` in `skills/loader.py`.
  The content goes in the **user turn**, tagged as reference data, never into the system
  prompt. This keeps the prompt-injection boundary in place.
- **Tools stay available.** If the router picked the wrong skill, the model can still call
  `load_skill`, so accuracy can't get worse. It just costs one extra call.
- **Expected result:** most FAQ questions answered in 1 call instead of 3, about 2-3× faster.
- Files: new `src/receptionist/router/` (`embeddings.py`, `service.py`), `agent/client.py`,
  `skills/loader.py` (compute embeddings when skills reload).

## Phase 3: Answer fast paths
1. **Cache approved answers.** If a question closely matches an approved knowledge entry
   (embedding similarity ≥ 0.92, same language), answer with **one fast call to the `-lite`
   model** that rephrases the approved answer. Don't return the cached text word for word; the
   caller's wording and context differ. Clear the cache whenever knowledge changes or skills
   reload.
2. **Workflow skills.** For multi-step tasks (admission steps, leaving a message, booking a
   tour), add a `workflow.yaml` to the skill. It lists the steps and the details to collect.
   The router inserts it, and the agent follows the steps one turn at a time instead of working
   them out each time. `take_message` is the first workflow: name, then contact, then message,
   then confirm.
3. **Choose the model by question type.** Use the `-lite` model for routed FAQ answers and
   rephrasing cached answers, and the main Flash model for workflows and anything unrouted.
   Make both settings in `config.py`.
- Files: `knowledge/service.py` (embedding search), `agent/client.py` (choose the path),
  `config.py` (`GEMINI_MODEL_FAST`, `ANSWER_CACHE_THRESHOLD`), skill folders (`workflow.yaml`),
  and the loader (allow `workflow.yaml`, which it already does because `.yaml` is permitted).

## Phase 4: Use the caller's context (this conversation only, privacy-safe)
- **Session facts:** keep a small set of facts per session: preferred language, role
  (parent/student/visitor), and the program or grade they're asking about. The model reads
  them from a short "known so far" note sent with each turn. They are **never** stored after
  the session ends.
- **Long conversations:** once a chat is past about 10 turns, summarise the older turns into
  one short note, instead of resetting at 20 turns as it does now. This keeps requests small
  and fast.
- **Follow-up questions:** the router also looks at the previous turn's skill, so "and for
  grade 10?" stays on the fees skill.
- Files: `agent/client.py` (`SessionStore` stores facts + a summary), `agent/prompts.py`.

## Phase 5: Learning loop upgrades (always with staff approval)
| Signal | What the system learns | Approval |
|---|---|---|
| 👍/👎 buttons under each answer | 👎 answers go to the review queue with the full context | Staff fix → knowledge |
| Questions the router got wrong (the model called `load_skill` on a different skill) | A **routing example** is added to that skill | Automatic. Low risk: routing only decides which approved content to load, never what the facts are |
| Unanswered questions | The **LLM drafts an answer** from the existing skills, shown in `/admin` | Staff edit and approve |
| A knowledge entry used often | An **"Export to faq.md"** button moves it into the skill file | Staff click; committed to Git |
| Slow turns (p95 above target) | Shown on `/admin` with their stage timings | Developer investigates |

- Callers still can never change facts. Only routing examples are learned automatically.
- Files: new `feedback` table + endpoint, `learning/service.py` (drafts, routing examples),
  `admin/router.py` + `web/js/admin.js` (feedback list, export, latency panel), and `web/js/app.js`
  (👍/👎 buttons).

## Phase 6: Infrastructure (when traffic grows)
- Use Redis for sessions, the answer cache and the rate limiter, with several uvicorn workers.
- Use explicit Gemini context caching (`client.caches.create`) for skill content once the
  combined skills are large.
- Host the server in a region close to Gemini's servers, and keep HTTP connections open
  between requests.

---

## File-by-file map: where each problem is in your project today

Open these spots first. Line numbers are from the current code.

### Problem A: three model calls per answer (the main cause of slowness)
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/agent/client.py` | `reply()` L132, loop L146-147 | `for _ in range(agent_max_tool_rounds)`, where each round is one blocking `generate_content` call | Run the router before the loop and add the matched skill's content to the first user turn, so most questions finish in round 1 |
| `src/receptionist/skills/loader.py` | `reload()` L82, `load()` L114, `read_file()` L122 | Skill text is only available through tools | Add `bundle(name)`, which returns SKILL.md + small data files in one string. Compute skill embeddings in `reload()` |
| `src/receptionist/agent/prompts.py` | L40 "Skills available" | Only names + descriptions; the model must call tools to see facts | Add a rule: "If a REFERENCE block is in the message, answer from it first" |
| `src/receptionist/agent/tools.py` | `tool_definitions()` L37 | 5 tools, always sent | No change. They stay as the fallback when the router misses |
| **new** `src/receptionist/router/service.py` | — | — | `route(question, prev_skill) → (skill, score)` using embeddings, with a fallback to `knowledge.similarity` |

### Problem B: the reply is only sent when it is 100% finished
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/chat/router.py` | `_answer()` L30, `chat()` L55, `voice()` L61 | Sync endpoints return one JSON object at the end | Add an `async` `POST /api/chat/stream` that returns Server-Sent Events, using `client.aio.models.generate_content_stream` |
| `src/receptionist/chat/schemas.py` | `ChatResponse` L11 | One final object | Add the stream event shapes (`delta`, `done`, `error`) |
| `web/js/app.js` | `handleResponse()` L44, `addBubble("bot"...)` L54, submit L59-72 | Waits for `res.json()` | Read the stream and add text to the bubble as it arrives (still `textContent` only) |
| `src/receptionist/agent/client.py` | L147 | `generate_content` | Add a streaming variant of `reply()` that yields text pieces |

### Problem C: the model settings are not tuned for speed
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/agent/client.py` | `_config()` L107-123 | `temperature=0.3`, no thinking setting | Add `thinking_config=ThinkingConfig(thinking_level=LOW)` for FAQ questions and `MEDIUM` for workflows. Choose the model per path |
| `src/receptionist/config.py` | L10-40 `Settings` | One `gemini_model` | Add `gemini_model_fast` (lite), `thinking_level`, `router_threshold`, `answer_cache_threshold` |

### Problem D: voice is slow, especially the first voice message
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/voice/stt.py` | `_model()` L36-45 | Whisper loads on the first request | Add `warm_up()`, which loads the models up front |
| `src/receptionist/main.py` | `lifespan()` L32-33 | Only `init_db()` | Call `stt.warm_up()` here when `STT_ENABLED=true` |
| `web/js/app.js` | `speak()` L31-41 | Speaks only after the full reply arrives | Speak each sentence as it finishes streaming |
| `src/receptionist/voice/tts.py` | `synthesize()` L18 / L48 | Whole-text synthesis | (Server TTS only) synthesize sentence by sentence |

### Problem E: no caching, so the same question costs the same every time
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/knowledge/service.py` | `similarity()` L33, `search()` L40, `MIN_SCORE` L11 | Word/bigram overlap only, scanning every row | Add embedding-based `best_match()` for the answer cache. Keep `similarity()` as the fallback |
| `src/receptionist/learning/service.py` | `approve()` L52 | Creates knowledge | Also clear the answer cache |
| `src/receptionist/admin/router.py` | `update_knowledge()` L67, `delete_knowledge()` L76, `reload_skills()` L109 | Change content | Also clear the cache and rebuild the router embeddings |

### Problem F: weak use of the caller's context
| File | Where | What you'll see | Change |
|---|---|---|---|
| `src/receptionist/agent/client.py` | `SessionStore` L51-81, reset L136-137 | Stores raw history and wipes it after 20 turns | Store `facts` (language, role, program/grade, last skill) + a `summary`. Summarise older turns instead of wiping them |
| `src/receptionist/config.py` | `max_history_turns` L36 | 20 | Add `summarize_after_turns` (~10) |
| `src/receptionist/agent/prompts.py` | — | — | Rule: use the "Known so far" note, but never store personal data |

### Problem G: the agent learns only from approved unanswered questions
| File | Where | What you'll see | Change |
|---|---|---|---|
| `web/js/app.js` | `addBubble()` / L54 | No feedback | Add 👍/👎 under each bot reply, which calls `POST /api/feedback` |
| `src/receptionist/learning/models.py` | L15 | `UnansweredQuestion` only | Add `Feedback`, `RoutingExample`, and `draft_answer` on the question |
| `src/receptionist/learning/service.py` | `log_unanswered()` L23 | Logs the question | Also write an LLM draft answer from the skills (for staff). Learn routing examples from router misses |
| `src/receptionist/admin/router.py` + `web/js/admin.js` | — | Queue / knowledge / messages | Add a 👎 feedback list, an "Export to faq.md" button and a latency panel |
| `src/receptionist/database.py` | `init_db()` L30 | Imports models | Import the new models (feedback, metrics) |

### Problem H: nothing measures speed
| File | Where | Change |
|---|---|---|
| **new** `src/receptionist/metrics/` | — | `TurnMetric` table + `record()`: stt/route/llm/tool/tts ms + tokens |
| `src/receptionist/agent/client.py` | around L147 and the tool loop | Time each call; read `response.usage_metadata` |
| `src/receptionist/chat/router.py` | `voice()` L61 | Time STT and TTS |
| **new** `tests/evals/questions.yaml` + `scripts/run_eval.py` | — | Accuracy + p50/p95 before and after each phase |

### Tests to update or add
- `tests/conftest.py`: `FakeGemini` also needs `aio.models.generate_content_stream` and `embed_content`.
- New: `tests/test_router.py`, `tests/test_stream.py`, `tests/test_cache.py`, `tests/test_feedback.py`.

## Priority order (most effect for the work)
1. Phase 0 (measure) → 2. Phase 1 (streaming and quick wins) → 3. **Phase 2 (router)** →
4. Phase 3.1 + 3.3 (cache + lite model) → 5. Phase 5 (feedback and learning) → 6. Phase 4 → 7. Phase 6

## Verification (for every phase)
- `pytest` stays green. Add tests for the router (correct skill for EN and KM questions, falls
  back below the threshold), the cache (cleared on reload), streaming (SSE chunks in order) and
  workflows.
- Run `scripts/run_eval.py` before and after each phase. Accuracy must not drop; compare p50/p95
  latency and the router hit rate.
- Manual: ask a fee question and check that the logged `llm_ms` shows **1 call** where it used
  to be 3. Also check that the first words appear in under 1 s in the browser.
- Security: the prompt-injection tests in USEIT 6.4 must still pass. Content inserted by the
  router stays in the user turn, marked as data.
