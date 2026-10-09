"""Export SEO pack: chapter times from the script sections and timing.json, YouTube limits,
the title-promise check, the mock (every field filled from the script) and a model answer."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from cashcow_studio.export import seo
from cashcow_studio.llm import LLMUsage, MockLLMClient
from cashcow_studio.llm.client import LLMError
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.export import Chapter, SeoLLMOutput, SeoPack
from cashcow_studio.models.script import ScriptDoc
from cashcow_studio.pipeline.stages.script import make_paragraph

TITLE = "The Waiter Who Never Forgot a Face"
SECTIONS = [
    ("Hook", ["The waiter who never forgot a face started with one coffee.",
              "Nobody in the diner knew what that cup would cost him."]),
    ("Setup", ["Every morning the same man sat in the corner booth.",
               "He paid in coins and never said a word."]),
    ("Turn", ["One winter the man stopped coming and a letter arrived instead.",
              "Inside was a key and a single line of thanks."]),
    ("Ending", ["The waiter kept the key on the wall above the till.",
                "He says a face is a promise you keep."]),
]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_script(sections=SECTIONS, fmt: str = "long") -> ScriptDoc:
    rows = []
    for si, (name, sentences) in enumerate(sections):
        paragraph = make_paragraph(si, 0, " ".join(sentences), False)
        rows.append({"id": f"sec-{si + 1:02d}", "name": name, "purpose": "",
                     "paragraphs": [paragraph.model_dump()]})
    return ScriptDoc.model_validate({
        "title": TITLE, "language": "English", "format": fmt, "target_words": 80,
        "word_count": 80, "model": "mock", "generated_at": datetime.now(UTC),
        "sections": rows,
    })


def make_timing(script: ScriptDoc, seconds: float = 12.0) -> dict[str, Any]:
    rows, clock = [], 0.0
    for sentence in script.sentences():
        rows.append({"id": sentence.id, "text": sentence.text, "start_s": clock,
                     "end_s": clock + seconds, "words": []})
        clock += seconds
    return {"sample_rate": 48000, "duration_s": clock, "source": "estimated", "sentences": rows}


def make_channel() -> Channel:
    return Channel.model_validate({
        "slug": "kind-ledger",
        "channel": {"name": "Kind Ledger", "niche": "Kindness stories", "audience": "Adults"},
    })


# Chapters -----------------------------------------------------------------------------------------


def test_format_and_parse_time() -> None:
    assert seo.format_time(0) == "00:00"
    assert seo.format_time(72.9) == "01:12"
    assert seo.format_time(3725) == "1:02:05"
    assert seo.parse_time("01:12") == 72.0
    assert seo.parse_time("1:02:05") == 3725.0
    assert seo.parse_time("soon") is None


def test_chapters_follow_the_voice_clock() -> None:
    script = make_script()
    chapters = seo.chapters_from(script, make_timing(script), "long")
    assert [(c.time, c.title) for c in chapters] == [
        ("00:00", "Hook"), ("00:24", "Setup"), ("00:48", "Turn"), ("01:12", "Ending"),
    ]
    # Shorts never get chapters; neither does a video with fewer than three usable ones.
    assert seo.chapters_from(script, make_timing(script), "shorts") == []
    assert seo.chapters_from(make_script(SECTIONS[:2]), None, "long") == []
    # Chapters closer than ten seconds to the previous one are dropped (YouTube ignores them).
    squeezed = seo.chapters_from(script, make_timing(script, seconds=6.0), "long")
    assert [c.time for c in squeezed] == ["00:00", "00:12", "00:24", "00:36"]
    tight = seo.chapters_from(script, make_timing(script, seconds=4.0), "long")
    assert tight == []  # 8 s apart: only the first survives, so none are shown
    # Without timing the starts are estimated from the word share of a given duration.
    estimated = seo.chapters_from(script, None, "long", duration_s=96.0)
    assert estimated[0].time == "00:00" and len(estimated) == 4
    assert all(seo.parse_time(c.time) is not None for c in estimated)


def test_merge_chapter_titles_keeps_the_computed_times() -> None:
    computed = [Chapter(time="00:00", title="Hook"), Chapter(time="00:24", title="Setup"),
                Chapter(time="00:48", title="Turn")]
    suggested = [Chapter(time="00:01", title="One coffee"), Chapter(time="00:30", title=""),
                 Chapter(time="00:50", title="The letter")]
    merged = seo.merge_chapter_titles(computed, suggested)
    assert [(c.time, c.title) for c in merged] == [
        ("00:00", "One coffee"), ("00:24", "Setup"), ("00:48", "The letter"),
    ]
    assert seo.merge_chapter_titles(computed, suggested[:2]) == computed


# Limits ---------------------------------------------------------------------------------------


def test_enforce_limits_applies_youtube_rules() -> None:
    notes: list[str] = []
    pack = SeoPack(
        title="x" * 140,
        description="d" * 6000,
        tags=[f"tag number {i} with some length" for i in range(60)]
        + ["Tag Number 0 With Some Length"],
        chapters=[Chapter(time="00:05", title="Start"), Chapter(time="00:40", title="Middle"),
                  Chapter(time="bad", title="x"), Chapter(time="01:10", title="End")],
        pinned_comment="  what   would you do?  ",
        hashtags=["#Kind ness", "waiter", "waiter", "#story"] + [f"t{i}" for i in range(20)],
    )
    fixed = seo.enforce_limits(pack, notes)
    assert len(fixed.title) == 100 and fixed.title.endswith("…")
    assert len(fixed.description) == 5000
    assert len(", ".join(fixed.tags)) <= 500 and len(fixed.tags) <= 25
    assert fixed.chapters[0].time == "00:00" and len(fixed.chapters) == 3
    assert fixed.pinned_comment == "what would you do?"
    assert fixed.hashtags[:3] == ["#Kindness", "#waiter", "#story"] and len(fixed.hashtags) <= 15
    assert any("title was cut" in n for n in notes) and any("Tags were trimmed" in n for n in notes)
    # Two chapters are too few: YouTube shows none, so the list is cleared with a note.
    few = seo.enforce_limits(SeoPack(title="t", chapters=pack.chapters[:2]), notes)
    assert few.chapters == [] and any("Fewer than three" in n for n in notes)


def test_title_promise_heuristic_and_tag_helpers() -> None:
    text = make_script().text()
    assert seo.title_promise_heuristic(TITLE, text) is True
    assert seo.title_promise_heuristic("Quantum Rockets Explained", text) is False
    assert seo.title_promise_heuristic("the and of", text) is True  # no keywords: nothing to miss
    assert seo.trim_tags(["a", "a", " A ", "b#c"], budget=5) == ["a", "bc"]
    assert seo.clean_hashtags(["#a b", "c", "#C"]) == ["#ab", "#c"]


# Building the pack -----------------------------------------------------------------------------


def test_mock_client_gives_a_complete_pack_from_the_script(app_env) -> None:
    script = make_script()
    llm = MockLLMClient(app_data_dir=app_env.app_data_dir)
    result = run(seo.build_seo_pack(
        llm, title=TITLE, script=script, timing=make_timing(script), channel=make_channel(),
        language="English", fmt="long", max_headline_words=4, project_id="p1",
        notes=["Mention the key"],
    ))
    pack = result.pack
    assert pack.title == TITLE
    assert "00:24 Setup" in pack.description and "Kind Ledger" in pack.description
    assert pack.tags and len(", ".join(pack.tags)) <= 500 and "Waiter" in pack.tags
    assert [c.time for c in pack.chapters] == ["00:00", "00:24", "00:48", "01:12"]
    assert pack.pinned_comment and pack.hashtags and pack.hashtags[0].startswith("#")
    assert result.title_promise_early is True and "keyword" in result.title_promise_note
    assert result.headlines == [] and result.from_model is False
    assert any("built from the script" in n for n in result.notes)
    assert result.model == "mock" and result.cost_usd == 0.0
    call = llm.calls[0]
    assert call["task"] == "seo" and call["model"] == "claude-sonnet-5-5"
    assert call["variables"]["chapters"][0] == "00:00 Hook"
    assert call["variables"]["notes"] == ["Mention the key"]
    assert json.dumps(pack.model_dump())


class AnsweringLLM:
    """A model client that returns a full SEO answer (times deliberately wrong)."""

    provider = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def complete(self, task: str, system: Any, user: str, schema: type, **kw: Any):
        self.calls.append({"task": task, "user": user, **kw})
        if self.fail:
            raise LLMError("Claude is busy right now.")
        output = SeoLLMOutput(
            title="He Never Forgot a Face",
            description="Un camarero que nunca olvida.\n\n00:00 Inicio",
            tags=["camarero", "historia", "bondad"],
            chapters=[Chapter(time="00:02", title="Un café"),
                      Chapter(time="00:30", title="La cabina"),
                      Chapter(time="00:50", title="La carta"),
                      Chapter(time="01:15", title="La llave")],
            pinned_comment="¿Qué habrías hecho tú?",
            hashtags=["camarero", "bondad"],
            headlines=["Nunca Olvidó", "La Llave Secreta", "Una Promesa"],
            title_promise_early=False,
            title_promise_note="The promise only appears in the ending.",
        )
        return output, LLMUsage(task=task, model="claude-sonnet-5-5",
                                requested_model="claude-sonnet-5-5", cost_usd=0.0123)


def test_model_answer_is_used_with_the_computed_chapter_times() -> None:
    script = make_script()
    llm = AnsweringLLM()
    result = run(seo.build_seo_pack(
        llm, title=TITLE, script=script, timing=make_timing(script), channel=make_channel(),
        language="Spanish", fmt="long", max_headline_words=3, project_id="p1",
    ))
    pack = result.pack
    assert pack.title == "He Never Forgot a Face"
    assert pack.tags == ["camarero", "historia", "bondad"]
    assert [(c.time, c.title) for c in pack.chapters] == [
        ("00:00", "Un café"), ("00:24", "La cabina"), ("00:48", "La carta"), ("01:12", "La llave"),
    ]
    assert pack.hashtags == ["#camarero", "#bondad"]
    assert result.headlines == ["Nunca Olvidó", "La Llave Secreta", "Una Promesa"]
    assert result.title_promise_early is False and "ending" in result.title_promise_note
    assert result.cost_usd == 0.0123 and result.from_model and result.notes == []
    assert "Spanish" in llm.calls[0]["user"] and llm.calls[0]["stage"] == "export"


def test_a_model_error_still_gives_a_pack() -> None:
    script = make_script()
    result = run(seo.build_seo_pack(
        AnsweringLLM(fail=True), title=TITLE, script=script, timing=None,
        channel=make_channel(), language="English", fmt="long", max_headline_words=4,
        project_id="p1",
    ))
    assert result.pack.title == TITLE and result.pack.description
    assert any("could not write the metadata" in n for n in result.notes)
    assert result.title_promise_early is True
