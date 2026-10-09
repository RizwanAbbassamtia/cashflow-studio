"""Policy rules and gates: every rule has a check, thresholds come from rules.yaml, and the
title, script and storyboard contexts are judged as the contract says."""

from __future__ import annotations

import re

from cashflow_studio.policy import blocking_reasons, evaluate, load_rules, rule, rules_for
from cashflow_studio.policy.gates import CHECKS, scene_band


def by_id(results: list) -> dict[str, object]:
    return {r.id: r for r in results}


def test_rules_file_is_complete_and_consistent() -> None:
    rules = load_rules()
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(CHECKS), "every rule needs a check and every check a rule"
    for item in rules:
        assert item.stage in {"title", "script", "storyboard"}
        assert item.severity in {"block", "warn"}
        assert item.source and item.title
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", item.last_verified), item.id
    assert {r.id for r in rules_for("title")} == {
        "title.count", "title.length", "title.similarity_source", "title.similarity_history",
        "title.keywords", "title.recommendable",
    }
    assert rule("script.ngram_source").parameters == {"n": 8, "max_overlap": 0.02}
    assert rule("script.ngram_history").parameters["max_overlap"] == 0.05
    assert rule("script.word_count").parameters["tolerance"] == 0.15
    assert rule("storyboard.popup_share").parameters["min_share"] == 0.35
    assert scene_band("long") == (8.0, 12.0) and scene_band("shorts") == (3.0, 4.0)


def _variant(**overrides):
    base = {"title": "x", "length": 40, "similarity_to_source": 0.3, "similarity_to_history": 0.2,
            "keywords_kept": True, "flags": []}
    base.update(overrides)
    return base


def test_title_gates() -> None:
    clean = [_variant() for _ in range(7)]
    results = by_id(evaluate("title", {"variants": clean, "source_is_competitor": True,
                                       "history_count": 5}))
    assert all(r.passed for r in results.values())
    assert results["title.recommendable"].severity == "block"

    variants = [
        _variant(length=75, flags=["long"]),
        _variant(similarity_to_source=0.9, flags=["copy"]),
        _variant(similarity_to_history=0.85, flags=["repeat"]),
        _variant(keywords_kept=False, flags=["no keyword"]),
        _variant(), _variant(),
    ]
    results = by_id(evaluate("title", {"variants": variants, "source_is_competitor": True,
                                       "history_count": 3}))
    assert not results["title.count"].passed and "6 options" in results["title.count"].detail
    assert not results["title.length"].passed and "#1" in results["title.length"].detail
    source = results["title.similarity_source"]
    assert not source.passed and "#2" in source.detail
    history = results["title.similarity_history"]
    assert not history.passed and "#3" in history.detail
    assert not results["title.keywords"].passed and "#4" in results["title.keywords"].detail
    assert results["title.recommendable"].passed  # two clean options remain
    assert blocking_reasons(list(results.values())) == []

    all_bad = [_variant(flags=["x"]) for _ in range(7)]
    results = evaluate(
        "title", {"variants": all_bad, "source_is_competitor": False, "history_count": 0}
    )
    reasons = blocking_reasons(results)
    assert len(reasons) == 1 and reasons[0].startswith("At least one option passes every check")
    own_topic = by_id(results)
    assert own_topic["title.similarity_source"].passed  # not judged for an own topic
    assert own_topic["title.similarity_history"].passed  # nothing to compare with yet


GOOD_SCRIPT = {
    "ngram_overlap_source": 0.02, "ngram_overlap_history_max": 0.05, "source_compared": True,
    "history_compared": 4, "advisory_persona": False, "sensitive_topic": False,
    "word_count": 1275, "target_words": 1500, "title_claim_early": True, "reasons": [],
}


def test_script_gates_thresholds() -> None:
    results = by_id(evaluate("script", GOOD_SCRIPT))
    assert all(r.passed for r in results.values())
    assert results["script.word_count"].detail.startswith("1275 words")

    bad = {**GOOD_SCRIPT, "ngram_overlap_source": 0.021, "ngram_overlap_history_max": 0.051,
           "advisory_persona": True, "reasons": ["Says 'you should buy gold'."],
           "word_count": 1274, "title_claim_early": False, "sensitive_topic": True}
    results = by_id(evaluate("script", bad))
    failed = {r.id for r in results.values() if not r.passed}
    assert failed == {"script.ngram_source", "script.ngram_history", "script.advisory_persona",
                      "script.word_count", "script.title_claim_early", "script.sensitive_topic"}
    assert results["script.advisory_persona"].detail == "Says 'you should buy gold'."
    reasons = blocking_reasons(list(results.values()))
    assert len(reasons) == 4  # the two warn rules are not blocking
    assert any("2.1%" in r for r in reasons)

    # Nothing to compare with: the originality rules pass with an explanation.
    none = {**GOOD_SCRIPT, "source_compared": False, "history_compared": 0,
            "ngram_overlap_source": 0.9, "ngram_overlap_history_max": 0.9,
            "title_claim_early": None}
    results = by_id(evaluate("script", none))
    assert results["script.ngram_source"].passed and results["script.ngram_history"].passed
    assert results["script.title_claim_early"].passed
    # Word count tolerance is symmetric: 1725 passes, 1726 fails.
    upper = by_id(evaluate("script", {**GOOD_SCRIPT, "word_count": 1725}))
    assert upper["script.word_count"].passed
    over = by_id(evaluate("script", {**GOOD_SCRIPT, "word_count": 1726}))
    assert not over["script.word_count"].passed


