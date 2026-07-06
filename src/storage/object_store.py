"""DuckDB-backed object store: local files now, Cloudflare R2 (S3) in production.

This is the scalable storage backend (see docs/MULTI_INDEX_DESIGN.md). It keeps
full history off git by writing Parquet to object storage and querying it with
DuckDB (column + row-group pruning), so the app repo stays lean and deployable.

Two write patterns:
  - `write_latest(dataset, snapshot)`  -> `<base>/<dataset>/latest.parquet` (overwrite).
    For precomputed, always-current tables (metrics, fundamentals, aggregates).
  - `write_partition(dataset, key, snapshot)` -> `<base>/<dataset>/<key>/data.parquet`.
    Append-only history (e.g. prices partitioned by `date=YYYY-MM-DD`); a re-run
    overwrites only that one partition, never the whole history.

Reads:
  - `read_latest(dataset)`  -> the latest table (FileNotFoundError if absent).
  - `read_dataset(dataset, columns=, where=)` -> all partitions unioned, with
    optional projection/filter pushed into DuckDB.
  - `query(sql)` -> escape hatch for the read layer.

`base_uri` is a local path (or `file://`) for dev, or `s3://bucket/prefix` for R2.
The same code path serves both; only DuckDB's S3 secret differs. Implements the
`Storage` ABC (`write_snapshot` == `write_latest`, `read_history` == `read_dataset`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd

from src.models import Snapshot
from src.storage.base import Storage

logger = logging.getLogger(__name__)

_PROVENANCE = ("_source", "_fetched_at", "_disclaimer", "_notes")


def _snapshot_to_frame(snapshot: Snapshot) -> pd.DataFrame:
    """Flatten a snapshot's rows + provenance into one DataFrame (matches ParquetStore)."""
    df = pd.DataFrame([row.model_dump() for row in snapshot.rows])
    prov = snapshot.provenance
    df["_source"] = prov.source.value
    df["_fetched_at"] = pd.Timestamp(prov.fetched_at)
    df["_disclaimer"] = prov.disclaimer
    df["_notes"] = prov.notes
    return df


def _sql_str(value: str) -> str:
    """Single-quote-escape a string for inlining into DuckDB SQL."""
    return "'" + value.replace("'", "''") + "'"


class ObjectStore(Storage):
    """Stores dataset snapshots as Parquet in object storage, queried via DuckDB."""

    def __init__(self, base_uri: str, *, s3_config: dict[str, str] | None = None) -> None:
        """Create a store rooted at `base_uri` (local path/`file://`, or `s3://…`).

        `s3_config` (endpoint/key_id/secret/region) is required for `s3://` bases
        and ignored for local ones.
        """
        if base_uri.startswith("file://"):
            base_uri = base_uri[len("file://") :]
        self._base = base_uri.rstrip("/")
        self._is_s3 = self._base.startswith("s3://")
        self._s3_config = s3_config

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        if self._is_s3:
            if not self._s3_config:
                raise RuntimeError("s3:// base_uri requires s3_config (R2 credentials)")
            endpoint = self._s3_config["endpoint"].replace("https://", "").replace("http://", "")
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute(
                f"""CREATE OR REPLACE SECRET r2 (
                    TYPE S3,
                    KEY_ID {_sql_str(self._s3_config['key_id'])},
                    SECRET {_sql_str(self._s3_config['secret'])},
                    ENDPOINT {_sql_str(endpoint)},
                    REGION {_sql_str(self._s3_config.get('region', 'auto'))},
                    URL_STYLE 'path',
                    USE_SSL true
                );"""
            )
        return con

    def _path(self, *parts: str) -> str:
        return "/".join([self._base, *parts])

    def _ensure_local_parent(self, path: str) -> None:
        """Create the parent directory for a local write (no-op for s3)."""
        if not self._is_s3:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def _copy_frame(self, con: duckdb.DuckDBPyConnection, df: pd.DataFrame, path: str) -> None:
        self._ensure_local_parent(path)
        con.register("_snapshot_tmp", df)
        con.execute(f"COPY _snapshot_tmp TO {_sql_str(path)} (FORMAT PARQUET);")
        con.unregister("_snapshot_tmp")

    # --- writes ----------------------------------------------------------

    def write_latest(self, dataset: str, snapshot: Snapshot) -> str:
        """Overwrite `<dataset>/latest.parquet` with this snapshot; return the path."""
        path = self._path(dataset, "latest.parquet")
        with self._connect() as con:
            self._copy_frame(con, _snapshot_to_frame(snapshot), path)
        logger.info("Wrote latest %s (%d rows) -> %s", dataset, len(snapshot.rows), path)
        return path

    def write_partition(self, dataset: str, partition_key: str, snapshot: Snapshot) -> str:
        """Overwrite one partition `<dataset>/<partition_key>/data.parquet`; return the path.

        `partition_key` is a hive-style key such as `date=2026-06-30`.
        """
        path = self._path(dataset, partition_key, "data.parquet")
        with self._connect() as con:
            self._copy_frame(con, _snapshot_to_frame(snapshot), path)
        logger.info("Wrote %s/%s (%d rows)", dataset, partition_key, len(snapshot.rows))
        return path

    def write_snapshot(self, name: str, snapshot: Snapshot) -> str:
        """Storage ABC hook — defaults to latest-only semantics."""
        return self.write_latest(name, snapshot)

    # --- reads -----------------------------------------------------------

    def read_latest(self, dataset: str) -> pd.DataFrame:
        """Read `<dataset>/latest.parquet`; FileNotFoundError if it doesn't exist."""
        path = self._path(dataset, "latest.parquet")
        try:
            with self._connect() as con:
                return con.execute(f"SELECT * FROM read_parquet({_sql_str(path)})").df()
        except (duckdb.IOException, duckdb.CatalogException) as exc:
            raise FileNotFoundError(f"No latest snapshot for dataset '{dataset}'") from exc

    def read_dataset(
        self,
        dataset: str,
        *,
        columns: list[str] | None = None,
        where: str | None = None,
    ) -> pd.DataFrame:
        """Read all partitions of `<dataset>` unioned; empty frame if none exist.

        `columns` projects; `where` is a raw DuckDB predicate pushed into the scan
        (e.g. "symbol = 'NVDA'"). Both keep large-history reads cheap.
        """
        glob = self._path(dataset, "*", "data.parquet")
        select = ", ".join(columns) if columns else "*"
        # hive_partitioning=false: the `date=...` dir names must NOT be parsed into a
        # column (that would collide with the row-level `date` column). Only real
        # Parquet columns are read.
        sql = (
            f"SELECT {select} FROM read_parquet({_sql_str(glob)}, "
            "union_by_name=true, hive_partitioning=false)"
        )
        if where:
            sql += f" WHERE {where}"
        try:
            with self._connect() as con:
                return con.execute(sql).df()
        except (duckdb.IOException, duckdb.CatalogException):
            return pd.DataFrame()

    def read_history(self, name: str) -> pd.DataFrame:
        """Storage ABC hook — all partitions unioned."""
        return self.read_dataset(name)

    def query(self, sql: str) -> pd.DataFrame:
        """Run an arbitrary DuckDB query (paths must be full URIs). For the read layer."""
        with self._connect() as con:
            return con.execute(sql).df()
