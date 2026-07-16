# data/ — frozen dev fixtures

**This directory is frozen** (architecture audit Phase A, owner decision Q2).
Production data lives in Cloudflare R2 (`DATA_URI`), written by the
`refresh_index_*` GitHub Actions workflows. The single latest snapshot per
dataset kept here is a development/test fixture for running the app locally
without R2 credentials (unset `DATA_URI`).

Do not commit new snapshots. The legacy git-commit workflows are disabled
(manual `workflow_dispatch` fallback only). Data here is Nasdaq-100-only and
stale by design.
