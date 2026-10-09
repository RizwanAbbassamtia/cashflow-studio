# M3 + M4 contract: voice, images, edit/render, export

Builds on `docs/M0-CONTRACT.md` and `docs/M1-M2-CONTRACT.md`. The engine, stage protocol
(`pipeline/stages/base.py`), project model, LLM client, provider registry and review UI
already exist; this wave fills the last four stages and their screens. Change this file first,
code second.

## 0. Rules for this wave

1. **Voice before pictures.** `timing.json` from the voice stage is the clock for scenes,
   images, popups, captions and music. Nothing in edit may invent its own timing.
2. **Every stage writes files first** into `05_voice/`, `06_images/`, `07_edit/`, `08_export/`
   and only then marks itself done. A person may replace any file and resume.
3. **Offline by default.** `CCS_VOICE_PROVIDER=mock`, `CCS_IMAGE_PROVIDER=mock`,
   `CCS_LLM_PROVIDER=mock` produce a complete, playable video with silent audio and placeholder
   pictures. Tests never touch the network. FFmpeg is installed and may be used in tests.
4. **No GPU.** Alignment, upscaling and rendering are CPU only. Prefer provider timestamps;
   fall back to sentence-level timing; never require WhisperX (optional adapter only).
5. **Keys stay in `.env`.** The ai33 voice tool is still unspecified: keep the stub and add a
   configurable generic HTTP adapter so it can be wired without code once its API is known.

## 1. Voice stage (`pipeline/stages/voice.py`, `providers/voice/*`, `audio/*`)

Inputs: `03_script/script.json` + `speech.json` (sentence ids and speech text), channel `voice`
config (tool, clone_ref, language, speed, style, api_key_env, returns_word_timestamps,
consent), settings `voice.speaking_rate_wpm`.

Behaviour:
- Synthesise **per sentence** with the provider (`SynthRequest(text=speech_text, voice_id=
  clone_ref, language, speed, style, output=wav 48 kHz mono 16-bit)`), cache each sentence WAV by
  `sha256(provider, model, voice, language, speed, style, text)` under
  `<app_data_dir>/cache/voice/<hash>.wav` so a redo re-bills only changed sentences.
- Concatenate with sample-accurate offsets into `05_voice/voice.wav`; write
  `05_voice/sentences/<sentence_id>.wav`.
- `05_voice/timing.json` (model `TimingDoc` in `models/timing.py`):
  `{sample_rate, duration_s, source: "provider_word"|"provider_sentence"|"estimated"|"aligned",
  sentences: [{id, text, start_s, end_s, words: [{text, start_s, end_s, confidence}]}]}`.
  Word timings: provider words when returned; otherwise estimate inside each sentence by
  character length (source `estimated`); sentence boundaries are always exact (they come from
  the concatenation offsets).
- Own recording (edits `{audio_path}` on approve or a file dropped at `05_voice/voice.wav` in
  manual mode): convert to 48 kHz mono WAV with FFmpeg, then align: if an aligner is configured
  (`audio/aligner.py` protocol with a `whisperx` adapter that imports lazily and a `none`
  default) use it; else distribute sentences over the total duration by character share
  (source `estimated`) and flag `timing_confidence: "low"` in the review payload.
