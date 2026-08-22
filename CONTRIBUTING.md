# Contributing to AutoGrid360

AutoGrid360 accepts focused changes that improve the vehicle marketplace and inventory workflow
without weakening its self-hosted, low-dependency design.

## Development setup

Develop the plugin inside a working Flask-AAS checkout at `app/plugins/autogrid360/` and follow the
Flask-AAS setup instructions for the host environment.

Run the plugin suite from the Flask-AAS repository root:

```bash
python -m pytest app/plugins/autogrid360/tests
```

Run the full Flask-AAS suite when a change affects host integration or a shared contract:

```bash
python -m pytest
```

## Engineering rules

- Keep AutoGrid360 concerns inside the plugin; reuse Flask-AAS for authentication, accounts, mail,
  CAPTCHA, audit, profile identity, and other host services.
- Prefer small, reviewable changes with focused regression coverage.
- For performance work, measure query count/object loading first and fix demonstrated N+1, eager-loading,
  or full-materialization problems with the smallest route-local change; do not add caches, indexes,
  or global loader changes from intuition alone.
- Preserve SQLite and PostgreSQL compatibility. SQL/schema changes need PostgreSQL validation.
- Keep seller privacy, listing ownership, moderation, filesystem paths, uploads, and restore bundles
  as explicit trust boundaries.
- Preserve caller-owned transaction behavior where services participate in larger operations.
- Avoid unnecessary JavaScript, dependencies, abstractions, and proprietary service requirements.
- Keep operator data refresh, maintenance, and migrations deterministic and testable.
- Treat released/supported migration checkpoints as durable upgrade origins. Development-only
  revisions created after the latest released checkpoint may be rolled up before the next release so
  permanent history records the net schema change rather than every intermediate edit.
- Before consolidating unpublished migration history, back up the development database and migration
  tree, regenerate the rolled-up revision, re-identify/stamp the known-equivalent development database
  at the new head, run the full AutoGrid360 regression suite, and remove backups only after validation
  succeeds.

## Documentation

Update documentation when a change alters public behavior, configuration, operator workflow, schema
requirements, or the release target. Keep `README.md` concise; detailed capability tracking belongs in
`OPENAUTO_parity.md`.
