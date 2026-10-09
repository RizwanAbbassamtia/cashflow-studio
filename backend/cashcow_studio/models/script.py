"""Script stage data: ``03_script/script.json``, ``speech.json``, ``originality.json`` and the
shapes Claude returns. See docs/M1-M2-CONTRACT.md section 5.

Ids are positional and stable within one document: ``sec-01``, ``p-01-02`` (section 1,
paragraph 2), ``s-01-02-03`` (sentence 3 of that paragraph). The storyboard groups sentences
by these ids; the speech file lists the same ids with the spoken form of each sentence.

The front end mirrors these in ``frontend/src/types/script.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# The document --------------------------------------------------------------------------------


class ScriptSentence(BaseModel):
    id: str
    text: str


class ScriptParagraph(BaseModel):
    id: str
    text: str
    sentences: list[ScriptSentence] = []
    locked: bool = Field(default=False, description="kept word for word on a redo")


class ScriptSection(BaseModel):
    id: str
    name: str
    purpose: str = ""
    paragraphs: list[ScriptParagraph] = []


class ScriptDoc(BaseModel):
    """``03_script/script.json``."""

    title: str
    language: str
    format: Literal["long", "shorts"]
    target_words: int
    sections: list[ScriptSection]
    word_count: int
    model: str
    generated_at: datetime
    speaking_rate_wpm: int = 150
    framework_source: Literal["file", "default"] = "default"
    framework_name: str | None = None
    notes: list[str] = []
    schema_version: int = 1

    def sentences(self) -> list[ScriptSentence]:
        return [s for sec in self.sections for p in sec.paragraphs for s in p.sentences]

    def paragraphs(self) -> list[ScriptParagraph]:
        return [p for sec in self.sections for p in sec.paragraphs]

    def text(self) -> str:
        return "\n\n".join(p.text for p in self.paragraphs())


class SpeechSentence(BaseModel):
    id: str
    text: str
    speech_text: str = Field(description="numbers, dates, currency, abbreviations spelled out")


class SpeechDoc(BaseModel):
    """``03_script/speech.json``: what the voice reads, sentence by sentence."""

    language: str
    sentences: list[SpeechSentence]
    model: str
    generated_at: datetime
    schema_version: int = 1


class PolicyFindings(BaseModel):
    advisory_persona: bool = False
    sensitive_topic: bool = False
    reasons: list[str] = []


class OriginalityDoc(BaseModel):
    """``03_script/originality.json``: the gate results of the script stage."""

    ngram_overlap_source: float = Field(description="8-gram overlap with the competitor transcript")
    ngram_overlap_history_max: float = Field(
        description="highest 8-gram overlap with one of the channel's last 30 scripts"
    )
    semantic_note: str = ""
    policy: PolicyFindings = PolicyFindings()
    passed: bool
    word_count: int = 0
    target_words: int = 0
    title_claim_early: bool | None = Field(
        default=None, description="the title's promise appears in the first 20% (reported)"
    )
    history_compared: int = 0
    source_compared: bool = False
    gate_results: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    schema_version: int = 1


# What Claude returns -------------------------------------------------------------------------


class TranscriptBeat(BaseModel):
    name: str
    purpose: str
    share_percent: int = Field(description="rough share of the running time, 0-100")


class TranscriptSummary(BaseModel):
    """Structure only: beats, pacing and devices. Never sentences to reuse."""

    beats: list[TranscriptBeat]
    hook_style: str = ""
    pacing: str = ""
    devices: list[str] = []
    ending_style: str = ""
    notes: str = ""


class ScriptDraftParagraph(BaseModel):
    text: str
    locked_id: str | None = Field(
        default=None, description="id of a locked paragraph this one stands for (kept verbatim)"
    )


class ScriptDraftSection(BaseModel):
    name: str
    purpose: str = ""
    paragraphs: list[ScriptDraftParagraph]


class ScriptDraft(BaseModel):
    sections: list[ScriptDraftSection]


class SpeechNormalizedSentence(BaseModel):
    id: str
    speech_text: str


class SpeechNormalizeOutput(BaseModel):
    sentences: list[SpeechNormalizedSentence]


class PolicyCheckOutput(BaseModel):
    advisory_persona: bool = Field(
        description="the narrator poses as a professional adviser or tells the viewer what to do"
    )
    sensitive_topic: bool = Field(
        description="health, money, legal, politics or other YouTube sensitive subject"
    )
    title_claim_early: bool = Field(
        description="the title's key claim appears in the first fifth of the script"
    )
    reasons: list[str] = []
    semantic_note: str = Field(
        default="", description="one sentence on how the script differs from the source"
    )


# Edits -------------------------------------------------------------------------------------


class ScriptApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/script/approve``."""

    script_md: str | None = None
    locked_paragraph_ids: list[str] | None = None


class ScriptReviewPayload(BaseModel):
    stage: Literal["script"] = "script"
    script: ScriptDoc
    script_md: str
    speech: SpeechDoc | None = None
    originality: OriginalityDoc
    transcript_summary: TranscriptSummary | None = None
