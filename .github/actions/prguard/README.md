# PRGuard for dbt

A GitHub Action that runs on every pull request in a **dbt** project, reads dbt's own
`manifest.json`, and posts a single, self-updating PR comment that flags:

1. **Downstream impact** — which models and exposures (dashboards/reports) are affected
   by the changed models.
2. **Missing tests** — changed models with no tests, and key columns missing
   `not_null`/`unique`.
3. **Breaking changes** — removed/renamed columns or removed models that downstream
   nodes depend on.
4. **Test-coverage delta** — % of models with ≥ 1 test, before vs after.

PRGuard is **metadata-only**: it never connects to your warehouse, needs no warehouse
credentials, and only reads what `dbt parse` produces. That is a deliberate scope
decision, not a missing feature — see [Design notes](#design-notes--limitations).

## Example comment

```
<!-- prguard:comment:v1 -->
## 🛡️ PRGuard — 1 breaking, 4 warnings

**Breaking changes**
- ⛔ `stg_payments.payment_method` was removed → affects **2 models**, **1 exposure** (`customers`, `orders`, "Customer Dashboard")
- ⚠️ `stg_customers.customer_id` disappeared while `id` appeared — possible rename (unverified) → affects **1 model**, **1 exposure** (`customers`, "Customer Dashboard")

**Missing tests**
- ⚠️ `stg_customers` has no tests; key column `id` has no not_null/unique
- ⚠️ `stg_signups` (new) has no tests

**Downstream impact**
- 2 changed models → **1 downstream model**, **1 exposure** affected

**Coverage**
- 100% → 67% (−33%) of models have ≥ 1 test

<details><summary>Full impact list</summary>
- model `customers`
- exposure "Customer Dashboard"
</details>

<sub>PRGuard ran on 6 models · metadata-only</sub>
```

(This is the real output of the end-to-end test in `tests/test_e2e_jaffle_shop.py`,
run with `python -m prguard.cli --dry-run`.)

## Setup

Add `.github/workflows/prguard.yml` to your dbt repo (see
[`examples/workflow.yml`](examples/workflow.yml)):

```yaml
name: PRGuard

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write
  checks: write

jobs:
  prguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # need full history so the base ref can be checked out

      - uses: <you>/prguard-dbt@v1
        with:
          dbt-project-dir: "."
          # base-manifest-path: path/to/base-manifest.json  # optional fast path
```

`fetch-depth: 0` matters: PRGuard needs to check out the PR's base ref into a git
worktree to generate the base manifest (see [How base + head manifests are
obtained](#how-base--head-manifests-are-obtained)).

Optionally, drop a `prguard.yml` in your repo root (see
[`examples/prguard.yml`](examples/prguard.yml)) to configure thresholds, `strict`
mode, and ignore patterns. All keys are optional.

```yaml
version: 1
dbt_project_dir: "."          # where dbt_project.yml lives
strict: false                 # fail the check on breaking changes
coverage:
  warn_below: 0.7             # warn if head coverage < 70%
ignore:
  models: ["stg_legacy_*"]    # glob patterns to skip
  paths: ["models/staging/vendor/**"]
comment:
  show_details: true
```

### Required permissions

The workflow needs:

- `contents: read` — to check out the repo.
- `pull-requests: write` — to post/update the PR comment.
- `checks: write` — to create the `PRGuard` check run.

## How base + head manifests are obtained

PRGuard needs two manifests to diff. In order of preference:

1. **Self-contained (default).** PRGuard runs `dbt deps` (if `packages.yml` exists) +
   `dbt parse` on the checked-out PR branch for the **head** manifest, then checks out
   the PR's base ref into a temporary `git worktree` and does the same there for the
   **base** manifest.
2. **Fast path.** Pass `base-manifest-path` pointing at a pre-built base manifest (for
   example, a workflow artifact uploaded by your default branch's own CI run). PRGuard
   then skips generating the base manifest itself.

`dbt parse` never opens a warehouse connection — it only needs a *loadable* adapter to
resolve macros. PRGuard exploits this: it generates a scratch `profiles.yml` pointing
at an **in-memory DuckDB** target, using whichever `profile:` name your
`dbt_project.yml` declares, regardless of what your project's real warehouse is. This
is what lets `dbt parse` succeed with **zero warehouse credentials** in CI. See
`prguard/manifests.py`.

## Configuration reference

| Key | Default | Meaning |
| --- | --- | --- |
| `dbt_project_dir` | `.` | Path to the dbt project (where `dbt_project.yml` lives). |
| `strict` | `false` | If `true`, the `PRGuard` check run fails (`conclusion: failure`) when any breaking change is detected. If `false`, the check is always `neutral` — the PR comment still surfaces breaking changes and warnings. |
| `coverage.warn_below` | `0.7` | Overall severity is bumped to `warnings` if head test coverage drops below this ratio. |
| `ignore.models` | `[]` | Glob patterns (matched against model name) to exclude from all analysis. |
| `ignore.paths` | `[]` | Glob patterns (matched against the model's file path) to exclude from all analysis. |
| `comment.show_details` | `true` | Whether to include the `<details>` full impact list in the comment. |

Unknown keys anywhere in the file are a hard error, so typos don't fail silently.

## Local dev / manual test recipe

```bash
pip install -e '.[dev]'
pytest

# Eyeball the rendered comment for a manifest pair without touching GitHub:
python -m prguard.cli --base tests/fixtures/jaffle_shop_base_manifest.json \
                       --head tests/fixtures/jaffle_shop_head_manifest.json \
                       --dry-run
```

### Regenerating the jaffle_shop fixture against a real dbt project

`tests/fixtures/jaffle_shop_*_manifest.json` are hand-crafted to mirror
[dbt-labs' `jaffle_shop` sample project](https://github.com/dbt-labs/jaffle-shop) —
staging models → marts → an exposure — so the test suite stays hermetic and fast. To
validate against a *real* `dbt parse` output instead:

```bash
git clone https://github.com/dbt-labs/jaffle-shop.git
cd jaffle-shop
dbt deps && dbt parse            # uses a dummy DuckDB profile, see above
cp target/manifest.json path/to/base_manifest.json

# make a breaking change on a branch, e.g. rename a staging column
dbt parse
cp target/manifest.json path/to/head_manifest.json

python -m prguard.cli --base path/to/base_manifest.json \
                       --head path/to/head_manifest.json --dry-run
```

## Design notes / limitations

- **Metadata-only is a product decision, not a shortcut.** PRGuard never connects to a
  warehouse: no credentials to manage, no on-call, nothing that breaks when a vendor
  changes an API. This also means it cannot see real column types or row-level data —
  see the next point.
- **Column-level breaking changes are best-effort.** Without `catalog.json` (which
  needs a live warehouse connection), PRGuard cannot see a model's actual columns. It
  only sees YAML-declared `columns:` docs and dbt model contracts
  (`config.contract.enforced`). A column that exists in the warehouse but isn't
  documented in YAML is invisible to PRGuard. Renames are a **heuristic** (a removed
  column + an added column on the same model, in the same PR) and are always reported
  as a lower-severity "possible rename," never asserted as fact.
- **No `dbt-artifacts-parser` dependency.** The manifest schema drifts across dbt
  versions; rather than pin a parser library (and inherit its own compatibility
  matrix), PRGuard reads the handful of documented `manifest.json` fields directly off
  the parsed JSON dict (`prguard/manifests.py`) and fails clearly on an unrecognized
  `dbt_schema_version`.
- **Determinism.** Every list the analysis layer produces is a `frozenset`/sorted
  tuple, and rendering sorts again — the same `(base, head, config)` always produces a
  byte-identical comment, so the idempotent PR comment doesn't churn on an empty re-run.
- **Out of scope for v1** (see the spec): live warehouse checks, freshness/volume
  anomaly detection, ML-based detection, Slack/email alerts, a hosted dashboard, and
  non-dbt stacks. These are explicitly deferred to keep the maintenance profile low.

## Repository layout

```
prguard/
  action.yml              # GitHub Action metadata (Docker container action)
  Dockerfile
  pyproject.toml
  prguard/
    cli.py                 # entrypoint: config -> manifests -> report -> render -> GitHub
    manifests.py            # load/generate base+head manifest.json
    config.py               # prguard.yml loading + validation
    render.py                # Report -> Markdown, idempotency marker
    github.py                 # find/update PR comment, check run
    analysis/
      diff.py                  # diff_nodes: added/removed/modified
      impact.py                 # downstream_impact: child_map BFS + exposures
      coverage.py                # test_coverage, missing_test_findings
      breaking.py                 # breaking_changes (best-effort)
      report.py                    # build_report: assembles everything
  tests/
    fixtures/                # hand-made manifests + a jaffle_shop-shaped pair
    test_*.py
  examples/
    prguard.yml
    workflow.yml
```

## Testing

```bash
pytest            # 67 unit + end-to-end tests, all pure/hermetic (no network, no dbt)
```

The end-to-end test (`tests/test_e2e_jaffle_shop.py`) exercises the full pipeline
against the jaffle_shop-shaped fixture pair and asserts the acceptance criteria from
the spec: a renamed column is flagged with the correct downstream models/exposures, a
silently-removed column is flagged as breaking, a new untested model produces a
missing-tests warning, the coverage delta is correct, and `strict` mode changes the
CLI's exit code.
