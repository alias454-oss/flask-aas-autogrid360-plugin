# AutoGrid360

AutoGrid360 is a self-hosted vehicle classifieds and inventory application plugin for Flask-AAS.

It focuses on publishing and managing vehicle inventory for individual sellers and site operators. AutoGrid360 is not an auction platform and does not require payment processing or proprietary marketplace services.

## What it provides

* Seller-owned vehicle listings with Draft, Pending Review, Active, Sale Pending, Sold, Expired, and Removed states.
* Configurable listing approval, re-review, expiration, retention, and public visibility rules.
* Listing image upload, normalization, thumbnails, ordering, and primary-image management.
* Public inventory browsing, Advanced Search, seller pages, postal-radius search, and seller inquiries that fail closed when host mail is unavailable.
* Administrator inventory, moderation, seller, reference-data, backup/restore, and settings tools.
* Automotive reference data for makes, models, vehicle types, drivetrains, and features.
* Versioned seller and full-site inventory backup/restore without exporting Flask-AAS authentication state.
* Public listing metadata, canonical URLs, sitemap, RSS feeds, and a payment calculator with currency-aware monetary entry.
* Scheduled or operator-driven listing lifecycle maintenance.

Flask-AAS provides authentication, accounts, MFA, sessions, host roles, mail, CAPTCHA, audit infrastructure, and plugin lifecycle management.

## Validation

Latest user-confirmed automated baseline after the current measured query/data-loading pass:

```text
AutoGrid360: 372 passed, 20 warnings, 276 subtests passed
Flask-AAS:   453 passed, 13 warnings, 34 subtests passed
```

The current public inventory path has been profiled for query scaling and ORM materialization. Representative 10/20/50-listing pages remain at a fixed 8 SELECTs after removing repeated settings/image work and unused list-route feature loading; sitemap/search/detail paths also use bounded or scalar loading where measurement showed material waste.

The current durable AutoGrid360 migration checkpoint is:

```text
98b97bf7aa67
```

AutoGrid360 has also been exercised with a clean Docker/PostgreSQL deployment from an empty database through:

* host database initialization and seeding;
* plugin schema installation and activation;
* packaged automotive and postal dataset loading;
* public inventory and Advanced Search;
* PostgreSQL-specific query behavior.

SQLite is supported for local development. PostgreSQL is the primary production database target.

## Installation

AutoGrid360 must be installed inside a compatible Flask-AAS checkout:

```text
flask-aas/
└── app/
    └── plugins/
        └── autogrid360/
```

Set up Flask-AAS first, then place the AutoGrid360 checkout at that path.

From the Flask-AAS administrator interface:

1. Enable the application plugin loader and reload the application configuration if required.
2. Enable AutoGrid360.
3. Run the AutoGrid360 schema upgrade when prompted.
4. Reload the application configuration.
5. Load the packaged automotive reference dataset.
6. Load postal data if postal lookup and radius search are required.

The repository includes the durable AutoGrid360 migration history. Normal installations should use
the shipped migration checkpoint rather than generating replacement migration files locally.

Released/supported migration checkpoints are durable upgrade origins. Development-only revisions
created after the latest released checkpoint may be consolidated before the next release so the
permanent history represents the net schema change between supported checkpoints rather than every
intermediate model edit.

## Configuration and storage

Marketplace policy is managed under **AutoGrid360 Admin → Settings**, including:

* listing approval and re-review;
* listing expiration and retention;
* Sale Pending and Sold public visibility;
* seller restore policy;
* currency and distance display;
* listing-image storage.

The configured currency symbol, decimal separator, and thousands separator also govern human-entered monetary values. Listing price, Payment Calculator amount/down payment, and Advanced Search price ranges accept configured human-readable formatting while stored and machine-facing values remain canonical decimals. Listing and Payment Calculator monetary fields provide a read-only live formatted preview without rewriting the submitted input.

Normalized listing images default to:

```text
uploads/listings
```

Relative image paths resolve from the host project root. Absolute paths are also supported.

Production deployments should use storage that survives container or instance replacement. Changing the configured image path does not move existing files.

`AUTOGRID360_IMAGE_ROOT` acts as an initial deployment seed or fallback before persisted AutoGrid360 settings exist.

Additional deployment settings control upload sizes, image counts, pagination, feed size, and backup/restore limits.

## Operator commands

AutoGrid360 exposes its CLI through the Flask-AAS plugin runner:

```bash
python manage.py plugin run autogrid360 status
python manage.py plugin run autogrid360 reference seed
python manage.py plugin run autogrid360 postal sync
python manage.py plugin run autogrid360 maintenance
```

Inventory portability commands are available under `inventory`:

```bash
python manage.py plugin run autogrid360 inventory --help
```

Lifecycle scheduling belongs to the deployment environment, such as cron, systemd, or the hosting platform. AutoGrid360 does not embed its own scheduler.

## Development

Develop AutoGrid360 inside a working Flask-AAS checkout.

The development helper uses the enclosing Flask-AAS Dockerfile and `.env`:

```bash
./scripts/dev.sh build
./scripts/dev.sh run
./scripts/dev.sh shell
```

Run AutoGrid360 tests from the Flask-AAS repository root:

```bash
python -m pytest app/plugins/autogrid360/tests
```

Run the complete host suite when a change affects Flask-AAS integration or shared contracts:

```bash
python -m pytest
```

Changes involving SQL behavior, schema lifecycle, or deployment should also be exercised against PostgreSQL. SQLite can permit SQL behavior that PostgreSQL rejects.

When consolidating unpublished migration revisions, back up the current development database and
migration tree, generate the rolled-up revision, re-identify/stamp the known-equivalent development
database at the new head, run the complete plugin regression suite, and remove the backups only after
validation succeeds. Do not rewrite a released checkpoint that real deployments may need as an
upgrade origin.

## Project boundaries

AutoGrid360 currently does not provide:

* auctions or bidding;
* payment processing, financing, or escrow;
* dealer/team organizations and delegated staff roles;
* mandatory proprietary data providers;
* full localization/i18n;
* hosted SaaS billing and account orchestration.

These capabilities may be added when there is a concrete requirement without weakening the self-hosted core.

## More detail

* [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution and development rules.
* [`CHANGELOG.md`](CHANGELOG.md) — implementation history.

## License

See [`LICENSE`](LICENSE).
