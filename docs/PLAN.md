# Cashflow Studio - Plan v0.2 (2026-10-09)

Status: approved direction, development starting at milestone M0. Supersedes v0.1.
Sources for every technical decision are in [research/2026-10-09-research-dump.md](research/2026-10-09-research-dump.md).

## 1. What we are building

A desktop app for a team of non-developers. Each person installs it on their own Windows
laptop. From a list of competitor channels it produces finished faceless videos, long-form
(16:9) and Shorts (9:16), in up to ten languages, with a human able to approve, edit or
replace the result of every stage.

```
competitor channels -> research -> title -> script -> storyboard -> voice -> images -> edit -> export
                                   (+ SEO keywords/tags, description, thumbnail from a competitor template)
```

Guiding rules:

- Human in the loop at every stage: each stage runs in `auto`, `review` or `manual` mode.
- A "perfect storyboard" is the centre of the product: every scene has narration, image,
  on-image popup text, motion, transition and timing, all editable in one screen.
- Running cost close to zero apart from the voice and image subscriptions the team already pays.
- Everything YouTube now calls "inauthentic content" is blocked by built-in quality gates.

## 2. Decisions taken (and why)

| Topic | Decision | Why |
|-------|----------|-----|
| Backend language | Python 3.12 (FastAPI, SQLite, FFmpeg, yt-dlp) | pipeline, providers and rendering are Python-native; team already runs Python tools |
| Front end | TypeScript + React + Vite, Tailwind, shown in a native WebView2 window (pywebview) | the Nexlev-grade dashboard and the CapCut-like timeline are browser technology; a Python-only UI cannot reach that quality. You lifted the Python-only rule, so the UI uses the right tool |
| Editor | Own timeline JSON is the source of truth; FFmpeg renders it; built-in "CapCut-lite" editor for the common 80% of fixes; one-click hand-off to Kdenlive (free), DaVinci Resolve (FCPXML) and CapCut (draft export, best effort) for anything deeper | a full CapCut-class editor is a 2-4 developer-year product; the hybrid gives a real editor without that cost |
| LLM | Claude API with each user's own API key, stored locally. Opus 5.5 for scripts and titles, Sonnet 5.5 for storyboard, SEO, image QA, Haiku for bulk | Anthropic's help centre (updated 2026-10-07) says Max plans now include a monthly API credit ($100 on Max 5x, $200 on Max 20x). If that holds in the Console, LLM cost per video is covered: roughly $0.30-0.60 per 10-minute video on Opus, far less on Sonnet. Two fallbacks: drive the pipeline from Claude Desktop/Code through a local MCP server, or a local model (Ollama) for drafts |
| Competitor data | yt-dlp on each user's laptop, two-tier fetching, SQLite cache | no API key needed; verified on this machine (450 videos listed in 12 s); residential IPs avoid bot checks |
| Voice | Pluggable `VoiceProvider` with normalised word timings. First adapter = the team's own voice-clone tool (to be named). Ready adapters: MiniMax, Cartesia, Inworld (all return word timestamps), Fish Audio (no timestamps), local Chatterbox/Qwen3-TTS. WhisperX forced alignment when the tool gives no timestamps | ElevenLabs excluded by you; Play.ht is dead, Hume shuts down Nov 2026, Resemble withdrew public pricing, so the design must not depend on one vendor |
| Images | Pluggable `ImageProvider`. Default Google Nano Banana 2.1 (Gemini API, 16:9 and 9:16 native, ~$0.034/image, up to 14 reference images); thumbnails on Nano Banana Pro or GPT Image 2.5; FLUX, Ideogram, Recraft as options. Google Flow has no API: manual import only | Imagen 4 was retired in Aug 2026; the market changes every few months, so adapters, not hard-coding |
| On-image text | Images are generated text-free; popups, callouts, arrows are drawn by the app (Pillow, Noto fonts) as editable timeline layers | model-rendered body text is unreliable, and overlays stay editable and translatable |
| Transitions | Claude picks a transition per cut from FFmpeg's xfade library plus motion-continuity rules; optional frame-interpolated morphs (FILM/RIFE) on GPU machines | smooth, varied cuts without manual work, and visual variety is now a monetisation requirement |
| Output | Presets 720p, 1080p, 4K. Images generated at 2K where the provider supports it; 4K via local upscaling (Real-ESRGAN when a GPU exists, Lanczos otherwise) | you asked for 720/1080 with 4K |
| Shared master data | A shared Google Drive folder, synced to every laptop with Google Drive for desktop, holds channel configs, frameworks, style references, voice samples, music, thumbnail templates. GitHub holds code only. Heavy per-video media stays on each user's local drive | non-developers do not use git; Drive sync is free, works offline, and already fits how the team shares files |
| Distribution | Reuse the SceneForge method: self-contained Python runtime + setup EXE built with PyInstaller + GitHub Releases manifest (sha256) + in-app auto-update. Bundle FFmpeg (BtbN build), Deno (for yt-dlp), Noto fonts, WebView2 bootstrapper | proven with your 20-editor team already |
| Languages | v1 order: English, Spanish, Hindi, Arabic (MSA), Portuguese (BR), Indonesian, Japanese, German, French, Russian; next: Vietnamese, Turkish, Korean | built from YouTube audience by country (DataReportal 2025-2026). Russian has near-zero ad yield; ship only with a non-AdSense plan |

