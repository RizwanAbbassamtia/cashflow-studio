"""Google Gemini image adapter (the "Nano Banana" models) on the official ``google-genai`` SDK.

Skeleton for M3: the request shape, the response handling and the error messages are in
place; it is not exercised by the tests and has not yet produced an image in this app.

How it works
------------
* The client is built on first use from the key named in ``config/providers.yaml``
  (``image.gemini.key_env``, default ``GEMINI_API_KEY``). Nothing touches the SDK at import
  time, so this module imports cleanly without a key or network.
* ``generate`` sends the reference images (style sheet, earlier scenes) as inline parts
  followed by the prompt, asks for an ``IMAGE`` response with the aspect ratio and the size
  label (``1K``/``2K``/``4K``), saves the first image part as PNG and records the provenance
  flags from the catalogue (Google states every Gemini image carries a SynthID watermark;
  C2PA is not asserted). Gemini has no negative-prompt field, so ``negative_prompt`` is folded
  into the prompt as a "Do not include" line.

Verifying the model id and prices (they change every few months)
----------------------------------------------------------------
Do this before shipping and whenever Google answers "model not found" (HTTP 404):

1. Open https://ai.google.dev/gemini-api/docs/models and
   https://ai.google.dev/gemini-api/docs/image-generation. Confirm that
   ``image.gemini.default_model`` in ``config/providers.yaml`` is listed as an image model,
   that it still accepts ``image_config.aspect_ratio`` (16:9 and 9:16) and
   ``image_config.image_size`` (1K/2K/4K), and note the reference-image limit.
2. Open https://ai.google.dev/gemini-api/docs/pricing and update ``price_by_size_usd``.
3. Open https://ai.google.dev/gemini-api/docs/deprecations and check for a shutdown date;
   if one is set, switch ``default_model`` to the successor named there.
4. Confirm the SDK call shape against https://googleapis.github.io/python-genai/ : this
   file uses ``client.models.generate_content(model=..., contents=[parts..., prompt],
   config=GenerateContentConfig(response_modalities=["IMAGE"], image_config=ImageConfig(
   aspect_ratio=..., image_size=...)))`` and reads ``candidates[0].content.parts[*]
   .inline_data``. Written against google-genai 2.29.0.
5. With a key saved in Settings > API keys, run one smoke image (prints the result, never
   the key)::

       .venv/Scripts/python.exe -m cashcow_studio.providers.image.gemini --out smoke.png

6. Set ``verified_on`` in ``config/providers.yaml`` to today's date.

Last verified: 2026-10-09 from Google's docs (see docs/research/2026-10-09-research-dump.md):
``gemini-nano-banana-2.1`` GA 2026-10-06; 1K $0.0336, 2K $0.0504, 4K $0.113 per image.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any

from ..base import (
    CostEstimate,
    ProviderError,
    ProviderHealth,
    ProviderNotConfigured,
    key_is_set,
    scrub_secret,
)
from .base import (
    REFERENCE_MIME,
    ImageCapabilities,
    ImageRequest,
    ImageResult,
    Provenance,
    size_label,
)

log = logging.getLogger(__name__)

GEMINI_ID = "gemini"
GEMINI_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-nano-banana-2.1"
MAX_TEXT_IN_ERROR = 300


def default_capabilities() -> ImageCapabilities:
    """Used when ``config/providers.yaml`` has no ``image.gemini`` entry."""
    return ImageCapabilities(
        id=GEMINI_ID,
        name="Google Gemini images (Nano Banana)",
        adapter="ready",
        models=[DEFAULT_MODEL, "gemini-3-pro-image", "gemini-3.1-flash-lite-image"],
        default_model=DEFAULT_MODEL,
        aspects=["1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        sizes=["1K", "2K", "4K"],
        max_reference_images=14,
        provenance=Provenance(c2pa=None, synthid=True),
        price_per_image_usd=0.0504,
        price_by_size_usd={"1K": 0.0336, "2K": 0.0504, "4K": 0.113},
        key_env=GEMINI_KEY_ENV,
        docs_url="https://ai.google.dev/gemini-api/docs/image-generation",
        notes="Text-free scene images; every output carries a SynthID watermark.",
        verified_on="2026-10-09",
    )


def explain_api_error(exc: BaseException, model: str, key_env: str, secret: str | None) -> str:
    """Turn an SDK error into one plain sentence for the reviewer. Never includes the key."""
    code = getattr(exc, "code", None)
    raw = getattr(exc, "message", None) or str(exc)
    message = scrub_secret(str(raw), secret)[:MAX_TEXT_IN_ERROR]
    if code in (401, 403):
        return (
            f"Google rejected the API key (HTTP {code}). Check {key_env} in Settings > API keys "
            "and that the key has access to the Gemini API."
        )
    if code == 404:
        return (
            f"Google does not know the image model {model!r} (HTTP 404). The model id in "
            "config/providers.yaml may be out of date; follow the verification steps in "
            "providers/image/gemini.py."
        )
    if code == 429:
        return (
            "Google is rate-limiting this key (HTTP 429). Wait a minute and try again, or check "
            "the quota of your Google AI plan."
        )
    if isinstance(code, int) and code >= 500:
        return f"Google's image service had a problem (HTTP {code}). Try again in a moment."
    prefix = f"Google returned an error (HTTP {code})" if code else "The image request failed"
    return f"{prefix}: {message}" if message else f"{prefix}."


class GeminiImageProvider:
    id = GEMINI_ID

    def __init__(
        self, capabilities: ImageCapabilities | None = None, model: str | None = None
    ) -> None:
        self.capabilities = capabilities or default_capabilities()
        self.model = model or self.capabilities.default_model or DEFAULT_MODEL
        self._client: Any = None
        self._client_secret: str | None = None

    # Configuration ------------------------------------------------------------------------

    @property
    def key_env(self) -> str:
        return self.capabilities.key_env or GEMINI_KEY_ENV

    def _secret(self) -> str:
        """The raw key, used only to build the client and to scrub error messages."""
        return os.environ.get(self.key_env, "").strip()

    def _get_client(self) -> Any:
        secret = self._secret()
        # Rebuilt when the key changed in Settings, so the new key is used without a restart.
        if self._client is not None and secret == self._client_secret:
            return self._client
        if not secret:
            raise ProviderNotConfigured(
                f"No Google API key is set. Add {self.key_env} in Settings > API keys, or use "
                "the mock images (CCS_IMAGE_PROVIDER=mock) for an offline run."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - the SDK is a dependency
            raise ProviderNotConfigured(
                "The google-genai package is not installed, so Google images cannot be used. "
                "Reinstall CashCow Studio or run: pip install google-genai"
            ) from exc
        self._client = genai.Client(api_key=secret)
        self._client_secret = secret
        return self._client

    # Generation ---------------------------------------------------------------------------

    def generate(self, request: ImageRequest) -> ImageResult:
        if self.capabilities.aspects and request.aspect not in self.capabilities.aspects:
            raise ProviderError(
                f"Google images do not support the aspect ratio {request.aspect}. Use one of: "
                f"{', '.join(self.capabilities.aspects)}."
            )
        client = self._get_client()
        from google.genai import errors as genai_errors
        from google.genai import types

        prompt = compose_prompt(request)
        contents: list[Any] = self._reference_parts(request.reference_images, types)
        contents.append(prompt)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=request.aspect, image_size=size_label(request.size)
            ),
            seed=request.seed,
        )
        secret = self._secret()
        try:
            response = client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            raise ProviderError(explain_api_error(exc, self.model, self.key_env, secret)) from exc
        except Exception as exc:  # network trouble, SDK surprises: keep the message clean
            detail = scrub_secret(str(exc), secret)[:MAX_TEXT_IN_ERROR]
            raise ProviderError(f"Google could not be reached: {detail}") from exc

        data, mime = first_image_part(response)
        path = Path(request.output_path)
        width, height = save_png(data, mime, path)
        model_used = getattr(response, "model_version", None) or self.model
        return ImageResult(
            path=path,
            width=width,
            height=height,
            model=str(model_used),
            seed=request.seed,
            provenance=self.capabilities.provenance.model_copy(),
            cost_usd=self.capabilities.price_for(request.size),
            provider=self.id,
            prompt_used=prompt,
            scene_id=request.scene_id,
        )

    def _reference_parts(self, paths: list[Path], types: Any) -> list[Any]:
        limit = self.capabilities.max_reference_images
        if limit and len(paths) > limit:
            log.warning(
                "Google images take at most %d reference images; %d were given, extra ones "
                "are skipped.",
                limit,
                len(paths),
            )
            paths = paths[:limit]
        parts: list[Any] = []
        for path in paths:
            path = Path(path)
            mime = REFERENCE_MIME.get(path.suffix.lower())
            if mime is None:
                raise ProviderError(
                    f"The reference image {path.name} must be a PNG, JPEG or WebP file."
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ProviderError(
                    f"The reference image could not be read: {path} ({exc.strerror or exc})."
                ) from exc
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        return parts

    # Cost and health ----------------------------------------------------------------------

    def estimate_cost(self, count: int = 1, size: str = "2K") -> CostEstimate:
        each = self.capabilities.price_for(size)
        checked = self.capabilities.verified_on or "never"
        return CostEstimate(
            provider=self.id,
            unit="image",
            units=max(count, 0),
            cost_usd=round(each * max(count, 0), 4),
            note=f"{self.model} at {size}: ${each:.4f} per image (prices last checked {checked}).",
        )

    def health(self) -> ProviderHealth:
        """Local facts only: no network call, so the Doctor page stays fast."""
        key_set = key_is_set(self.key_env)
        if not key_set:
            return ProviderHealth(
                provider=self.id,
                kind="image",
                status="not_configured",
                detail=f"{self.key_env} is not set. Add your Google AI Studio key in Settings > "
                "API keys.",
                key_env=self.key_env,
                key_set=False,
                model=self.model,
            )
        try:
            import google.genai  # noqa: F401  (only checks that the SDK is installed)
        except ImportError:  # pragma: no cover - the SDK is a dependency
            return ProviderHealth(
                provider=self.id,
                kind="image",
                status="fail",
                detail="The google-genai package is not installed. Reinstall CashCow Studio.",
                key_env=self.key_env,
                key_set=True,
                model=self.model,
            )
        checked = self.capabilities.verified_on
        return ProviderHealth(
            provider=self.id,
            kind="image",
            status="ok" if checked else "warn",
            detail=f"Key set. Model {self.model}; model id and prices last checked "
            f"{checked or 'never (verify before the first paid run)'}.",
            key_env=self.key_env,
            key_set=True,
            model=self.model,
        )


# Helpers (module level so they can be tested without a client) ---------------------------


def compose_prompt(request: ImageRequest) -> str:
    """Prompt plus the style hint and the things to avoid; Gemini has no negative-prompt field."""
    parts = [request.prompt.strip()]
    if request.style.strip():
        parts.append(f"Style: {request.style.strip()}")
    if request.negative_prompt.strip():
        parts.append(f"Do not include: {request.negative_prompt.strip()}")
    return "\n\n".join(parts)


def first_image_part(response: Any) -> tuple[bytes, str]:
    """The bytes and MIME type of the first image in a ``generate_content`` response."""
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        reason = getattr(block_reason, "name", None) or str(block_reason)
        raise ProviderError(
            f"Google declined this prompt ({reason.lower().replace('_', ' ')}). Reword the "
            "scene description: avoid real people, brands, violence and anything sexual."
        )
    texts: list[str] = []
    finish = ""
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None:
            finish = getattr(reason, "name", None) or str(reason)
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None)
            mime = str(getattr(blob, "mime_type", "") or "")
            if data and mime.startswith("image/"):
                return bytes(data), mime
            text = getattr(part, "text", None)
            if text:
                texts.append(str(text))
    note = " ".join(texts).strip()[:MAX_TEXT_IN_ERROR]
    reason_text = f" (reason: {finish.lower().replace('_', ' ')})" if finish else ""
    raise ProviderError(
        f"Google returned no image{reason_text}. " + (note or "Try rewording the prompt.")
    )


def save_png(data: bytes, mime: str, path: Path) -> tuple[int, int]:
    """Write the image as PNG (converting if needed) and return its width and height."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if mime == "image/png":
            path.write_bytes(data)
        else:
            with Image.open(io.BytesIO(data)) as image:
                image.save(path, "PNG")
        with Image.open(path) as image:
            return image.width, image.height
    except OSError as exc:
        raise ProviderError(f"The image could not be saved to {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual smoke test
    """Generate one test image with the key saved in Settings. Prints the result, never the key."""
    from ...config import load_settings

    parser = argparse.ArgumentParser(description="Generate one test image with Google Gemini.")
    parser.add_argument("--out", default="gemini-smoke.png", help="where to write the PNG")
    parser.add_argument(
        "--prompt", default="A calm mountain lake at sunrise, soft light, no text, no people"
    )
    parser.add_argument("--aspect", default="16:9")
    parser.add_argument("--size", default="1K")
    args = parser.parse_args(argv)

    load_settings()  # loads <app_data_dir>/.env into the environment
    provider = GeminiImageProvider()
    print(provider.health().model_dump_json(indent=2))
    try:
        result = provider.generate(
            ImageRequest(
                prompt=args.prompt, output_path=Path(args.out), aspect=args.aspect, size=args.size
            )
        )
    except ProviderError as exc:
        print(f"Failed: {exc}")
        return 1
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
