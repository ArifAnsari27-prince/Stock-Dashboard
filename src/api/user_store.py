"""Saved screener configurations — the dashboard's small mutable user store.

Screens are persisted as one `saved_screens` dataset (latest-only) through the
same `Storage` backend as everything else, so they live on R2 in production and
survive Streamlit Community Cloud's ephemeral filesystem. This is deliberately
the simplest free-tier persistence that works for a single user (audit Q5); if
the app ever becomes multi-user this module is the seam to swap for a real DB.

The filter/sort spec itself is opaque JSON owned by the frontend
(`app/screener_filters.py`), so UI schema changes never require a model change.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache

from src.models import DataSource, Provenance, SavedScreen, Snapshot
from src.storage.base import Storage

logger = logging.getLogger(__name__)

SAVED_SCREENS_DATASET = "saved_screens"


def _write(storage: Storage, screens: list[SavedScreen]) -> None:
    storage.write_snapshot(
        SAVED_SCREENS_DATASET,
        Snapshot[SavedScreen](
            provenance=Provenance(
                source=DataSource.USER,
                fetched_at=datetime.now(timezone.utc),
                disclaimer="user-authored screener configurations",
                notes=f"{len(screens)} saved screens",
            ),
            rows=screens,
        ),
    )


def list_screens(storage: Storage) -> dict[str, dict]:
    """All saved screens as {name: params_dict}, newest save wins per name."""
    try:
        df = storage.read_latest(SAVED_SCREENS_DATASET)
    except FileNotFoundError:
        return {}
    if df.empty or "name" not in df.columns:
        return {}
    screens: dict[str, dict] = {}
    for record in df.sort_values("updated_at").to_dict("records"):
        try:
            screens[str(record["name"])] = json.loads(record["params_json"])
        except (ValueError, TypeError, KeyError):
            logger.warning("Skipping unreadable saved screen: %r", record.get("name"))
    return screens


def save_screen(storage: Storage, name: str, params: dict) -> dict[str, dict]:
    """Create or overwrite the screen `name`; return the updated collection."""
    name = name.strip()
    if not name:
        raise ValueError("Screen name must not be empty")
    screens = list_screens(storage)
    screens[name] = params
    _write(
        storage,
        [
            SavedScreen(
                name=screen_name,
                params_json=json.dumps(screen_params),
                updated_at=datetime.now(timezone.utc),
            )
            for screen_name, screen_params in screens.items()
        ],
    )
    logger.info("Saved screen '%s' (%d total)", name, len(screens))
    return screens


@lru_cache(maxsize=1)
def _default_storage() -> Storage:
    """The configured storage backend (mirrors read_api.default_read_api)."""
    from src.config import get_config
    from src.storage.factory import get_storage

    return get_storage(get_config())


def list_saved_screens() -> dict[str, dict]:
    """Module-level convenience for the frontend: all screens via default storage."""
    return list_screens(_default_storage())


def save_saved_screen(name: str, params: dict) -> dict[str, dict]:
    """Module-level convenience for the frontend: save via default storage."""
    return save_screen(_default_storage(), name, params)


def delete_saved_screen(name: str) -> dict[str, dict]:
    """Module-level convenience for the frontend: delete via default storage."""
    return delete_screen(_default_storage(), name)


def delete_screen(storage: Storage, name: str) -> dict[str, dict]:
    """Remove the screen `name` if present; return the updated collection."""
    screens = list_screens(storage)
    if screens.pop(name, None) is not None:
        _write(
            storage,
            [
                SavedScreen(
                    name=screen_name,
                    params_json=json.dumps(screen_params),
                    updated_at=datetime.now(timezone.utc),
                )
                for screen_name, screen_params in screens.items()
            ],
        )
        logger.info("Deleted screen '%s' (%d remain)", name, len(screens))
    return screens