## 3. Architecture

```
+----------------------------------------------------------------------------------+
|  Desktop window (WebView2)  -  React app: Dashboard, Channel Setup, Research,    |
|  Storyboard Board, Timeline Editor, Review queue, Settings                       |
+-------------------------------^--------------------------------------------------+
                                | HTTP + WebSocket (localhost only)
+-------------------------------v--------------------------------------------------+
|  Python backend (FastAPI)                                                        |
|   pipeline/   stage engine, job state (SQLite + JSON per project), modes, review |
|   research/   yt-dlp engine, outlier scoring, transcripts, thumbnails            |
|   llm/        Claude client, prompts, structured outputs, cost log               |
|   voice/      VoiceProvider adapters, WhisperX aligner, timing normaliser        |
|   images/     ImageProvider adapters, style sheets, vision QA, pHash dedupe      |
|   storyboard/ scene model, popups, transitions, motion presets                   |
|   render/     timeline JSON -> FFmpeg (zoompan, xfade, ASS, ducking), presets    |
|   export/     thumbnails, SEO metadata, hand-off writers (Kdenlive, FCPXML,      |
|               CapCut draft), provenance bundle                                   |
|   policy/     versioned YouTube rule set + QA gates                              |
|   shared/     Google Drive folder access (channels, frameworks, assets)          |
+----------------------------------------------------------------------------------+
```

### Per-video project folder (local drive)

```
projects/2026-10-09_kind-ledger_why-she-paid-for-strangers/
  job.json                  stage, modes, approvals, costs, provenance
  01_research/candidates.json, transcript.json, competitor_thumbnail.jpg
  02_title/title.json
  03_script/script.md, script.json (sections, sentences)
  04_storyboard/storyboard.json
  05_voice/sentence_###.wav, voice.wav, timing.json
  06_images/scene_##.png (+ rejected/), style_sheet.png
  07_edit/timeline.json, captions.ass, popups/, music.wav
  08_export/final_1080p.mp4 (and other presets), thumbnail.png, metadata.json, provenance.pdf
```

### Storyboard JSON (the heart of the product)

Each scene carries: index, narration text, start/end time (from voice timing), image prompt,
image path and QA result, popup text with style and timing, motion preset (zoom in/out, pan,
hold, start and end rectangles), transition into the next scene (type, duration), caption
cues, and a `locked` flag so a human edit is never overwritten by a regeneration.

### Stage modes

`auto` runs and continues. `review` runs and waits for approval in the Review queue.
`manual` skips the AI and waits for a human to supply the asset. Modes are set per channel
and can be overridden per video.

## 4. The eight stages in detail

| # | Stage | What the app does | Human options |
|---|-------|-------------------|---------------|
| 1 | Research | Flat-lists each competitor's /videos and /shorts tabs with yt-dlp, caches in SQLite, scores outliers (views vs the median of neighbouring videos, age-normalised, subscriber ratio), fetches exact numbers for the top candidates, pulls the transcript and HD thumbnail of the chosen ones | pick a candidate, add a topic by hand, exclude channels |
| 2 | Title | Opus 5.5 writes 7 variants using the channel's title framework, keeps the proven keywords, scores each (formula, emotional trigger, curiosity, viral score), rejects copies | edit, choose, reorder |
| 3 | Script | Opus 5.5 writes the script from the channel framework, in the target language, with sections and sentence boundaries; originality gate against the competitor transcript and the channel's last scripts | edit in the script editor, regenerate a section, lock paragraphs |
| 4 | Storyboard | Sonnet 5.5 splits sentences into scenes (8-12 s long-form, 3-4 s Shorts), writes one text-free image prompt per scene in the channel style, assigns popup text, motion and transition | edit any field, split or merge scenes, regenerate a scene |
| 5 | Voice | Synthesises per sentence with the cloned voice, normalises word timings (provider or WhisperX), builds the caption clock | upload own recording, re-record a sentence, nudge timings |
| 6 | Images | Generates each scene with the style sheet as reference, runs vision QA (prompt match, no garbled text, no real people), pHash dedupe against the channel, regenerates failures | regenerate with a note, upload an image, lock an image |
| 7 | Edit | Builds timeline JSON, renders a low-res proxy, then final renders per preset | open the Timeline Editor, or export to Kdenlive / Resolve / CapCut |
| 8 | Export | Thumbnail from the competitor template (layout extracted by vision, rendered as text-free subject + app-drawn text), SEO keywords, tags, description, chapters, disclosure flag, provenance bundle; copies everything to the channel's export folder | approve, edit metadata, re-export |

