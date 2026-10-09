"""The vision check of a generated picture: prompt, call, verdict and retry wording.

``check_image`` asks the LLM client (``analyze_image``, Claude Sonnet with the picture as a
base64 block before the text) for an :class:`ImageQA`. The picture is rejected when it does
not match the prompt, shows text, a real person or a logo, or scores under the minimum from
``policy/rules.yaml`` (``images.qa``). The reason is appended to the next generation prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..llm.prompts import load_prompt
from ..models.images import MIN_QA_SCORE, ImageQA

TASK = "image_qa"
STAGE = "images"


def qa_prompt(
    scene_prompt: str, negative: str, narration: str = "", previous_rejection: str = ""
) -> str:
    """System and user text of ``llm/prompts/image_qa.md`` as one block (sent after the image).

    ``previous_rejection`` is the retry sentence the stage also appends to the generation
    prompt ("The previous picture was rejected because ..."), so the checker knows what to
    look for; blank on the first try.
    """
    template = load_prompt(TASK)
    variables = {
        "scene_prompt": scene_prompt.strip() or "(no description)",
        "negative": negative.strip() or "(nothing beyond the standard rules)",
        "narration": narration.strip() or "(not given)",
        "previous_rejection": previous_rejection.strip() or "(none; this is the first try)",
    }
    return template.render("system", variables) + "\n" + template.render("user", variables)


async def check_image(
    llm: Any, image_path: Path, prompt: str, *, project_id: str | None = None
) -> tuple[ImageQA, float]:
    """The verdict for one picture and what the check cost (USD; the mock is free)."""
    verdict = await llm.analyze_image(
        task=TASK, image_path=Path(image_path), prompt=prompt, schema=ImageQA,
        project_id=project_id, stage=STAGE,
    )
    usage = getattr(llm, "last_usage", None)
    cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
    return verdict, cost


def join_reasons(reasons: list[str]) -> str:
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    return ", ".join(reasons[:-1]) + " and " + reasons[-1]


def verdict_text(qa: ImageQA | None, min_score: int = MIN_QA_SCORE) -> str:
    """One plain line for the review grid: ``Passed (8/10)`` or ``Rejected: it contains text``."""
    if qa is None:
        return "Not checked"
    reasons = qa.rejection_reasons(min_score)
    if not reasons:
        return f"Passed ({qa.score}/10)"
    return f"Rejected: {join_reasons(reasons)}"
