# Vendored copy — do not edit here

This directory is a verbatim copy of
[pocadek10-debug/prguard-dbt](https://github.com/pocadek10-debug/prguard-dbt),
vendored only because that repository is still private and GitHub Actions
cannot resolve `uses:` against a private action repo from another repository.

Once the action repo is public, delete this directory and change
`.github/workflows/prguard.yml` to:

```yaml
- uses: pocadek10-debug/prguard-dbt@v1
```

Make changes in the action repo, never here.
