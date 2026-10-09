"""``evaluate(stage, context) -> [GateResult]``: the measurements a stage made, judged
against ``rules.yaml``.

Each rule id has a check function below. The stage builds a plain ``context`` dict with
its measurements (listed in each check's docstring), and gets back one row per rule of that
stage: ``{id, title, severity, passed, detail}``. ``detail`` is plain English for the review
panel. Thresholds live in the YAML file only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

RULES_FILE = Path(__file__).resolve().parent / "rules.yaml"

Severity = Literal["block", "warn"]


class Rule(BaseModel):
    id: str
    title: str
    stage: str
    severity: Severity
    description: str = ""
    source: str = ""
    last_verified: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class GateResult(BaseModel):
    id: str
    title: str
    severity: Severity
    passed: bool
    detail: str = ""


class RulesError(Exception):
    """``rules.yaml`` is missing, malformed or names a rule without a check."""


@lru_cache(maxsize=1)
def load_rules(path: Path | None = None) -> tuple[Rule, ...]:
    file = path or RULES_FILE
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RulesError(f"The policy rules file {file} could not be read: {exc}") from exc
    rows = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RulesError(f"The policy rules file {file} has no rules.")
    rules = tuple(Rule.model_validate({**row, "last_verified": str(row.get("last_verified", ""))})
                  for row in rows)
    missing = [r.id for r in rules if r.id not in CHECKS]
    if missing:
        raise RulesError(f"No check is implemented for rule(s): {', '.join(missing)}.")
    return rules


def rules_for(stage: str) -> list[Rule]:
    return [r for r in load_rules() if r.stage == stage]


def rule(rule_id: str) -> Rule:
    for candidate in load_rules():
        if candidate.id == rule_id:
            return candidate
    raise RulesError(f"Unknown policy rule '{rule_id}'.")


def params(rule_id: str) -> dict[str, Any]:
    return dict(rule(rule_id).parameters)


def evaluate(stage: str, context: dict[str, Any]) -> list[GateResult]:
    """Every rule of ``stage`` judged against the stage's measurements."""
    results: list[GateResult] = []
    for item in rules_for(stage):
        passed, detail = CHECKS[item.id](item, context)
        results.append(
            GateResult(
                id=item.id, title=item.title, severity=item.severity, passed=passed, detail=detail
            )
        )
    return results


def blocking_reasons(results: list[GateResult]) -> list[str]:
    return [f"{r.title}: {r.detail}" if r.detail else r.title
            for r in results if r.severity == "block" and not r.passed]


def to_dicts(results: list[GateResult]) -> list[dict[str, Any]]:
    return [r.model_dump() for r in results]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _indexes(items: list[int]) -> str:
    return ", ".join(f"#{i + 1}" for i in items)


# Title checks ------------------------------------------------------------------------------
# context: {"variants": [{"title", "length", "similarity_to_source", "similarity_to_history",
#           "keywords_kept": bool, "flags": [str]}], "source_is_competitor": bool,
#           "history_count": int, "recommended_index": int | None}


def _variants(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in context.get("variants", []) if isinstance(v, dict)]


