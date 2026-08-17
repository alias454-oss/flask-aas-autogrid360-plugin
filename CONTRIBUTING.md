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
- Preserve SQLite and PostgreSQL compatibility. SQL/schema changes need PostgreSQL validation.
- Keep seller privacy, listing ownership, moderation, filesystem paths, uploads, and restore bundles
  as explicit trust boundaries.
- Preserve caller-owned transaction behavior where services participate in larger operations.
- Avoid unnecessary JavaScript, dependencies, abstractions, and proprietary service requirements.
- Keep operator data refresh, maintenance, and migrations deterministic and testable.
- Do not rewrite published migration history. After the initial public migration is frozen, schema
  changes must move forward with new revisions.

## Documentation

Update documentation when a change alters public behavior, configuration, operator workflow, schema
requirements, or the Alpha release target. Keep `README.md` concise; detailed capability tracking
belongs in `OPENAUTO_parity.md`.
