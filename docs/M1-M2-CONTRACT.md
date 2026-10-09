# M1 + M2 contract: research, title, pipeline engine, script, storyboard, provider interfaces

Builds on `docs/M0-CONTRACT.md` (app shell, channels, settings, doctor). Both the backend and the
front end are built against this file. Change it first, code second.

## 0. Product rules that drive this milestone

1. **Many competitors, one pick.** A channel's competitor list holds any number of channel URLs
   (`Channel.competitors`, already in the model). The research stage scans **all** of them and
   ranks their videos together. The AI pick is **one video**, and production starts from that one.
   A person can override the pick, choose another candidate, or type their own topic.
2. **Every stage is gated.** Each stage reads the channel's `stage_modes` (`auto`, `review`,
   `manual`). `review` pauses the project until a person approves; `manual` pauses until a person
   provides the stage's output file; `auto` continues.
3. **Files are the truth.** Every stage writes its outputs into the project folder before it is
   marked done. The database is an index, never the only copy.
4. **No key ever leaves the laptop.** LLM and provider calls use keys from `<app_data_dir>/.env`.
5. **Mock everything for tests.** `CFS_LLM_PROVIDER=mock`, `CFS_RESEARCH_PROVIDER=mock`,
   `CFS_IMAGE_PROVIDER=mock`, `CFS_VOICE_PROVIDER=mock` make every stage run offline and
   deterministically. Tests never hit the network or spend money.

## 1. Project and pipeline engine (`backend/cashflow_studio/pipeline/`)

### Project folder

`<projects_dir>/<YYYY-MM-DD>_<channel-slug>_<topic-slug>/` with:

```
job.json
01_research/candidates.json  pick.json  transcript.json  competitor_thumbnail.jpg  video.json
02_title/title.json
03_script/script.md  script.json  speech.json  originality.json
04_storyboard/storyboard.json
05_voice/  06_images/  07_edit/  08_export/      (created empty now, filled in M3-M4)
```

### `job.json` (Pydantic model `Project` in `models/project.py`)

```
id (uuid4 str), channel_slug, topic_slug, title (current best title or topic), format: "long"|"shorts",
language, created_at, updated_at, folder (absolute path),
source: {kind: "ai_pick"|"manual_pick"|"own_topic", video_id?, video_url?, topic_text?},
stage_modes: {research..export: auto|review|manual}   (copied from the channel at creation),
stages: {
  research|title|script|storyboard|voice|images|edit|export: {
    status: "pending"|"running"|"awaiting_review"|"awaiting_manual"|"approved"|"done"|"failed"|"skipped",
    started_at?, finished_at?, error?, attempts: int, approved_by?, approved_at?, notes: [str]
  }
},
costs: {llm_usd: float, voice_usd: float, images_usd: float},
current_stage: str
}
```

Stage order is fixed: research, title, script, storyboard, voice, images, edit, export. A project
created from `own_topic` marks research as `skipped`. Voice, images, edit and export exist in the
state machine now but their runners raise `NotImplementedStage` until M3/M4; the engine marks them
`awaiting_manual` so a project can still be advanced by hand.

### Engine

- `pipeline/engine.py`: `PipelineEngine` with `create_project(...)`, `run(project_id)` (advances
  until a gate or the end), `approve(project_id, stage, by, notes)`, `redo(project_id, stage, notes)`,
  `skip(project_id, stage)`, `set_mode(project_id, stage, mode)`. Runs stages in a background
  asyncio task per project; at most `CFS_MAX_PARALLEL_PROJECTS` (default 2) at a time.
- `pipeline/stages/base.py`: `class Stage(Protocol): name; async def run(ctx: StageContext) -> StageResult`.
  `StageContext` carries the project, the channel, settings, the LLM client, providers, and a
  `progress(msg, pct)` callback. `StageResult` carries `outputs: list[Path]`, `summary: str`,
  `cost_usd`, `needs_review_payload: dict` (what the review UI shows).
- `pipeline/events.py`: in-process event bus; every status change and progress message is
  broadcast on WebSocket `/api/ws` as `{type: "project.update"|"stage.progress"|"job.log", ...}`.
