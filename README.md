# CashCow Studio

Turns a list of competitor YouTube channels into finished faceless videos, with a human
able to step in at every stage.

```
competitor channels -> research -> title -> script -> storyboard -> voice -> images -> edit -> export
```

Status: **planning / M0**. See [docs/PLAN.md](docs/PLAN.md) for the architecture, roadmap and open questions.

## How a channel is set up

Every channel is created once through the **Channel Setup** form in the app. The form has
these sections, and the app saves them as `channels/<channel-slug>/channel.json` on the
user's PC (the structure is shown in `channels/example-channel.yaml`):

| Section | Contents |
|---------|----------|
| Channel | name, URL, language, niche, formats, export and music folders, owner |
| Competitors | the channels the research stage scans, with priority |
| Frameworks | title, script, scene-prompt, thumbnail and SEO frameworks, uploaded as files or links |
| Voice | voice-clone tool, clone link or voice ID, language, settings |
| Images | image tool, model, style guide, on-image text (popup) rules |
| Thumbnail | competitor thumbnail templates to model, fonts, colours |
| Stage Modes | auto / review / manual for each of the 8 stages, and who reviews |

API keys are entered once in the app's Settings screen and stored in `.env` on that PC,
never inside a channel file.

## Repository layout (planned)

```
docs/            plan, architecture notes, decisions
channels/        example channel config (real channels are saved by the app)
backend/         Python pipeline, providers, API (FastAPI)
frontend/        storyboard and timeline editor (TypeScript)
projects/        per-video work folders (ignored by git, lives on each user's PC)
```

## Working agreement

- Development happens on GitHub through pull requests, one milestone per PR.
- Each user runs the app on their own Windows PC; all media stays on their local drive.
- Secrets go in `.env` (see `.env.example`). The repo never contains a key.
