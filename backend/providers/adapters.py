"""Concrete provider adapters.

One class per *protocol*, not per vendor: nine of the fourteen providers speak the OpenAI
chat-completions dialect, so they share :class:`OpenAICompatAdapter` and differ only by
config. That keeps "add a provider without touching code" honest for the common case.

Every adapter:
* raises :class:`ProviderError` with a classified :class:`FailureKind` on failure,
* never logs or returns the API key,
* reports its own :meth:`test_connection` result for the BYOK settings cards.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

import httpx

from ..core.models import FailureKind
from .base import (
    AudioHit,
    ConnectionTest,
    ImageResult,
    LLMResponse,
    ProviderAdapter,
    ProviderError,
    TranscriptResult,
    VideoHit,
    WebDocument,
    classify_http_error,
    retry_after_from,
)

log = logging.getLogger(__name__)

USER_AGENT = "PulseGStudio/0.9 (+https://github.com/Topaz-code/PulseG)"
DEFAULT_TIMEOUT = 120.0


# --- shared helpers -------------------------------------------------------------------


def _client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _raise_for_status(response: httpx.Response, provider: str, model: str = "") -> None:
    if response.status_code < 400:
        return
    body = ""
    try:
        body = response.text[:2000]
    except Exception:  # pragma: no cover
        pass
    kind = classify_http_error(response.status_code, body)
    raise ProviderError(
        f"{provider} returned HTTP {response.status_code}: {body[:300]}",
        kind=kind,
        provider=provider,
        model=model,
        status_code=response.status_code,
        retry_after=retry_after_from(response.headers),
        body=body,
    )


def _wrap_transport(exc: Exception, provider: str, model: str = "") -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            f"{provider} timed out: {exc}", kind=FailureKind.TIMEOUT, provider=provider, model=model
        )
    if isinstance(exc, httpx.HTTPError):
        return ProviderError(
            f"{provider} unreachable: {exc}",
            kind=FailureKind.UNAVAILABLE,
            provider=provider,
            model=model,
        )
    return ProviderError(str(exc), kind=FailureKind.UNKNOWN, provider=provider, model=model)


def _save_image_bytes(data: bytes, out_path: str) -> tuple[int, int]:
    """Persist image bytes, normalising to PNG. Returns (width, height)."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(data))
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            target = target.with_suffix(".png")
        if target.suffix.lower() == ".png" and image.mode not in ("RGBA", "RGB"):
            image = image.convert("RGBA")
        image.save(target)
        return image.width, image.height
    except Exception:
        # Not a decodable image (an error page, or Pillow missing): keep raw bytes so the
        # caller can inspect, but tell it the size is unknown.
        target.write_bytes(data)
        return 0, 0


