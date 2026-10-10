# M5A contract: login, team accounts, and the Automation / Editing / Master Sheet interface

Builds on M0-M4 (`docs/M0-CONTRACT.md` ... `docs/M3-M4-CONTRACT.md`). This wave changes how the
app is organised for the team, mirroring the structure of the owner's earlier 9 Sigma apps:
a login page first, then four areas: **Automation**, **Editing**, **Master Sheet**, **Settings**.
Change this file first, code second.

## 0. Rules for this wave

1. **Login first.** Nothing in the app is reachable without signing in. The first run of a fresh
   install shows "Create the first admin account"; there is no default password.
2. **Accounts are shared by the team, sessions are per laptop.** The user roster lives in the
   shared Google Drive folder (`<shared_dir>/team/users.json`) so every laptop knows the same
   accounts; a copy is cached in `<app_data_dir>/users.cache.json` for offline starts. Sessions,
   "keep me signed in" and "remember my login details" are per laptop, like 9 Sigma Automation.
3. **Passwords are never stored**, only PBKDF2-HMAC-SHA256 hashes with a per-user salt
   (same scheme as `sceneforge/auth.py`). A new or reset password is shown once to the admin.
4. **Who did what is recorded.** Every approve / redo / skip / export / settings change is written
   to an audit log with the signed-in user, the laptop name and the time. Stage approvals use the
   session user, never a name typed by the client.
5. **Roles:** `admin` (everything, including users, channels, settings, keys), `editor` (run the
   pipeline, review, edit, export, read channels), `viewer` (read only). Default for new users:
   `editor`. An account may be locked to one laptop (`machine` = Windows computer name).
6. **No feature regressions.** Every screen that exists today stays reachable under the new areas.

## 1. Backend: accounts, sessions, audit (`backend/cashcow_studio/auth/`, `api/auth.py`, `api/users.py`)

### Storage
- `auth/users.py`: `User {username (lowercase, 3-32 chars, [a-z0-9._-]), display_name, role, hash, salt, iterations, created_at, created_by, disabled: bool, machine: str|null, must_change_password: bool, last_login_at}`; `UserStore` reads/writes `<shared_dir>/team/users.json` atomically with a file lock and mirrors to `<app_data_dir>/users.cache.json`; if the shared file is unreadable at start-up, the cache is used read-only and the UI shows a notice. `add()` returns the generated password once (`generate_password()` like SceneForge: strong but typeable). `verify(username, password)` is constant-time. `change_password`, `reset_password` (admin), `set_role`, `set_disabled`, `set_machine`.
- `auth/sessions.py`: signed session cookie `ccs_session` (HttpOnly, SameSite=Strict, Secure off because the server is loopback only), payload `{user, machine, issued_at, expires_at, remember}` signed with HMAC using a per-laptop secret stored at `<app_data_dir>/session.secret` (created with 32 random bytes, file permissions as restrictive as Windows allows). Lifetime 12 hours, or 30 days with "keep me signed in". `<app_data_dir>/saved-login.json` holds only the remembered username when "remember my login details" is on (never the password).
- `auth/audit.py`: SQLite table `audit (id, at, user, machine, action, project_id, channel_slug, detail json)` via `storage/db.py`; `record(action, user, ...)`. Actions: `login`, `logout`, `stage.approve`, `stage.redo`, `stage.skip`, `stage.mode`, `project.create`, `project.archive`, `export.done`, `settings.update`, `keys.update`, `channel.create|update|archive`, `user.create|update|reset|disable`.

### Middleware
- `auth/middleware.py`: pure-ASGI (same style as `origin_guard.py`): every `/api/*` request and the `/api/ws` WebSocket requires a valid session except `POST /api/auth/login`, `POST /api/auth/first-run`, `GET /api/auth/status`, `GET /api/system/info`, `/api/docs`, `/api/openapi.json`. Unauthenticated -> `401 {detail: "Please sign in."}`. Role checks are per endpoint (`require_role("admin")` dependency). The SPA shell (`/` and static files) stays public; the React app redirects to `/login` when `/api/auth/me` returns 401.
- The engine's approve/redo/skip receive `by` from the session (the `ReviewAction.by` body field is ignored and removed from the front end).