- Gates (policy ids `voice.consent`, `voice.duration`): when the channel names a cloned voice
  (`voice.clone_ref`, a clone made in the tool's own website) or a voice sample
  (`voice.sample_path`), a consent record must be present (the channel's `voice.consent`
  fields `{owner_name, consented_by, consented_at, statement}`, filled in the Channel Setup
  Voice tab, or a `consent.json` next to the sample) else block; a voice with neither is a
  stock voice and needs none; total duration within +/-25% of target length else warn;
  words < 40 ms or > 2 s flagged. The provenance bundle names the record it found, or says
  `none recorded`.
- Cost: provider `estimate_cost` per sentence, summed into `costs.voice_usd`.
- Review payload: duration, per-sentence list with start/end and a `play_url` served by
  `GET /api/projects/{id}/files/05_voice/sentences/{sid}.wav` (see section 5), timing source,
  warnings. Edits on approve: `{re_record: [sentence_ids]}` (re-synthesise only those),
  `{audio_path}` (use own recording), `{timing: TimingDoc}` (manual nudges).

Providers (`providers/voice/`): keep `base.py`, `mock.py`, `ai33.py` (stub). Add
`minimax.py` and `cartesia.py` (real adapters following their public REST docs, word timestamps
on, untestable without keys, must import without them), and `generic_http.py`: a provider
driven entirely by `config/providers.yaml` (`url`, `method`, `headers` with `${ENV}` expansion,
`body_template` with `{{text}} {{voice}} {{language}} {{speed}}`, `response.audio_field` or
`response.audio_url_field`, `response.word_timings_path`) so ai33 can be connected by config.
Mock voice: silence of `len(words)/wpm` seconds per sentence plus evenly spaced word timings.

## 2. Images stage (`pipeline/stages/images.py`, `providers/image/*`, `images/*`)

Inputs: `04_storyboard/storyboard.json`, channel `images` config (tool, model, style_guide,
negative_rules, aspect, resolution, reference_folder, api_key_env), `06_images/style_sheet.png`
if present.

Behaviour:
- Build the per-scene prompt: `style_guide + ". " + image_prompt + ". No text, no logos, no
  watermarks."`; negative from `negative_rules`.
- Style sheet: if the channel has reference images, use the first as `style_sheet.png`; else
  the first approved scene image becomes the style sheet. Pass the style sheet (and the previous
  scene's image) as `reference_images` when the provider supports it.
- Generate at the provider's largest size for the aspect (`16:9` or `9:16`); save
  `06_images/scene_NN.png`; record `provider, model, seed, cost, provenance` in
  `06_images/images.json` (model `ImagesDoc` in `models/images.py`).
- QA with `LLMClient.analyze_image(task="image_qa", image_path, prompt, schema=ImageQA)` ->
  `{matches_prompt: bool, has_text: bool, has_real_person: bool, has_logo: bool, artifacts:
  [str], score: 0-10, reason}`; reject when `matches_prompt` is false, `has_text`,
  `has_real_person`, `has_logo` or `score < 5`; regenerate up to 3 times with the reason
  appended to the prompt; rejected files go to `06_images/rejected/`.
- Dedupe: 64-bit perceptual hash (`images/phash.py`, pure Pillow) compared with the channel's
  past images (`image_hashes` SQLite table: channel_slug, project_id, scene, hash); Hamming
  distance <= 6 counts as reuse -> regenerate with a different seed.
- Locks: scenes with `locked.image_prompt` keep their prompt; scenes with an approved image are
  not regenerated on redo unless named in edits.
- Gates: `images.qa` (all scenes have an accepted image, else block), `images.variety`
  (no two accepted images within distance 6, warn).
- Cost into `costs.images_usd`; budget guard from `images.monthly_budget_images` (warn).
- Review payload: grid of scenes with `image_url`, QA verdict, attempts, locked; edits:
  `{regenerate: [{scene: int, note: str}], uploads: [{scene: int, path: str}], lock: [int]}`.
- `LLMClient.analyze_image` is new: add it to `llm/client.py` (Anthropic vision with base64
  image block before the text, Sonnet 5.5 at effort low) and to `llm/mock.py` (returns a pass
  verdict, or a fail when the prompt contains "FAIL_QA"). Signature:
  `analyze_image(task: str, image_path: Path, prompt: str, schema: type[T], model: str | None = None) -> T`.

Providers (`providers/image/`): finish `gemini.py` using the official `google-genai` SDK
(generate image from text with aspect ratio, optional reference images, read `GEMINI_API_KEY`,
model from `config/images.yaml`, record SynthID/C2PA flags when the SDK reports them; import
cleanly without the key; a `@pytest.mark.live` test when the key exists). Keep `mock.py`
(placeholder PNG with the prompt text, deterministic colour per scene).

## 3. Edit stage (`pipeline/stages/edit.py`, `render/*`, `models/timeline.py`)

### Timeline JSON (source of truth; M5's editor edits this file)

`07_edit/timeline.json` (model `Timeline`):
```
{ version: 1, project_id, format, aspect, fps: 30, width, height,
  duration_s, voice: {path, start_s: 0},
  music: {path|null, start_s, gain_db, duck_db: -12, duck_attack_s: 0.3, duck_release_s: 0.8, fade_in_s, fade_out_s, license_ok: bool},
  scenes: [{index, image: path, start_s, end_s, motion: {preset, start_rect:[x,y,w,h], end_rect:[x,y,w,h]} (rects in source-image pixel space),
            transition_out: {type, duration_s}, locked: bool}],
  popups: [{scene, text, style, position, start_s, end_s, anim: "slide_left"|"fade"|"pop", png: path}],
  captions: {enabled, style: {font, size, primary, outline, position, max_lines, max_chars_per_line, rtl: bool}, cues: [{start_s, end_s, text}]},
  intro: {path|null}, outro: {path|null},
  presets: ["1080p"], proxy: {width: 854|480, height: 480|854} }
```

### Builder (`render/timeline_builder.py`)
- Scenes get `start_s/end_s` from the sentences they cover in `timing.json` (first sentence start
  to last sentence end); the last scene extends to the voice end plus `tail_s` (0.8 s).
- Transitions: `transition_out.duration_s` is clipped to 25% of the shorter neighbouring scene.
- Popups: in at the start of the scene's key sentence + 0.3 s, out after min(4 s, scene end - 0.2 s).
- Captions: words grouped into cues of at most `max_chars_per_line * max_lines` characters,
  breaking at sentence ends and pauses > 0.35 s; per-language rules from
  `config/captions.yaml` (Arabic rtl + Noto Naskh Arabic, Hindi Noto Sans Devanagari, Japanese
  Noto Sans JP with 13-char lines and no space-dependence, Korean Noto Sans KR, default Noto Sans).
- Music: pick a track from the channel `music_folder` (deterministic by project id); require a
  sidecar `<track>.license.txt` or a `LICENSE*` file in the folder for `license_ok`.

### Renderer (`render/ffmpeg.py`, `render/captions.py`, `render/popups.py`)
- Captions: write `07_edit/captions.ass` (libass styles, `\an2` bottom-centre, outline 2,
  shadow 0, margin 60 px at 1080p scaled by height) and burn with
  `subtitles=captions.ass:fontsdir=assets/fonts`.
- Popups: render each popup to a transparent PNG with Pillow (rounded box, brand colour,
  bold Noto Sans 64 px at 1080p, padding 24, scaled per preset) and overlay with
  `overlay=x='...':y=...:enable='between(t,start,end)'`; `slide_left` animates x over 0.35 s.
- Scenes: each image -> `scale` to cover the frame -> `zoompan` (or `crop` with expressions)
  interpolating `start_rect` to `end_rect` over the scene duration at the preset fps -> `format=
  yuv420p`; chained with `xfade=transition=<type>:duration=<d>:offset=<t>`; all inputs
  normalised to the same size and fps before `xfade`.
- Audio: voice.wav + music via `sidechaincompress` (threshold 0.03, ratio 8, attack 300,
  release 800) + `amix`, loudness normalised to -16 LUFS with `loudnorm`, AAC 192 kbps 48 kHz.
- Presets (`config/render.yaml`): `720p` 1280x720 / 720x1280, `1080p` 1920x1080 / 1080x1920,
  `2160p` 3840x2160 / 2160x3840 with `scale=flags=lanczos`; H.264 libx264, crf 20, preset
  `medium` (configurable), 30 fps, `-movflags +faststart`; `proxy` 854x480 ultrafast crf 30.
- Progress: run FFmpeg with `-progress pipe:1 -nostats`, parse `out_time_us`, report
  `ctx.report(f"Rendering {preset} {pct}%", pct)`; cancellable through the context cancel flag.
- Outputs: `07_edit/proxy.mp4` always; `07_edit/final_<preset>.mp4` for each requested preset
  (default `["1080p"]`; edits `{presets: [...]}` add more: a finished render is kept while
  the timeline without its preset list, the popup switch, the x264 speed and the source
  files are unchanged, recorded as `render_key` per preset in `render_state.json`);
  `07_edit/render.log`.
- The build must stay correct for 1 to 80 scenes; use filter scripts via `-filter_complex_script`
  to avoid command-line length limits on Windows.
- Gates: `edit.music_license` (warn when music used without licence), `edit.duration`
  (rendered duration within 1 s of timeline duration else fail), `edit.audio_peaks` (warn when
  true peak > -1 dBTP).
- Review payload: proxy `play_url`, timeline summary (scene count, durations, transitions),
  render status per preset, warnings; edits: `{presets: [...]}`, `{music_path}`, `{captions_enabled}`,
  `{timeline: Timeline}` (full replacement, re-validated).

Fonts: vendor the Noto fonts needed (Noto Sans Regular/Bold, Noto Sans Devanagari, Noto Naskh
Arabic, Noto Sans JP, Noto Sans KR, Noto Sans Arabic) into `assets/fonts/` with their OFL
licence file; the doctor gets a `fonts` check.

## 4. Export stage (`pipeline/stages/export.py`, `export/*`)

Inputs: final renders, approved title, `script.json`, `timing.json`, `01_research/
competitor_thumbnail.jpg` (if any), channel `thumbnail` config, brand colours, language.

- Thumbnail template (`export/thumbnail.py`): `LLMClient.analyze_image(task="thumbnail_template",
  image=competitor_thumbnail.jpg, schema=ThumbnailTemplate)` ->
  `{subject_box, text_blocks: [{box, role, color, stroke}], palette, mood, has_face, layout_notes}`;
  cached per competitor video id in the channel folder `thumbnail-templates/<video_id>.json`.
  Render: text-free subject image from the image provider (prompt from the mood and the
  video's title, same style guide, aspect 16:9 and 9:16), then Pillow composes the headline
  (max `max_headline_words`, brand font or Noto Sans Bold, colours from channel config), shapes
  from the template; outputs `08_export/thumbnail.png` (1280x720) and `thumbnail_shorts.png`
  (1080x1920), plus 2 variants `thumbnail_v2.png`, `thumbnail_v3.png` with different headlines.
  Gate `export.thumbnail_similarity`: pHash distance to the competitor thumbnail must be > 12.
- SEO pack (`export/seo.py`, prompt `llm/prompts/seo.md`, Sonnet 5.5): `{title, description,
  tags: [<= 500 chars total], chapters: [{time: "mm:ss", title}], pinned_comment, hashtags}` in
  the target language; chapters derive from script sections and `timing.json`.
- Disclosure: `altered_or_synthetic: true` by default; `provenance: {images: [{scene, provider,
  model, synthid|c2pa flags}], voice: {provider, model, consent_ref}}`.
- Provenance bundle: `08_export/provenance.json` and `provenance.md` (research pick, title
  variants, script versions, prompts used, QA verdicts, review log from `job.json`, licences).
- Export (Approve = export): the run writes the whole pack into `08_export` only. When the
  reviewer approves, the engine calls the stage's `on_approve(ctx)` hook, which copies
  `final_<preset>.mp4` as `<topic-slug>_<preset>.mp4`, the chosen thumbnails, `metadata.json`
  and the provenance files into `<channel export_folder or settings exports_dir>/<channel-slug>/<date>_<topic-slug>/`
  (a copy whose destination already holds the same file is skipped). Only when the export
  stage is set to `auto` does the run itself copy. `export.json` and the review payload carry
  `exported: bool` so the screen can tell "ready" from "in the export folder".
  Gate `export.title_promise` (LLM check that the title's claim appears early in the script;
  warn), `export.files_present` (block if a selected preset has no rendered file).
- Review payload: thumbnails (urls), metadata fields, disclosure flag, export folder,
  `exported`; edits: `{metadata: {...}}`, `{thumbnail_choice: "v1"|"v2"|"v3"}`,
  `{altered_or_synthetic: bool}`, `{headline: str}` (re-render thumbnail text only). Edits
  rewrite the pack in `08_export`; the copy happens on the approval that carries them.

## 5. File serving and API additions

- `GET /api/projects/{id}/files/{path}`: serves a file from inside the project folder only
  (path traversal refused, 404 outside); correct content types for wav/mp4/png/jpg/json; supports
  HTTP range requests for mp4/wav so the browser can seek.
- `POST /api/projects/{id}/upload/{stage}` multipart (`file`, optional `scene`): saves into the
  stage folder with a safe name and returns the relative path (used by "upload own recording"
  and "upload image").
- `GET /api/settings` / `PUT` gain `render: {default_presets: ["1080p"], x264_preset: "medium",
  enable_4k: bool}` and `captions: {enabled: true, style: "bold-white"}` (additive).
- Doctor: `fonts` check (assets/fonts present), `ffmpeg_filters` (libass, xfade, zoompan,
  sidechaincompress available via `ffmpeg -filters`).

## 6. Front end

Stubs exist and are wired in `components/review/ReviewPanel.tsx`: `VoiceReview`, `ImagesReview`,
`EditReview`, `ExportReview` (props `{projectId}`; keep the signature).
- `VoiceReview`: audio player for `voice.wav` with a sentence list (text, start, end, play
  button per sentence), timing source and confidence, warnings, "Re-record selected sentences"
  (approve with `re_record`), "Use my own recording" (upload then approve with `audio_path`).
- `ImagesReview`: scene grid with image, QA verdict pill, attempts, lock toggle, per-scene
  "Regenerate with a note", "Upload image", select-all approve; progress while generating.
- `EditReview`: proxy video player, scene strip under the player (click to seek), popup and
  caption toggles, music selector (tracks from the channel folder with licence badge), preset
  checkboxes (720p, 1080p, 4K) with render buttons and progress bars, warnings, Approve.
- `ExportReview`: thumbnail picker (3 variants + headline edit), metadata editor (title,
  description, tags, chapters, pinned comment), disclosure toggle, export folder with Open
  folder, Approve = export (the folder panel says "Not exported yet" until the payload's
  `exported` is true).
- `DELETE /api/projects/{id}` (archive) waits for the running step's worker thread and
  answers 409 when a file in the folder is still in use (wait until the step stops); it
  never leaves a half-copied project behind.
- Settings: "Render" card (default presets, x264 preset, 4K on/off, captions on/off) and the
  Doctor shows fonts and FFmpeg filter checks. Dashboard: exports count becomes real.
- Types mirror `models/timing.py`, `models/images.py`, `models/timeline.py`, `models/export.py`.

## 7. Tests

Voice: per-sentence caching, concat offsets, estimated timings, consent gate, own-recording
path with the `none` aligner. Images: QA rejection and retry with the mock (`FAIL_QA` hook),
pHash distance maths with synthetic images, dedupe, locks. Edit: timeline builder maths
(scene bounds, transition clipping, caption chunking per language, popup timing), ASS output,
popup PNG size, a real FFmpeg render of a 3-scene mock project at proxy and 720p (skipped if
ffmpeg is missing) checking duration with ffprobe. Export: template cache, Pillow thumbnail
output sizes, SEO mock, provenance contents, export folder layout, similarity gate. API: file
serving with range requests and traversal refusal; upload endpoint. End to end:
`tests/test_pipeline_e2e.py` extended to run research -> export with all mocks and assert
`final_1080p.mp4` plus thumbnails and metadata exist (proxy and 720p are enough if 1080p is too
slow on CI; make the preset list configurable in the test).