def _encode_image(path: str) -> tuple[str, str]:
    """Return (mime, base64) for a local image, for multimodal prompts."""
    raw = Path(path).read_bytes()
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return mime, base64.b64encode(raw).decode("ascii")


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction: raw, then fenced, then first balanced object."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(candidate)):
        char = candidate[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


# --- OpenAI-compatible (Groq, Mistral, OpenRouter, SEA-LION, Z.ai, Routeway, Zen, NIM) ---


class OpenAICompatAdapter(ProviderAdapter):
    """Chat, vision and (where offered) Whisper transcription over the OpenAI dialect."""

    supports_chat = True
    supports_vision = True
    supports_stt = True

    def __init__(self, api_key: str | None = None, base_url: str = "", **options: Any) -> None:
        super().__init__(api_key, base_url, **options)
        self.extra_headers: dict[str, str] = options.get("extra_headers") or {}
        self.provider_name: str = options.get("provider_name", self.provider_id)

    @property
    def requires_key(self) -> bool:
        return bool(self.options.get("requires_key", True))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema:
            # Widely supported across the dialect; providers that ignore it still return
            # JSON because the prompt asks for it, and _extract_json is tolerant.
            payload["response_format"] = {"type": "json_object"}
        try:
            with _client(timeout) as client:
                response = client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        _raise_for_status(response, self.provider_id, model)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        result = LLMResponse(
            text=text,
            provider=self.provider_id,
            model=data.get("model") or model,
            latency_ms=self._ms(started),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason") or "",
            raw=data,
        )
        result.parsed = _extract_json(text)
        return result

    def vision(
        self,
        prompt: str,
        images: Sequence[str],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        timeout: float = 180.0,
    ) -> LLMResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            mime, encoded = _encode_image(path)
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
            )
        return self.chat(
            [{"role": "user", "content": content}],
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def transcribe(
        self, audio_path: str, model: str = "", *, language: str = "", timeout: float = 600.0
    ) -> TranscriptResult:
        model = model or "whisper-large-v3"
        files = {"file": (Path(audio_path).name, Path(audio_path).read_bytes())}
        data = {"model": model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        try:
            with _client(timeout) as client:
                response = client.post(
                    f"{self.base_url.rstrip('/')}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    files=files,
                    data=data,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        _raise_for_status(response, self.provider_id, model)
        payload = response.json()
        return TranscriptResult(
            text=payload.get("text", ""),
            provider=self.provider_id,
            language=payload.get("language", language),
            duration_s=float(payload.get("duration") or 0.0),
            segments=payload.get("segments") or [],
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key and self.requires_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/models", headers=self._headers()
                )
            latency = self._ms(started)
            if response.status_code == 200:
                models = [
                    item.get("id", "")
                    for item in (response.json().get("data") or [])
                    if isinstance(item, dict)
                ]
                detail = f"Key accepted. {len(models)} models visible."
                if model and models and model not in models:
                    detail += f" Note: '{model}' is not in the visible list - pick another."
                return ConnectionTest("valid", detail, latency, {"models": len(models)})
            kind = classify_http_error(response.status_code, response.text[:500])
            status = {
                FailureKind.RATE_LIMITED: "rate_limited",
                FailureKind.QUOTA_EXHAUSTED: "rate_limited",
                FailureKind.INVALID_KEY: "invalid",
            }.get(kind, "unreachable")
            return ConnectionTest(status, f"HTTP {response.status_code}: {response.text[:200]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Google AI Studio (Gemini) ---------------------------------------------------------


class GeminiAdapter(ProviderAdapter):
    """Gemini over the REST API.

    Implemented with httpx rather than the SDK so the exact request shape is visible and
    version-proof; ``google-genai`` remains installed for future live-audio features.
    """

    provider_id = "google_ai_studio"
    supports_chat = True
    supports_vision = True

    def _url(self, model: str, method: str) -> str:
        base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        return f"{base.rstrip('/')}/models/{model}:{method}?key={self.api_key}"

    def _contents(self, messages: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        system_parts: list[dict[str, Any]] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append({"text": content if isinstance(content, str) else json.dumps(content)})
                continue
            parts: list[dict[str, Any]] = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for chunk in content:
                    if chunk.get("type") == "text":
                        parts.append({"text": chunk.get("text", "")})
                    elif chunk.get("type") == "image_url":
                        url = chunk.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            header, _, encoded = url.partition(",")
                            mime = header.split(";")[0].replace("data:", "") or "image/png"
                            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        system = {"system_instruction": {"parts": system_parts}} if system_parts else {}
        return system, contents

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        system, contents = self._contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
            **system,
        }
        if json_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            with _client(timeout) as client:
                response = client.post(
                    self._url(model, "generateContent"),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        _raise_for_status(response, self.provider_id, model)
        data = response.json()
        text = ""
        for candidate in data.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                text += part.get("text", "")
        usage = data.get("usageMetadata") or {}
        result = LLMResponse(
            text=text,
            provider=self.provider_id,
            model=model,
            latency_ms=self._ms(started),
            tokens_in=int(usage.get("promptTokenCount") or 0),
            tokens_out=int(usage.get("candidatesTokenCount") or 0),
            finish_reason=(data.get("candidates") or [{}])[0].get("finishReason", ""),
            raw=data,
        )
        result.parsed = _extract_json(text)
        return result

    def vision(
        self,
        prompt: str,
        images: Sequence[str],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        timeout: float = 180.0,
    ) -> LLMResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            mime, encoded = _encode_image(path)
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        return self.chat(
            [{"role": "user", "content": content}],
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            url = f"{(self.base_url or '').rstrip('/')}/models?key={self.api_key}&pageSize=5"
            with _client(30) as client:
                response = client.get(url)
            latency = self._ms(started)
            if response.status_code == 200:
                models = [m.get("name", "") for m in response.json().get("models") or []]
                return ConnectionTest("valid", f"Key accepted. {len(models)} models visible.", latency)
            status = "rate_limited" if response.status_code == 429 else "invalid"
            return ConnectionTest(status, f"HTTP {response.status_code}: {response.text[:200]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Deepgram -------------------------------------------------------------------------


class DeepgramAdapter(ProviderAdapter):
    provider_id = "deepgram"
    supports_stt = True

    def _auth(self) -> dict[str, str]:
        # Deepgram uses "Token <key>", not "Bearer".
        token = self.api_key if self.api_key.lower().startswith("token ") else f"Token {self.api_key}"
        return {"Authorization": token}

    def transcribe(
        self, audio_path: str, model: str = "", *, language: str = "", timeout: float = 600.0
    ) -> TranscriptResult:
        model = model or "nova-3"
        params = {"model": model, "smart_format": "true", "punctuate": "true"}
        if language:
            params["language"] = language
        try:
            with _client(timeout) as client:
                response = client.post(
                    f"{self.base_url.rstrip('/')}/listen",
                    headers={**self._auth(), "Content-Type": "application/octet-stream"},
                    params=params,
                    content=Path(audio_path).read_bytes(),
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        _raise_for_status(response, self.provider_id, model)
        data = response.json()
        try:
            alternative = data["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError) as exc:
            raise ProviderError(
                f"Deepgram returned an unexpected payload: {str(data)[:200]}",
                kind=FailureKind.EMPTY_RESPONSE,
                provider=self.provider_id,
                model=model,
            ) from exc
        words = alternative.get("words") or []
        segments: list[dict[str, Any]] = []
        if words:
            bucket: list[dict[str, Any]] = []
            for word in words:
                bucket.append(word)
                if word.get("punctuated_word", "").endswith((".", "?", "!")):
                    segments.append(
                        {
                            "start": bucket[0].get("start", 0.0),
                            "end": bucket[-1].get("end", 0.0),
                            "text": " ".join(w.get("punctuated_word") or w.get("word", "") for w in bucket),
                        }
                    )
                    bucket = []
            if bucket:
                segments.append(
                    {
                        "start": bucket[0].get("start", 0.0),
                        "end": bucket[-1].get("end", 0.0),
                        "text": " ".join(w.get("punctuated_word") or w.get("word", "") for w in bucket),
                    }
                )
        duration = float((data.get("metadata") or {}).get("duration") or 0.0)
        return TranscriptResult(
            text=alternative.get("transcript", ""),
            provider=self.provider_id,
            language=language or "en",
            duration_s=duration,
            segments=segments,
            confidence=float(alternative.get("confidence") or 0.0),
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(f"{self.base_url.rstrip('/')}/projects", headers=self._auth())
            latency = self._ms(started)
            if response.status_code == 200:
                projects = response.json().get("projects") or []
                return ConnectionTest("valid", f"Key accepted. {len(projects)} project(s) visible.", latency)
            status = "rate_limited" if response.status_code == 429 else "invalid"
            return ConnectionTest(status, f"HTTP {response.status_code}: {response.text[:200]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Stable Horde ---------------------------------------------------------------------


class StableHordeAdapter(ProviderAdapter):
    """Asynchronous crowdsourced generation: submit, poll, download.

    The job id is returned so a crashed or interrupted run can be resumed instead of
    regenerating (and re-queueing behind other users).
    """

    provider_id = "stable_horde"
    supports_image = True
    ANON_KEY = "0000000000"

    @property
    def requires_key(self) -> bool:
        return False

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.api_key or self.ANON_KEY, "Content-Type": "application/json"}

    def _submit(self, payload: dict[str, Any], timeout: float) -> str:
        with _client(timeout) as client:
            response = client.post(
                f"{self.base_url.rstrip('/')}/generate/async",
                headers=self._headers(),
                json=payload,
            )
        _raise_for_status(response, self.provider_id)
        job_id = response.json().get("id")
        if not job_id:
            raise ProviderError(
                "Stable Horde did not return a job id.",
                kind=FailureKind.EMPTY_RESPONSE,
                provider=self.provider_id,
            )
        return job_id

    def _poll(self, job_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        base = self.base_url.rstrip("/")
        with _client(60) as client:
            while time.time() < deadline:
                check = client.get(f"{base}/generate/check/{job_id}", headers=self._headers())
                if check.status_code == 404:
                    raise ProviderError(
                        "Stable Horde lost the job (404). It may have expired in the queue.",
                        kind=FailureKind.UNAVAILABLE,
                        provider=self.provider_id,
                    )
                if check.status_code == 200 and check.json().get("done"):
                    status = client.get(f"{base}/generate/status/{job_id}", headers=self._headers())
                    _raise_for_status(status, self.provider_id)
                    return status.json()
                time.sleep(4)
        raise ProviderError(
            f"Stable Horde job {job_id} did not finish within {int(timeout)}s. It is still "
            "queued - retrying later, or contributing GPU time for kudos, will speed it up.",
            kind=FailureKind.TIMEOUT,
            provider=self.provider_id,
        )

    def image(
        self,
        prompt: str,
        out_path: str,
        *,
        model: str = "",
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        timeout: float = 600.0,
    ) -> ImageResult:
        payload: dict[str, Any] = {
            "prompt": prompt,
            "params": {
                "width": max(64, min(1024, width)),
                "height": max(64, min(1024, height)),
                "steps": 30,
                "n": 1,
                "sampler_name": "k_euler_a",
                "cfg_scale": 7.0,
                "negative_prompt": negative_prompt or "text, watermark, blurry, lowres",
                "karras": True,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "models": [model] if model else ["stable_diffusion"],
        }
        if reference_image and Path(reference_image).exists():
            mime, encoded = _encode_image(reference_image)
            payload["source_image"] = f"data:{mime};base64,{encoded}"
            payload["source_processing"] = "img2img"
            payload["params"]["denoising_strength"] = float(denoise)
        job_id = self._submit(payload, 60)
        result = self._poll(job_id, timeout)
        generations = result.get("generations") or []
        if not generations:
            raise ProviderError(
                "Stable Horde finished the job but returned no image (the worker likely "
                "censored or failed). Retrying will re-queue it.",
                kind=FailureKind.EMPTY_RESPONSE,
                provider=self.provider_id,
            )
        image_url = generations[0].get("img", "")
        with _client(120) as client:
            download = client.get(image_url)
        _raise_for_status(download, self.provider_id)
        width_out, height_out = _save_image_bytes(download.content, out_path)
        return ImageResult(
            path=out_path,
            provider=self.provider_id,
            prompt=prompt,
            width=width_out or width,
            height=height_out or height,
            seed=generations[0].get("seed"),
            job_id=job_id,
            raw={"job_id": job_id, "kudos": result.get("kudos")},
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        try:
            with _client(30) as client:
                response = client.get(f"{self.base_url.rstrip('/')}/models")
            latency = self._ms(started)
            if response.status_code == 200:
                models = response.json() if isinstance(response.json(), list) else []
                detail = f"Reachable. {len(models)} models served by volunteers."
                if not self.api_key:
                    detail += " Using the anonymous key, so requests queue behind contributors."
                return ConnectionTest("valid", detail, latency)
            return ConnectionTest("rate_limited", f"HTTP {response.status_code}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Pollinations ---------------------------------------------------------------------


class PollinationsAdapter(ProviderAdapter):
    provider_id = "pollinations"
    supports_image = True

    @property
    def requires_key(self) -> bool:
        return False

    def image(
        self,
        prompt: str,
        out_path: str,
        *,
        model: str = "",
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        timeout: float = 300.0,
    ) -> ImageResult:
        # Legacy keyless endpoint: build a URL, get an image. Throttled (~1 req/15s) and
        # may watermark on the anonymous tier, which the Asset Library surfaces to the user.
        encoded = urllib.parse.quote(prompt[:1200], safe="")
        params = {
            "width": width,
            "height": height,
            "model": model or "flux",
            "nologo": "true",
            "safe": "true",
        }
        if self.api_key:
            params["token"] = self.api_key
        url = f"{self.base_url.rstrip('/')}/prompt/{encoded}?{urllib.parse.urlencode(params)}"
        try:
            with _client(timeout) as client:
                response = client.get(url)
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        _raise_for_status(response, self.provider_id, model)
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            raise ProviderError(
                f"Pollinations returned {content_type or 'no content type'} instead of an "
                "image. The anonymous tier throttles hard; retrying usually works.",
                kind=FailureKind.RATE_LIMITED,
                provider=self.provider_id,
                model=model,
            )
        width_out, height_out = _save_image_bytes(response.content, out_path)
        return ImageResult(
            path=out_path,
            provider=self.provider_id,
            prompt=prompt,
            width=width_out or width,
            height=height_out or height,
            watermarked=not bool(self.api_key),
            raw={"anonymous": not bool(self.api_key)},
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        try:
            with _client(30) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/prompt/a%20small%20test%20image?width=64&height=64&nologo=true&model=flux"
                )
            latency = self._ms(started)
            if response.status_code == 200 and "image" in response.headers.get("content-type", ""):
                return ConnectionTest(
                    "valid",
                    "Keyless endpoint works. Anonymous images may be watermarked and are throttled.",
                    latency,
                )
            return ConnectionTest(
                "rate_limited" if response.status_code == 429 else "unreachable",
                f"HTTP {response.status_code}",
                latency,
            )
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- HuggingFace ----------------------------------------------------------------------


class HuggingFaceAdapter(ProviderAdapter):
    provider_id = "huggingface"
    supports_image = True

    def image(
        self,
        prompt: str,
        out_path: str,
        *,
        model: str = "",
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        timeout: float = 300.0,
    ) -> ImageResult:
        model = model or "black-forest-labs/FLUX.1-schnell"
        url = f"{self.base_url.rstrip('/')}/models/{model}"
        payload: dict[str, Any] = {"inputs": prompt, "parameters": {"width": width, "height": height}}
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt
        try:
            with _client(timeout) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id, model) from exc
        if response.status_code == 503:
            # Documented "model is loading" state - the router backs off and retries.
            raise ProviderError(
                f"{model} is loading on HuggingFace. Retrying in a moment usually succeeds.",
                kind=FailureKind.UNAVAILABLE,
                provider=self.provider_id,
                model=model,
                status_code=503,
                retry_after=20.0,
            )
        _raise_for_status(response, self.provider_id, model)
        if not response.content[:4].startswith((b"\x89PNG", b"\xff\xd8\xff")) and b"<html" in response.content[:200].lower():
            raise ProviderError(
                "HuggingFace returned HTML instead of an image - the model is probably not "
                "warm yet, or your free credits for this month are spent.",
                kind=FailureKind.EMPTY_RESPONSE,
                provider=self.provider_id,
                model=model,
            )
        width_out, height_out = _save_image_bytes(response.content, out_path)
        return ImageResult(
            path=out_path,
            provider=self.provider_id,
            prompt=prompt,
            width=width_out or width,
            height=height_out or height,
            raw={"model": model},
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(
                    "https://huggingface.co/api/whoami-v2",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            latency = self._ms(started)
            if response.status_code == 200:
                name = response.json().get("name", "user")
                return ConnectionTest("valid", f"Token accepted for '{name}'.", latency)
            status = "rate_limited" if response.status_code == 429 else "invalid"
            return ConnectionTest(status, f"HTTP {response.status_code}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Firecrawl ------------------------------------------------------------------------


class FirecrawlAdapter(ProviderAdapter):
    provider_id = "firecrawl"
    supports_scrape = True

    def scrape(self, url: str, *, timeout: float = 90.0) -> WebDocument:
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        try:
            with _client(timeout) as client:
                response = client.post(
                    f"{self.base_url.rstrip('/')}/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id) from exc
        _raise_for_status(response, self.provider_id)
        data = response.json().get("data") or {}
        markdown = data.get("markdown") or ""
        if not markdown.strip():
            raise ProviderError(
                f"Firecrawl returned no readable content for {url}.",
                kind=FailureKind.EMPTY_RESPONSE,
                provider=self.provider_id,
            )
        return WebDocument(
            url=url,
            markdown=markdown,
            title=(data.get("metadata") or {}).get("title", ""),
            raw=data,
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/team/credit-usage",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            latency = self._ms(started)
            if response.status_code == 200:
                data = response.json().get("data") or {}
                remaining = data.get("remaining_credits", data.get("remainingCredits"))
                return ConnectionTest(
                    "valid",
                    f"Key accepted. Remaining credits: {remaining}"
                    if remaining is not None
                    else "Key accepted. Credit usage is not exposed for this plan.",
                    latency,
                    {"remaining_credits": remaining},
                )
            status = "rate_limited" if response.status_code == 429 else "invalid"
            return ConnectionTest(status, f"HTTP {response.status_code}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- YouTube --------------------------------------------------------------------------


class YoutubeDataAdapter(ProviderAdapter):
    provider_id = "youtube_data"
    supports_video_search = True

    def search_video(self, query: str, *, limit: int = 8) -> list[VideoHit]:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(limit, 25),
            "key": self.api_key,
            "relevanceLanguage": "en",
            "safeSearch": "moderate",
        }
        try:
            with _client(60) as client:
                response = client.get(f"{self.base_url.rstrip('/')}/search", params=params)
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id) from exc
        _raise_for_status(response, self.provider_id)
        hits: list[VideoHit] = []
        for item in response.json().get("items", []):
            snippet = item.get("snippet") or {}
            video_id = (item.get("id") or {}).get("videoId", "")
            if not video_id:
                continue
            hits.append(
                VideoHit(
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    title=snippet.get("title", ""),
                    channel=snippet.get("channelTitle", ""),
                    source="youtube",
                )
            )
        return hits

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/videos",
                    params={"part": "snippet", "chart": "mostPopular", "maxResults": 1, "key": self.api_key},
                )
            latency = self._ms(started)
            if response.status_code == 200:
                return ConnectionTest(
                    "valid",
                    "Key accepted. Daily quota is 10,000 units and a search costs 100 - "
                    "about 100 searches per day.",
                    latency,
                    {"units_per_day": 10000},
                )
            status = "invalid"
            if response.status_code == 403 and "quota" in response.text.lower():
                status = "rate_limited"
            return ConnectionTest(status, f"HTTP {response.status_code}: {response.text[:160]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


class YtDlpAdapter(ProviderAdapter):
    """Keyless subtitle/metadata extraction.

    Chosen deliberately over the YouTube Data API for transcripts: the Data API does not
    return captions for videos the project does not own. Failures here are expected and
    normal (age gates, no subtitle track, region blocks) and are classified so the
    Transcriptor can fall through to real transcription.
    """

    provider_id = "ytdlp"
    supports_video_search = True

    @property
    def requires_key(self) -> bool:
        return False

    def _ydl(self, extra: dict[str, Any] | None = None):
        import yt_dlp

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "socket_timeout": 45,
        }
        options.update(extra or {})
        return yt_dlp.YoutubeDL(options)

    def metadata(self, url: str) -> dict[str, Any]:
        try:
            with self._ydl() as ydl:
                return ydl.extract_info(url, download=False) or {}
        except Exception as exc:
            message = str(exc)
            kind = FailureKind.UNAVAILABLE
            if "video unavailable" in message.lower() or "private" in message.lower():
                kind = FailureKind.BAD_REQUEST
            elif "no subtitles" in message.lower():
                kind = FailureKind.EMPTY_RESPONSE
            raise ProviderError(
                f"yt-dlp could not read {url}: {message[:200]}",
                kind=kind,
                provider=self.provider_id,
            ) from exc

    def search_video(self, query: str, *, limit: int = 8) -> list[VideoHit]:
        try:
            with self._ydl({"extract_flat": True}) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id) from exc
        hits: list[VideoHit] = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            video_id = entry.get("id", "")
            hits.append(
                VideoHit(
                    url=entry.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                    title=entry.get("title", ""),
                    channel=entry.get("uploader", "") or "",
                    duration_s=float(entry.get("duration") or 0.0),
                    source="ytdlp",
                )
            )
        return hits

    def subtitles(self, url: str, *, language: str = "en") -> TranscriptResult:
        """Download the best available subtitle track and flatten it to text.

        Tries human-authored captions first, then automatic ones. No key, no quota.
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="pulseg-subs-") as tmp:
            options = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [language, f"{language}-orig", "en"],
                "subtitlesformat": "vtt/srt/best",
                "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
            }
            try:
                with self._ydl(options) as ydl:
                    info = ydl.extract_info(url, download=True) or {}
            except Exception as exc:
                raise ProviderError(
                    f"No usable subtitle track for {url}: {str(exc)[:200]}. Transcribing the "
                    "audio instead will cost credit, so this falls through to Deepgram only "
                    "when the user asks for it.",
                    kind=FailureKind.EMPTY_RESPONSE,
                    provider=self.provider_id,
                ) from exc
            files = sorted(Path(tmp).glob("*"))
            if not files:
                raise ProviderError(
                    f"{url} has no subtitle track available.",
                    kind=FailureKind.EMPTY_RESPONSE,
                    provider=self.provider_id,
                )
            raw = files[0].read_text(encoding="utf-8", errors="replace")
        text, segments = _parse_cues(raw)
        return TranscriptResult(
            text=text,
            provider="ytdlp",
            language=language,
            duration_s=float(info.get("duration") or 0.0),
            segments=segments,
            confidence=1.0 if info.get("subtitles") else 0.75,
        )

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        try:
            import importlib.util

            if importlib.util.find_spec("yt_dlp") is None:
                raise ImportError("yt-dlp is not installed")

            with _client(30) as client:
                client.get("https://www.youtube.com", timeout=15)
            return ConnectionTest(
                "valid",
                "yt-dlp installed and YouTube is reachable. Subtitles are free and need no key.",
                self._ms(started),
            )
        except ImportError:
            return ConnectionTest("unreachable", "yt-dlp is not installed.", self._ms(started))
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


def _parse_cues(raw: str) -> tuple[str, list[dict[str, Any]]]:
    """Flatten VTT/SRT into (plain_text, segments). Strips timestamps and inline tags."""
    segments: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
    )
    current_start: float | None = None
    buffer: list[str] = []
    for line in raw.splitlines():
        match = pattern.search(line)
        if match:
            if buffer and current_start is not None:
                segments.append({"start": current_start, "end": current_start, "text": " ".join(buffer).strip()})
                buffer = []
            current_start = _to_seconds(match.group(1))
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        clean = re.sub(r"<[^>]+>", "", stripped)
        clean = re.sub(r"\{\\[^}]*\}", "", clean)
        if clean and current_start is not None:
            if buffer and buffer[-1] == clean:  # VTT often repeats a rolling caption
                continue
            buffer.append(clean)
    if buffer and current_start is not None:
        segments.append({"start": current_start, "end": current_start, "text": " ".join(buffer).strip()})
    text = "\n".join(segment["text"] for segment in segments if segment["text"])
    return text, segments


def _to_seconds(stamp: str) -> float:
    stamp = stamp.replace(",", ".")
    hours, minutes, seconds = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


# --- Freesound ------------------------------------------------------------------------


class FreesoundAdapter(ProviderAdapter):
    provider_id = "freesound"
    supports_audio_search = True

    LICENCE_FILTERS = {
        "cc0": 'license:"Creative Commons 0"',
        "cc-by": 'license:"Attribution"',
        "any": "",
    }

    def search_audio(self, query: str, *, limit: int = 10, licence: str = "cc0") -> list[AudioHit]:
        params: dict[str, Any] = {
            "query": query,
            "page_size": min(limit, 50),
            "fields": "id,name,previews,license,username,duration,tags,url",
            "sort": "score",
        }
        licence_filter = self.LICENCE_FILTERS.get(licence, self.LICENCE_FILTERS["cc0"])
        if licence_filter:
            params["filter"] = licence_filter
        try:
            with _client(60) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/search/text/",
                    headers={"Authorization": f"Token {self.api_key}"},
                    params=params,
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id) from exc
        _raise_for_status(response, self.provider_id)
        hits: list[AudioHit] = []
        for result in response.json().get("results", []):
            previews = result.get("previews") or {}
            hits.append(
                AudioHit(
                    title=result.get("name", ""),
                    url=result.get("url", ""),
                    preview_url=previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3", ""),
                    licence=_normalise_licence(result.get("license", "")),
                    author=result.get("username", ""),
                    duration_s=float(result.get("duration") or 0.0),
                    source="freesound",
                    tags=result.get("tags") or [],
                )
            )
        return hits

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            with _client(30) as client:
                response = client.get(
                    f"{self.base_url.rstrip('/')}/search/text/",
                    headers={"Authorization": f"Token {self.api_key}"},
                    params={"query": "ui click", "page_size": 1, "fields": "id,name"},
                )
            latency = self._ms(started)
            if response.status_code == 200:
                return ConnectionTest("valid", "Key accepted. CC0 filtering is enabled.", latency)
            status = "rate_limited" if response.status_code == 429 else "invalid"
            return ConnectionTest(status, f"HTTP {response.status_code}: {response.text[:160]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


def _normalise_licence(url: str) -> str:
    lowered = (url or "").lower()
    if "publicdomain/zero" in lowered or "creativecommons.org/publicdomain" in lowered:
        return "CC0"
    if "by-nc" in lowered:
        return "CC-BY-NC"
    if "by-sa" in lowered:
        return "CC-BY-SA"
    if "by/4" in lowered or "/by/" in lowered:
        return "CC-BY"
    return url or "unknown"


# --- Kenney local library -------------------------------------------------------------


class KenneyAdapter(ProviderAdapter):
    """Local CC0 pack cache. No API exists, so this reads an index the app builds.

    The index is a JSON file listing cached files; the user points Settings at their own
    Kenney download folder and the index is rebuilt from it.
    """

    provider_id = "kenney"
    supports_audio_search = True
    supports_image = True

    @property
    def requires_key(self) -> bool:
        return False

    def _index(self) -> list[dict[str, Any]]:
        root = Path(self.options.get("cache_dir") or "")
        if not root or not root.exists():
            return []
        index_file = root / "pulseg_kenney_index.json"
        if index_file.exists():
            try:
                return json.loads(index_file.read_text(encoding="utf-8")).get("files", [])
            except Exception:
                pass
        entries: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".ogg", ".wav", ".mp3", ".png"}:
                entries.append({"path": str(path), "name": path.stem, "kind": path.suffix.lower()[1:]})
        try:
            root.mkdir(parents=True, exist_ok=True)
            index_file.write_text(json.dumps({"files": entries}, indent=1), encoding="utf-8")
        except Exception:  # pragma: no cover
            pass
        return entries

    def search_audio(self, query: str, *, limit: int = 10, licence: str = "cc0") -> list[AudioHit]:
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in self._index():
            name = entry["name"].lower()
            score = sum(1 for term in terms if term in name)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        return [
            AudioHit(
                title=entry["name"],
                url=entry["path"],
                preview_url=entry["path"],
                licence="CC0",
                author="Kenney",
                source="kenney",
            )
            for _, entry in scored[:limit]
        ]

    def test_connection(self, model: str = "") -> ConnectionTest:
        cache = self.options.get("cache_dir") or ""
        if not cache:
            return ConnectionTest(
                "unreachable",
                "No Kenney cache folder configured. Download a CC0 pack from kenney.nl "
                "and point Settings at the folder to enable the offline fallback.",
            )
        entries = self._index()
        if not entries:
            return ConnectionTest("unreachable", f"No usable files found in {cache}.")
        return ConnectionTest("valid", f"{len(entries)} CC0 files indexed in {cache}.")


# --- Telegram -------------------------------------------------------------------------


class TelegramAdapter(ProviderAdapter):
    provider_id = "telegram"
    supports_notify = True

    def _base(self) -> str:
        return f"{self.base_url.rstrip('/')}/bot{self.api_key}"

    def notify(self, title: str, body: str, chat_id: str = "") -> bool:
        target = chat_id or self.options.get("chat_id") or ""
        if not target:
            raise ProviderError(
                "No Telegram chat id configured. Send any message to your bot and use "
                "'Detect chat id' in Settings.",
                kind=FailureKind.NOT_CONFIGURED,
                provider=self.provider_id,
            )
        text = f"{title}\n\n{body}" if body else title
        try:
            with _client(30) as client:
                response = client.post(
                    f"{self._base()}/sendMessage",
                    json={"chat_id": target, "text": text[:4000], "disable_web_page_preview": True},
                )
        except Exception as exc:
            raise _wrap_transport(exc, self.provider_id) from exc
        _raise_for_status(response, self.provider_id)
        return bool(response.json().get("ok"))

    def detect_chat_id(self) -> str:
        """Read the most recent update and return the chat id the user messaged from."""
        with _client(30) as client:
            response = client.get(f"{self._base()}/getUpdates", params={"limit": 5})
        _raise_for_status(response, self.provider_id)
        for update in reversed(response.json().get("result") or []):
            for key in ("message", "edited_message", "channel_post"):
                chat = (update.get(key) or {}).get("chat") or {}
                if chat.get("id"):
                    return str(chat["id"])
        return ""

    def test_connection(self, model: str = "") -> ConnectionTest:
        started = time.perf_counter()
        if not self.api_key:
            return ConnectionTest("invalid", "No bot token saved. Create one with @BotFather.")
        try:
            with _client(30) as client:
                response = client.get(f"{self._base()}/getMe")
            latency = self._ms(started)
            if response.status_code == 200 and response.json().get("ok"):
                username = response.json().get("result", {}).get("username", "")
                return ConnectionTest("valid", f"Bot @{username} is reachable.", latency)
            return ConnectionTest("invalid", f"HTTP {response.status_code}: {response.text[:160]}", latency)
        except Exception as exc:
            return ConnectionTest("unreachable", str(exc)[:200], self._ms(started))


# --- Demo -----------------------------------------------------------------------------

DEMO_GDSCRIPT = '''extends CharacterBody2D
## Player controller generated by PulseG Studio (demo provider).
## Move with the configured input actions; gravity is applied when not on the floor.

const SPEED: float = 180.0
const JUMP_VELOCITY: float = -360.0

@export var acceleration: float = 1200.0
@export var friction: float = 1400.0

@onready var sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var coyote_timer: Timer = $CoyoteTimer

var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)
var _facing: int = 1


func _physics_process(delta: float) -> void:
\tvar direction: float = Input.get_axis("move_left", "move_right")
\tif direction != 0.0:
\t\t_facing = signi(int(direction))
\t\tvelocity.x = move_toward(velocity.x, direction * SPEED, acceleration * delta)
\t\tsprite.flip_h = _facing < 0
\t\tsprite.play("run")
\telse:
\t\tvelocity.x = move_toward(velocity.x, 0.0, friction * delta)
\t\tsprite.play("idle")

\tif not is_on_floor():
\t\tvelocity.y += _gravity * delta
\telif Input.is_action_just_pressed("jump") or coyote_timer.time_left > 0.0:
\t\tvelocity.y = JUMP_VELOCITY
\t\tcoyote_timer.stop()

\tmove_and_slide()


func _on_hazard_body_entered(_body: Node2D) -> void:
\tGameState.damage(1)


func _ready() -> void:
\tadd_to_group("player")
'''

DEMO_SCENE = '''[gd_scene load_steps=4 format=3 uid="uid://c0pulsegdemo1"]

[ext_resource type="Script" path="res://scripts/player.gd" id="1_player"]
[ext_resource type="Texture2D" path="res://assets/sprites/player_idle_0.png" id="2_idle"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_player"]
size = Vector2(16, 28)

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_player")
collision_layer = 2
collision_mask = 1

[node name="CollisionShape2D" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_player")

[node name="AnimatedSprite2D" type="AnimatedSprite2D" parent="."]
sprite_frames = SubResource("SpriteFrames_demo")
'''

DEMO_AUDIT = {
    "verdict": "APPROVED",
    "score": 8,
    "skill_flags": {
        "no_ai_slop": [],
        "humanizer": [],
        "deslop": [],
        "harvard_shape": [],
    },
    "rubric": {
        "instruction_match": 1.0,
        "godot4_correctness": 0.9,
        "structure": 0.9,
        "specificity": 0.8,
        "evidence": 0.7,
    },
    "screenshots_reviewed": [],
    "fix_note": "",
    "summary": "Player scene and controller are complete, typed for Godot 4, and include the collision shape.",
}


class DemoAdapter(ProviderAdapter):
    """Deterministic offline provider.

    Powers ``PULSEG_DEMO=1`` and the test-suite: the entire pipeline - dispatch, fallback,
    audit, human approval, git commit - runs end to end with no keys and no network. Every
    response is tagged ``provider=demo`` in the UI so demo output is never mistaken for a
    real model's work.
    """

    provider_id = "demo"
    supports_chat = True
    supports_vision = True
    supports_image = True
    supports_stt = True
    supports_scrape = True
    supports_video_search = True
    supports_audio_search = True

    @property
    def requires_key(self) -> bool:
        return False

    def _payload_for(self, messages: Sequence[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(
            (m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
            or ""
            for m in messages
        ).lower()
        # The agents stamp a PULSEG_AGENT marker into every user message. Routing on it is
        # exact; routing on keywords alone is not (the Documenter's rules mention
        # "no_ai_slop", which used to make it return an audit verdict as its own answer).
        marker = re.search(r"pulseg_agent:\s*([a-z_]+)", text)
        agent_id = marker.group(1) if marker else ""
        if agent_id == "auditor" or (not agent_id and ('"verdict"' in text or "no_ai_slop" in text)):
            return DEMO_AUDIT
        if agent_id in {"documenter"} or (not agent_id and "progress_lines" in text):
            return {
                "progress_lines": [
                    "- now | TASK_001 | documenter | demo/demo-1 | NOTE | froze the design summary"
                ],
                "gdd_sections": {
                    "SUMMARY": (
                        "A 2D side-on lighthouse adventure. The keeper maintains a light that "
                        "holds a storm back while the coastline rearranges itself between "
                        "shifts. Art direction: flat pixel art, dusk palette, 32x32 tiles."
                    )
                },
                "decisions": [],
                "open_questions": ["Does the storm damage the tower between chapters?"],
                "notes": "demo provider",
            }
        if agent_id == "planning_agent" or (not agent_id and "grillme" in text) or "ready_for_handoff" in text:
            return {
                "reply": (
                    "A lighthouse-keeping game with a storm that rewrites the coastline. "
                    "Two quick things before I draft the design document: what does losing "
                    "look like, and is the keeper alone the whole game or are there visits "
                    "from the mainland?"
                ),
                "genre": "narrative adventure",
                "gdd_template": "narrative_adventure",
                "questions": [
                    {"id": "q1", "theme": "mechanics", "text": "What ends a run - a timer, a storm meter, or story progress?"},
                    {"id": "q2", "theme": "characters", "text": "Should the supply boat's pilot be a recurring character?"},
                ],
                "ledger": {
                    "mechanics_complete": False,
                    "characters_complete": False,
                    "art_complete": True,
                    "scope_complete": False,
                    "missing": ["win/loss condition", "supporting character behaviour"],
                },
                # The intake extraction contract. The Planning Agent merges these keys into the
                # design state, and the gate reads that state, so a demo run has to satisfy the
                # contract exactly or PULSEG_DEMO would stall at the first screen. Every field
                # below is deliberately a complete one: rule long enough to be a rule, a character
                # with personality and a recognised behaviour pattern, art direction, and scope.
                "mechanics": [
                    {
                        "name": "The lamp beam",
                        "rule": (
                            "The beam is an 80 degree cone that sweeps at 45 degrees per second "
                            "while the aim key is held and stays put when released. A boat inside "
                            "the cone moves toward harbour at 30 px/s; outside it drifts toward the "
                            "nearest rock at 12 px/s."
                        ),
                        "inputs": ["aim left", "aim right", "release"],
                        "feedback": "beam colour warms as a boat enters it, and the sea audio lifts",
                        "failure": "a boat that reaches the rocks restarts the level from the last lantern",
                    }
                ],
                "characters": [
                    {
                        "name": "Maren",
                        "role": "the player character, the lighthouse keeper",
                        "personality": "calm, methodical, quietly stubborn",
                        "abilities": ["walk", "climb ladders", "carry one fuel can", "trim the wick", "aim the lamp"],
                        "ai_behaviour": "scripted - she only does what the player presses",
                    },
                    {
                        "name": "Tomas",
                        "role": "harbour master who hands over each night's list of boats",
                        "personality": "warm but impatient",
                        "abilities": ["hands over the boat list", "warns about incoming storms"],
                        "ai_behaviour": "stationary - he never leaves the dock",
                    },
                ],
                "art_direction": (
                    "Warm 32x32 hand-made pixel art in a 24 colour dusk palette: deep indigo sky, "
                    "amber lamp light, ochre rock, grey-green sea. Silhouette-first shapes, one "
                    "pixel outline, no anti-aliasing."
                ),
                "reference_images": [],
                "level_count": 6,
                "play_length_minutes": 4,
                "linear": True,
                "ready_for_handoff": False,
                "gdd_draft_markdown": None,
                "assumptions": ["2D side-on view unless you prefer top-down"],
            }
        if agent_id == "programmer" or (not agent_id and ("godot" in text or "gdscript" in text)):
            return {
                "files": [
                    {"path": "godot_project/scripts/player.gd", "purpose": "Player movement and damage", "lines": 42},
                    {"path": "godot_project/scenes/player.tscn", "purpose": "Player scene with collision", "lines": 20},
                ],
                "scene_tree": "Player (CharacterBody2D)\n  CollisionShape2D\n  AnimatedSprite2D",
                "input_actions_required": ["move_left", "move_right", "jump"],
                "autoloads": ["GameState"],
                "validate_requested": True,
                "risks": ["Animation frames are placeholders until the asset task lands"],
                # Demo runs must write real files, otherwise the "does the pipeline produce
                # something a human can approve" question is never actually exercised. This
                # block is appended to the response text by chat() below.
                "_file_blocks": (
                    "```file: godot_project/scripts/player.gd\n"
                    "extends CharacterBody2D\n\n"
                    "@export var speed: float = 220.0\n"
                    "@export var jump_velocity: float = -380.0\n"
                    "@export var acceleration: float = 1200.0\n"
                    "@export var friction: float = 1600.0\n\n"
                    "var _gravity: float = ProjectSettings.get_setting(\"physics/2d/default_gravity\", 980.0)\n\n\n"
                    "func _physics_process(delta: float) -> void:\n"
                    "\tvar direction := Input.get_axis(\"move_left\", \"move_right\")\n"
                    "\tif direction:\n"
                    "\t\tvelocity.x = move_toward(velocity.x, direction * speed, acceleration * delta)\n"
                    "\telse:\n"
                    "\t\tvelocity.x = move_toward(velocity.x, 0.0, friction * delta)\n"
                    "\tif not is_on_floor():\n"
                    "\t\tvelocity.y += _gravity * delta\n"
                    "\telif Input.is_action_just_pressed(\"jump\"):\n"
                    "\t\tvelocity.y = jump_velocity\n"
                    "\tif velocity.y > 0.0 and Input.is_action_just_released(\"jump\"):\n"
                    "\t\tvelocity.y *= 0.5\n"
                    "\tmove_and_slide()\n"
                    "```\n"
                    "```file: godot_project/scenes/player.tscn\n"
                    "[gd_scene load_steps=4 format=3]\n\n"
                    "[ext_resource type=\"Script\" path=\"res://scripts/player.gd\" id=\"1_player\"]\n"
                    "[ext_resource type=\"Texture2D\" path=\"res://icon.svg\" id=\"2_icon\"]\n\n"
                    "[sub_resource type=\"RectangleShape2D\" id=\"RectangleShape2D_1\"]\n"
                    "size = Vector2(24, 32)\n\n"
                    "[node name=\"Player\" type=\"CharacterBody2D\"]\n"
                    "script = ExtResource(\"1_player\")\n\n"
                    "[node name=\"Sprite2D\" type=\"Sprite2D\" parent=\".\"]\n"
                    "texture = ExtResource(\"2_icon\")\n\n"
                    "[node name=\"CollisionShape2D\" type=\"CollisionShape2D\" parent=\".\"]\n"
                    "shape = SubResource(\"RectangleShape2D_1\")\n"
                    "```\n"
                ),
            }
        if agent_id == "tester" or (not agent_id and "screenshot" in text):
            return {
                "verdict": "PASS",
                "checks": {
                    "player_render": True,
                    "physics": True,
                    "collisions": True,
                    "animation": True,
                    "ui": True,
                    "no_error_dialogs": True,
                    "framerate_ok": True,
                },
                "evidence": [
                    {"screenshot": "screenshots/demo_001.png", "observation": "Player visible on the platform, grounded."}
                ],
                "failures": [],
                "cannot_verify": ["hazard damage over a longer run"],
                "avg_fps": 60,
                "recommended_capture": "",
            }
        return {
            "dispatch": [],
            "create_tasks": [
                {
                    "assigned_to": "programmer",
                    "title": "Implement player movement",
                    "instruction": "Create a CharacterBody2D player with acceleration, friction, jump and a collision shape.",
                    "kind": "implementation",
                    "expected_outputs": ["godot_project/scenes/player.tscn", "godot_project/scripts/player.gd"],
                    "file_claims": ["godot_project/scenes/player.tscn", "godot_project/scripts/player.gd"],
                    "dependencies": [],
                    "phase": 1,
                }
            ],
            "phase_complete": False,
            "phase_notes": "Phase 1: playable character",
            "reasoning": "Nothing else is unblocked until the player exists.",
        }

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        time.sleep(0.05)  # make latency visible in the UI without slowing tests down
        payload = self._payload_for(messages)
        # File blocks are appended as text rather than embedded in the JSON so the normal
        # ```file:path parsing path is genuinely exercised in demo mode.
        file_blocks = str(payload.pop("_file_blocks", ""))
        text = json.dumps(payload, indent=2)
        if file_blocks:
            text = f"{text}\n\n{file_blocks}"
        result = LLMResponse(
            text=text,
            provider="demo",
            model=model or "demo-1",
            latency_ms=50,
            tokens_in=sum(len(str(m)) for m in messages) // 4,
            tokens_out=len(text) // 4,
            finish_reason="stop",
            raw={"demo": True},
        )
        result.parsed = payload
        return result

    def vision(
        self,
        prompt: str,
        images: Sequence[str],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        timeout: float = 180.0,
    ) -> LLMResponse:
        result = self.chat([{"role": "user", "content": prompt}], model)
        payload = dict(result.parsed or {})
        if "checks" in payload:
            payload["evidence"] = [
                {"screenshot": Path(path).name, "observation": "demo vision: frame looks correct"}
                for path in images
            ]
            payload["screenshots_reviewed"] = [Path(path).name for path in images]
            result.parsed = payload
            result.text = json.dumps(payload, indent=2)
        return result

    def image(
        self,
        prompt: str,
        out_path: str,
        *,
        model: str = "",
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        timeout: float = 600.0,
    ) -> ImageResult:
        """Generate a deterministic procedural placeholder so demo runs have real files."""
        from PIL import Image, ImageDraw

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        palette = _demo_palette(prompt)
        image = Image.new("RGBA", (width, height), palette[0])
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, height - height // 4, width, height], fill=palette[1])
        box = min(width, height) // 3
        draw.rectangle(
            [width // 2 - box // 2, height // 2 - box // 2, width // 2 + box // 2, height // 2 + box // 2],
            fill=palette[2],
            outline=palette[3],
        )
        image.save(target)
        return ImageResult(
            path=str(target),
            provider="demo",
            prompt=prompt,
            width=width,
            height=height,
            seed=abs(hash(prompt)) % 1_000_000,
            raw={"demo": True},
        )

    def transcribe(self, audio_path: str, model: str = "", *, language: str = "", timeout: float = 600.0):
        return TranscriptResult(
            text=(
                "In this tutorial we set up a CharacterBody2D, add a CollisionShape2D with a "
                "rectangle, and wire the input actions in the project settings."
            ),
            provider="demo",
            language=language or "en",
            duration_s=42.0,
            segments=[
                {"start": 0.0, "end": 12.0, "text": "In this tutorial we set up a CharacterBody2D."},
                {"start": 12.0, "end": 30.0, "text": "Add a CollisionShape2D with a rectangle."},
                {"start": 30.0, "end": 42.0, "text": "Wire the input actions in project settings."},
            ],
            confidence=0.98,
        )

    def scrape(self, url: str, *, timeout: float = 90.0) -> WebDocument:
        return WebDocument(
            url=url,
            title="Godot 4 CharacterBody2D reference (demo)",
            markdown=(
                "# CharacterBody2D (demo content)\n\n"
                "`CharacterBody2D` is the node for code-driven 2D movement. Call "
                "`move_and_slide()` in `_physics_process` after setting `velocity`.\n\n"
                "For a top-down game set `motion_mode = MOTION_MODE_FLOATING`.\n"
            ),
            provider="demo",
        )

    def search_video(self, query: str, *, limit: int = 8) -> list[VideoHit]:
        return [
            VideoHit(
                url="https://www.youtube.com/watch?v=demo0000001",
                title=f"{query} - Godot 4 tutorial (demo)",
                channel="Demo Channel",
                duration_s=612.0,
                has_subtitles=True,
                source="demo",
            )
        ]

    def search_audio(self, query: str, *, limit: int = 10, licence: str = "cc0") -> list[AudioHit]:
        return [
            AudioHit(
                title=f"{query} 01",
                url="https://freesound.org/s/demo1/",
                preview_url="",
                licence="CC0",
                author="demo_artist",
                duration_s=1.2,
                source="demo",
            )
        ]

    def test_connection(self, model: str = "") -> ConnectionTest:
        return ConnectionTest(
            "valid",
            "Demo provider is always available. It returns deterministic canned output so "
            "the pipeline can be demonstrated without spending quota.",
            1,
            {"demo": True},
        )


def _demo_palette(prompt: str) -> tuple[tuple[int, int, int, int], ...]:
    """Stable colour choice from the prompt so re-runs look identical."""
    palettes = [
        ((30, 28, 33, 255), (85, 80, 92, 255), (250, 243, 62, 255), (214, 248, 214, 255)),
        ((30, 28, 33, 255), (93, 115, 126, 255), (127, 198, 164, 255), (214, 248, 214, 255)),
        ((30, 28, 33, 255), (85, 80, 92, 255), (214, 123, 168, 255), (250, 243, 62, 255)),
    ]
    return palettes[abs(hash(prompt)) % len(palettes)]


# --- factory --------------------------------------------------------------------------

#: provider id -> adapter class. Everything else is OpenAI-compatible by config.
SPECIALISED: dict[str, type[ProviderAdapter]] = {
    "google_ai_studio": GeminiAdapter,
    "deepgram": DeepgramAdapter,
    "stable_horde": StableHordeAdapter,
    "pollinations": PollinationsAdapter,
    "huggingface": HuggingFaceAdapter,
    "firecrawl": FirecrawlAdapter,
    "youtube_data": YoutubeDataAdapter,
    "ytdlp": YtDlpAdapter,
    "freesound": FreesoundAdapter,
    "kenney": KenneyAdapter,
    "telegram": TelegramAdapter,
    "demo": DemoAdapter,
}

OPENAI_COMPATIBLE = {
    "groq",
    "mistral",
    "nvidia_nim",
    "sealion",
    "openrouter",
    "opencode_zen",
    "zai",
    "routeway",
}


def adapter_class_for(provider_id: str) -> type[ProviderAdapter]:
    if provider_id in SPECIALISED:
        return SPECIALISED[provider_id]
    if provider_id in OPENAI_COMPATIBLE:
        return OpenAICompatAdapter
    raise KeyError(
        f"No adapter for '{provider_id}'. OpenAI-compatible providers just need an entry "
        "in providers.yaml plus OPENAI_COMPATIBLE in adapters.py; a new protocol needs "
        "one adapter class."
    )
