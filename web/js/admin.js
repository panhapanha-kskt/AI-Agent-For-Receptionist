"use strict";

// The admin key is kept only for this tab (sessionStorage), never in localStorage.
const KEY = "adminKey";
const $ = (id) => document.getElementById(id);
let skillNames = [];

function getKey() { try { return sessionStorage.getItem(KEY) || ""; } catch (_) { return ""; } }
function setKey(v) { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch (_) {} }

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const res = await fetch("/api/admin" + path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Admin-Key": getKey(), ...(options.headers || {}) },
  });
  if (res.status === 401) { setKey(""); showPanel(false); throw new Error("Wrong admin key."); }
  if (!res.ok) {
    let detail = "Request failed.";
    try { const d = await res.json(); if (typeof d.detail === "string") detail = d.detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

// Small DOM helper: text is always set via textContent.
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") node.textContent = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else node.setAttribute(k, v);
  }
  children.forEach((c) => c && node.appendChild(c));
  return node;
}

function skillSelect(selected) {
  const sel = el("select", { "aria-label": "Skill" });
  skillNames.forEach((name) => {
    const opt = el("option", { value: name, text: name });
    if (name === selected) opt.selected = true;
    sel.appendChild(opt);
  });
  return sel;
}

async function action(fn, okText) {
  try { await fn(); setStatus(okText); await refresh(); }
  catch (e) { setStatus(e.message, true); }
}

function renderQuestions(items) {
  const box = $("questions");
  box.replaceChildren();
  if (!items.length) { box.appendChild(el("p", { class: "note", text: "Nothing waiting. 🎉" })); return; }
  items.forEach((q) => {
    const question = el("textarea", { "aria-label": "Question" });
    question.value = q.question;
    const answer = el("textarea", { placeholder: q.lang === "km" ? "ចម្លើយ..." : "Answer...", "aria-label": "Answer" });
    if (q.draft_answer && !q.draft_answer.startsWith("NOT FOUND")) answer.value = q.draft_answer;
    const skill = skillSelect(q.suggested_skill);
    const source = q.source === "feedback" ? "👎 rated by a caller" : "couldn't answer";
    box.appendChild(el("div", { class: "card" },
      el("div", { class: "meta", text: `#${q.id} · ${q.lang.toUpperCase()} · asked ${q.times_asked}× · ${source}` }),
      question,
      q.previous_answer ? el("p", { class: "prev", text: `Answer the caller didn't like: ${q.previous_answer}` }) : null,
      q.draft_answer && q.draft_answer.startsWith("NOT FOUND")
        ? el("p", { class: "note", text: "AI draft: not found in the skills. Please write the answer (and consider adding it to a skill)." })
        : null,
      answer,
      el("div", { class: "row" },
        skill,
        el("button", { type: "button", class: "secondary", text: "Draft with AI", onclick: () => action(() =>
          api(`/questions/${q.id}/draft`, { method: "POST" }), "Draft ready. Check and edit it before approving.") }),
        el("button", { type: "button", text: "Approve", onclick: () => action(() => {
          if (!answer.value.trim()) throw new Error("Write an answer first.");
          return api(`/questions/${q.id}/approve`, { method: "POST",
            body: JSON.stringify({ answer: answer.value.trim(), skill: skill.value, question: question.value.trim() }) });
        }, "Approved. The receptionist now knows this.") }),
        el("button", { type: "button", class: "secondary", text: "Dismiss",
          onclick: () => action(() => api(`/questions/${q.id}/dismiss`, { method: "POST" }), "Dismissed.") }),
      ),
    ));
  });
}

function renderMessages(items) {
  const box = $("messages");
  box.replaceChildren();
  if (!items.length) { box.appendChild(el("p", { class: "note", text: "No messages." })); return; }
  items.forEach((m) => {
    box.appendChild(el("div", { class: "card" },
      el("div", { class: "meta", text: `#${m.id} · for ${m.for_office} · ${new Date(m.created_at).toLocaleString()} · ${m.status}` }),
      el("div", { text: `${m.caller_name} (${m.contact})` }),
      el("p", { text: m.body }),
      m.status === "new" ? el("button", { type: "button", class: "secondary", text: "Mark done",
        onclick: () => action(() => api(`/messages/${m.id}/done`, { method: "POST" }), "Marked done.") }) : null,
    ));
  });
}

