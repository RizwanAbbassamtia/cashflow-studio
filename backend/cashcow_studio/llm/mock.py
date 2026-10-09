"""``CCS_LLM_PROVIDER=mock``: schema-valid answers without the network.

Every answer is derived from the stage's own inputs (the ``variables`` the stage rendered its
prompt from), so it is deterministic: the title options embed the source keywords, the script
repeats the title in its hook and lands on the word target, the storyboard groups the real
sentence ids. Tests and offline demos run on this; the real client ignores ``variables``.

The storyboard draft deliberately breaks a few rules (an over-long popup, a repeated
transition and motion) so the stage's rule fix-ups are exercised end to end.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .client import BaseLLMClient, LLMError, LLMUsage
from .config import LLMConfig
from .text import keywords_of

T = TypeVar("T", bound=BaseModel)

MOCK_MODEL = "mock"

TITLE_TEMPLATES = (
    "The {k1} {k2} Story Nobody Talks About",
    "What a {k1} Taught Me About {k2}",
    "Why This {k1} Still Matters Today",
    "The Truth Behind the {k1} {k2}",
    "{k1} vs {k2}: The Part They Hide",
    "I Watched One {k1} Moment Change a {k2}",
    "How a Quiet {k1} Became a {k2} Lesson",
)
TITLE_FORMULAS = (
    "The [subject] story nobody talks about",
    "What [subject] taught me about [theme]",
    "Why this [subject] still matters today",
    "The truth behind [subject]",
    "[A] vs [B]: the part they hide",
    "I watched one [moment] change a [life]",
    "How a quiet [subject] became a [theme] lesson",
)
TITLE_SCORES = (8, 7, 9, 6, 7, 8, 6)
TITLE_EMOTIONS = ("warmth", "surprise", "hope", "regret", "pride", "awe", "calm")

SCRIPT_SECTIONS_LONG = (
    ("Hook", "Open on the promise of the title and make the viewer stay."),
    ("Setup", "Introduce the people, the place and what is at stake."),
    ("Turning point", "The moment everything changes."),
    ("Resolution", "What happened next and what it cost."),
    ("Takeaway", "The quiet lesson, without preaching."),
)
SCRIPT_SECTIONS_SHORTS = (
    ("Hook", "One line that makes the thumb stop."),
    ("Story", "The whole arc in three or four beats."),
    ("Payoff", "The twist and a reason to watch again."),
)
SENTENCE_TEMPLATES = (
    "Here is how {topic} began, and {kw} is the detail everyone misses.",
    "Nobody expected {topic} to matter, yet {kw} changed the whole room.",
    "Picture the morning when {topic} started, because {kw} was already waiting.",
    "People still ask about {topic}, and {kw} is the honest answer.",
    "For a long time {topic} looked ordinary until {kw} proved otherwise.",
    "Think about {topic} for a second, since {kw} explains the rest.",
    "Something small about {topic} stayed hidden while {kw} grew louder.",
    "Every story like {topic} has a turn, and here {kw} was that turn.",
    "What makes {topic} unforgettable is how {kw} refused to fade.",
    "Later on {topic} would make sense, but {kw} came first.",
    "Few noticed {topic} at the time, though {kw} left a mark on everyone.",
    "Remember {topic} as you watch this, because {kw} returns at the end.",
)
NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
    70: "seventy", 80: "eighty", 90: "ninety",
}
ABBREVIATIONS = {
    "mr": "Mister", "mrs": "Missus", "dr": "Doctor", "st": "Saint", "vs": "versus",
    "etc": "et cetera", "approx": "approximately", "km": "kilometres", "kg": "kilograms",
}
ADVISORY_RE = re.compile(
    r"\b(you should (buy|sell|invest|take|stop taking)|as your (doctor|adviser|advisor|lawyer|"
    r"accountant)|i recommend (you|that you)|my advice (is|to you))\b",
    re.IGNORECASE,
)
SENSITIVE_RE = re.compile(
    r"\b(suicide|self[- ]harm|overdose|gambling|crypto|stock tips|diagnos\w*|medication|"
    r"lawsuit|election|terror\w*)\b",
    re.IGNORECASE,
)


def _seed(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def number_to_words(value: int) -> str:
    if value < 0:
        return "minus " + number_to_words(-value)
    if value in NUMBER_WORDS:
        return NUMBER_WORDS[value]
    if value < 100:
        tens, rest = divmod(value, 10)
        return f"{NUMBER_WORDS[tens * 10]}-{NUMBER_WORDS[rest]}"
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        tail = f" and {number_to_words(rest)}" if rest else ""
        return f"{NUMBER_WORDS[hundreds]} hundred{tail}"
    if value < 1_000_000:
        thousands, rest = divmod(value, 1000)
        tail = f" {number_to_words(rest)}" if rest else ""
        return f"{number_to_words(thousands)} thousand{tail}"
    millions, rest = divmod(value, 1_000_000)
    tail = f" {number_to_words(rest)}" if rest else ""
    return f"{number_to_words(millions)} million{tail}"


def year_to_words(value: int) -> str:
    """1998 -> nineteen ninety-eight, 2007 -> two thousand and seven, 2019 -> twenty nineteen."""
    high, low = divmod(value, 100)
    if 2000 <= value <= 2009:
        return "two thousand" + (f" and {NUMBER_WORDS[low]}" if low else "")
    if low == 0:
        return f"{number_to_words(high)} hundred"
    return f"{number_to_words(high)} {number_to_words(low)}"


def _number_match(match: re.Match[str]) -> str:
    raw = match.group(1)
    value = int(raw.replace(",", ""))
    if "," not in raw and len(raw) == 4 and 1100 <= value <= 2099:
        return year_to_words(value)
    return number_to_words(value)


def spell_out(text: str) -> str:
    """A small, deterministic stand-in for the speech-normalisation prompt."""
    out = text.replace("&", " and ")
    out = re.sub(r"\$\s?(\d[\d,]*)", lambda m: f"{m.group(1)} dollars", out)
    out = re.sub(r"(\d[\d,]*)\s?%", lambda m: f"{m.group(1)} percent", out)
    out = re.sub(r"\b(\d{1,3}(?:,\d{3})+|\d+)\b", _number_match, out)

    def abbreviation(match: re.Match[str]) -> str:
        word = match.group(1)
        return ABBREVIATIONS.get(word.lower(), word)

    out = re.sub(r"\b([A-Za-z]{2,6})\.(?=\s|$)", abbreviation, out)
    return re.sub(r"\s{2,}", " ", out).strip()


class MockLLMClient(BaseLLMClient):
    provider = "mock"

    def __init__(self, config: LLMConfig | None = None, app_data_dir: Path | None = None) -> None:
        super().__init__(config or LLMConfig.load(), app_data_dir)
        self.calls: list[dict[str, Any]] = []
        """Every call as ``{task, model, variables}`` so tests can inspect what the stage sent."""

    async def complete(
        self,
        task: str,
        system: str | Sequence[str],
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        variables: dict[str, Any] | None = None,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> tuple[T, LLMUsage]:
        started = time.monotonic()
        variables = dict(variables or {})
        requested = self.model_for(task, model)
        self.calls.append({"task": task, "model": requested, "variables": variables})
        generator = GENERATORS.get(task)
        try:
            data = generator(variables) if generator else _empty_instance(schema)
            parsed = schema.model_validate(data)
        except ValidationError as exc:
            raise LLMError(f"The mock answer for '{task}' did not fit its schema: {exc}") from exc
        system_text = system if isinstance(system, str) else "\n".join(system)
        output_text = parsed.model_dump_json()
        usage = LLMUsage(
            task=task,
            model=MOCK_MODEL,
            requested_model=requested,
            input_tokens=max(1, (len(system_text) + len(user)) // 4),
            output_tokens=max(1, len(output_text) // 4),
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            request_id=f"mock-{_seed(task, user) % 1_000_000:06d}",
        )
        self.record(usage, project_id=project_id, stage=stage)
        return parsed, usage

    async def analyze_image(
        self,
        task: str,
        image_path: Path,
        prompt: str,
        schema: type[T],
        model: str | None = None,
        *,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> T:
        """A pass verdict for any readable picture; a fail when the prompt says ``FAIL_QA``.

        ``FAIL_QA_ONCE`` fails only the first try: once the stage's retry wording ("rejected")
        is in the prompt, the picture passes, so the rejection-and-retry path can be tested.
        """
        started = time.monotonic()
        requested = self.model_for(task, model)
        path = Path(image_path)
        try:
            from PIL import Image

            with Image.open(path) as image:
                size = image.size
        except OSError as exc:
            raise LLMError(f"The mock could not read the picture {path.name}: {exc}") from exc
        self.calls.append({"task": task, "model": requested, "image": str(path), "size": size,
                           "prompt": prompt})
        generator = IMAGE_GENERATORS.get(task)
        try:
            data = generator(prompt) if generator else _empty_instance(schema)
            parsed = schema.model_validate(data)
        except ValidationError as exc:
            raise LLMError(f"The mock answer for '{task}' did not fit its schema: {exc}") from exc
        usage = LLMUsage(
            task=task,
            model=MOCK_MODEL,
            requested_model=requested,
            input_tokens=max(1, len(prompt) // 4) + 1500,  # a picture costs about 1500 tokens
            output_tokens=max(1, len(parsed.model_dump_json()) // 4),
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            request_id=f"mock-{_seed(task, path.name, prompt) % 1_000_000:06d}",
        )
        self.last_usage = usage
        self.record(usage, project_id=project_id, stage=stage)
        return parsed


def _empty_instance(schema: type[BaseModel]) -> dict[str, Any]:
    """For a task the mock does not know: every list empty, every string blank."""
    data: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        if info.is_required():
            annotation = str(info.annotation)
            if "list" in annotation:
                data[name] = []
            elif "bool" in annotation:
                data[name] = False
            elif "int" in annotation or "float" in annotation:
                data[name] = 0
            else:
                data[name] = ""
    return data


# Generators, one per prompt file -----------------------------------------------------------


def mock_title(variables: dict[str, Any]) -> dict[str, Any]:
    source = str(variables.get("source_title") or variables.get("topic") or "A quiet story")
    raw_keywords = variables.get("keywords_list")
    if raw_keywords is None and isinstance(variables.get("keywords"), list):
        raw_keywords = variables["keywords"]
    keywords = [str(k) for k in (raw_keywords or [])] or keywords_of(source)
    if not keywords:
        keywords = ["Story"]
    if len(keywords) == 1:
        keywords.append("Lesson")
    caps = [k[:1].upper() + k[1:] for k in keywords]
    variants = []
    for index, template in enumerate(TITLE_TEMPLATES):
        k1 = caps[index % len(caps)]
        k2 = caps[(index + 1) % len(caps)]
        title = template.format(k1=k1, k2=k2)[:69].rstrip()
        kept = [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", title, re.IGNORECASE)]
        variants.append(
            {
                "title": title,
                "formula": TITLE_FORMULAS[index],
                "emotional_trigger": TITLE_EMOTIONS[index],
                "curiosity_trigger": "an unanswered question in the title",
                "hidden_gap": f"the viewer does not know what {k1.lower()} led to",
                "viral_score": TITLE_SCORES[index],
                "why_it_outperforms": f"Keeps '{k1}' from the proven title and adds a new angle.",
                "keywords_kept": kept,
            }
        )
    return {"variants": variants, "recommended_index": 2}


def mock_transcript_summary(variables: dict[str, Any]) -> dict[str, Any]:
    text = str(variables.get("transcript_text") or "")
    words = len(text.split())
    minutes = max(1, round(words / 150)) if words else 0
    beats = [
        {"name": "Hook", "purpose": "a question that promises a reveal", "share_percent": 10},
        {"name": "Setup", "purpose": "who, where, and what is at stake", "share_percent": 30},
        {"name": "Turn", "purpose": "the moment the situation flips", "share_percent": 40},
        {"name": "Ending", "purpose": "the result and a soft call to watch more",
         "share_percent": 20},
    ]
    return {
        "beats": beats,
        "hook_style": "a direct question to the viewer",
        "pacing": f"about {minutes} minute(s); short sentences, one idea per sentence",
        "devices": ["question hook", "time jump", "callback to the opening"],
        "ending_style": "a reflective line, then a nudge to the next video",
        "notes": "Structure only; no sentences from the source are kept.",
    }


def mock_script(variables: dict[str, Any]) -> dict[str, Any]:
    title = str(variables.get("title") or "An untitled story")
    target = int(variables.get("target_words") or 300)
    fmt = str(variables.get("format") or "long")
    plan = SCRIPT_SECTIONS_SHORTS if fmt == "shorts" else SCRIPT_SECTIONS_LONG
    count = max(1, min(len(plan), target // 40))
    plan = plan[:count]
    keywords = keywords_of(title) or ["story"]
    topic = " ".join(keywords[:4]).lower()
    rng = random.Random(_seed("script", title, target, fmt))
    locked = [p for p in (variables.get("locked_paragraphs") or []) if isinstance(p, dict)]
    locked_words = sum(len(str(p.get("text", "")).split()) for p in locked)

    cumulative = []
    running = 0
    weights = [2, 3, 3, 3, 2][:count] if fmt != "shorts" else [1, 3, 1][:count]
    total_weight = sum(weights)
    for weight in weights:
        running += weight
        cumulative.append(round(target * running / total_weight))

    sections = []
    words = locked_words
    for index, (name, purpose) in enumerate(plan):
        paragraphs: list[dict[str, Any]] = []
        for lock in locked:
            if str(lock.get("section", "")).lower() == name.lower():
                paragraphs.append({"text": str(lock.get("text", "")), "locked_id": lock.get("id")})
        if index == 0:
            hook = f"{title}. That is the promise, and this is the story behind it."
            paragraphs.append({"text": hook, "locked_id": None})
            words += len(hook.split())
        sentences: list[str] = []
        guard = 0
        while (words + 6 < cumulative[index] or not sentences and not paragraphs) and guard < 200:
            guard += 1
            kw = keywords[rng.randrange(len(keywords))].lower()
            template = SENTENCE_TEMPLATES[rng.randrange(len(SENTENCE_TEMPLATES))]
            sentence = template.format(topic=topic, kw=kw)
            sentences.append(sentence)
            words += len(sentence.split())
            if len(sentences) >= 3:
                paragraphs.append({"text": " ".join(sentences), "locked_id": None})
                sentences = []
        if sentences:
            paragraphs.append({"text": " ".join(sentences), "locked_id": None})
        sections.append({"name": name, "purpose": purpose, "paragraphs": paragraphs})
    # Locked paragraphs whose section no longer exists go to the last section.
    known = {s["name"].lower() for s in sections}
    for lock in locked:
        if str(lock.get("section", "")).lower() not in known:
            sections[-1]["paragraphs"].append(
                {"text": str(lock.get("text", "")), "locked_id": lock.get("id")}
            )
    return {"sections": sections}


def mock_speech_normalize(variables: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in variables.get("sentences") or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        rows.append({"id": str(item["id"]), "speech_text": spell_out(str(item.get("text", "")))})
    return {"sentences": rows}


def mock_storyboard(variables: dict[str, Any]) -> dict[str, Any]:
    sentences = [s for s in (variables.get("sentences") or []) if isinstance(s, dict)]
    min_s = float(variables.get("min_s") or 8.0)
    max_s = float(variables.get("max_s") or 12.0)
    transitions = [str(t) for t in (variables.get("transition_types") or ["fade", "wipeleft"])]
    motions = [str(m) for m in (variables.get("motion_presets") or ["zoom_in", "pan_right"])]
    positions = [str(p) for p in (variables.get("popup_positions") or ["bottom-left"])]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_s = 0.0
    for sentence in sentences:
        est = float(sentence.get("est_s") or 1.0)
        if current and current_s + est > max_s and current_s >= min_s * 0.75:
            groups.append(current)
            current, current_s = [], 0.0
        current.append(sentence)
        current_s += est
        if current_s >= min_s and current_s <= max_s:
            groups.append(current)
            current, current_s = [], 0.0
    if current:
        groups.append(current)
    scenes = []
    for index, group in enumerate(groups):
        narration = " ".join(str(s.get("text", "")) for s in group)
        picture = " ".join(re.findall(r"[A-Za-z']+", narration)[:8]).lower()
        words = keywords_of(narration, limit=3)
        popup = " ".join(words) if index % 2 == 0 and words else None
        if index == 0:
            popup = "This is a deliberately long popup line of eight words"  # fixed by the stage
        scenes.append(
            {
                "sentence_ids": [str(s["id"]) for s in group],
                "image_prompt": "A cinematic, text-free scene that shows "
                f"{picture or 'the moment'}, soft natural light, no people's faces, no logos.",
                "negative_prompt": "text, letters, watermark",
                "popup_text": popup,
                "popup_position": positions[index % len(positions)],
                # scenes 1 and 2 share a motion; scenes 0 and 1 share a transition: on purpose
                "motion_preset": motions[(index if index != 2 else 1) % len(motions)],
                "transition_type": transitions[(index if index != 1 else 0) % len(transitions)],
                "on_screen_text": None,
            }
        )
    return {"scenes": scenes}


def mock_policy_check(variables: dict[str, Any]) -> dict[str, Any]:
    text = str(variables.get("script_text") or "")
    title = str(variables.get("title") or "")
    reasons: list[str] = []
    advisory = bool(ADVISORY_RE.search(text))
    if advisory:
        reasons.append(
            "The narrator speaks like a personal adviser (for example 'you should buy')."
        )
    sensitive = bool(SENSITIVE_RE.search(text))
    if sensitive:
        reasons.append("The script touches a subject YouTube treats as sensitive.")
    tokens = re.findall(r"\w+", text.lower())
    head = set(tokens[: max(1, len(tokens) // 5)])
    keys = [k.lower() for k in keywords_of(title)]
    early = any(k in head for k in keys) if keys else True
    return {
        "advisory_persona": advisory,
        "sensitive_topic": sensitive,
        "title_claim_early": early,
        "reasons": reasons,
        "semantic_note": "Mock check: the script tells its own story and does not follow the "
        "source sentence by sentence.",
    }


GENERATORS = {
    "title": mock_title,
    "transcript_summary": mock_transcript_summary,
    "script": mock_script,
    "speech_normalize": mock_speech_normalize,
    "storyboard": mock_storyboard,
    "policy_check": mock_policy_check,
}


# Vision generators, one per ``analyze_image`` task; they get the prompt text ----------------

FAIL_QA_MARKER = "FAIL_QA"
FAIL_QA_ONCE_MARKER = "FAIL_QA_ONCE"
RETRY_MARKER = "was rejected"
"""Part of the retry wording the images stage appends (config/images.yaml, prompt.retry_note)."""


def mock_image_qa(prompt: str) -> dict[str, Any]:
    """Pass, unless the prompt carries ``FAIL_QA`` (``FAIL_QA_ONCE``: only before a retry)."""
    fail = FAIL_QA_MARKER in prompt
    if FAIL_QA_ONCE_MARKER in prompt and RETRY_MARKER in prompt.lower():
        fail = False
    if fail:
        return {
            "matches_prompt": False,
            "has_text": "FAIL_QA_TEXT" in prompt,
            "has_real_person": "FAIL_QA_PERSON" in prompt,
            "has_logo": "FAIL_QA_LOGO" in prompt,
            "artifacts": ["warped shapes (mock)"],
            "score": 2,
            "reason": "Mock check: the prompt asked for a failure (FAIL_QA).",
        }
    return {
        "matches_prompt": True,
        "has_text": False,
        "has_real_person": False,
        "has_logo": False,
        "artifacts": [],
        "score": 8,
        "reason": "Mock check: the picture matches the scene and carries no text or logos.",
    }


IMAGE_GENERATORS = {
    "image_qa": mock_image_qa,
}


def mock_answer_json(task: str, variables: dict[str, Any]) -> str:
    """The mock's raw answer for a task, for debugging and the CLI."""
    generator = GENERATORS.get(task)
    if generator is None:
        raise LLMError(f"The mock has no answer for the task '{task}'.")
    return json.dumps(generator(variables), indent=2)
