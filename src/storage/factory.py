"""Factory for the configured Storage backend.

DATA_URI unset  -> local git-committed Parquet (ParquetStore, the V1 default).
DATA_URI set    -> DuckDB object store (ObjectStore): a local path for testing,
                   or an s3://bucket/prefix on Cloudflare R2 in production.
"""

from __future__ import annotations

from src.config import Config, get_config
from src.storage.base import Storage


def get_storage(config: Config | None = None) -> Storage:
    """Return the Storage backend selected by DATA_URI in config/env."""
    config = config or get_config()

    if config.data_uri:
        from src.storage.object_store import ObjectStore

        return ObjectStore(config.data_uri, s3_config=config.s3_config())

    from src.storage.parquet_store import ParquetStore

    return ParquetStore(config.data_dir)
