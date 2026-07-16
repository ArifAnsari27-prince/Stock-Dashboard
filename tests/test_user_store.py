"""Tests for the saved-screens user store (network-free, tmp_path storage)."""

from __future__ import annotations

import pytest

from src.api.user_store import delete_screen, list_screens, save_screen
from src.storage.parquet_store import ParquetStore


def test_roundtrip_save_list_delete(tmp_path):
    storage = ParquetStore(tmp_path)
    assert list_screens(storage) == {}

    spec = {"numeric": [{"column": "rsi_14", "max": 30.0}], "sort_by": "market_cap"}
    screens = save_screen(storage, "oversold", spec)
    assert screens == {"oversold": spec}
    assert list_screens(storage) == {"oversold": spec}

    save_screen(storage, "growth", {"numeric": [{"column": "revenue_growth", "min": 0.2}]})
    assert set(list_screens(storage)) == {"oversold", "growth"}

    # Overwrite keeps one entry per name.
    save_screen(storage, "oversold", {"numeric": []})
    assert list_screens(storage)["oversold"] == {"numeric": []}

    remaining = delete_screen(storage, "oversold")
    assert set(remaining) == {"growth"}
    assert set(list_screens(storage)) == {"growth"}

    # Deleting a missing name is a no-op.
    assert set(delete_screen(storage, "nope")) == {"growth"}


def test_empty_name_rejected(tmp_path):
    storage = ParquetStore(tmp_path)
    with pytest.raises(ValueError):
        save_screen(storage, "   ", {})
