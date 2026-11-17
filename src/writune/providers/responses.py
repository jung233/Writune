"""OpenAI Responses API adapter with request-scoped streaming watchdogs."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from .base import ProviderError
from .tps import TokenCounter

if TYPE_CHECKING:
    from writune.config import ProviderConfig, Settings
    from writune.polishing.prompt import Prompt


@dataclass(frozen=True, slots=True)
class Generation:
    """Text and observed upstream metrics from one generation attempt."""

    text: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    first_token_latency: float
    generation_tps: float
    total_elapsed: float
    tps_estimated: bool


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _secret_value(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _capability_enabled(capabilities: Mapping[str, Any], name: str, *, section: str | None = None) -> bool:
    """Read direct and grouped capability declarations without guessing support."""

    candidates = [capabilities.get(name), capabilities.get(f"supports_{name}")]
    if section:
        grouped = capabilities.get(section)
        if isinstance(grouped, Mapping):
            candidates.extend((grouped.get(name), grouped.get(f"supports_{name}")))
    params = capabilities.get("parameters")
    if isinstance(params, Mapping):
        candidates.append(params.get(name))
    elif isinstance(params, (list, tuple, set)):
        candidates.append(name in params)
    return any(value is True for value in candidates)


def _event_type(event: object) -> str:
    value = _get(event, "type", "")
    return value if isinstance(value, str) else ""


def _event_text_key(event: object) -> tuple[int, int]:
    """Return the stable (output_index, content_index) key for a text event."""

    def index(name: str) -> int:
        value = _get(event, name, 0)
        if isinstance(value, bool):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed >= 0 else 0

    return index("output_index"), index("content_index")


def _extract_response_text(response: object) -> str:
    output = _get(response, "output", [])
    if not isinstance(output, (list, tuple)):
        return ""
    parts: list[str] = []
    for item in output:
        content = _get(item, "content", [])
        if not isinstance(content, (list, tuple)):
            continue
        for block in content:
            if _get(block, "type") in {"output_text", "text"}:
                text = _get(block, "text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _usage_metrics(response: object) -> tuple[int | None, int | None, int | None, int | None]:
    usage = _get(response, "usage")
    if usage is None:
        return None, None, None, None

    def integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    input_tokens = integer(_get(usage, "input_tokens"))
    output_tokens = integer(_get(usage, "output_tokens"))
    details = _get(usage, "input_tokens_details", {})
    cached = integer(
        _get(details, "cached_tokens", _get(details, "cache_read_input_tokens", _get(usage, "cached_input_tokens")))
    )
    cache_write = integer(
        _get(
            details,
            "cache_creation_input_tokens",
            _get(details, "cache_write_tokens", _get(usage, "cache_write_tokens")),
        )
    )
    return input_tokens, output_tokens, cached, cache_write


def _has_repeating_suffix(text: str, *, minimum_chars: int = 512, max_period: int = 256) -> bool:
    """Return whether the recent suffix repeats exactly over at least 512 chars."""

    if len(text) < minimum_chars:
        return False
    for period in range(1, min(max_period, len(text) // 4) + 1):
        unit = text[-period:]
        repetitions = 1
        # Only inspect enough adjacent copies to establish the minimum suffix
        # length. The recent buffer is bounded by the caller.
        while repetitions < 4 or repetitions * period < minimum_chars:
            start = len(text) - (repetitions + 1) * period
            end = len(text) - repetitions * period
            if start < 0 or text[start:end] != unit:
                break
            repetitions += 1
        if repetitions * period >= minimum_chars and repetitions >= 4:
            return True
    return False


class _StreamingApproxTokenCounter:
    """Count approximate lexical tokens incrementally without rescanning output."""

    def __init__(self) -> None:
        self._tokens = 0
        self._in_word = False

    def observe(self, text: str) -> None:
        for character in text:
            codepoint = ord(character)
            if (
                0x3400 <= codepoint <= 0x4DBF
                or 0x4E00 <= codepoint <= 0x9FFF
                or 0xF900 <= codepoint <= 0xFAFF
                or 0x3040 <= codepoint <= 0x30FF
                or 0xAC00 <= codepoint <= 0xD7AF
            ):
                self._tokens += 1
                self._in_word = False
            elif character.isalnum() or character == "_":
                if not self._in_word:
                    self._tokens += 1
                    self._in_word = True
            elif character.isspace():
                self._in_word = False
            else:
                self._tokens += 1
                self._in_word = False

    @property
    def token_count(self) -> int:
        return self._tokens


class ResponsesProvider:
    """A single-attempt OpenAI or OpenAI-compatible Responses API client.

    Each ``generate`` call creates and closes its own AsyncOpenAI and httpx
    clients. ``transport`` is an optional httpx transport seam for deterministic
    tests. SDK retries are disabled; worker-level retry policy owns retries.
    """

    def __init__(
        self,
        provider: ProviderConfig,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._transport = transport
        self.last_metrics: dict[str, int | float | bool | None] = {}

    def _timeout(self, name: str, default: float, provider: object | None = None) -> float:
        configured_provider = provider or self.provider
        aliases = {
            "first_token_timeout": ("first_token_timeout", "first_text_timeout"),
            "connect_timeout": ("connect_timeout",),
            "inactivity_timeout": ("inactivity_timeout",),
            "total_timeout": ("total_timeout",),
        }
        for attr in aliases.get(name, (name,)):
            value = getattr(configured_provider, attr, None)
            if value is not None:
                try:
                    candidate = float(value)
                    if candidate > 0:
                        return candidate
                except (TypeError, ValueError):
                    pass
        timeout_map = _as_mapping(getattr(configured_provider, "timeouts", {}))
        for attr in aliases.get(name, (name,)):
            try:
                candidate = float(timeout_map.get(attr, 0))
                if candidate > 0:
                    return candidate
            except (TypeError, ValueError):
                pass
        try:
            candidate = float(getattr(self.settings, name, default))
            return candidate if candidate > 0 else default
        except (TypeError, ValueError):
            return default

    def _request_body(
        self,
        model: str,
        prompt: Prompt,
        options: dict[str, Any] | None,
        *,
        provider: object | None = None,
    ) -> dict[str, Any]:
        prefix = getattr(prompt, "prefix", None)
        text = getattr(prompt, "input", None)
        if not isinstance(prefix, str) or not isinstance(text, str):
            raise ProviderError("INVALID_PROMPT", "The prompt must contain text prefix and input fields.", False)

        configured_provider = provider or self.provider
        capabilities = _as_mapping(getattr(configured_provider, "capabilities", {}))
        configured = dict(_as_mapping(getattr(configured_provider, "parameters", {})))
        configured.update(options or {})

        if _capability_enabled(capabilities, "instructions"):
            request_input = text
        else:
            request_input = f"{prefix}\n\n{text}" if prefix else text
        body: dict[str, Any] = {"model": model, "input": request_input, "stream": True}
        if _capability_enabled(capabilities, "instructions") and prefix:
            body["instructions"] = prefix

        known_fields = {"max_output_tokens", "temperature", "reasoning", "text"}
        for field in known_fields:
            if field not in configured:
                continue
            if field == "text":
                if _capability_enabled(capabilities, "text.format") or _capability_enabled(capabilities, "text_format"):
                    body[field] = configured[field]
            elif _capability_enabled(capabilities, field):
                body[field] = configured[field]

        # Unknown provider-specific parameters are carried through extra_body
        # only when the configured capability matrix explicitly allows them.
        extra: dict[str, Any] = {}
        reserved = {"model", "input", "stream", "instructions", "extra_body", "extra_headers", "extra_query"}
        for field, value in configured.items():
            if field in known_fields or field in reserved:
                continue
            if _capability_enabled(capabilities, field):
                extra[field] = value

        cache_config = _as_mapping(getattr(configured_provider, "cache", {}))
        cache_parameters = _as_mapping(cache_config.get("parameters", {}))
        cache_enabled = bool(cache_config.get("enabled", bool(cache_config)))
        if cache_enabled:
            cache_fields = dict(cache_parameters)
            for key in (
                "prompt_cache_key",
                "prompt_cache_retention",
                "cache_control",
                "cache_lifetime",
                "cache_breakpoint",
            ):
                if key in cache_config:
                    cache_fields[key] = cache_config[key]
            if "prompt_cache_key" not in cache_fields and _capability_enabled(
                capabilities, "prompt_cache_key", section="cache"
            ):
                # Stable across paragraphs sharing the same model and prefix;
                # the variable paragraph text and task IDs never enter the key.
                material = f"{model}\0{prefix}".encode("utf-8")
                cache_fields["prompt_cache_key"] = hashlib.sha256(material).hexdigest()
            if "prompt_cache_retention" not in cache_fields:
                retention = cache_config.get("retention", cache_config.get("ttl"))
                if retention is not None:
                    cache_fields["prompt_cache_retention"] = retention
            for field, value in cache_fields.items():
                if _capability_enabled(capabilities, field, section="cache"):
                    extra[field] = value

        if extra:
            body["extra_body"] = extra
        return body

    async def generate(
        self,
        model: str,
        prompt: Prompt,
        options: dict[str, Any] | None = None,
    ) -> Generation:
        self.last_metrics = {
            "input_tokens": None,
            "output_tokens": 0,
            "cached_input_tokens": None,
            "cache_write_tokens": None,
            "first_token_latency": None,
            "generation_tps": 0.0,
            "total_elapsed": 0.0,
            "tps_estimated": True,
        }
        if not model:
            raise ProviderError("INVALID_MODEL", "A model name is required.", False)
        provider = self.provider
        resolver = getattr(provider, "resolve_model", None)
        if callable(resolver):
            provider = resolver(model)
        base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
        if not base_url:
            raise ProviderError("PROVIDER_NOT_CONFIGURED", "The provider URL is not configured.", False)
        api_key = _secret_value(getattr(provider, "api_key", ""))
        if not api_key:
            raise ProviderError("PROVIDER_NOT_CONFIGURED", "The provider API key is not configured.", False)

        connect_timeout = self._timeout("connect_timeout", 8.0, provider)
        first_timeout = self._timeout("first_token_timeout", 120.0, provider)
        inactivity_timeout = self._timeout("inactivity_timeout", 120.0, provider)
        try:
            request_body = self._request_body(model, prompt, options, provider=provider)
        except ProviderError:
            raise
        except (TypeError, ValueError):
            raise ProviderError("INVALID_REQUEST", "The provider request configuration is invalid.", False) from None

        first_text_at: float | None = None
        last_text_at: float | None = None
        delta_texts: dict[tuple[int, int], str] = {}
        done_texts: dict[tuple[int, int], str] = {}
        completed_text = ""
        completed = False
        usage_response: object | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached_input_tokens: int | None = None
        cache_write_tokens: int | None = None
        counter = TokenCounter(getattr(provider, "tokenizer", None))
        request_text = "\n\n".join(
            value
            for value in (request_body.get("instructions"), request_body.get("input"))
            if isinstance(value, str) and value
        )
        input_counter = _StreamingApproxTokenCounter()
        input_counter.observe(request_text)
        estimated_input_token_count = max(
            1,
            input_counter.token_count,
            (len(request_text) + 3) // 4,
        )
        configured_input_token_count = counter.count(request_text)
        input_token_estimate = max(1, configured_input_token_count)
        output_counter = _StreamingApproxTokenCounter()
        configured_output_token_count = 0
        configured_output_pending_parts: list[str] = []
        configured_output_pending_chars = 0
        observed_output_chars = 0

        def input_guard_tokens() -> int:
            if counter.estimated:
                return estimated_input_token_count
            return input_token_estimate

        def output_guard_tokens() -> int:
            if counter.estimated:
                return max(output_counter.token_count, (observed_output_chars + 3) // 4)
            pending_text = "".join(configured_output_pending_parts)
            pending_tokens = counter.count(pending_text)
            if counter.estimated:
                return max(output_counter.token_count, (observed_output_chars + 3) // 4)
            return configured_output_token_count + pending_tokens

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=None,
            write=connect_timeout,
            pool=connect_timeout,
        )
        http_client = httpx.AsyncClient(transport=self._transport, timeout=timeout)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=timeout,
            http_client=http_client,
        )

        async def next_event(iterator: Any) -> object:
            now = time.monotonic()
            if first_text_at is None:
                deadline = started_at + first_timeout
            elif last_text_at is not None:
                deadline = last_text_at + inactivity_timeout
            else:
                deadline = started_at + first_timeout
            remaining = deadline - now
            if remaining <= 0:
                if first_text_at is None:
                    raise ProviderError("FIRST_TOKEN_TIMEOUT", "No output text arrived before the first-text timeout.")
                raise ProviderError("INACTIVITY_TIMEOUT", "The provider stopped producing output text.")
            try:
                return await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except asyncio.TimeoutError:
                if first_text_at is None:
                    raise ProviderError("FIRST_TOKEN_TIMEOUT", "No output text arrived before the first-text timeout.") from None
                raise ProviderError("INACTIVITY_TIMEOUT", "The provider stopped producing output text.") from None

        started_at: float | None = None
        reasoning_text = ""
        visible_recent = ""
        reasoning_recent = ""

        def accumulated_visible_text() -> str:
            return "".join(delta_texts[key] for key in sorted(delta_texts))

        def raise_output_guard(code: str, message: str) -> None:
            error = ProviderError(code, message, False)
            error.partial_text = accumulated_visible_text()
            if reasoning_text:
                error.partial_reasoning = reasoning_text
            raise error

        def record_delta(
            value: str,
            key: tuple[int, int],
            *,
            reasoning: bool = False,
            observed_at: float | None = None,
        ) -> None:
            nonlocal first_text_at, last_text_at, reasoning_text, visible_recent, reasoning_recent
            if not value:
                return
            now = observed_at if observed_at is not None else time.monotonic()
            if first_text_at is None:
                first_text_at = now
            last_text_at = now
            if reasoning:
                reasoning_text += value
                reasoning_recent = (reasoning_recent + value)[-1024:]
            else:
                delta_texts[key] = delta_texts.get(key, "") + value
                visible_recent = (visible_recent + value)[-1024:]
            # Count both visible and reasoning deltas for the relative guards,
            # while retaining a separate visible response. The fallback counter
            # is incremental so long streams do not trigger cumulative rescans.
            nonlocal configured_output_token_count, configured_output_pending_parts
            nonlocal configured_output_pending_chars, observed_output_chars
            output_counter.observe(value)
            observed_output_chars += len(value)
            if not counter.estimated:
                configured_output_pending_parts.append(value)
                configured_output_pending_chars += len(value)
                while configured_output_pending_chars >= 1024 and not counter.estimated:
                    pending_text = "".join(configured_output_pending_parts)
                    offset = 0
                    while len(pending_text) - offset >= 1024 and not counter.estimated:
                        whitespace = pending_text.rfind(" ", offset, offset + 1024)
                        cut = whitespace + 1 if whitespace > offset else offset + 1024
                        configured_output_token_count += counter.count(pending_text[offset:cut])
                        offset = cut
                    remainder = pending_text[offset:]
                    configured_output_pending_parts = [remainder] if remainder else []
                    configured_output_pending_chars = len(remainder)
            elapsed = max(0.0, now - started_at) if started_at is not None else 0.0
            observed_tokens = output_guard_tokens()
            measured_tps = observed_tokens / max(now - first_text_at, 0.001)
            self.last_metrics.update(
                output_tokens=observed_tokens,
                first_token_latency=max(0.0, first_text_at - started_at) if started_at is not None else None,
                generation_tps=measured_tps,
                total_elapsed=elapsed,
                tps_estimated=True,
            )
            if observed_tokens >= input_guard_tokens() * 100:
                raise_output_guard(
                    "OUTPUT_LIMIT_EXCEEDED",
                    "The provider output exceeded the configured relative output limit.",
                )
            if observed_tokens >= input_guard_tokens() * 20 and (
                _has_repeating_suffix(visible_recent) or _has_repeating_suffix(reasoning_recent)
            ):
                raise_output_guard(
                    "REPETITIVE_OUTPUT",
                    "The provider output contained a sustained repeating suffix.",
                )

        try:
            # Start the attempt clock only after local setup and tokenizer
            # discovery, immediately before opening the upstream request.
            started_at = time.monotonic()
            async with client:
                sdk_arguments = dict(request_body)
                sdk_arguments.pop("stream", None)
                try:
                    stream = await asyncio.wait_for(
                        client.responses.create(stream=True, **sdk_arguments),
                        timeout=first_timeout,
                    )
                except asyncio.TimeoutError:
                    raise ProviderError(
                        "FIRST_TOKEN_TIMEOUT",
                        "No output text arrived before the first-text timeout.",
                    ) from None
                try:
                    iterator = stream.__aiter__()
                    while True:
                        try:
                            event = await next_event(iterator)
                        except StopAsyncIteration:
                            break
                        kind = _event_type(event)
                        if kind == "response.output_text.delta":
                            delta = _get(event, "delta", "")
                            if isinstance(delta, str) and delta:
                                record_delta(delta, _event_text_key(event))
                        elif kind in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
                            delta = _get(event, "delta", "")
                            if isinstance(delta, str) and delta:
                                record_delta(delta, _event_text_key(event), reasoning=True)
                        elif kind == "response.output_text.done":
                            final_text = _get(event, "text", "")
                            if isinstance(final_text, str) and final_text:
                                # A done event may precede late deltas on some
                                # compatible streams. It is tentative per content
                                # slot and is ignored once that slot has deltas.
                                key = _event_text_key(event)
                                done_texts[key] = final_text
                        elif kind == "response.completed":
                            usage_response = _get(event, "response", event)
                            input_tokens, output_tokens, cached_input_tokens, cache_write_tokens = _usage_metrics(
                                usage_response
                            )
                            self.last_metrics.update(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens if output_tokens is not None else output_guard_tokens(),
                                cached_input_tokens=cached_input_tokens,
                                cache_write_tokens=cache_write_tokens,
                                tps_estimated=counter.estimated or output_tokens is None,
                            )
                            response_status = _get(usage_response, "status")
                            if isinstance(response_status, str):
                                normalized_status = response_status.casefold()
                                if normalized_status == "failed":
                                    raise ProviderError(
                                        "UPSTREAM_FAILED",
                                        "The provider reported a failed response.",
                                        True,
                                    )
                                if normalized_status in {"incomplete", "cancelled", "canceled"}:
                                    raise ProviderError(
                                        "INCOMPLETE_RESPONSE",
                                        "The provider returned an incomplete response.",
                                        True,
                                    )
                            completed_text = _extract_response_text(usage_response)
                            completed = True
                            break
                        elif kind in {"response.failed", "error", "response.incomplete"}:
                            code = "UPSTREAM_FAILED" if kind != "response.incomplete" else "INCOMPLETE_RESPONSE"
                            message = "The provider reported a failed response." if kind != "response.incomplete" else "The provider returned an incomplete response."
                            raise ProviderError(code, message, True)
                finally:
                    await stream.close()
        except ProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if first_text_at is None:
                raise ProviderError("FIRST_TOKEN_TIMEOUT", "No output text arrived before the first-text timeout.") from None
            raise ProviderError("INACTIVITY_TIMEOUT", "The provider stopped producing output text.") from None
        except AuthenticationError:
            raise ProviderError("AUTHENTICATION_ERROR", "The provider rejected its credentials.", False) from None
        except RateLimitError:
            raise ProviderError("RATE_LIMITED", "The provider rate limit was reached.", True) from None
        except BadRequestError:
            raise ProviderError("INVALID_REQUEST", "The provider rejected the request parameters.", False) from None
        except APITimeoutError as error:
            cause: BaseException | None = error.__cause__
            while cause is not None:
                if isinstance(cause, httpx.ConnectTimeout):
                    raise ProviderError("CONNECT_TIMEOUT", "The provider connection timed out.", True) from None
                cause = cause.__cause__
            raise ProviderError("CONNECTION_TIMEOUT", "The provider connection timed out.", True) from None
        except APIStatusError as error:
            status = error.status_code
            if status == 401 or status == 403:
                raise ProviderError("AUTHENTICATION_ERROR", "The provider rejected its credentials.", False) from None
            if status == 429:
                raise ProviderError("RATE_LIMITED", "The provider rate limit was reached.", True) from None
            retryable = status >= 500 or status in {408, 409, 425}
            if status >= 500:
                code, message = "UPSTREAM_ERROR", "The provider returned a server error."
            else:
                code, message = "INVALID_REQUEST", "The provider rejected the request."
            raise ProviderError(code, message, retryable) from None
        except APIConnectionError:
            raise ProviderError("CONNECTION_ERROR", "The provider connection failed.", True) from None
        except httpx.ConnectTimeout:
            raise ProviderError("CONNECT_TIMEOUT", "The provider connection timed out.", True) from None
        except httpx.TimeoutException:
            raise ProviderError("CONNECTION_TIMEOUT", "The provider connection timed out.", True) from None
        except Exception:
            # Do not include exception text: SDK errors can carry response bodies,
            # request metadata, or credentials supplied by an incompatible proxy.
            raise ProviderError("PROVIDER_ERROR", "The provider request failed.", True) from None
        finally:
            if started_at is not None:
                elapsed = max(0.0, time.monotonic() - started_at)
                measured_tokens = output_tokens if output_tokens is not None else output_guard_tokens()
                measured_tps = measured_tokens / max(elapsed - (first_text_at - started_at), 0.001) if first_text_at is not None else 0.0
                self.last_metrics.update(
                    input_tokens=input_tokens,
                    output_tokens=measured_tokens,
                    cached_input_tokens=cached_input_tokens,
                    cache_write_tokens=cache_write_tokens,
                    first_token_latency=(
                        max(0.0, first_text_at - started_at) if first_text_at is not None else None
                    ),
                    generation_tps=measured_tps,
                    total_elapsed=elapsed,
                    tps_estimated=counter.estimated or output_tokens is None,
                )

        for key in delta_texts.keys() & done_texts.keys():
            if delta_texts[key] != done_texts[key]:
                raise ProviderError(
                    "INCONSISTENT_TEXT",
                    "The provider's completed text slot did not match its streamed text.",
                    True,
                )

        text_slots = delta_texts.keys() | done_texts.keys()
        streamed_text = "".join(
            delta_texts.get(key, done_texts.get(key, ""))
            for key in sorted(text_slots)
        )
        if delta_texts and completed_text and streamed_text != completed_text:
            raise ProviderError(
                "INCONSISTENT_TEXT",
                "The provider's completed response did not match its streamed text.",
                True,
            )
        text = streamed_text or completed_text
        # Enforce both guards after reconciling done/completed-only text as
        # well. Stream-time estimates above use only actual deltas and never
        # count a done or completed event twice.
        final_output_text = text + reasoning_text
        estimated_final_output_tokens = counter.count(final_output_text)
        if counter.estimated:
            estimated_final_output_tokens = max(estimated_final_output_tokens, (len(final_output_text) + 3) // 4)
        guard_input_tokens = max(1, input_tokens) if input_tokens is not None else input_guard_tokens()
        guard_output_tokens = output_tokens if output_tokens is not None else estimated_final_output_tokens
        if guard_output_tokens >= guard_input_tokens * 100:
            error = ProviderError(
                "OUTPUT_LIMIT_EXCEEDED",
                "The provider output exceeded the configured relative output limit.",
                False,
            )
            error.partial_text = text
            if reasoning_text:
                error.partial_reasoning = reasoning_text
            raise error
        if guard_output_tokens >= guard_input_tokens * 20 and (
            _has_repeating_suffix(text[-1024:]) or _has_repeating_suffix(reasoning_text[-1024:])
        ):
            error = ProviderError(
                "REPETITIVE_OUTPUT",
                "The provider output contained a sustained repeating suffix.",
                False,
            )
            error.partial_text = text
            if reasoning_text:
                error.partial_reasoning = reasoning_text
            raise error
        if not completed:
            raise ProviderError("TRUNCATED_RESPONSE", "The provider stream ended before completion.", True)
        if not text.strip():
            raise ProviderError("EMPTY_RESPONSE", "The provider completed without output text.", True)
        if first_text_at is None:
            raise ProviderError("NO_TEXT_DELTA", "The provider completed without a text delta.", True)

        if usage_response is not None:
            input_tokens, output_tokens, cached_input_tokens, cache_write_tokens = _usage_metrics(usage_response)
        elapsed = max(0.0, time.monotonic() - started_at)
        measured_output_tokens = output_tokens if output_tokens is not None else estimated_final_output_tokens
        generation_tps = measured_output_tokens / max(elapsed - (first_text_at - started_at), 0.001)
        self.last_metrics.update(
            input_tokens=input_tokens,
            output_tokens=measured_output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            generation_tps=generation_tps,
            tps_estimated=counter.estimated or output_tokens is None,
        )
        return Generation(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            first_token_latency=max(0.0, first_text_at - started_at),
            generation_tps=generation_tps,
            total_elapsed=elapsed,
            tps_estimated=counter.estimated or output_tokens is None,
        )


__all__ = ["Generation", "ResponsesProvider"]
