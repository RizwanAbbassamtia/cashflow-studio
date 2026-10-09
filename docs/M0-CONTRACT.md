# M0 contract: backend API, storage and front-end shell

This is the agreement between the backend and the front end for milestone M0. Both sides are
built against this file. Change it first, code second.

## Processes and ports

- Backend: FastAPI app, `uvicorn`, bound to `127.0.0.1:8765` only. Never `0.0.0.0`.
- Front end: Vite + React + TypeScript, built to `frontend/dist`. The backend serves `frontend/dist`
  at `/` when it exists (SPA fallback to `index.html` for unknown non-API paths).
- Dev: `npm run dev` in `frontend/` proxies `/api` to `http://127.0.0.1:8765`.
- CLI (`cfs`, defined in `backend/cashflow_studio/cli.py`):
  - `cfs serve [--port 8765] [--reload]` runs the API server.
  - `cfs open` starts the server in a background thread and opens a native pywebview window
    titled "Cashflow Studio" (WebView2 on Windows), size 1440x900, at `http://127.0.0.1:8765`.
  - `cfs doctor` prints the doctor checks as a table and exits 1 if any check fails.
- Package: `cashflow_studio` under `backend/` (src layout). Entry: `python -m cashflow_studio` = `cfs open`.

## Folders

| Name | Default | Purpose |
|------|---------|---------|
| `app_data_dir` | `%LOCALAPPDATA%\CashflowStudio` | per-user settings, `.env`, `cache/`, `db.sqlite`, logs |
| `shared_dir` | unset -> falls back to `<app_data_dir>\shared` | the synced Google Drive folder: `channels/<slug>/channel.json`, `channels/<slug>/frameworks/`, `assets/` |
| `projects_dir` | `%USERPROFILE%\Videos\CashflowStudio\projects` | per-video work folders (local only) |
| `exports_dir` | `%USERPROFILE%\Videos\CashflowStudio\exports` | finished videos (local only), overridable per channel |

Settings live in `<app_data_dir>\settings.json`. API keys live in `<app_data_dir>\.env` as
`NAME=value` lines (0600-style: the file is only for this Windows user; never copied to the
shared dir). The backend loads `.env` at start and after every settings update.

## Data model

`backend/cashflow_studio/models/channel.py` is the source of truth (Pydantic v2). The front end
mirrors it in `frontend/src/types/channel.ts`. Example instance: `channels/example-channel.yaml`
(same keys, YAML form).

Top level: `slug, channel{...}, competitors[], frameworks[], voice{}, images{}, thumbnail{}, stage_modes{}, reviewer, created_at, updated_at, schema_version`.

## HTTP API (all JSON, prefix `/api`)

| Method | Path | Request | Response | Notes |
|--------|------|---------|----------|-------|
| GET | `/api/system/info` | - | `{version, platform, python, app_data_dir, shared_dir, shared_dir_is_default, projects_dir, exports_dir}` | |
| GET | `/api/doctor` | - | `{ok: bool, checks: [{id, name, status: "ok"\|"warn"\|"fail", detail, fix_hint}]}` | see checks below |
| GET | `/api/settings` | - | `{shared_dir, projects_dir, exports_dir, shared_dir_is_default, keys: {NAME: {set: bool, masked: string}}}` | `masked` = first 3 + "..." + last 4 chars, or "" |
| PUT | `/api/settings` | `{shared_dir?, projects_dir?, exports_dir?, keys?: {NAME: string\|null}}` | same as GET | `null` (or blank) for a path means "back to the default"; `null` deletes a key; raw values are never returned; key names must match `^[A-Z][A-Z0-9_]*$` |
| GET | `/api/channels` | - | `ChannelSummary[]` sorted by name | |
| POST | `/api/channels` | `Channel` without `slug` (or with) | `201 Channel` | slug derived from `channel.name` with python-slugify if missing; `409` if the slug exists |
| GET | `/api/channels/{slug}` | - | `Channel` | `404` if missing |
| PUT | `/api/channels/{slug}` | `Channel` | `Channel` | slug in body must match path; `updated_at` set by server |
| DELETE | `/api/channels/{slug}` | - | `204` | moves the folder to `<shared_dir>/channels/_archived/<slug>-<timestamp>`; never hard-deletes |
| POST | `/api/channels/{slug}/frameworks/upload` | multipart file + `type` field | `Framework` | saves to `channels/<slug>/frameworks/<safe-name>`; returns the framework entry with its `path` (relative to shared_dir) |
| GET | `/api/channels/validate-url?url=` | - | `{ok, kind: "channel"\|"video"\|"unknown", normalized}` | syntactic check only in M0, no network |

