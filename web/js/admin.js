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
    const skill = skillSelect(q.suggested_skill);
    box.appendChild(el("div", { class: "card" },
      el("div", { class: "meta", text: `#${q.id} · ${q.lang.toUpperCase()} · asked ${q.times_asked}×` }),
      question, answer,
      el("div", { class: "row" },
        skill,
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
        el("button", { type: "button", class: "secondary", text: "Remove", onclick: () => action(() =>
          api(`/knowledge/${k.id}`, { method: "DELETE" }), "Removed.") }),
      ),
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
  const [skills, questions, messages, knowledge] = await Promise.all([
    api("/skills"), api("/questions"), api("/messages"), api("/knowledge"),
  ]);
  renderSkills(skills);
  renderQuestions(questions);
  renderMessages(messages);
  renderKnowledge(knowledge);
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
