"use strict";

// One conversation id per browser tab.
const sessionId = crypto.randomUUID();
const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("text");
const sendBtn = form.querySelector("button[type=submit]");
const micBtn = document.getElementById("mic");
const langSel = document.getElementById("lang");
const statusEl = document.getElementById("status");
let busy = false;

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  micBtn.disabled = value;
}

// textContent only: replies are never parsed as HTML.
function addBubble(role, text, note) {
  const li = document.createElement("li");
  li.className = role;
  if (note) {
    const small = document.createElement("small");
    small.textContent = note;
    li.appendChild(small);
  }
  const body = document.createTextNode(text);
  li.appendChild(body);
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
  return { li, body };
}

// --- Speech: speak each sentence as soon as it is complete -------------------
const SENTENCE_END = /[.!?។៕\n]/;
let spokenUpTo = 0;

function speakText(text, lang) {
  if (!("speechSynthesis" in window) || !text.trim()) return;
  const utter = new SpeechSynthesisUtterance(text.trim());
  utter.lang = lang === "km" ? "km-KH" : "en-US";
  speechSynthesis.speak(utter); // utterances queue up and play in order
}

function speakNewSentences(fullText, lang, final = false) {
  const pending = fullText.slice(spokenUpTo);
  let cut = -1;
  if (final) {
    cut = pending.length;
  } else {
    for (let i = pending.length - 1; i >= 0; i--) {
      if (SENTENCE_END.test(pending[i])) { cut = i + 1; break; }
    }
  }
  if (cut > 0) {
    speakText(pending.slice(0, cut), lang);
    spokenUpTo += cut;
  }
}

function guessLang(text) {
  return /[ក-៿]/.test(text) ? "km" : langSel.value === "km" ? "km" : "en";
}

// --- Feedback: rate each answer (helps staff improve the receptionist) -------
function addFeedback(li, turnId) {
  const box = document.createElement("div");
  box.className = "feedback";
  const send = async (rating) => {
    box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, turn_id: turnId, rating }),
      });
      box.textContent = res.ok
        ? (rating === "up" ? "Thanks!" : "Thanks, staff will review this answer.")
        : "Could not send feedback.";
    } catch (_) {
      box.textContent = "Could not send feedback.";
    }
  };
  for (const [rating, label, title] of [["up", "👍", "Helpful"], ["down", "👎", "Not helpful"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "secondary";
    b.textContent = label;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.addEventListener("click", () => send(rating));
    box.appendChild(b);
  }
  li.appendChild(box);
}

// --- Streaming reply (Server-Sent Events over POST) ---------------------------
function parseSse(block) {
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try { return { event, data: JSON.parse(data.join("\n")) }; } catch (_) { return null; }
}

async function streamReply(message, lang) {
  const payload = { session_id: sessionId, message };
  if (lang === "en" || lang === "km") payload.lang = lang;

  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    let detail = "Something went wrong.";
    try { const d = await res.json(); if (typeof d.detail === "string") detail = d.detail; } catch (_) {}
    setStatus(res.status === 429 ? "Too many requests, please wait a moment." : detail, true);
    return;
  }

  if ("speechSynthesis" in window) speechSynthesis.cancel();
  spokenUpTo = 0;
  let bubble = null;
  let text = "";
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const msg = parseSse(buffer.slice(0, sep));
      buffer = buffer.slice(sep + 2);
      if (!msg) continue;

      if (msg.event === "delta") {
        if (!bubble) { bubble = addBubble("bot", ""); setStatus(""); }
        text += msg.data.text;
        bubble.body.textContent = text;
        log.scrollTop = log.scrollHeight;
        speakNewSentences(text, guessLang(text));
      } else if (msg.event === "done") {
        const d = msg.data;
        if (d.session_reset) addBubble("bot", "(Starting a new conversation.)");
        if (!bubble) bubble = addBubble("bot", "");
        bubble.body.textContent = d.reply; // final, cleaned text
        if (d.turn_id) addFeedback(bubble.li, d.turn_id);
        setStatus("");
        if (d.audio_base64) {
          new Audio("data:audio/wav;base64," + d.audio_base64).play().catch(() => {});
        } else {
          speakNewSentences(d.reply, d.lang, true);
        }
      } else if (msg.event === "error") {
        setStatus(typeof msg.data.detail === "string" ? msg.data.detail : "Something went wrong.", true);
      }
    }
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = "";
  addBubble("user", message);
  setStatus("Thinking...");
  setBusy(true);
  try {
    await streamReply(message, null);
  } catch (_) {
    setStatus("Network error. Please try again.", true);
  } finally {
    setBusy(false);
  }
});

// --- Voice messages: hold the mic button to record, release to send ----------
let recorder = null;
let chunks = [];
const MAX_RECORD_MS = 120000;

async function startRecording() {
  if (recorder || busy) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      sendVoice(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      recorder = null;
    };
    recorder.start();
    micBtn.classList.add("recording");
    setStatus("Recording... release to send.");
    setTimeout(stopRecording, MAX_RECORD_MS);
  } catch (_) {
    setStatus("Microphone not available. Check browser permissions.", true);
  }
}

function stopRecording() {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    micBtn.classList.remove("recording");
  }
}

async function sendVoice(blob) {
  if (blob.size < 1000) { setStatus("Recording too short.", true); return; }
  setStatus("Listening...");
  setBusy(true);
  const fd = new FormData();
  fd.append("lang", langSel.value);
  fd.append("audio", blob, "voice.webm");
  try {
    // 1) speech → text, 2) stream the reply like a typed message.
    const res = await fetch("/api/transcribe", { method: "POST", body: fd });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "Could not understand the recording.";
      setStatus(res.status === 429 ? "Too many requests, please wait a moment." : detail, true);
      return;
    }
    addBubble("user", data.transcript, "🎤 voice message");
    setStatus("Thinking...");
    await streamReply(data.transcript, data.lang);
  } catch (_) {
    setStatus("Network error. Please try again.", true);
  } finally {
    setBusy(false);
  }
}

micBtn.addEventListener("pointerdown", startRecording);
micBtn.addEventListener("pointerup", stopRecording);
micBtn.addEventListener("pointerleave", stopRecording);

addBubble("bot", "Hello! I'm the school's AI receptionist. Ask me about admissions, fees, " +
  "or how to reach us. សួស្តី! អ្នកអាចសួរជាភាសាខ្មែរបាន។");
