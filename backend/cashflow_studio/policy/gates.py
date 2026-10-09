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
}