## 5. YouTube policy gates (built into the app)

YouTube's July 2025 "inauthentic content" policy and its July 2026 clarification name
exactly the output of a naive slideshow pipeline. These gates run automatically; a failure
blocks export until a reviewer overrides it with a logged reason.

Hard blocks:
- No AI persona presented as a doctor, financial adviser, lawyer or political authority; no personalised advice.
- Only the team's own enrolled voices may be cloned; a consent record is stored with every voice.
- Never reuse competitor footage, thumbnail images, faces, logos or branding. Thumbnails must pass a similarity ceiling against the reference.
- Script must not paraphrase the competitor transcript (n-gram and semantic ceilings) and must differ from the channel's recent scripts.
- Title and thumbnail promise must appear in the script early.

Variety and originality:
- Minimum scene density, varied motion and transitions, consistent per-channel art style, popups that paraphrase (never replace) narration, no image reused across videos.
- Script frameworks and openings rotate; same structure capped per channel per 30 days.

Disclosure:
- "Altered or synthetic content" flag defaults to yes; the app records whether the image tool embeds C2PA/SynthID and never strips it.
- Music must be licensed; licence stored with the project.

Evidence:
- Every review decision is logged; a provenance bundle (research notes, script versions, prompts, review log, licences) is exportable for appeals.
- Rules live in a versioned config file with source URLs and a "last verified" date, re-checked quarterly.

## 6. Languages and captions

| Tier | Languages | Notes |
|------|-----------|-------|
| A (high RPM) | English, German, Japanese, French, Spanish (Spain/US) | a 10-minute video pays back on tens of thousands of views |
| B (volume) | Hindi, Indonesian, Arabic, LatAm Spanish, Portuguese | throughput matters; consider English master + YouTube auto-dubbing or uploaded audio tracks instead of native production for some |
| Special | Russian | large audience, near-zero AdSense yield |

Engineering rules: Noto font bundle (Sans, Devanagari, Naskh Arabic, Sans JP, Sans KR);
Arabic right-to-left with bidi wrapping; Japanese wraps at about 13 full-width characters with
kinsoku rules; a per-language speech-normalisation pass before TTS (numbers, currency, dates,
kanji readings, optional Arabic diacritics); Hindi word alignment from Whisper is unreliable,
so the voice tool's own timestamps or sentence-level timing are used there.

## 7. Voice and image provider contracts

Voice: `VoiceProvider` declares capabilities (clone, languages, timestamp granularity,
billing unit, max chars) and implements `list_voices`, `create_clone(samples, consent)`,
`synthesize(request) -> SynthResult` with normalised `WordTiming(text, start, end,
confidence, source)`. Synthesis is per sentence so sentence boundaries are exact even when
word alignment is weak. A `ForcedAligner` (WhisperX default) fills in timings when the
provider returns none. Audio is cached locally by a content hash so edits never re-bill.

Images: `ImageProvider.generate(prompt, aspect, size, reference_images, style) -> PNG +
metadata (seed, model, provenance)`. A per-channel style sheet image is passed as a reference
on every call for consistency. Vision QA uses Sonnet 5.5. Budget guards warn before a batch
exceeds the monthly plan.

## 8. Roadmap

