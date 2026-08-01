"""Provider-neutral portfolio boundary.

This module deliberately knows nothing about IBKR Flex or SnapTrade wire formats.
Providers accept a trusted request context and return the portfolio dictionary used
by the rest of the application.  The registry adds canonical provenance fields so
downstream consumers do not need provider-specific branches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


class PortfolioProviderError(RuntimeError):
    """Stable provider-boundary failure suitable for API/tool error handling."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PortfolioRequest:
    """Trusted identifiers resolved by the backend, never by the LLM."""

    user_id: str
    connection_id: str | None = None
    account_id: str | None = None


@runtime_checkable
class PortfolioProvider(Protocol):
    """Contract implemented by each brokerage portfolio source."""

    name: str
    data_source: str

    async def get_snapshot(self, request: PortfolioRequest) -> dict[str, Any]: ...


FetchSnapshot = Callable[[PortfolioRequest], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CallbackPortfolioProvider:
    """Small adapter used to place an existing fetch function behind the seam."""

    name: str
    data_source: str
    fetch: FetchSnapshot

    async def get_snapshot(self, request: PortfolioRequest) -> dict[str, Any]:
        return await self.fetch(request)


_ALIASES = {
    "ibkr": "ibkr_flex",
    "flex": "ibkr_flex",
}


def canonical_provider_name(name: str) -> str:
    value = (name or "").strip().lower()
    return _ALIASES.get(value, value)


def with_provider_provenance(
    snapshot: dict[str, Any], *, provider: str, data_source: str
) -> dict[str, Any]:
    """Return a copy carrying authoritative, provider-neutral provenance.

    ``source`` is intentionally untouched for backward compatibility.  New code
    should use ``provider`` for routing and ``data_source`` for display/audit text.
    """
    if not isinstance(snapshot, dict):
        raise PortfolioProviderError(
            "portfolio_contract_invalid", "portfolio provider returned a non-object"
        )
    return {
        **snapshot,
        "provider": canonical_provider_name(provider),
        "data_source": data_source,
    }


class PortfolioProviderRegistry:
    """Explicit provider registry; duplicate registration fails unless replaced."""

    def __init__(self) -> None:
        self._providers: dict[str, PortfolioProvider] = {}

    def register(self, provider: PortfolioProvider, *, replace: bool = False) -> None:
        name = canonical_provider_name(provider.name)
        if not name:
            raise ValueError("portfolio provider name must not be empty")
        if name in self._providers and not replace:
            raise ValueError(f"portfolio provider already registered: {name}")
        self._providers[name] = provider

    def resolve(self, name: str) -> PortfolioProvider:
        canonical = canonical_provider_name(name)
        provider = self._providers.get(canonical)
        if provider is None:
            raise PortfolioProviderError(
                "portfolio_provider_not_configured",
                f"portfolio provider is not registered: {canonical or '<empty>'}",
            )
        return provider

    async def get_snapshot(
        self, name: str, request: PortfolioRequest
    ) -> dict[str, Any]:
        provider = self.resolve(name)
        snapshot = await provider.get_snapshot(request)
        return with_provider_provenance(
            snapshot,
            provider=provider.name,
            data_source=provider.data_source,
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


portfolio_providers = PortfolioProviderRegistry()