def _scene(**overrides):
    base = {"est_duration_s": 10.0, "motion_preset": "zoom_in", "transition_type": "fade",
            "popup_text": None, "on_screen_text": None, "narration": "A quiet street at dawn.",
            "image_prompt": "A quiet street at dawn, soft light, no text, no logos."}
    base.update(overrides)
    return base


KNOWN = ["fade", "dissolve", "wipeleft", "fadeblack"]


def test_storyboard_gates() -> None:
    scenes = [
        _scene(motion_preset="zoom_in", transition_type="fade", popup_text="Quiet Dawn"),
        _scene(motion_preset="pan_left", transition_type="dissolve"),
        _scene(motion_preset="zoom_out", transition_type="wipeleft", popup_text="Second Chance"),
        _scene(motion_preset="pan_right", transition_type="fadeblack"),
    ]
    results = by_id(evaluate("storyboard", {"format": "long", "scenes": scenes,
                                            "known_transitions": KNOWN}))
    failed = [r.detail for r in results.values() if not r.passed]
    assert not failed, failed
    assert "2 of 4 scenes carry text (50.0%" in results["storyboard.popup_share"].detail

    bad = [
        _scene(est_duration_s=14.0, motion_preset="zoom_in", transition_type="fade",
               popup_text="one two three four five six seven"),
        _scene(est_duration_s=5.0, motion_preset="zoom_in", transition_type="fade",
               image_prompt="A shop sign that reads OPEN"),
        _scene(motion_preset="pan_left", transition_type="whoosh"),
        _scene(motion_preset="zoom_in", transition_type="fadeblack"),
    ]
    results = by_id(evaluate("storyboard", {"format": "long", "scenes": bad,
                                            "known_transitions": KNOWN}))
    failed_ids = {r.id for r in results.values() if not r.passed}
    assert failed_ids == {"storyboard.scene_length", "storyboard.motion_alternates",
                          "storyboard.transition_no_repeat", "storyboard.transition_known",
                          "storyboard.popup_words", "storyboard.popup_share",
                          "storyboard.prompt_text_free"}
    assert "#2" in results["storyboard.motion_alternates"].detail
    assert "#3" in results["storyboard.transition_known"].detail
    assert "#1" in results["storyboard.popup_words"].detail
    assert "#2" in results["storyboard.prompt_text_free"].detail
    assert "1 of 4 scenes" in results["storyboard.popup_share"].detail
    reasons = blocking_reasons(list(results.values()))
    assert len(reasons) == 5  # scene_length and prompt_text_free only warn

    identical = [_scene(popup_text="A quiet street at dawn."), _scene(motion_preset="hold")]
    results = by_id(evaluate("storyboard", {"format": "long", "scenes": identical,
                                            "known_transitions": KNOWN}))
    assert not results["storyboard.popup_words"].passed

    shorts = [
        _scene(est_duration_s=3.5, popup_text="Hi"),
        _scene(est_duration_s=3.5, motion_preset="hold", transition_type="dissolve"),
    ]
    results = by_id(evaluate("storyboard", {"format": "shorts", "scenes": shorts,
                                            "known_transitions": KNOWN}))
    assert results["storyboard.scene_length"].passed


def test_prompt_text_free_ignores_negations() -> None:
    def judge(prompt: str) -> bool:
        scenes = [_scene(image_prompt=prompt, popup_text="A")]
        results = by_id(evaluate("storyboard", {"format": "long", "scenes": scenes,
                                                "known_transitions": KNOWN}))
        return results["storyboard.prompt_text_free"].passed

    assert judge("A diner at night, no text, no logos, text-free, without captions.")
    assert judge("A cinematic, text-free scene that shows a nurse, no people's faces, no logos.")
    assert not judge("A storefront with the words OPEN LATE in neon.")
    assert not judge("A man holding a poster that says welcome home.")
    assert not judge("A truck featuring the brand logo on its side.")
