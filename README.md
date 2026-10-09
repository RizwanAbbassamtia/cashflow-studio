# Cashflow Studio

Turns a list of competitor YouTube channels into finished faceless videos, with a human
able to step in at every stage.

```
competitor channels -> research -> title -> script -> storyboard -> voice -> images -> edit -> export
```

Status: **planning / M0**. See [docs/PLAN.md](docs/PLAN.md) for the architecture, roadmap and open questions.

## How a channel is set up

Every channel has one **master sheet**: a copy of
[templates/channel-master-sheet.xlsx](templates/channel-master-sheet.xlsx) saved as
`channels/<channel-name>.xlsx`. It holds everything needed to start work on that channel:

| Tab | Contents |
|-----|----------|
| Channel | name, URL, language, niche, formats, export and music folders, owner |
| Competitors | the channels the research stage scans, with priority |
| Frameworks | title, script, scene-prompt, thumbnail and SEO frameworks (file paths or links) |
| Voice | voice-clone tool, clone link or voice ID, language, settings |
| Images | image tool, model, style guide, on-image text (popup) rules |
| Thumbnail | competitor thumbnail templates to model, fonts, colours |
| Stage Modes | auto / review / manual for each of the 8 stages, and who reviews |
| Security | reminder: API keys live in `.env`, never in the sheet |

Yellow cells are for people, grey cells are filled by the app. `channels/example-channel.yaml`
shows the same data in the form the app reads after import.

## Repository layout (planned)

```
docs/            plan, architecture notes, decisions
templates/       channel master sheet template
channels/        one master sheet per channel (xlsx) + imported yaml
backend/         Python pipeline, providers, API (FastAPI)
frontend/        storyboard and timeline editor (TypeScript)
projects/        per-video work folders (ignored by git, lives on each user's PC)
```

## Working agreement

- Development happens on GitHub through pull requests, one milestone per PR.
- Each user runs the app on their own Windows PC; all media stays on their local drive.
- Secrets go in `.env` (see `.env.example`). The repo never contains a key.
