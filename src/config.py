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
from dataclasses import dataclass, field
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


def get_config() -> Config:
    """Build a Config from the current environment.

    Reads:
      - DATA_DIR        (optional, defaults to <repo>/data)
      - SEC_USER_AGENT  (optional here, required at EDGAR call sites)
    """
    data_dir_env = os.environ.get("DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else (REPO_ROOT / "data")

    return Config(
        data_dir=data_dir,
        sec_user_agent=os.environ.get("SEC_USER_AGENT"),
    )
