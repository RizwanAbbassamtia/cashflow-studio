"""YouTube policy rules and the quality gates built on them.

``rules.yaml`` lists every rule with an id, the stage it applies to, its severity (``block``
stops the stage, ``warn`` is reported), the source it was checked against and the date, and
the thresholds. ``gates.py`` turns a stage's measurements into ``GateResult`` rows that the
title, script and storyboard stages store and the review panels show.
"""

from .gates import (
    GateResult,
    Rule,
    blocking_reasons,
    evaluate,
    load_rules,
    rule,
    rules_for,
    to_dicts,
)

__all__ = [
    "GateResult",
    "Rule",
    "blocking_reasons",
    "evaluate",
    "load_rules",
    "rule",
    "rules_for",
    "to_dicts",
]