- `storage/db.py`: SQLite at `<app_data_dir>/db.sqlite` with tables `projects` (id, channel_slug,
  folder, status json, updated_at), `research_videos` (cache), `research_scans`, `llm_calls`
  (project_id, stage, model, input_tokens, output_tokens, cost_usd, created_at),
  `title_history` (channel_slug, title, project_id), `script_history` (channel_slug, project_id,
  path, fingerprint). Use the standard library `sqlite3` with a small migration list; no ORM.

### HTTP API (prefix `/api`)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/channels/{slug}/research/scan` | `{tabs?: ["videos","shorts"], max_videos_per_channel?: 200, force?: bool}` | `202 {job_id}` |
| GET | `/api/channels/{slug}/research/candidates` | `?format=long\|shorts&limit=50` | `{scanned_at, channels: [{name,url,id,videos_found,last_scanned,error?}], candidates: Candidate[]}` |
| GET | `/api/jobs/{job_id}` | | `{id, kind, status: queued\|running\|done\|failed, progress: 0-100, message, result?, error?}` |
| POST | `/api/projects` | `{channel_slug, format, source: {kind: "ai_pick"} \| {kind:"manual_pick", video_id} \| {kind:"own_topic", topic_text}, stage_mode_overrides?: {...}}` | `201 Project` |
| GET | `/api/projects` | `?channel_slug=&status=` | `ProjectSummary[]` |
| GET | `/api/projects/{id}` | | `Project` |
| GET | `/api/projects/{id}/stage/{stage}` | | the stage's review payload (title variants, script, storyboard, candidates) |
| POST | `/api/projects/{id}/stage/{stage}/approve` | `{by, notes?, edits?}` | `Project` (edits are stage specific, see below) |
| POST | `/api/projects/{id}/stage/{stage}/redo` | `{by, notes}` | `Project` (re-runs the stage with the notes added to the prompt) |
| POST | `/api/projects/{id}/stage/{stage}/skip` | `{by, notes}` | `Project` |
| PUT | `/api/projects/{id}/stage/{stage}/mode` | `{mode}` | `Project` |
| POST | `/api/projects/{id}/run` | | `202` (resume after a manual change) |
| DELETE | `/api/projects/{id}` | | `204` (archives the folder to `<projects_dir>/_archived/`) |
| WS | `/api/ws` | | event stream |

Stage-specific `edits` on approve: title `{chosen_index?: int, title_text?: str}`; script
`{script_md?: str, locked_paragraph_ids?: [str]}`; storyboard `{storyboard: StoryboardDoc}`;
research `{video_id}` (changes the pick before title runs).

## 2. Research stage (`backend/cashflow_studio/research/`)

### Engine

- `ytdlp_client.py` (yt-dlp Python API, pinned `yt-dlp[default]`):
  - `list_tab(channel_url, tab)` -> flat entries for `/videos` or `/shorts` with
    `extract_flat='in_playlist'`, `skip_download`, `sleep_interval_requests=0.75`,
    extractor arg `youtubetab:approximate_date`. Returns id, title, url, duration (videos tab),
    view_count (rounded), approximate timestamp, thumbnail, channel_id, channel_follower_count.
  - `video_details(video_id)` -> exact view/like/comment counts, upload_date, tags, description,
    chapters, caption languages, best thumbnail URL; extractor args `youtube:player_skip=js;skip=hls,dash`.
  - `transcript(video_id, lang)` -> downloads auto or manual subtitles as json3, returns
    `{language, segments: [{start, end, text}], words: [{start, end, text}]}`; falls back to
    `youtube-transcript-api` when yt-dlp returns no track.
  - `thumbnail(video_id, dest)` -> tries maxresdefault, sddefault, hqdefault via HTTP HEAD, saves JPEG.
  - Back-off: on "Sign in to confirm", "try again later" or HTTP 429, sleep 10 minutes doubling to
    1 hour, surface `ResearchBlocked` with a plain-English message; never loop forever.
  - Cache: every flat entry and detail in `research_videos` keyed by video id with `fetched_at`;
    a channel tab is not re-listed within 60 minutes unless `force`.
