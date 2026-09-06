(function () {
  function notifyOpener() {
    var params = new URLSearchParams(window.location.search.replace(/^\?/, ''))
    var message = {
      source: 'helix:spotify-oauth',
      status: params.get('status') === 'ok' ? 'ok' : 'error',
      error: params.get('error') || '',
      display_name: params.get('display_name') || '',
    }
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage(message, '*')
      }
    } catch (err) {
      window.opener = null
    }
    try {
      window.close()
    } catch (err) {
      window.opener = null
    }
  }

  // The parent tab may not have its message listener ready at the exact
  // moment Spotify redirects back, so give it a brief moment to attach.
  window.setTimeout(notifyOpener, 300)
})()