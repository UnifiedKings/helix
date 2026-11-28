/* Helix is online-first. Never intercept audio, API calls, or authenticated assets. */
self.addEventListener('install', () => {
  // Do not force activation over an existing playback session.
})

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim())
})

self.addEventListener('fetch', event => {
  const request = event.request
  const url = new URL(request.url)
  if (request.method !== 'GET' || request.mode !== 'navigate' || url.origin !== self.location.origin) return
  if (request.headers.has('Range')) return
  if (/^\/(api|auth|setup|health|admin|settings)(\/|$)/.test(url.pathname)) return
  event.respondWith(fetch(request).catch(() => new Response(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Helix is offline</title></head><body><main><h1>Helix is unavailable</h1><p>Check your connection to your Helix server and try again.</p><p><a href="/">Open Helix</a></p></main></body></html>`, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
  })))
})
