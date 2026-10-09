# CashCow Studio front end

The desktop UI: Vite 6 + React 19 + TypeScript, Tailwind CSS v4, react-router 7 (library mode),
TanStack Query 5, react-hook-form + zod, lucide-react icons. It is built into `frontend/dist`,
which the Python backend serves at `http://127.0.0.1:8765/`.

The API shapes it uses are defined in `docs/M0-CONTRACT.md`; `src/types/channel.ts` mirrors
`backend/cashcow_studio/models/channel.py` field for field.

## Commands

Node.js LTS is required (on this machine it is in `C:\Program Files\nodejs`).

```powershell
cd frontend
npm install          # once, and after package.json changes
npm run dev          # Vite dev server on http://127.0.0.1:5173, proxies /api to 127.0.0.1:8765
npm run typecheck    # tsc --noEmit
npm run build        # tsc -b && vite build  ->  frontend/dist
npm run preview      # serve the production build locally
```

For `npm run dev` the backend must be running: `.venv/Scripts/python.exe -m cashcow_studio.cli serve`.

## Layout

```
src/
  api/          typed fetch wrapper (client.ts) and one hooks file per resource
  types/        TypeScript mirror of the Pydantic models and the contract's JSON shapes
  lib/          zod schema for the channel form, theme, formatting, slugify
  layout/       AppShell (sidebar + top bar), DoctorPill, theme toggle
  components/   ui/ primitives, form/ controlled inputs, channels/ setup form tabs,
                settings/ panels, dashboard/ cards
  pages/        one component per route
```

Routes: `/` Dashboard, `/channels`, `/channels/new`, `/channels/:slug`, `/settings`, and the
placeholders `/research` (M1), `/storyboard` (M2), `/editor` (M5), `/review` (M5).

## Notes

- Dark theme is the default (`class="dark"` on `<html>`); the toggle in the top bar stores the
  choice in `localStorage` under `cfs-theme`.
- API keys are typed into password fields, sent once with `PUT /api/settings` and never shown
  again in full; the UI only displays the masked value the backend returns.
- "Use default" on a folder sends `{ "<folder>": null }` to `PUT /api/settings`, which the backend
  treats as "forget the chosen folder and fall back to the default".