def check_title_count(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    wanted = int(r.parameters.get("count", 7))
    count = len(_variants(context))
    if count == wanted:
        return True, f"{count} options."
    return False, f"{count} options instead of {wanted}."


def check_title_length(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    limit = int(r.parameters.get("max_chars", 70))
    long = [i for i, v in enumerate(_variants(context)) if int(v.get("length", 0)) >= limit]
    if not long:
        return True, f"All options are under {limit} characters."
    return False, f"Too long ({limit}+ characters): {_indexes(long)}."


def check_title_similarity_source(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    if not context.get("source_is_competitor", True):
        return True, "Own topic; no competitor title to compare with."
    limit = float(r.parameters.get("max_ratio", 0.8))
    close = [i for i, v in enumerate(_variants(context))
             if float(v.get("similarity_to_source", 0.0)) >= limit]
    if not close:
        return True, f"No option is {_pct(limit)} or more similar to the source title."
    return False, f"Too close to the competitor's title: {_indexes(close)}."


def check_title_similarity_history(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    limit = float(r.parameters.get("max_ratio", 0.8))
    history = int(context.get("history_count", 0))
    if history == 0:
        return True, "No recent titles on this channel yet."
    close = [i for i, v in enumerate(_variants(context))
             if float(v.get("similarity_to_history", 0.0)) >= limit]
    if not close:
        return True, f"No option repeats one of the last {history} titles."
    return False, f"Too close to a recent title of this channel: {_indexes(close)}."


def check_title_keywords(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    missing = [i for i, v in enumerate(_variants(context)) if not v.get("keywords_kept", False)]
    if not missing:
        return True, "Every option keeps a keyword from the source title."
    return False, f"No source keyword kept: {_indexes(missing)}."


def check_title_recommendable(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    clean = [i for i, v in enumerate(_variants(context)) if not v.get("flags")]
    if not clean:
        return False, "Every option failed at least one check; ask for new options."
    return True, f"{len(clean)} of {len(_variants(context))} options pass every check."


# Script checks -----------------------------------------------------------------------------
# context: {"ngram_overlap_source", "ngram_overlap_history_max", "source_compared": bool,
#           "history_compared": int, "advisory_persona", "sensitive_topic", "word_count",
#           "target_words", "title_claim_early": bool | None, "reasons": [str]}


def check_script_ngram_source(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    if not context.get("source_compared", False):
        return True, "No competitor transcript to compare with."
    limit = float(r.parameters.get("max_overlap", 0.02))
    value = float(context.get("ngram_overlap_source", 0.0))
    n = int(r.parameters.get("n", 8))
    if value <= limit:
        return True, (
            f"{_pct(value)} of {n}-word sequences also appear in the transcript "
            f"(limit {_pct(limit)})."
        )
    return False, (
        f"{_pct(value)} of {n}-word sequences copy the competitor transcript "
        f"(limit {_pct(limit)})."
    )


def check_script_ngram_history(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    compared = int(context.get("history_compared", 0))
    if compared == 0:
        return True, "No earlier scripts on this channel to compare with."
    limit = float(r.parameters.get("max_overlap", 0.05))
    value = float(context.get("ngram_overlap_history_max", 0.0))
    if value <= limit:
        return True, (
            f"Highest overlap with the last {compared} scripts: {_pct(value)} "
            f"(limit {_pct(limit)})."
        )
    return False, (
        f"{_pct(value)} overlap with one of the last {compared} scripts (limit {_pct(limit)})."
    )


def check_script_advisory_persona(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    if context.get("advisory_persona", False):
        reasons = [str(x) for x in context.get("reasons", [])]
        return False, (reasons[0] if reasons else "The narrator gives the viewer advice.")
    return True, "The narrator tells a story and gives no advice."


def check_script_word_count(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    tolerance = float(r.parameters.get("tolerance", 0.15))
    count = int(context.get("word_count", 0))
    target = int(context.get("target_words", 0))
    if target <= 0:
        return True, f"{count} words (no target)."
    low, high = round(target * (1 - tolerance)), round(target * (1 + tolerance))
    if low <= count <= high:
        return True, f"{count} words (target {target}, allowed {low}-{high})."
    return False, f"{count} words; the target is {target} (allowed {low}-{high})."


def check_script_title_claim_early(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    value = context.get("title_claim_early")
    share = float(r.parameters.get("first_share", 0.2))
    if value is None:
        return True, "Not checked."
    if value:
        return True, f"The title's promise appears in the first {_pct(share)} of the script."
    return False, f"The title's promise does not appear in the first {_pct(share)} of the script."


def check_script_sensitive_topic(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    if context.get("sensitive_topic", False):
        return False, "The script centres on an advertiser-sensitive subject; ads may be limited."
    return True, "No advertiser-sensitive subject found."


# Storyboard checks -------------------------------------------------------------------------
# context: {"format": "long"|"shorts", "scenes": [{"est_duration_s", "motion_preset",
#           "transition_type", "popup_text", "on_screen_text", "narration", "image_prompt"}],
#           "known_transitions": [str]}


def _scenes(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in context.get("scenes", []) if isinstance(s, dict)]


def scene_band(fmt: str, parameters: dict[str, Any] | None = None) -> tuple[float, float]:
    parameters = parameters if parameters is not None else params("storyboard.scene_length")
    band = parameters.get("shorts" if fmt == "shorts" else "long") or {}
    return float(band.get("min_s", 8.0)), float(band.get("max_s", 12.0))


def check_storyboard_scene_length(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    scenes = _scenes(context)
    if not scenes:
        return False, "The storyboard has no scenes."
    low, high = scene_band(str(context.get("format", "long")), r.parameters)
    in_band = [s for s in scenes if low - 0.01 <= float(s.get("est_duration_s", 0)) <= high + 0.01]
    share = len(in_band) / len(scenes)
    need = float(r.parameters.get("min_share_in_band", 0.8))
    detail = f"{len(in_band)} of {len(scenes)} scenes are {low:g}-{high:g} s long."
    return share >= need, detail


def check_storyboard_motion_alternates(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    scenes = _scenes(context)
    repeats = [i for i in range(1, len(scenes))
               if scenes[i].get("motion_preset") == scenes[i - 1].get("motion_preset")]
    if not repeats:
        return True, "Every scene moves differently from the one before."
    return False, f"Same motion as the previous scene: {_indexes(repeats)}."


def check_storyboard_transition_no_repeat(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    scenes = _scenes(context)
    repeats = [i for i in range(1, len(scenes))
               if scenes[i].get("transition_type") == scenes[i - 1].get("transition_type")]
    if not repeats:
        return True, "No transition is used twice in a row."
    return False, f"Same transition as the previous scene: {_indexes(repeats)}."


def check_storyboard_transition_known(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    known = set(context.get("known_transitions", []))
    unknown = [i for i, s in enumerate(_scenes(context)) if s.get("transition_type") not in known]
    if not unknown:
        return True, "All transitions come from the approved list."
    return False, f"Transition not in the approved list: {_indexes(unknown)}."


def _norm(text: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


def check_storyboard_popup_words(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    limit = int(r.parameters.get("max_words", 6))
    bad: list[int] = []
    for i, s in enumerate(_scenes(context)):
        text = (s.get("popup_text") or "").strip()
        if not text:
            continue
        if len(text.split()) > limit or _norm(text) == _norm(s.get("narration")):
            bad.append(i)
    if not bad:
        return True, f"Every popup has at most {limit} words and paraphrases the narration."
    return False, f"Popup too long or identical to the narration: {_indexes(bad)}."


def check_storyboard_popup_share(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    scenes = _scenes(context)
    if not scenes:
        return False, "The storyboard has no scenes."
    need = float(r.parameters.get("min_share", 0.35))
    with_text = sum(1 for s in scenes if (s.get("popup_text") or "").strip()
                    or (s.get("on_screen_text") or "").strip())
    share = with_text / len(scenes)
    detail = f"{with_text} of {len(scenes)} scenes carry text ({_pct(share)}; needed {_pct(need)})."
    return share + 1e-9 >= need, detail


def check_storyboard_prompt_text_free(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    patterns = [re.compile(str(p), re.IGNORECASE) for p in r.parameters.get("banned_patterns", [])]
    bad = [i for i, s in enumerate(_scenes(context))
           if any(p.search(str(s.get("image_prompt", ""))) for p in patterns)]
    if not bad:
        return True, "No prompt asks for text or logos in the picture."
    return False, f"Prompt asks for text or a logo: {_indexes(bad)}."


# Images checks -----------------------------------------------------------------------------
# context: {"scenes": [{"scene": int, "accepted": bool, "attempts": int, "reason": str}],
#           "duplicates": [{"scenes": [int, int], "distance": int}],
#           "monthly_budget": int | None, "used_this_month": int, "generated_now": int}
# (scene numbers in the details are 1-based, like the file names scene_NN.png)


def _image_scenes(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in context.get("scenes", []) if isinstance(s, dict)]


def check_images_qa(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    scenes = _image_scenes(context)
    if not scenes:
        return False, "No scenes to make pictures for."
    missing = [s for s in scenes if not s.get("accepted", False)]
    if not missing:
        return True, f"All {len(scenes)} scenes have an accepted picture."
    retries = int(r.parameters.get("max_retries", 3))
    parts = []
    for s in missing[:6]:
        number = int(s.get("scene", 0)) + 1
        reason = str(s.get("reason") or "no picture").strip().rstrip(".")
        parts.append(f"#{number} ({reason})")
    more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
    return False, (
        f"{len(missing)} of {len(scenes)} scenes have no accepted picture after up to "
        f"{retries + 1} tries: {', '.join(parts)}{more}."
    )


def check_images_variety(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    limit = int(r.parameters.get("max_distance", 6))
    pairs = [p for p in context.get("duplicates", []) if isinstance(p, dict)]
    if not pairs:
        return True, f"No two pictures are within {limit} bits of each other."
    labels = []
    for p in pairs[:6]:
        scenes = [int(i) + 1 for i in p.get("scenes", [])][:2]
        if len(scenes) == 2:
            labels.append(f"#{scenes[0]} and #{scenes[1]} ({int(p.get('distance', 0))})")
    more = f" and {len(pairs) - 6} more" if len(pairs) > 6 else ""
    return False, f"Pictures that look the same (distance): {', '.join(labels)}{more}."


def check_images_budget(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    budget = context.get("monthly_budget")
    used = int(context.get("used_this_month", 0))
    now = int(context.get("generated_now", 0))
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        return True, f"No monthly image budget set; {now} picture(s) made in this run."
    total = used + now
    if total <= budget:
        return True, f"{total} of {budget} pictures used this month ({now} in this run)."
    return False, (
        f"Over the monthly image budget: {total} of {budget} pictures used this month "
        f"({now} in this run)."
    )


# Export checks -----------------------------------------------------------------------------
# context: {"thumbnail_distance": int | None (pHash bits between the chosen thumbnail and the
#           competitor's), "competitor_thumbnail": bool, "title_promise_early": bool | None,
#           "title_promise_note": str, "presets": [str], "missing_presets": [str]}


def check_export_thumbnail_similarity(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    minimum = int(r.parameters.get("min_distance", 12))
    distance = context.get("thumbnail_distance")
    if not context.get("competitor_thumbnail", False):
        return True, "No competitor thumbnail to compare with."
    if distance is None:
        return True, "The competitor thumbnail has no detail to compare with."
    distance = int(distance)
    if distance > minimum:
        return True, (
            f"The thumbnail differs from the competitor's by {distance} of 64 bits "
            f"(more than {minimum} needed)."
        )
    return False, (
        f"The thumbnail looks too much like the competitor's: {distance} of 64 bits differ, "
        f"more than {minimum} are needed. Change the headline or the picture."
    )


def check_export_title_promise(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    value = context.get("title_promise_early")
    note = str(context.get("title_promise_note") or "").strip()
    share = float(r.parameters.get("first_share", 0.2))
    if value is None:
        return True, "Not checked."
    if value:
        return True, note or (
            f"The title's promise appears in the first {_pct(share)} of the script."
        )
    return False, note or (
        f"The title's promise does not appear in the first {_pct(share)} of the script."
    )


def check_export_files_present(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    presets = [str(p) for p in context.get("presets", [])]
    missing = [str(p) for p in context.get("missing_presets", [])]
    if not presets:
        return False, "No video preset was selected for the export."
    if not missing:
        return True, f"Every selected video file is there: {', '.join(presets)}."
    return False, (
        f"Missing rendered video for: {', '.join(missing)}. Render it in the edit step "
        "(or drop the preset) and run the export again."
    )


# Edit checks -------------------------------------------------------------------------------
# context: {"music_path": str | None, "music_license_ok": bool,
#           "renders": [{"preset": str, "expected_s": float, "actual_s": float | None}],
#           "true_peak_dbtp": float | None (None = silent or not measured)}


def check_edit_music_license(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    path = context.get("music_path")
    if not path:
        return True, "No background music in this video."
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    if context.get("music_license_ok", False):
        return True, f"The music track {name} has a licence file."
    return False, (
        f"The music track {name} has no licence file. Put {name}.license.txt next to it (or a "
        "LICENSE file in the music folder), or choose another track."
    )


def check_edit_duration(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    tolerance = float(r.parameters.get("tolerance_s", 1.0))
    renders = [x for x in context.get("renders", []) if isinstance(x, dict)]
    if not renders:
        return False, "No video was rendered."
    bad: list[str] = []
    for item in renders:
        expected = item.get("expected_s")
        actual = item.get("actual_s")
        label = str(item.get("preset", "?"))
        if not isinstance(expected, int | float) or not isinstance(actual, int | float):
            bad.append(f"{label} (length unknown)")
        elif abs(float(actual) - float(expected)) > tolerance:
            bad.append(f"{label} ({float(actual):.1f} s instead of {float(expected):.1f} s)")
    if not bad:
        return True, (
            f"All {len(renders)} rendered file(s) are within {tolerance:g} s of the timeline."
        )
    return False, "Rendered length is off: " + ", ".join(bad) + "."


def check_edit_audio_peaks(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    limit = float(r.parameters.get("max_true_peak_dbtp", -1.0))
    peak = context.get("true_peak_dbtp")
    if not isinstance(peak, int | float):
        return True, "No audio peak to measure (silent audio or not measured)."
    if float(peak) <= limit:
        return True, f"True peak {float(peak):.1f} dBTP (limit {limit:g} dBTP)."
    return False, f"True peak {float(peak):.1f} dBTP is above {limit:g} dBTP; the audio may clip."


CHECKS: dict[str, Callable[[Rule, dict[str, Any]], tuple[bool, str]]] = {
    "title.count": check_title_count,
    "title.length": check_title_length,
    "title.similarity_source": check_title_similarity_source,
    "title.similarity_history": check_title_similarity_history,
    "title.keywords": check_title_keywords,
    "title.recommendable": check_title_recommendable,
    "script.ngram_source": check_script_ngram_source,
    "script.ngram_history": check_script_ngram_history,
    "script.advisory_persona": check_script_advisory_persona,
    "script.word_count": check_script_word_count,
    "script.title_claim_early": check_script_title_claim_early,
    "script.sensitive_topic": check_script_sensitive_topic,
    "storyboard.scene_length": check_storyboard_scene_length,
    "storyboard.motion_alternates": check_storyboard_motion_alternates,
    "storyboard.transition_no_repeat": check_storyboard_transition_no_repeat,
    "storyboard.transition_known": check_storyboard_transition_known,
    "storyboard.popup_words": check_storyboard_popup_words,
    "storyboard.popup_share": check_storyboard_popup_share,
    "storyboard.prompt_text_free": check_storyboard_prompt_text_free,
    "images.qa": check_images_qa,
    "images.variety": check_images_variety,
    "images.budget": check_images_budget,
    "edit.music_license": check_edit_music_license,
    "edit.duration": check_edit_duration,
    "edit.audio_peaks": check_edit_audio_peaks,
    "export.thumbnail_similarity": check_export_thumbnail_similarity,
    "export.title_promise": check_export_title_promise,
    "export.files_present": check_export_files_present,
}


# Voice checks ------------------------------------------------------------------------------
# context: {"provider": str, "is_clone": bool, "consent_found": bool, "consent_ref": str,
#           "consent_error": str, "owner_name": str, "duration_s": float, "target_s": float}


def check_voice_consent(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    exempt = {str(p) for p in r.parameters.get("exempt_providers", ["mock"])}
    provider = str(context.get("provider") or "")
    if provider in exempt:
        return True, "The mock voice is nobody's voice, so no consent is needed."
    if not context.get("is_clone", False):
        return True, (
            "A stock voice is used (no clone, no sample recording), so no consent is needed."
        )
    if context.get("consent_found", False):
        owner = str(context.get("owner_name") or "the voice owner")
        where = str(context.get("consent_ref") or "the channel")
        return True, f"Consent from {owner} is recorded ({where})."
    error = str(context.get("consent_error") or "")
    if error:
        return False, f"The consent record could not be used: {error}"
    return False, (
        "No consent record for the cloned voice. Fill in the Consent fields in the channel's "
        "Voice tab (owner, recorded by, date) or put a consent.json next to the voice sample "
        "(owner_name, consented_by, consented_at)."
    )


def check_voice_duration(r: Rule, context: dict[str, Any]) -> tuple[bool, str]:
    tolerance = float(r.parameters.get("tolerance", 0.25))
    duration = float(context.get("duration_s", 0.0) or 0.0)
    target = float(context.get("target_s", 0.0) or 0.0)
    if target <= 0:
        return True, f"{duration:.1f} s of narration (no target length)."
    low, high = target * (1 - tolerance), target * (1 + tolerance)
    detail = (
        f"{duration:.1f} s of narration; target {target:.0f} s (allowed {low:.0f}-{high:.0f} s)."
    )
    return low <= duration <= high, detail


CHECKS.update(
    {
        "voice.consent": check_voice_consent,
        "voice.duration": check_voice_duration,
    }
)
