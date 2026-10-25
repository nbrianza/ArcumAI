# ADR 0001: Transport security posture

Status: accepted (design and owner decisions in issue #38)

## Context

ArcumAI is often deployed on an office LAN. Plain HTTP and WebSocket expose login passwords, session cookies, the add-in API key and full client emails to anyone on the network.

## Decision

- ArcumAI supports TLS natively through uvicorn (`SSL_CERTFILE`, `SSL_KEYFILE`) or behind a TLS reverse proxy (`TLS_TERMINATED_UPSTREAM=true`). No new dependency.
- Without TLS on a non-loopback bind the server starts and logs one WARNING; it does not refuse to start (owner decision, #38).
- A half-configured TLS setup (only one file set, or a path that does not exist) stops the server at startup with a message naming the variable (D1).
- The Outlook add-in validates that a non-loopback server uses a secure connection, with a parsed-host check (`localhost`, `127.x.x.x`, `[::1]`), and relies on the Windows certificate store. Certificate validation is never bypassed. The add-in refuses to connect when the connection would be cleartext to a non-loopback host, that is when the `ServerUrl` does not start with `wss://`, `UseSecureConnection` is false, and the host is not `localhost`, `127.x.x.x` or `[::1]` (#47, #48). It logs one ERROR naming the setting to change and does not retry until Outlook restarts. Other configuration check failures still only log a WARNING.

## Consequences

- Default installs (`HOST=0.0.0.0`, no TLS) log a warning at every start until TLS is configured.
- Certificate provisioning and renewal stay with the administrator; `doc/TLS_SETUP.md` explains the options.
- `TLS_TERMINATED_UPSTREAM` is a declaration, not a check.
- Installations that used `ws://` to a remote server stop connecting until TLS is configured.