- `outliers.py` (pure functions, fully unit-tested):
  - Separate long-form and Shorts.
  - Baseline for a video = median `view_count` of its neighbours by position (10 before, 10 after)
    on the same channel, excluding videos younger than 7 days; fall back to the channel median.
  - `outlier_score = views / baseline`; `views_per_day = views / max(age_days, 1)`;
    `vpd_ratio = views_per_day / median(views_per_day of the window)`; `sub_ratio = views / followers`.
  - Label: `>=10` "one-of-ten", `>=5` "strong", `>=3` "notable", else "normal".
  - Rank across **all competitors of the channel** by `outlier_score`, tie-break by `vpd_ratio`.
  - Exclusions (configurable in `config/research.yaml` defaults): age < 3 days, duration outside
    the channel's format band (long: 4-40 min, shorts: <= 180 s), already used by this channel
    (`title_history`), live streams, premieres.
- `picker.py`: `pick_one(candidates, channel, history) -> Candidate`: the AI pick. Default
  strategy `top_outlier_fresh`: highest score among candidates not used before, preferring the
  last 90 days, language matching the channel. Optional LLM rerank (Sonnet 5.5) of the top 10 by
  fit to the channel niche when `CFS_RESEARCH_LLM_RERANK=1`; off by default.
- `research_stage.py`: scans (or reuses a fresh scan), computes candidates, picks one (or uses the
  manual pick), fetches exact details, transcript and thumbnail for the picked video, writes
  `candidates.json`, `pick.json`, `video.json`, `transcript.json`, `competitor_thumbnail.jpg`.
  Review payload: top 50 candidates with the pick highlighted.
- Mock provider (`CFS_RESEARCH_PROVIDER=mock`): deterministic fixtures in
  `tests/fixtures/research/` (3 channels, 30 videos each, one obvious outlier each).

### `Candidate` (models/research.py)

```
video_id, url, title, channel_name, channel_url, channel_id, format: long|shorts,
views, views_exact?: bool, published_at?, age_days, duration_s?,
baseline_views, outlier_score, vpd, vpd_ratio, sub_ratio, label, thumbnail_url,
used_before: bool, excluded_reason?: str, rank
```

## 3. LLM layer (`backend/cashflow_studio/llm/`)

