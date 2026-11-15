"""Provider and model reference resolution for configured Responses adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .base import ProviderError
from .responses import ResponsesProvider

ProviderFactory = Callable[..., ResponsesProvider]

_ADAPTERS: dict[str, ProviderFactory] = {
    "responses": ResponsesProvider,
    "openai_responses": ResponsesProvider,
    "openai_compatible_responses": ResponsesProvider,
}


def register_adapter(name: str, factory: ProviderFactory) -> None:
    """Register an adapter factory under a stable adapter name."""

    if not name or not isinstance(name, str):
        raise ValueError("Adapter name must be a non-empty string.")
    _ADAPTERS[name.casefold()] = factory


def split_model(reference: str, settings: object) -> tuple[str, str]:
    """Split ``provider/model`` references and validate the configured provider.

    An unqualified model is accepted only when a ``default`` provider exists or
    exactly one provider is configured. Model names may themselves contain
    slashes; only the first slash is treated as the provider separator.
    """

    reference = reference.strip() if isinstance(reference, str) else ""
    providers = getattr(settings, "providers", {})
    if not isinstance(providers, Mapping) or not providers:
        raise ProviderError("PROVIDER_NOT_CONFIGURED", "No upstream provider is configured.", False)
    if "/" in reference:
        provider_name, model_name = reference.split("/", 1)
    elif "default" in providers:
        provider_name, model_name = "default", reference
    elif len(providers) == 1:
        provider_name, model_name = next(iter(providers)), reference
    else:
        raise ProviderError("INVALID_MODEL_REFERENCE", "Use a provider/model reference.", False)
    if not provider_name or not model_name or provider_name not in providers:
        raise ProviderError("INVALID_MODEL_REFERENCE", "The model reference is not configured.", False)
    return provider_name, model_name


def get_adapter(name: str = "responses") -> ProviderFactory:
    """Return a registered adapter factory or a sanitized configuration error."""

    factory = _ADAPTERS.get(name.casefold())
    if factory is None:
        raise ProviderError("UNKNOWN_ADAPTER", "The configured provider adapter is unavailable.", False)
    return factory


def get_provider(
    provider_name: str,
    settings: object,
    *,
    model: str | None = None,
    adapter: str | None = None,
    **kwargs: Any,
) -> ResponsesProvider:
    """Construct a provider adapter for one configured provider name."""

    providers = getattr(settings, "providers", {})
    if not isinstance(providers, Mapping) or provider_name not in providers:
        raise ProviderError("PROVIDER_NOT_CONFIGURED", "The requested provider is not configured.", False)
    configured=providers[provider_name]
    if model:
        resolver = getattr(configured, "resolve_model", None)
        if callable(resolver):
            configured = resolver(model)
    return get_adapter(adapter or getattr(configured,"adapter","responses"))(configured, settings, **kwargs)


def provider_for_model(reference: str, settings: object, **kwargs: Any) -> tuple[ResponsesProvider, str]:
    """Resolve a model reference and return its provider adapter and model ID."""

    provider_name, model_name = split_model(reference, settings)
    return get_provider(provider_name, settings, model=model_name, **kwargs), model_name


__all__ = ["get_adapter", "get_provider", "provider_for_model", "register_adapter", "split_model"]
