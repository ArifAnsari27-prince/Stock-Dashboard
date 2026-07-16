"""Central configuration for the Nasdaq 100 dashboard backend.

All configuration comes from environment variables (CLAUDE.md §4). For local
development, values may be placed in a `.env` file at the repo root; this module
loads that file into `os.environ` on import if present, without requiring a
third-party dependency. In GitHub Actions, the same variables are provided as
repository secrets.

Nothing here performs network I/O. Importing this module is side-effect-free
apart from reading the optional `.env` file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Repo root is two levels up from this file: <root>/src/config.py.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs from a .env file into os.environ.

    Existing environment variables take precedence (we never overwrite them).
    Supports `#` comments, blank lines, optional `export ` prefixes, and
    single/double-quoted values. Intentionally minimal — not a full parser.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Config:
    """Resolved backend configuration.

    Construct via `get_config()`, which reads from the environment. Treat
    instances as immutable.
    """

    # --- Storage ---------------------------------------------------------
    # Directory where Parquet snapshots are written/read. Committed by the
    # scheduled jobs (CLAUDE.md §2).
    data_dir: Path

    # --- SEC EDGAR -------------------------------------------------------
    # Descriptive User-Agent sent on every EDGAR request, "AppName contact@email".
    # Optional at import time so the package imports without it; required at the
    # point of actually calling EDGAR (use `require_sec_user_agent()`).
    sec_user_agent: str | None
    # EDGAR etiquette: never exceed 10 requests/second (CLAUDE.md §2).
    sec_max_requests_per_second: int = 10

    # --- Prices (Massive.com / yfinance) ---------------------------------
    # PRICE_SOURCE: "yfinance" (default) or "massive".
    price_source: str = "yfinance"
    # Massive.com API key (free tier: EOD data, 5 requests/min). Also accepts
    # POLYGON_API_KEY (deprecated alias). Required when price_source=massive.
    massive_api_key: str | None = None
    # Minimum seconds between Massive REST calls. Free tier ≈ 5/min; 13s (~4.6/min)
    # keeps margin so we don't trip the limit under clock skew or brief retries.
    massive_min_request_interval_seconds: float = 13.0

    # --- News (Finnhub free tier) -----------------------------------------
    # Optional: the news job skips gracefully when unset.
    finnhub_api_key: str | None = None
    # Company headlines are fetched for the top-N universe names by market cap
    # (each symbol is one API call; keep this modest for the 60/min free tier).
    news_top_symbols: int = 25

    # --- Storage backend (object store / Cloudflare R2) ------------------
    # DATA_URI selects where snapshots are read/written:
    #   unset             -> local Parquet under data_dir (git-committed, V1 default)
    #   "s3://bucket/pfx"  -> object store (Cloudflare R2 / any S3-compatible)
    #   local path         -> local object store (DuckDB-backed), for testing
    # The object store keeps full history off git (see docs/MULTI_INDEX_DESIGN.md).
    data_uri: str | None = None
    # S3/R2 credentials (required when data_uri is an s3:// URI).
    r2_endpoint: str | None = None  # e.g. https://<acct>.r2.cloudflarestorage.com
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_region: str = "auto"

    # --- Universe --------------------------------------------------------
    # Nasdaq 100 is proxied by QQQ holdings (CLAUDE.md §1).
    universe_etf: str = "QQQ"
    # Expected number of constituents; used as a sanity check, not a hard cap.
    universe_target_size: int = 100

    # --- Benchmarks ------------------------------------------------------
    # Used later for beta/correlation in compute/returns.py (CLAUDE.md §1).
    benchmark_symbols: tuple[str, ...] = ("QQQ", "SPY")

    # --- Refresh cadences ------------------------------------------------
    # Documented here as the single source of truth; the GitHub Actions cron
    # schedules (build step 9) mirror these. Units are noted per field.
    #
    # Prices: every ~15 minutes during US market hours (delayed data, §1).
    price_refresh_minutes: int = 15
    # Daily-bar history pulled on each price refresh. ~2 years gives a safe
    # buffer for the longest technicals in scope: 200-day MA, 252-day
    # volatility, 12-month momentum, and beta (CLAUDE.md §1).
    price_lookback_days: int = 730
    # Fundamentals, filings: daily.
    fundamentals_refresh_hours: int = 24
    filings_refresh_hours: int = 24
    # Universe: weekly.
    universe_refresh_days: int = 7

    def require_sec_user_agent(self) -> str:
        """Return the SEC User-Agent or raise if it is unset.

        Call this from EDGAR code paths so misconfiguration fails loudly there
        rather than silently sending blockable requests.
        """
        if not self.sec_user_agent:
            raise RuntimeError(
                "SEC_USER_AGENT is not set. EDGAR requires a descriptive "
                'User-Agent formatted as "AppName contact@email". '
                "Set it in .env locally or as a repo secret in Actions."
            )
        return self.sec_user_agent

    def s3_config(self) -> dict[str, str] | None:
        """S3/R2 connection settings for DuckDB httpfs, or None for local storage.

        Returns None unless `data_uri` is an s3:// URI. Raises if s3:// is
        selected but credentials are missing, so misconfiguration fails loudly.
        """
        if not (self.data_uri or "").startswith("s3://"):
            return None
        missing = [
            name
            for name, value in (
                ("R2_ENDPOINT", self.r2_endpoint),
                ("R2_ACCESS_KEY_ID", self.r2_access_key_id),
                ("R2_SECRET_ACCESS_KEY", self.r2_secret_access_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"DATA_URI is s3:// but these are unset: {missing}. Set them in "
                ".env locally or as repo/Streamlit secrets."
            )
        return {
            "endpoint": self.r2_endpoint,  # type: ignore[dict-item]
            "key_id": self.r2_access_key_id,  # type: ignore[dict-item]
            "secret": self.r2_secret_access_key,  # type: ignore[dict-item]
            "region": self.r2_region,
        }

    def require_finnhub_api_key(self) -> str:
        """Return the Finnhub API key or raise if unset."""
        if not self.finnhub_api_key:
            raise RuntimeError(
                "FINNHUB_API_KEY is not set. Required for the news refresh job. "
                "Get a free key at https://finnhub.io and set it in .env locally "
                "or as a repo secret in Actions."
            )
        return self.finnhub_api_key

    def require_massive_api_key(self) -> str:
        """Return the Massive API key or raise if unset."""
        if not self.massive_api_key:
            raise RuntimeError(
                "MASSIVE_API_KEY is not set. Required when PRICE_SOURCE=massive. "
                "Get a key at https://massive.com/dashboard and set it in .env "
                "locally or as a repo secret in Actions."
            )
        return self.massive_api_key


def get_config() -> Config:
    """Build a Config from the current environment.

    Reads:
      - DATA_DIR        (optional, defaults to <repo>/data)
      - SEC_USER_AGENT  (optional here, required at EDGAR call sites)
      - PRICE_SOURCE    (optional, "yfinance" or "massive")
      - MASSIVE_API_KEY (required when PRICE_SOURCE=massive)
      - POLYGON_API_KEY (deprecated alias for MASSIVE_API_KEY)
    """
    data_dir_env = os.environ.get("DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else (REPO_ROOT / "data")

    massive_key = os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")

    return Config(
        data_dir=data_dir,
        sec_user_agent=os.environ.get("SEC_USER_AGENT"),
        price_source=os.environ.get("PRICE_SOURCE", "yfinance").strip().lower(),
        massive_api_key=massive_key,
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY"),
        data_uri=os.environ.get("DATA_URI"),
        r2_endpoint=os.environ.get("R2_ENDPOINT"),
        r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        r2_region=os.environ.get("R2_REGION", "auto"),
    )
