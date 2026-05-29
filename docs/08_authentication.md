# 08 · Authentication (v0.1.2)

This document describes the **administrator login** introduced in v0.1.2 and
the security trade-offs it makes. It deliberately keeps the model small: a
single admin account, cookie sessions, and login-gating of the whole UI/API.

## Goals

- Stop anonymous LAN users from operating servers through the web UI.
- Gate **all** change operations (start/stop/restart, register, save
  `server.properties`, create backup) behind a login.
- Stay within the v0.1 hard rules: no root, no sudo, no new privileged surface.

## Non-goals (deferred to later versions)

- Multiple users, roles, or a user-management UI.
- Self-service signup / password reset / change-password screens.
- HTTPS / TLS termination (still out of scope — see "Transport security").
- Full CSRF token framework (see "CSRF" below).

## Data model

A new `admins` table (`app/models.py :: Admin`):

| column          | type        | notes                                  |
| --------------- | ----------- | -------------------------------------- |
| `id`            | integer PK  |                                        |
| `username`      | string(120) | unique, indexed                        |
| `password_hash` | string(255) | **bcrypt** hash, never the plaintext   |
| `created_at`    | datetime    | UTC                                    |
| `updated_at`    | datetime    | UTC, `onupdate`                        |

Passwords are hashed with **passlib** using the **bcrypt** scheme
(`app/auth.py :: hash_password` / `verify_password`). The plaintext password is
never written to disk or logged.

> Dependency note: `bcrypt` is pinned to `4.0.1` because `passlib==1.7.4` is
> incompatible with `bcrypt>=4.1` (the backend version probe and the 72-byte
> truncation behaviour changed). Keep this pin until passlib is upgraded.

## Session management

- Uses Starlette's `SessionMiddleware` (signed cookie sessions), wired in
  `app/main.py`.
- The signing key comes from `config.SECRET_KEY`, which reads the
  `MC_APPLIANCE_SECRET_KEY` environment variable and falls back to a
  **development-only** default. The fallback is insecure on purpose and must be
  overridden in any real deployment (see README "Security notes").
- On successful login the session stores `admin_id` and `admin_username`
  (`app/auth.py :: login_session`). Logout pops both keys.
- Cookie flags: `same_site="lax"` (CSRF mitigation, below) and
  `https_only=False` (no HTTPS yet — see "Transport security").

## Enforcement model

A single `AuthMiddleware` (`app/auth.py`) gates every request:

```
request
  └─ SessionMiddleware      (outermost: populates request.session)
       └─ AuthMiddleware    (reads session; allow / redirect / 401)
            └─ route
```

Middleware ordering matters: `SessionMiddleware` must run **before**
`AuthMiddleware`, so in `main.py` `AuthMiddleware` is added first (inner) and
`SessionMiddleware` second (outer). See the comment there.

Decision table for an **unauthenticated** request:

| Path                         | Behaviour                                |
| ---------------------------- | ---------------------------------------- |
| `/login`, `/logout`          | allowed (public)                         |
| `/static/*`                  | allowed (public)                         |
| `/api/*`                     | **401 JSON** `{"detail": "..."}`         |
| everything else (HTML/forms) | **303 redirect** to `/login?next=<path>` |

Authenticated requests pass straight through. Because enforcement is global,
individual routers were left unchanged — keeping routers thin per project
convention. The post-login `next` target is sanitised
(`routers/auth.py :: _safe_next`) to same-site absolute paths only, preventing
open-redirects.

## Initial admin creation

There is no signup UI. The first admin is created from the CLI:

```bash
python scripts/create_admin.py
```

It prompts for a username and password (twice, hidden), enforces a minimum
length, refuses to overwrite an existing username, and stores the bcrypt hash.
It does not require root.

## CSRF

v0.1.2 ships a **lightweight** mitigation only: the session cookie uses
`SameSite=Lax`, so a cross-site `<form>`/`fetch` POST from a malicious page does
not carry the session cookie and therefore cannot trigger an authenticated
change operation. Combined with the LAN-only deployment guidance this is
considered acceptable for v0.1.2.

**Future direction:** add per-session CSRF tokens (synchroniser-token pattern)
embedded in every state-changing form and validated server-side, so protection
no longer depends solely on cookie `SameSite` behaviour. This becomes more
important once the app is reachable over HTTPS / the public internet.

## Transport security

HTTPS is **not** implemented in v0.1.2. The login form and session cookie
travel in cleartext, so credentials and the session can be sniffed on an
untrusted network. Run only on a trusted LAN or behind a VPN/reverse proxy that
terminates TLS. When HTTPS lands, set `https_only=True` on the session cookie.

## Future work

- HTTPS (then `Secure` cookies + HSTS).
- CSRF tokens on all mutating forms.
- Multi-user accounts, roles/permissions, and a management UI.
- Change-password / reset flows; optional account lockout / rate limiting on
  failed logins.
