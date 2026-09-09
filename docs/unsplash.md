# Unsplash login background setup

Helix can optionally use the [Unsplash API](https://unsplash.com/developers) to provide randomized music-themed backgrounds on the login screen.

The integration is optional. If it is disabled, not configured, or Unsplash cannot be reached, Helix falls back to its normal dark login background.

## How it works

Helix does not request a new Unsplash search every time somebody opens the login page.

Instead:

1. The Helix backend searches Unsplash for relevant landscape images.
2. Unsplash returns up to 24 search results.
3. Helix keeps usable results and randomly chooses from the highest-ranked relevant results.
4. The search result pool is cached server-side.
5. The browser loads the selected image directly from the Unsplash image URL.

By default, Helix chooses from up to the top 12 usable results and refreshes the search pool every 15 minutes.

This means repeated login-page visits normally reuse the cached result pool rather than consuming an Unsplash API request for every page load.

## 1. Create an Unsplash developer application

1. Create or sign in to an Unsplash account.
2. Open the [Unsplash Developers](https://unsplash.com/developers) page.
3. Create a new application.
4. Accept the Unsplash API terms.
5. Copy the application's **Access Key**.

Helix only needs the Access Key for this integration.

Do **not** put the Unsplash Secret Key into Helix. The login-background feature only uses public read-only API requests.

New Unsplash applications normally begin in demo mode with a limited API request allowance. Because Helix caches search results, the default configuration is intended to stay comfortably below that limit for normal self-hosted use.

## 2. Configure Helix

Add:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_ENABLED=true
HELIX_UNSPLASH_ACCESS_KEY=replace-with-your-access-key
```

The default search query is:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=vinyl record player turntable music
```

The default cache lifetime is 900 seconds:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS=900
```

Restart Helix after changing the environment:

```bash
docker compose up -d
```

Reload the login page. A relevant landscape image should be selected as the background.

## Search query

The query can be customized to change the visual style of the login screen.

Default:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=vinyl record player turntable music
```

Other examples:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=dark music studio
```

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=concert stage lights
```

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=vinyl turntable dark
```

Helix requests landscape images and asks Unsplash to order results by relevance.

A narrower query generally produces a more consistent login-page identity. A broader query produces more visual variety.

## Cache duration

The cache controls how often Helix asks Unsplash for a fresh candidate pool.

Default:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS=900
```

That is 15 minutes.

For a small personal installation, increasing it to one hour is also reasonable:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS=3600
```

The random image can still change between page loads while the cache is active; Helix is choosing randomly from the cached pool.

The cache primarily controls how often the candidate pool itself is refreshed from the Unsplash API.

## API requests versus image loading

Unsplash API limits apply to API requests, not to every image file loaded by visitors.

Helix uses an API request to refresh the search result pool. The selected image is then hotlinked from the image URL returned by Unsplash.

With the default 15-minute cache, a continuously used Helix instance would normally make at most roughly four search requests per hour from this feature, rather than one request per login-page view.

## Attribution

The Helix login page displays attribution for the selected photograph, including:

- the photographer's name
- a link to the photographer on Unsplash
- a link to Unsplash

Helix also uses the image URLs returned by the Unsplash API rather than downloading and re-hosting the photographs.

These behaviors are important for following the Unsplash API guidelines.

Do not remove the attribution or change the implementation to silently proxy/store Unsplash photographs without first reviewing the current Unsplash API terms and guidelines.

## Complete environment reference

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_ENABLED=false
HELIX_UNSPLASH_ACCESS_KEY=
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=vinyl record player turntable music
HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS=900
```

## Privacy and network behavior

When the feature is enabled:

- the Helix server makes HTTPS requests to `api.unsplash.com` when refreshing the search pool
- the visitor's browser loads the chosen image from Unsplash's image CDN
- clicking the attribution links opens Unsplash

If you want a login page that makes no external image requests, leave the integration disabled and use the built-in fallback background instead.

## Troubleshooting

### The normal dark background still appears

Check that both of these are set:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_ENABLED=true
HELIX_UNSPLASH_ACCESS_KEY=your-access-key
```

Then restart the Helix container.

Also make sure the container can make outbound HTTPS requests to the Unsplash API.

### Backgrounds are unrelated to music

Use a narrower search query, for example:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=vinyl turntable
```

or:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_QUERY=record player music
```

### API requests are being used too quickly

Increase the cache duration:

```env
HELIX_LOGIN_BACKGROUND_UNSPLASH_CACHE_SECONDS=3600
```

The browser can still receive different randomly selected images from the existing cached result pool.

### Unsplash is unavailable

The login page should continue to work. Helix falls back to its normal background if it cannot retrieve a usable Unsplash result.

Unsplash is visual decoration only and is not required for authentication.
