import * as backend from "./api/backend.js";

const modeTitle = document.getElementById("modeTitle");
const modeHint = document.getElementById("modeHint");
const uEl = document.getElementById("username");
const pEl = document.getElementById("password");
const statusEl = document.getElementById("status");
const submitBtn = document.getElementById("submitBtn");

let setupMode = false;

function setStatus(t) { statusEl.textContent = t || ""; }

async function init() {
  // If already logged in, go to search
  try {
    await backend.me();
    window.location.href = "index.html";
    return;
  } catch {}

  try {
    const se = await backend.setupEnabled();
    setupMode = !!se.enabled;
  } catch {
    setupMode = false;
  }

  if (setupMode) {
    modeTitle.textContent = "Initial setup (create first admin)";
    modeHint.textContent = "No users exist yet. Create the first admin account (password min 8 chars).";
  } else {
    modeTitle.textContent = "Sign in";
    modeHint.textContent = "Enter your username and password.";
  }
}

async function submit() {
  const u = uEl.value.trim();
  const p = pEl.value;

  if (!u || !p) { setStatus("Enter username and password."); return; }
  setStatus("Working…");

  try {
    if (setupMode) await backend.setup(u, p);
    else await backend.login(u, p);

    window.location.href = "index.html";
  } catch (e) {
    setStatus(e.message);
  }
}

submitBtn.addEventListener("click", submit);
pEl.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

init();
