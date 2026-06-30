"""Factory for the configured PriceSource implementation."""

from __future__ import annotations

from src.config import Config, get_config
from src.data_sources.base import PriceSource


def get_price_source(config: Config | None = None) -> PriceSource:
    """Return the price adapter selected by PRICE_SOURCE in config/env."""
    config = config or get_config()
    source = config.price_source.strip().lower()

    if source == "massive":
        from src.data_sources.massive_prices import MassivePriceSource

        return MassivePriceSource(
            config.require_massive_api_key(),
            min_request_interval_seconds=config.massive_min_request_interval_seconds,
        )

    if source != "yfinance":
        raise ValueError(
            f"Unknown PRICE_SOURCE={source!r}. Expected 'yfinance' or 'massive'."
        )

    from src.data_sources.prices import YFinancePriceSource

    return YFinancePriceSource()
