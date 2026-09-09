# authentik OIDC setup

Helix can use [authentik](https://goauthentik.io/) as an OpenID Connect (OIDC) identity provider for browser sign-in.

OIDC is optional. Helix's normal local username/password login remains available, which is useful as a recovery path if the identity provider is unavailable or misconfigured.

## What Helix uses

Helix uses the OAuth 2.0 Authorization Code flow with PKCE.

After authentik completes login, Helix reads the OIDC user information, links the authentik identity to a Helix user, and then creates a normal Helix session. The browser continues to use Helix's existing session cookie after sign-in.

Helix uses the OIDC `sub` claim as the stable external identity. Usernames are not treated as the permanent OIDC identifier.

## 1. Create the authentik application

In the authentik Admin interface:

1. Open **Applications → Applications**.
2. Select **New Application**.
3. Give the application a name such as `Helix`.
4. Use a slug such as `helix`.
5. Choose **OAuth2/OpenID Connect** as the provider type.
6. Configure the provider and save the application.

Using authentik's combined application/provider wizard is the recommended approach for a normal OIDC integration.

## 2. Configure the redirect URI

Add a **Strict** redirect URI for your public Helix address:

```text
https://helix.example.com/auth/oidc/callback
```

Replace `helix.example.com` with the actual hostname used to reach Helix.

The redirect URI in authentik and `HELIX_OIDC_REDIRECT_URI` in Helix must match exactly.

For a local HTTP-only test installation, the same endpoint can be used with your local Helix URL, for example:

```text
http://localhost:10011/auth/oidc/callback
```

For an internet-accessible deployment, use HTTPS.

## 3. Record the authentik provider values

Copy the following values from the authentik OAuth2/OIDC provider:

- Client ID
- Client Secret
- Application slug

With authentik's default per-provider issuer mode, the issuer is:

```text
https://auth.example.com/application/o/<application_slug>/
```

For example:

```text
https://auth.example.com/application/o/helix/
```

authentik serves the discovery document for that provider at:

```text
https://auth.example.com/application/o/helix/.well-known/openid-configuration
```

Helix builds the discovery URL from the configured issuer.

## 4. Configure Helix

Add the following environment variables to Helix:

```env
HELIX_OIDC_ENABLED=true
HELIX_OIDC_DISPLAY_NAME=authentik

HELIX_OIDC_ISSUER=https://auth.example.com/application/o/helix/
HELIX_OIDC_CLIENT_ID=replace-with-client-id
HELIX_OIDC_CLIENT_SECRET=replace-with-client-secret

HELIX_OIDC_REDIRECT_URI=https://helix.example.com/auth/oidc/callback

HELIX_OIDC_SCOPES=openid profile email
HELIX_OIDC_USERNAME_CLAIM=preferred_username
HELIX_OIDC_GROUPS_CLAIM=groups
```

Restart the Helix container after changing environment variables:

```bash
docker compose up -d
```

The Helix login page should now show an authentik single sign-on option below the local login form.

## User creation

By default, Helix can create a local Helix user record the first time a new authentik identity signs in:

```env
HELIX_OIDC_AUTO_CREATE_USERS=true
```

The Helix record is still required because Helix stores application-specific data and permissions against a Helix user.

For safety, OIDC auto-provisioning does **not** claim the initial Helix administrator account by default. Create the first local Helix admin through the normal setup screen before allowing OIDC users to sign in.

If you deliberately want the first successful OIDC login to become the initial Helix administrator, opt in explicitly:

```env
HELIX_OIDC_ALLOW_FIRST_USER_ADMIN=true
```

Only enable this when access to the authentik application is already tightly controlled.

To require users to already exist in Helix before OIDC login, use:

```env
HELIX_OIDC_AUTO_CREATE_USERS=false
```

## Existing local accounts

Helix does **not** automatically link an incoming authentik identity to an existing local account just because the usernames match.

The safer default is:

```env
HELIX_OIDC_AUTO_LINK_BY_USERNAME=false
```

This avoids accidentally linking an external identity to a privileged local account because of a matching username.

If you intentionally want username-based automatic linking, it can be enabled:

```env
HELIX_OIDC_AUTO_LINK_BY_USERNAME=true
```

Only enable this when the authentik usernames and Helix usernames are under the same trusted administrative control.

## Optional admin group synchronization

Helix can check an authentik group claim and use membership in one configured group to determine whether the user should be a Helix administrator.

Example:

```env
HELIX_OIDC_GROUPS_CLAIM=groups
HELIX_OIDC_ADMIN_GROUP=helix-admins
HELIX_OIDC_SYNC_ROLES=true
```

With role synchronization enabled:

- members of `helix-admins` are assigned the Helix admin role
- users who are no longer in that group can be returned to the normal user role on a later OIDC login

Leave role synchronization disabled if you want Helix roles to be managed only inside Helix:

```env
HELIX_OIDC_SYNC_ROLES=false
```


## Environment variable reference

| Variable | Default | What it does |
|---|---|---|
| `HELIX_OIDC_ENABLED` | `false` | Enables or disables OIDC login. When disabled, Helix only shows and uses its normal local authentication flow. |
| `HELIX_OIDC_DISPLAY_NAME` | `authentik` | Controls the provider name shown on the Helix login page, for example `authentik`, `Keycloak`, or another OIDC provider name. |
| `HELIX_OIDC_ISSUER` | empty | The OIDC issuer URL for the provider. For authentik this is typically the application-specific issuer, such as `https://auth.example.com/application/o/helix/`. Helix uses this to discover the provider's authorization, token, user-info, and JWKS endpoints. |
| `HELIX_OIDC_CLIENT_ID` | empty | The OAuth/OIDC client ID assigned to Helix by the provider. |
| `HELIX_OIDC_CLIENT_SECRET` | empty | The client secret assigned to Helix by the provider. Treat this as a secret and keep it server-side. |
| `HELIX_OIDC_REDIRECT_URI` | empty | The exact callback URL the provider redirects the browser to after login. This must match the Strict redirect URI configured in authentik, for example `https://helix.example.com/auth/oidc/callback`. |
| `HELIX_OIDC_SCOPES` | `openid profile email` | Space-separated OIDC scopes requested during login. `openid` is required for OIDC. `profile` and `email` provide common identity claims used for usernames and profile data. |
| `HELIX_OIDC_USERNAME_CLAIM` | `preferred_username` | The claim Helix prefers when choosing the Helix username for a newly provisioned OIDC user. If the claim is unavailable, Helix can fall back to other identity data. |
| `HELIX_OIDC_GROUPS_CLAIM` | `groups` | The claim Helix reads when checking OIDC group membership for optional role synchronization. |
| `HELIX_OIDC_AUTO_CREATE_USERS` | `true` | When enabled, Helix automatically creates a local Helix user record the first time a previously unknown OIDC identity signs in. When disabled, the user must already exist or be linked before OIDC login can succeed. |
| `HELIX_OIDC_ALLOW_FIRST_USER_ADMIN` | `false` | Controls whether the first successful OIDC login is allowed to create the initial Helix administrator account when no Helix users exist. The safer default is `false`; create the first admin locally instead. |
| `HELIX_OIDC_AUTO_LINK_BY_USERNAME` | `false` | Allows Helix to link an OIDC identity to an existing local account when the usernames match. This is disabled by default because username matching alone is weaker than explicit identity linkage. |
| `HELIX_OIDC_ADMIN_GROUP` | empty | Optional OIDC group name that should correspond to the Helix administrator role, for example `helix-admins`. This is used together with `HELIX_OIDC_SYNC_ROLES=true`. |
| `HELIX_OIDC_SYNC_ROLES` | `false` | When enabled, Helix updates a user's Helix role based on membership in `HELIX_OIDC_ADMIN_GROUP` during OIDC login. When disabled, Helix roles remain managed inside Helix. |

### Recommended starting configuration

For a typical authentik deployment where you want OIDC sign-in but want Helix to retain control over roles:

```env
HELIX_OIDC_ENABLED=true
HELIX_OIDC_DISPLAY_NAME=authentik
HELIX_OIDC_ISSUER=https://auth.example.com/application/o/helix/
HELIX_OIDC_CLIENT_ID=replace-with-client-id
HELIX_OIDC_CLIENT_SECRET=replace-with-client-secret
HELIX_OIDC_REDIRECT_URI=https://helix.example.com/auth/oidc/callback

HELIX_OIDC_SCOPES=openid profile email
HELIX_OIDC_USERNAME_CLAIM=preferred_username
HELIX_OIDC_GROUPS_CLAIM=groups

HELIX_OIDC_AUTO_CREATE_USERS=true
HELIX_OIDC_ALLOW_FIRST_USER_ADMIN=false
HELIX_OIDC_AUTO_LINK_BY_USERNAME=false

HELIX_OIDC_ADMIN_GROUP=
HELIX_OIDC_SYNC_ROLES=false
```

If you want authentik to control Helix administrator membership through a group:

```env
HELIX_OIDC_ADMIN_GROUP=helix-admins
HELIX_OIDC_SYNC_ROLES=true
```

## Complete environment reference

```env
HELIX_OIDC_ENABLED=false
HELIX_OIDC_DISPLAY_NAME=authentik
HELIX_OIDC_ISSUER=
HELIX_OIDC_CLIENT_ID=
HELIX_OIDC_CLIENT_SECRET=
HELIX_OIDC_REDIRECT_URI=
HELIX_OIDC_SCOPES=openid profile email
HELIX_OIDC_USERNAME_CLAIM=preferred_username
HELIX_OIDC_GROUPS_CLAIM=groups
HELIX_OIDC_AUTO_CREATE_USERS=true
HELIX_OIDC_ALLOW_FIRST_USER_ADMIN=false
HELIX_OIDC_AUTO_LINK_BY_USERNAME=false
HELIX_OIDC_ADMIN_GROUP=
HELIX_OIDC_SYNC_ROLES=false
```

## Reverse proxies and HTTPS

OIDC involves both browser redirects and server-to-server requests.

Make sure:

- the browser can reach both Helix and authentik
- the Helix container can reach the authentik issuer/discovery endpoints
- your reverse proxy allows `/auth/oidc/login` and `/auth/oidc/callback`
- `HELIX_OIDC_REDIRECT_URI` uses the same public URL that users actually visit
- `HELIX_COOKIE_SECURE=true` is used when Helix is served over HTTPS

If Cloudflare or another proxy sits in front of authentik, it must not block Helix's server-side OIDC discovery, token, or user-info requests.

## Troubleshooting

### The authentik button does not appear

Check:

```env
HELIX_OIDC_ENABLED=true
```

Then restart the container.

Also verify that the OIDC configuration endpoint is reachable from the browser:

```text
/auth/oidc/config
```

### Redirect URI error in authentik

The URI must match exactly.

For example, if Helix is configured with:

```env
HELIX_OIDC_REDIRECT_URI=https://helix.example.com/auth/oidc/callback
```

authentik should contain that exact URI as a Strict redirect URI.

Differences in scheme, hostname, port, path, or trailing characters can cause the authorization request to be rejected.

### Discovery or issuer error

With the default authentik issuer mode, use the application-specific issuer including the application slug:

```text
https://auth.example.com/application/o/helix/
```

Do not point `HELIX_OIDC_ISSUER` directly at the `.well-known/openid-configuration` URL.

### Username already exists

If an authentik user has the same username as an existing local Helix account, Helix will refuse to silently merge them while:

```env
HELIX_OIDC_AUTO_LINK_BY_USERNAME=false
```

This is intentional.

Either explicitly enable username linking if appropriate for your deployment, or use a distinct username.

## Android client

This OIDC integration currently targets the Helix web login.

The native Android client can continue using a local Helix account. Native OIDC login requires a separate mobile authorization flow and is not covered by this setup guide.


## OIDC token validation

Before Helix creates a session, it validates the OIDC ID token using the signing keys published by the provider's JWKS endpoint.

Validation covers the signature, issuer, audience, expiration, issued-at time, subject, authorization-flow nonce, and `azp` rules for multi-audience tokens. Helix also requires the `userinfo` subject to match the subject in the validated ID token.

Unsigned ID tokens and symmetric/HMAC signing algorithms are not accepted.

## Local authentik icon

The authentik icon used on the Helix login page is bundled with the Helix frontend. Opening the login page no longer requires the browser to fetch the icon from `goauthentik.io`.