- Official `anthropic` Python SDK only. Default model `claude-opus-5-5` for title and script,
  `claude-sonnet-5-5` for storyboard, summaries, classification; both configurable in
  `config/llm.yaml`. Adaptive thinking (omit `thinking` or `{type: "adaptive"}`), `output_config`
  effort `high` for script/title, `medium` for storyboard. Streaming with
  `client.messages.stream(...)` and `get_final_message()` for anything long. Structured outputs
  through `client.messages.parse()` with Pydantic models. Server-side fallback enabled by default
  (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`) and `stop_reason ==
  "refusal"` handled: one retry with a reworded prompt, then a stage failure with the reason.
  Never assistant prefill. Prompt caching: framework text and style guide placed first with a
  `cache_control` breakpoint. Read the `claude-api` skill notes in `docs/llm-notes.md`
  (the orchestrator writes it) before coding the client.
- `llm/client.py`: `LLMClient.complete(task, system, user, schema, model?, effort?) -> (parsed, usage)`;
  logs every call to `llm_calls` with cost from a price table in `config/llm.yaml`.
- `llm/prompts/*.md`: one file per task (`title.md`, `script.md`, `speech_normalize.md`,
  `storyboard.md`, `transcript_summary.md`, `policy_check.md`). Prompts are plain Markdown with
  `{{placeholders}}`; no prompt text inside Python strings.
- `llm/mock.py`: `CFS_LLM_PROVIDER=mock` returns deterministic, schema-valid outputs derived from
  the inputs (for example the title variants embed the keywords; the script repeats the title in
  the hook) so tests and demos work offline.

## 4. Title stage (`pipeline/stages/title.py`)

Inputs: picked video (title, views, score), channel niche and audience, the channel's `title`
framework text (from the uploaded framework file; if none, a built-in default framework in
`llm/prompts/title.md`), language, format, recent titles of the channel (`title_history`).

Output `title.json`:
```
{ variants: [{index, title, formula, emotional_trigger, curiosity_trigger, hidden_gap, viral_score (1-10),
   why_it_outperforms, keywords_kept: [str], similarity_to_source: 0-1, similarity_to_history: 0-1,
   length: int, flags: [str]}], recommended_index, source_title, generated_at, model }
```
Gates: 7 variants; each under 70 characters; `similarity_to_source < 0.8` (difflib ratio on
lower-cased text) and `similarity_to_history < 0.8`; keep at least one keyword from the source
title. Variants failing a gate carry the reason in `flags` and cannot be `recommended_index`.
Review payload: the variants; approve edits `{chosen_index | title_text}`; the chosen title is
written back to `job.json.title` and appended to `title_history`.

## 5. Script stage (`pipeline/stages/script.py`)

Inputs: approved title, format, language, target length (`long_form_minutes` or `shorts_seconds`),
speaking rate (`config/voice.yaml` default 150 words per minute; per language overrides), the
channel's script framework text (long or shorts), a **structure-only summary** of the competitor
transcript (produced by `transcript_summary.md` with Sonnet: beats, pacing, devices, never
sentences to reuse), niche, audience, brand notes.

Outputs:
- `script.json`: `{title, language, format, target_words, sections: [{id, name, purpose,
  paragraphs: [{id, text, sentences: [{id, text}], locked: bool}]}], word_count, model}`.
- `script.md`: readable form with `## Section` headings.
- `speech.json`: the same sentence ids with `speech_text` where numbers, dates, currency and
  abbreviations are written out in the target language (prompt `speech_normalize.md`).
- `originality.json`: `{ngram_overlap_source: 0-1, ngram_overlap_history_max: 0-1,
  semantic_note: str, policy: {advisory_persona: bool, sensitive_topic: bool, reasons: [str]},
  passed: bool}`.

Gates (block when failing; the stage ends `failed` with reasons unless the reviewer overrides):
8-gram overlap with the competitor transcript <= 0.02 and with any of the channel's last 30
scripts <= 0.05; `advisory_persona == false`; word count within +/-15% of target; the title's key
claim appears in the first 20% of the script (checked by the LLM policy pass, reported, not
blocking). Redo with notes re-generates only unlocked paragraphs.

## 6. Storyboard stage (`pipeline/stages/storyboard.py`)

Inputs: `script.json` + `speech.json`, channel `images.style_guide`, `images.popup_style`,
format and aspect ratio, speaking rate for duration estimates (real timing arrives in M3).

Output `storyboard.json` (model `StoryboardDoc` in `models/storyboard.py`):
```
{ project_id, format, aspect: "16:9"|"9:16", style_guide, generated_at, model,
  scenes: [{
    index, sentence_ids: [str], narration: str, est_start_s, est_end_s, est_duration_s,
    image_prompt: str,            # text-free, style guide prepended by the stage, no real people/logos
    negative_prompt: str,
    popup: {text: str (<= 6 words) | null, style: str, position: "top-left"|"top-right"|"bottom-left"|"bottom-right"|"center", in_offset_s, out_offset_s},
    motion: {preset: "zoom_in"|"zoom_out"|"pan_left"|"pan_right"|"pan_up"|"pan_down"|"hold"|"slow_push", start_rect: [x,y,w,h], end_rect: [x,y,w,h]},
    transition_out: {type: str (one of config/transitions.yaml), duration_s: float},
    on_screen_text: str | null,
    image: {path: str|null, status: "pending"|"generated"|"approved"|"rejected", qa: dict|null},
    locked: {image_prompt: bool, popup: bool, motion: bool, transition_out: bool, narration: bool},
    notes: [str]
  }],
  variety: {scenes: int, avg_scene_s: float, popup_share: float, distinct_transitions: int, warnings: [str]}
}
```
Rules the stage enforces after the LLM call (fix up, do not just report): scene length 8-12 s
long-form / 3-4 s Shorts (estimated from words / speaking rate); no two neighbouring scenes with
the same motion preset; no transition type used twice in a row; transition types only from
`config/transitions.yaml` (xfade names with a friendly label and default duration); popup text
<= 6 words and never identical to the narration sentence; at least 35% of scenes have a popup or
on-screen text; prompts are prefixed with the style guide and suffixed with the negative rules.
Review payload: the whole document; approve edits `{storyboard}` replaces it (server re-validates
and re-runs the rule fix-ups except for locked fields).

Shorts variant: when the channel format is `both`, the storyboard stage produces
`storyboard.json` for the primary format and `storyboard.shorts.json` with 9:16 rects and faster
transitions is produced in M4 from the same scenes (not now).

## 7. Provider interfaces (`backend/cashflow_studio/providers/`) - interfaces now, adapters in M3

- `providers/voice/base.py`: `ProviderCapabilities`, `VoiceProvider` protocol
  (`list_voices`, `create_clone`, `synthesize(SynthRequest) -> SynthResult`, `estimate_cost`,
  `health`), `WordTiming(text, start_s, end_s, confidence, source)`, `SynthResult`.
  `providers/voice/mock.py` produces silence WAV of the right length and evenly spaced timings.
  `providers/voice/ai33.py`: stub that raises `ProviderNotConfigured("ai33: API details pending")`
  with the capability object filled from `config/providers.yaml` so the UI can show it.
- `providers/image/base.py`: `ImageProvider.generate(ImageRequest) -> ImageResult` with
  `prompt, negative_prompt, aspect, size, reference_images, seed, style` and result
  `path, width, height, model, seed, provenance: {c2pa|synthid: bool|null}, cost_usd`.
  `providers/image/mock.py` renders a placeholder PNG with Pillow (prompt text drawn on a
  coloured background) so the storyboard board shows pictures offline.
  `providers/image/gemini.py`: adapter skeleton using the official `google-genai` SDK; model id
  from `config/providers.yaml` (default `gemini-nano-banana-2.1`, verify against Google's current
  model list before shipping); not exercised in tests.
- `providers/registry.py`: builds providers from env/config; `mock` everywhere by default in tests.

## 8. Policy rules (`backend/cashflow_studio/policy/`)

`rules.yaml` with id, title, stage, severity (`block`|`warn`), source URL, last_verified date,
parameters (thresholds above). `gates.py` exposes `evaluate(stage, context) -> [GateResult]`
used by the title, script and storyboard stages; results are stored in the project and shown in
the review payloads.

## 9. Front end (`frontend/src/`)

Routes added (placeholders exist from M0; fill them):
- `/research/:slug?` Research: channel picker; competitor list (from the channel) with
  "Scan now" and per-channel status; candidates table (thumbnail, title, channel, views, age,
  outlier score with label pill, used-before flag); the AI pick highlighted; buttons
  **Start production with the AI pick**, **Use this one** per row, **Own topic** form; format
  toggle long/shorts; progress from `/api/jobs/{id}` and the WebSocket.
- `/projects` list with channel, title, current stage, status pill, updated; `/projects/:id`
  project page: stage timeline (8 steps with status), costs, folder path with "Open folder"
  (backend endpoint `POST /api/projects/{id}/open-folder` runs `explorer`), and the **Review
  panel** for the current gate.
- Review panels: research (candidates with change-pick), title (variants with scores and flags,
  edit text, choose, redo with notes), script (section editor with paragraph lock toggles,
  originality and policy results, regenerate with notes), storyboard (**Storyboard Board**: grid
  of scene cards showing narration, image placeholder or image, popup text, motion and transition
  pickers, duration; edit inline; split/merge/reorder; lock toggles; regenerate scene; variety
  warnings; approve).
- `/review` queue: all projects awaiting review across channels, filter by channel and stage.
- Dashboard cards become real: projects in progress, awaiting review, exports (0 for now).
- Types mirror the Pydantic models in `frontend/src/types/{project,research,title,script,storyboard}.ts`.
- All long operations show progress; nothing blocks the UI.

## 10. Tests

Backend (`tests/`): outlier maths with hand-computed expectations; picker exclusions; yt-dlp client
against recorded fixtures (no network); engine state machine (auto/review/manual paths, redo,
skip, failure, resume); title gates; script originality maths; storyboard rule fix-ups; API
endpoints with mock providers end to end: create project from `ai_pick` -> research done ->
title awaiting review -> approve -> script awaiting review -> approve -> storyboard awaiting
review -> approve -> voice awaiting_manual. Front end: typecheck and build; one Playwright smoke
test is optional (not required).

## 11. Settings additions

Settings page gets a "Models and providers" card: LLM model per task (dropdown of
`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5`), effort, research provider
(yt-dlp or mock), max parallel projects, speaking rate. Stored in `settings.json` under
`llm`, `research`, `pipeline`, `voice` keys; `GET/PUT /api/settings` extended with those nested
objects (additive).
