# OIDC provider

The MCP server works with any OpenID Connect provider that offers the
authorization code flow. MCP clients register with the server itself (dynamic
registration or a client metadata document), so your provider needs one
ordinary confidential client and no dynamic registration.

## The client

Create one confidential web client (authorization code, with a client secret):

| Setting | Value |
|---|---|
| Redirect URIs | `https://<MCP_HOST>/auth/callback`, `https://<MCP_HOST>/elevate/callback`, and `https://<WEB_HOST>/oauth2/callback` if you run Obsidian in a browser |
| Grant types | authorization code, refresh token. Nothing else. |
| Scopes | `openid` (plus `email` for oauth2-proxy) |

Then in `.env`: `OIDC_ISSUER` (the issuer URL, exactly as in the provider's
`/.well-known/openid-configuration`), `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`.

**`AGENT_NOTES_ALLOWED_SUBS`** is your `sub` at the provider: the only account
the server serves. Anyone else who can log in at your provider gets a 403 (and
an audit line, `event=auth result=denied`). Find it in the provider's user page,
or log in once and read the refusal line.

**`OIDC_AUDIENCE`**: only for providers that issue JWT access tokens for an API
you define (Auth0). Leave it empty otherwise: the server then verifies the ID
token instead.

## Proving a second factor for elevation

The approval page forces a fresh login and then needs proof, in the ID token,
that a second factor was used in the last 5 minutes. Providers say it
differently, so it's configuration:

| Variable | Meaning |
|---|---|
| `AGENT_NOTES_STRONG_AMR` | `amr` values that count (RFC 8176). Default `mfa,phr,phrh`. |
| `AGENT_NOTES_STRONG_ACR` | `acr` values that count, for providers that express step-up as a level. |
| `AGENT_NOTES_ACR_VALUES` | sent as `acr_values` on the approval login, to ask the provider for that level. |
| `AGENT_NOTES_METHODS_CLAIM` | a custom claim listing the login's methods with timestamps (Auth0, below). |

Nothing that matches means every approval is refused. When a login is refused,
the page shows you (and only you: it checks your `sub` first) what the provider
sent: `amr`, `acr`, the methods claim and the login's age. Start there.

### Auth0

Auth0 skips the MFA challenge after a passkey login (fair: a passkey is already
two factors) and then puts nothing about it in the token's `amr`. Its own login
log knows (`performed_amr: ["phr"]`), but the token doesn't carry it. A
post-login Action fixes that, and also forces MFA when no passkey was used:

```js
exports.onExecutePostLogin = async (event, api) => {
  if (event.client.client_id !== event.secrets.AGENT_NOTES_CLIENT_ID) return;
  const methods = (event.authentication?.methods ?? [])
    .map((m) => ({ name: m.name, timestamp: m.timestamp }));
  api.idToken.setCustomClaim('https://notes.example.com/methods', methods);
  if (!methods.some((m) => m.name === 'passkey')) {
    api.multifactor.enable('any', { allowRememberBrowser: false });
  }
};
```

Then `AGENT_NOTES_METHODS_CLAIM=https://notes.example.com/methods` (any URL
you own; Auth0 wants namespaced custom claims). A refusal that says
`methods claim: absent` means the Action didn't run for this client.

### Other providers

Keycloak, Authentik, Zitadel, Pocket ID and others all work for the login. For
elevation, check what the approval page shows after one refused attempt, then
set `AGENT_NOTES_STRONG_AMR` or `AGENT_NOTES_STRONG_ACR` to what your provider
sends for a second factor. Providers that model step-up as levels (Keycloak's
authentication flows with levels, for example) usually want
`AGENT_NOTES_ACR_VALUES` too.
