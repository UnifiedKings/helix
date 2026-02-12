let lastFetchTime = 0;
let fetchChain = Promise.resolve();

/**
 * A simple throttle queue: ensures at least throttleMs between fetches.
 * Useful for MusicBrainz rate limiting.
 */
export function createThrottledFetch(throttleMs = 1100) {
  return function throttledFetch(url, options) {
    fetchChain = fetchChain.then(async () => {
      const now = Date.now();
      const wait = Math.max(0, (lastFetchTime + throttleMs) - now);
      if (wait) await new Promise(r => setTimeout(r, wait));
      lastFetchTime = Date.now();
      return fetch(url, options);
    });
    return fetchChain;
  };
}
