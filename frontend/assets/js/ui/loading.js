let _timer = null;

export function showLoading(subtitle = "Working on it", timeoutMs = 15000) {
  console.log("Showing loading")
  const el = document.getElementById("globalLoading");
  const sub = document.getElementById("globalLoadingSub");
  if (!el) return;

  if (sub) sub.textContent = subtitle;
  el.classList.remove("hidden");

  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => {
    // keep it visible but change messaging
    if (sub) sub.textContent = "Still working… (try refreshing if this hangs)";
  }, timeoutMs);
}

export function hideLoading() {
  const el = document.getElementById("globalLoading");
  if (!el) return;
  el.classList.add("hidden");
  if (_timer) clearTimeout(_timer);
  _timer = null;
}