| Milestone | Deliverable | Done when |
|-----------|-------------|-----------|
| M0 Foundation | Repo structure, backend skeleton (FastAPI, SQLite, settings, channel model), React shell with Dashboard, Channel Setup form, Settings (API keys), shared-drive path, `doctor` checks (FFmpeg, Deno, WebView2, keys) | app opens in a window, a channel can be created and saved, doctor passes |
| M1 Research + Title | yt-dlp engine, outlier scoring, candidates screen, title variants, review gate | 3 competitor URLs produce a ranked list and an approved title |
| M2 Script + Storyboard | framework ingestion (PDF, TXT, MD), script generation with originality gate, storyboard generation, Storyboard Board screen with edit/regenerate/lock | a reviewed storyboard JSON exists for a real title |
| M3 Voice + Images | first voice adapter (the team's tool) + MiniMax/Cartesia/Fish, WhisperX aligner, image adapter (Gemini) + style sheet, vision QA, popup rendering, caches | all assets generated and approved for one video |
| M4 Edit + Export | timeline JSON, FFmpeg renderer (zoompan, xfade, ASS captions, popups, ducking), presets 720p/1080p/4K, thumbnail from template, SEO metadata, provenance bundle, export folder | first complete video on disk from competitor URL to MP4 |
| M5 Timeline Editor | CapCut-lite editor: reorder, trim to voice boundaries, swap/regenerate image, popup and caption editing, music and ducking, transition picker, proxy preview; Kdenlive and FCPXML export; CapCut draft export (best effort) | an editor fixes a video without leaving the app |
| M6 Scale | batch runs, language variants, policy QA dashboard, installer + auto-update, team rollout | 5 videos run overnight with review gates on a teammate's laptop |
| M7 Optional | upload hook (YouTube Data API with the synthetic-media flag, or the existing ixBrowser publisher), analytics feedback into research | one click from export to upload queue |

Effort guide from the research: the basic editor is 4-6 developer-weeks on top of an existing
timeline component; the CapCut-lite tier adds 8-12 weeks. M0-M4 is the first usable product.
One pull request per milestone.

## 9. Open questions (answer when convenient; M0 does not depend on them)

1. Which voice-clone tool and which image tool does the team pay for, and do they have an API? This sets the first adapters.
2. Which Claude plan do users have (Max 5x, Max 20x, Pro)? Max plans carry the monthly API credit; Pro users would use the MCP mode.
3. Which Google account owns the shared Drive folder, and is Google Drive for desktop installed on the laptops?
4. Do the laptops have NVIDIA GPUs and how much RAM? This decides local alignment, upscaling and morph transitions.
5. Which CapCut version is installed (for the draft export)?
6. For Tier B languages: native scripts per language, or English master plus auto-dub?
7. Arabic: Modern Standard Arabic or a dialect?
8. Confirm the repository name `cashflow-studio`.

## 10. Fact-check corrections folded in (2026-10-09, second pass)

- **Paid consumer subscriptions do not include API access.** Google AI Pro/Ultra (Flow, Gemini app), ChatGPT Plus and Midjourney give no API quota, and Midjourney's terms ban automation. If the team's image or voice tool is a consumer subscription, the automated pipeline still needs an API plan for that provider, or the stage runs in `manual` mode with images imported by hand. This is the first thing to confirm (open question 1).
- **Max plan API credit confirmed** on Anthropic's help centre (Max 5x $100/month, Max 20x $200/month; Team $20/$100 per seat). Constraints: the person linking the Console organisation must be the subscriber, one Console org per plan, credits do not roll over. Each teammate therefore needs their own Max plan and their own key.
- **Safety-classifier refusals** (HTTP 200 with `stop_reason: refusal`) are a normal failure mode on current Claude models; the LLM layer uses the server-side fallback option and retries with a reworded prompt before surfacing an error.
- **Disclosure flag must be set by the uploader.** FFmpeg re-encoding drops image-level C2PA metadata, so YouTube's auto-label will not fire from source images; the app sets the synthetic-media flag explicitly.
- **Arabic and Hindi text in Pillow:** use `arabic-reshaper` + `python-bidi` (pure Python) instead of shipping `fribidi.dll`; FFmpeg drawtext needs libfreetype + libharfbuzz.
- **Vision pass for thumbnail templates** uses the current Gemini text/vision model (gemini-3.8-flash on 2026-10-09) or Claude Sonnet 5.5, not an image-generation model.
- **YouTube Data API (optional power-user key):** quota changed 2026-06-01 (100 search calls/day, separate buckets); new `videos.batchGetStats` costs 1 unit per call; thumbnails now go up to 1920x1080, 2560x1440 and 3840x2160 for some videos, which is useful for 4K thumbnail templates. yt-dlp remains the default engine.
- **yt-dlp needs a JavaScript runtime** (Deno) for downloads and must be pinned together with `yt-dlp-ejs`; research-only calls worked without it in testing. Nothing is installed on this machine yet (no yt-dlp, Deno, Node).
- **Distribution:** Inno Setup 7.1 is GA; the front end is built once in CI into static files served by FastAPI, so no Node runs on user laptops. Bundled FFmpeg: BtbN GPL build (has libx264) is acceptable for internal team distribution; revisit if the app is ever sold.

## 11. Facts to re-verify before they are relied on

- Anthropic Max plan monthly API credit (help centre, 2026-10-07) and the availability and pricing of Haiku 5.5.
- Current wording of YouTube's monetisation policy page sections before hard-coding rule text.
- Price per image of Nano Banana 2.1 / Pro and GPT Image 2.5 in the Console at build time.
- yt-dlp's PO-token requirement for subtitles after each monthly release.
