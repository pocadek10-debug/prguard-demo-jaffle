FROM python:3.11-slim

# git is needed for the base-manifest git-worktree generation path (M9).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY prguard /app/prguard

# dbt-core + dbt-duckdb are pinned together: DuckDB is used only as a
# zero-credential "parse-only" adapter target (see prguard/manifests.py) so
# `dbt parse` can run without real warehouse connections, regardless of the
# analyzed project's actual adapter. Verified against dbt-core 1.12 /
# dbt-duckdb 1.11 (schema https://schemas.getdbt.com/dbt/manifest/v12.json).
RUN pip install --no-cache-dir \
        "dbt-core>=1.8,<2.0" \
        "dbt-duckdb>=1.8,<2.0" \
        .

ENTRYPOINT ["prguard"]