### API (prefix `/api`)
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/auth/status` | | `{needs_first_run: bool, signed_in: bool, user?: PublicUser, machine: str, roster_source: "shared"\|"cache"\|"none"}` | public |
| POST | `/auth/first-run` | `{username, display_name, password}` | `PublicUser` + session cookie | only while no users exist |
| POST | `/auth/login` | `{username, password, remember: bool, remember_username: bool}` | `PublicUser` + cookie | public; 401 on bad credentials (same message for unknown user), 423 when disabled, 403 when locked to another machine |
| POST | `/auth/logout` | | `204` | signed in |
| GET | `/auth/me` | | `PublicUser {username, display_name, role, machine, must_change_password}` | signed in |
| POST | `/auth/change-password` | `{current_password, new_password}` | `204` | signed in |
| GET | `/users` | | `PublicUser[]` with `disabled, machine, last_login_at, created_at` | admin |
| POST | `/users` | `{username, display_name, role, machine?}` | `{user: PublicUser, password: string}` (shown once) | admin |
| PUT | `/users/{username}` | `{display_name?, role?, machine?, disabled?}` | `PublicUser` | admin; an admin cannot disable or demote the last admin |
| POST | `/users/{username}/reset-password` | | `{password}` (shown once; sets must_change_password) | admin |
| GET | `/audit` | `?limit=200&user=&action=&project_id=` | `AuditRow[]` | admin (editors see their own rows) |

Password rules: at least 10 characters; the first-run and change-password screens show the rule.
Rate limiting: 5 failed logins per username per 10 minutes per laptop -> 429 with a plain message.

## 2. Backend: libraries and production log (`api/library.py`, `library/`)

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/api/library/frameworks` | `[{channel_slug, channel_name, type, name, path, version, formats, size_bytes, updated_at, extracted: bool}]` | aggregated from every channel; `extracted` = `<name>.extracted.txt` exists |
| GET | `/api/library/voices` | `[{channel_slug, channel_name, tool, clone_ref, name, language, consent: {present: bool, owner_name?, consented_at?}, sample_path, sample_exists}]` | from channel configs |
| GET | `/api/library/music` | `[{folder, file, duration_s?, size_bytes, license_ok: bool, license_file?, used_by_channels: [slug]}]` | scans every channel `music_folder` (dedup by folder); duration via ffprobe cached in SQLite `music_cache` |
| GET | `/api/production-log` | `[{at, user, machine, channel_slug, title, project_id, format, language, presets: [str], duration_s, size_bytes, export_folder}]` | built from `08_export/export.json` of every project plus the `export.done` audit rows; newest first; `?channel_slug=&since=` |
| GET | `/api/production-log.csv` | CSV of the same | for the team sheet; a Google Sheet sync arrives in M6 |

`export.done` is recorded by the export stage (add the audit call there) with user, machine, presets, duration and size.

## 3. Front end: login and the four areas

### Login (`pages/auth/LoginPage.tsx`, `pages/auth/FirstRunPage.tsx`)
- `/login`: product mark, username, password, "Keep me signed in on this laptop", "Remember my login details" (username only), Sign in; errors in plain English; disabled/locked messages. If `needs_first_run`, `/first-run` instead: create the first admin (username, display name, password with the rule, confirm).
- After sign-in: redirect to the page the user wanted, else `/automation`.
- Top bar: user chip (display name, role) with Change password and Sign out; machine name shown in the chip tooltip.
- `must_change_password` forces the Change password dialog before anything else.
- Route guard: every route except `/login` and `/first-run` requires `/api/auth/me`; 401 anywhere (including from the WebSocket) returns to `/login` with a "signed out" notice.

### Navigation (sidebar groups, in this order)
1. **Automation** (`/automation`): Dashboard (`/automation`), New video (`/automation/research`, the Research page), Projects (`/automation/projects`, `/automation/projects/:id`), Review queue (`/automation/review`), Batch (`/automation/batch`, a planned-for-M6 page that already lists channels with "videos per week" and explains batch runs).
2. **Editing** (`/editing`): Edit room (`/editing`, `/editing/:projectId`): project picker on the left (projects at edit, export or done), the EditReview and ExportReview panels on the right for the chosen project, plus a "Timeline editor arrives in M5" strip; Renders (`/editing/renders`): every rendered file across projects with preset, size, duration, Open folder.
3. **Master Sheet** (`/master`): Channels (`/master/channels`, `/master/channels/new`, `/master/channels/:slug` = the Channel Setup form), Frameworks (`/master/frameworks`), Voices (`/master/voices`), Music (`/master/music`), Production log (`/master/production-log`, with Download CSV).
4. **Settings** (`/settings`): existing cards (paths, keys, models, render, doctor) plus Users & roles (`/settings/users`, admin only: table, Add user dialog that shows the one-time password with a copy button, Reset password, Disable, Lock to laptop, role select) and Activity (`/settings/activity`: audit rows).
- Old paths (`/channels`, `/research`, `/projects`, `/review`, `/storyboard`, `/editor`) redirect to the new ones.
- Role gating in the UI: viewers see no Approve/Redo/Run buttons (disabled with a tooltip); editors see no Settings → Users and cannot create or edit channels (the form is read-only for them); admins see everything. The server enforces the same.
- Keep the design system; the sidebar shows group headers; the current group is highlighted; the top bar title shows "Area · Page".

### Dashboard additions
- Signed-in user greeting, roster source notice when running from the cache, counts per area (projects in progress, awaiting review, renders this week, exports this week), and "Recent activity" (last 10 audit rows visible to the user).

## 4. Tests

Backend: user store (hashing, generate_password, last-admin protection, machine lock, disabled), sessions (sign/verify/expiry/tamper), middleware (401 paths, public paths, WebSocket refusal), login rate limit, first-run only when empty, approvals stamped from session, audit rows for each action, library endpoints with a temp channel set, production log from export.json. Front end: typecheck and build; the e2e API test signs in first.

## 5. Settings and doctor additions
- `GET /api/system/info` adds `machine` (computer name) and `auth_required: true`.
- Doctor: `team_roster` check (shared users.json readable; warns when running from cache).
