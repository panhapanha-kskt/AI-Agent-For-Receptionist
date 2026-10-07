"use strict";

// One conversation id per browser tab.
const sessionId = crypto.randomUUID();
const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("text");
const micBtn = document.getElementById("mic");
const langSel = document.getElementById("lang");
const statusEl = document.getElementById("status");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
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
  li.appendChild(document.createTextNode(text));
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
}

function speak(data) {
  if (data.audio_base64) {
    new Audio("data:audio/wav;base64," + data.audio_base64).play().catch(() => {});
    return;
  }
  // No server audio: use the browser's own voice, if it has one for the language.
  if (!("speechSynthesis" in window)) return;
  const utter = new SpeechSynthesisUtterance(data.reply);
  utter.lang = data.lang === "km" ? "km-KH" : "en-US";
  speechSynthesis.cancel();
  speechSynthesis.speak(utter);
}

async function handleResponse(res) {
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error */ }
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong.";
    setStatus(res.status === 429 ? "Too many requests, please wait a moment." : detail, true);
    return;
  }
  if (data.transcript) addBubble("user", data.transcript, "🎤 voice message");
  if (data.session_reset) addBubble("bot", "(Starting a new conversation.)");
  addBubble("bot", data.reply);
  setStatus("");
  speak(data);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addBubble("user", message);
  setStatus("Thinking...");
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    await handleResponse(res);
  } catch (_) {
    setStatus("Network error. Please try again.", true);
  }
});

// --- Voice messages: hold the mic button to record, release to send ---
let recorder = null;
let chunks = [];
const MAX_RECORD_MS = 120000;

async function startRecording() {
  if (recorder) return;
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
  const fd = new FormData();
  fd.append("session_id", sessionId);
  fd.append("lang", langSel.value);
  fd.append("audio", blob, "voice.webm");
  try {
    const res = await fetch("/api/voice", { method: "POST", body: fd });
    await handleResponse(res);
  } catch (_) {
    setStatus("Network error. Please try again.", true);
  }
}

micBtn.addEventListener("pointerdown", startRecording);
micBtn.addEventListener("pointerup", stopRecording);
micBtn.addEventListener("pointerleave", stopRecording);

addBubble("bot", "Hello! I'm the school's AI receptionist. Ask me about admissions, fees, " +
  "or how to reach us. សួស្តី! អ្នកអាចសួរជាភាសាខ្មែរបាន។");