Errors: FastAPI default `{detail: ...}`; validation errors `422` with Pydantic details.

Known key names (settings page shows these as rows, others can be added):
`ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, FAL_KEY, REPLICATE_API_TOKEN, IDEOGRAM_API_KEY, MINIMAX_API_KEY, MINIMAX_GROUP_ID, CARTESIA_API_KEY, INWORLD_API_KEY, FISH_AUDIO_API_KEY, AZURE_SPEECH_KEY, AZURE_SPEECH_REGION`.

## Doctor checks (ids)

`python_version` (>=3.12), `ffmpeg` (on PATH or `FFMPEG_PATH`; capture version), `ffprobe`,
`deno` (warn if missing: needed by yt-dlp downloads), `yt_dlp` (python module importable; warn),
`webview2` (Windows registry key `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` or HKCU equivalent; warn),
`shared_dir` (exists and writable; warn if using the default fallback), `projects_dir` (writable),
`exports_dir` (writable), `anthropic_key` (set; warn), `voice_key` and `image_key` (warn if no
known voice/image key is set), `disk_space` (projects_dir drive has >= 20 GB free; warn).

A check never raises; failures become `status: "fail"` with the exception text in `detail`.

## Front end

Stack: Vite 6, React 19, TypeScript, Tailwind CSS v4, react-router v7, @tanstack/react-query,
react-hook-form + zod, lucide-react icons. Dark theme by default with a light toggle. Look and
feel: a modern SaaS analytics dashboard (left sidebar, top bar, cards, tables), accent colour
`#FFC000` on deep navy `#0B1220`. Inter font via Google Fonts (fallback system-ui).

Routes:
- `/` Dashboard: cards (channels count, videos in progress placeholder 0, exports placeholder 0, doctor status), "Get started" checklist (configure settings -> create channel -> run research).
- `/channels` list with search, status badge, language, competitors count, "New channel".
- `/channels/new` and `/channels/:slug` Channel Setup form with sections as tabs: Channel, Competitors, Frameworks, Voice, Images, Thumbnail, Stage Modes. Save button (PUT/POST), unsaved-changes guard, validation messages from zod mirroring the Pydantic rules. Competitors and frameworks are editable tables (add/remove rows). Framework upload uses the upload endpoint.
- `/settings` Paths (three folders, with "Use default" buttons), API keys (masked, set/replace/clear), Doctor panel (run, list checks with status colours and fix hints).
- `/research`, `/storyboard`, `/editor`, `/review`: placeholder pages stating the milestone they arrive in.

API client in `frontend/src/api/client.ts` (fetch wrapper, typed), hooks per resource.

## Tests

- `tests/test_channels_api.py`: create, list, get, update, archive, slug conflict, validation error; uses a temp `shared_dir` via env `CFS_APP_DATA_DIR` and `CFS_SHARED_DIR`.
- `tests/test_settings_api.py`: keys are masked, null deletes, invalid names rejected, `.env` written.
- `tests/test_doctor.py`: each check returns a result even when the tool is missing (monkeypatch PATH).
- `tests/test_channel_model.py`: example YAML loads into `Channel`.
- Front end: `npm run build` must succeed; `npm run typecheck` (tsc --noEmit).

## Environment variables (backend)

`CFS_APP_DATA_DIR`, `CFS_SHARED_DIR`, `CFS_PROJECTS_DIR`, `CFS_EXPORTS_DIR`, `CFS_PORT`,
`FFMPEG_PATH`, `FFPROBE_PATH`. Prefix `CFS_` is read by pydantic-settings; the others are plain.
