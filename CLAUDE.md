# CashCow Studio - working notes for Claude

## What this is
Desktop app (Windows) that turns competitor YouTube channels into finished faceless videos with a
human in the loop at every stage. Plan: `docs/PLAN.md`. Current milestone contract: `docs/M0-CONTRACT.md`.
Research behind the decisions: `docs/research/`.

## Layout
- `backend/cashcow_studio/` Python 3.12 package (FastAPI API, pipeline, providers, CLI `ccs`).
- `frontend/` Vite + React + TypeScript + Tailwind v4 app, built into `frontend/dist`, served by the backend.
- `tests/` pytest. `channels/example-channel.yaml` is the reference channel config.
- Per-user data never lives in the repo: app data in `%LOCALAPPDATA%\CashCowStudio`, shared channel
  data in the synced Google Drive folder, media in `projects/` and `exports/` (git-ignored).

## Commands (Windows, Git Bash)
- Python env: `F:/CashCowStudio/.venv/Scripts/python.exe` (create with `python -m venv .venv`; install with
  `.venv/Scripts/python.exe -m pip install -e ".[dev]"`).
- Tests: `.venv/Scripts/python.exe -m pytest -q`
- Lint: `.venv/Scripts/python.exe -m ruff check backend tests`
- API server: `.venv/Scripts/python.exe -m cashcow_studio.cli serve` (port 8765, 127.0.0.1 only)
- Front end: `cd frontend && npm install && npm run build` (also `npm run dev`, `npm run typecheck`).
  Node is at `C:\Program Files\nodejs` if not on PATH.
- FFmpeg 9 is installed on this machine and on PATH.

## Rules
- Bind servers to 127.0.0.1 only. Never log, print or return raw API keys; mask them.
- No secrets in the repo. Keys go in `<app_data_dir>/.env`; channel files only name the env var.
- Follow `docs/M0-CONTRACT.md` exactly for endpoint paths and JSON shapes; change the contract first if
  something must differ, and say so.
- Pydantic v2 models are the source of truth; mirror them in `frontend/src/types/`.
- Windows paths may contain spaces; always quote. Python's 260-char path limit applies: keep temp
  files in short paths, not deep ones.
- Keep user-facing text plain English, no jargon; the users are video editors, not developers.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