function renderKnowledge(items) {
  const box = $("knowledge");
  box.replaceChildren();
  if (!items.length) { box.appendChild(el("p", { class: "note", text: "No approved answers yet." })); return; }
  items.forEach((k) => {
    const answer = el("textarea", { "aria-label": "Answer" });
    answer.value = k.answer;
    box.appendChild(el("div", { class: "card" },
      el("div", { class: "meta", text: `#${k.id} · ${k.skill} · ${k.lang.toUpperCase()} · v${k.version}` }),
      el("strong", { text: k.question }),
      answer,
      el("div", { class: "row" },
        el("button", { type: "button", text: "Save new version", onclick: () => action(() =>
          api(`/knowledge/${k.id}`, { method: "PUT", body: JSON.stringify({ answer: answer.value.trim() }) }), "Saved.") }),
        el("button", { type: "button", class: "secondary", text: "Move to skill faq.md",
          title: "Write this Q&A into the skill's faq.md (permanent; remember to commit it to git)",
          onclick: () => action(async () => {
            const r = await api(`/knowledge/${k.id}/export`, { method: "POST" });
            return r;
          }, "Moved into the skill's faq.md. Commit the skills folder to git.") }),
        el("button", { type: "button", class: "secondary", text: "Remove", onclick: () => action(() =>
          api(`/knowledge/${k.id}`, { method: "DELETE" }), "Removed.") }),
      ),
    ));
  });
}

function stat(label, value) {
  return el("div", { class: "card" }, el("strong", { text: value == null ? "–" : String(value) }), el("span", { class: "meta", text: label }));
}

function renderMetrics(m) {
  const box = $("metrics");
  const secs = (ms) => (ms == null ? null : (ms / 1000).toFixed(1) + " s");
  const fb = m.feedback || {};
  box.replaceChildren(
    stat("answers", m.turns),
    stat("first words (median)", secs(m.ttft_ms.p50)),
    stat("full answer (median)", secs(m.total_ms.p50)),
    stat("full answer (slowest 5%)", secs(m.total_ms.p95)),
    stat("model calls per answer", m.avg_llm_calls),
    stat("answered from routed skill", (m.by_path.routed || 0) + (m.by_path.knowledge || 0)),
    stat("errors", m.errors),
    stat("👍 / 👎", `${fb.up || 0} / ${fb.down || 0}`),
  );
}

function renderFeedback(items) {
  const box = $("feedback");
  box.replaceChildren();
  if (!items.length) { box.appendChild(el("p", { class: "note", text: "No 👎 ratings. 🎉" })); return; }
  items.slice(0, 20).forEach((f) => {
    box.appendChild(el("div", { class: "card" },
      el("div", { class: "meta", text: `${new Date(f.created_at).toLocaleString()} · ${f.skill || "no skill"}` }),
      el("strong", { text: f.question }),
      el("p", { class: "prev", text: f.answer }),
      f.comment ? el("p", { text: `Comment: ${f.comment}` }) : null,
    ));
  });
}

function renderExamples(items) {
  const box = $("examples");
  box.replaceChildren();
  if (!items.length) { box.appendChild(el("p", { class: "note", text: "None learned yet." })); return; }
  items.forEach((e) => {
    box.appendChild(el("div", { class: "row card" },
      el("span", { class: "tag", text: e.skill }),
      el("span", { text: e.text }),
      el("button", { type: "button", class: "secondary", text: "Delete", onclick: () => action(() =>
        api(`/routing-examples/${e.id}`, { method: "DELETE" }), "Deleted.") }),
    ));
  });
}

function renderSkills(data) {
  skillNames = data.skills;
  const box = $("skills");
  box.replaceChildren(el("p", { text: data.skills.join(", ") || "(none)" }));
  data.errors.forEach((err) => box.appendChild(el("p", { class: "status error", text: err })));
}

async function refresh() {
  const [skills, questions, messages, knowledge, metrics, feedback, examples] = await Promise.all([
    api("/skills"), api("/questions"), api("/messages"), api("/knowledge"),
    api("/metrics"), api("/feedback?rating=down"), api("/routing-examples"),
  ]);
  renderSkills(skills);
  renderMetrics(metrics);
  renderQuestions(questions);
  renderMessages(messages);
  renderKnowledge(knowledge);
  renderFeedback(feedback);
  renderExamples(examples);
}

function showPanel(show) {
  $("panel").classList.toggle("hidden", !show);
  $("login").classList.toggle("hidden", show);
}

$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  setKey($("key").value);
  $("key").value = "";
  try { await refresh(); showPanel(true); setStatus(""); }
  catch (err) { setStatus(err.message, true); }
});
$("refresh").addEventListener("click", () => refresh().catch((e) => setStatus(e.message, true)));
$("reload-skills").addEventListener("click", () =>
  action(() => api("/skills/reload", { method: "POST" }), "Skills reloaded."));
$("logout").addEventListener("click", () => { setKey(""); showPanel(false); setStatus("Signed out."); });

if (getKey()) refresh().then(() => showPanel(true)).catch(() => showPanel(false));
