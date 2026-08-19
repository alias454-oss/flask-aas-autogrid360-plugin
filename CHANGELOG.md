# AutoGrid360 Changelog

This changelog records implementation changes to the Flask-AAS AutoGrid360 application plugin.


## 2026-08-19 — First durable migration checkpoint

- Froze the first durable AutoGrid360 migration baseline as `98b97bf7aa67_initial_autogrid360_schema.py`, replacing the earlier ignored/provisional development chain through `6e71dd7952ab`.
- The durable baseline represents the complete accepted current AutoGrid360 schema and owns only the `plugin_autogrid360_*` namespace plus its independent `plugin_autogrid360_alembic_version` state.
- Re-identified the existing development database at the new durable head after confirming the schema was already equivalent; normal plugin startup then returned `ACTIVE` with `current=98b97bf7aa67` and `head=98b97bf7aa67`.
- Completed the full post-PostgreSQL-facet-fix AutoGrid360 regression at **347 passed, 15 warnings, 256 subtests passed in 55.02s**. The enclosing Flask-AAS suite is green at **428 passed, 13 warnings, 22 subtests passed in 29.43s**.
- AutoGrid360 migration history now follows rolled-up release checkpoints: released/supported checkpoints remain durable upgrade origins, while development-only revisions after the latest released checkpoint may be consolidated before the next release so permanent history records the net schema change rather than every intermediate development edit.
- For future development-only consolidation, use the simple release-engineering workflow: back up the development database and migration tree, roll up the unpublished revisions, re-identify/stamp the known-equivalent development database at the new head, run the plugin regression suite, and remove the backups only after validation succeeds.
- The remaining release path is now packaged-migration PostgreSQL validation, repository/release hygiene, immutable host/plugin commit references for the official distribution, composed-image/bootstrap validation, and hosted/Railway validation.


## 2026-08-17 — Public-Alpha preflight and PostgreSQL validation

- Completed the final Flask-AAS/AutoGrid360 preflight hardening pass. The latest user-confirmed automated baseline before the final PostgreSQL facet adjustment is **347 AutoGrid360 tests plus 256 subtests** and **407 Flask-AAS tests plus 22 subtests**.
- Moved the fresh listing-image storage default out of the Flask static tree to project-root `uploads/listings`; relative paths now resolve from the host project root while absolute operator paths remain supported.
- Rebuilt the Docker image without cache and initialized an empty PostgreSQL database successfully through Flask-AAS bootstrap, AutoGrid360 activation, application reload, and automotive/postal dataset loading.
- Fixed the PostgreSQL-only Advanced Search facet query that ordered a `SELECT DISTINCT` result by an unselected country-name column. The same public search flow now works on the clean PostgreSQL deployment.
- Release documentation was reduced and reorganized around installation, operation, project boundaries, and development rather than duplicating the detailed Alpha checklist.
- The remaining public-release blocker is repository packaging of the first durable AutoGrid360 migration, followed by the initial public commit/push/tag. Railway deployment is not a prerequisite for the first GitHub Alpha.

## 2026-08-15 — Alpha presentation, density, and PostgreSQL deployment checkpoint

- Completed the remaining individual-seller image integration by reusing the canonical Flask-AAS `User.image` capability through the host profile-image service. AutoGrid360 does not add a seller-avatar column, upload/delete workflow, storage tree, or image lifecycle of its own.
- Refined the public seller identity block into a compact business-card presentation: when a host profile image exists it appears to the left of the seller display name and company name; without an image the text stacks naturally with no empty frame or placeholder. Coarse public location remains below the identity block under the existing privacy policy.
- Completed and accepted the representative inventory/media density shakedown using the canonical backup/restore demo path. The exercised corpus reached 1,211 listings after the 1,000-listing restore, covering public inventory/search, seller and administrator surfaces, lifecycle presentation, and image/gallery behavior without exposing a release-blocking scaling defect.
- Verified the AutoGrid360 plugin lifecycle on a clean PostgreSQL-backed Flask-AAS deployment: enable/reload enters `NEEDS_MIGRATION`, plugin schema initialization reaches the current provisional head `6e71dd7952ab`, reload activates the plugin, and packaged automotive/postal dataset actions complete successfully. SQLite remains the normal disposable development/test backend; PostgreSQL is the production/integration target.
- The private-Alpha migration history is still intentionally unfrozen. With the presentation and density shakedowns accepted, the next release-structure task is to freeze the first durable AutoGrid360 migration, package it, and repeat the clean deployment against that frozen migration.
- User-confirmed current regression baseline: **347 AutoGrid360 tests passed, 15 warnings, 256 subtests passed in 63.00s**. The enclosing Flask-AAS suite is also green at **377 passed, 13 warnings, 22 subtests**.
- Remaining Alpha release work is now limited to the migration freeze, final packaged-migration deployment validation, Railway/release/support documentation, and the initial AutoGrid360 public commit/push/tag.

## 2026-08-14 — Pre-Alpha production-code reduction audit

- Removed confirmed dead or misleading runtime surface: the unused miles-only haversine wrapper and the `Listing.is_public` property, whose fixed status-only answer could not represent configurable Sale Pending/Sold visibility.
- Removed the private-Alpha `/autogrid360/listings/<id>/view` compatibility route before the public URL contract is frozen; internal links/tests now use the canonical slugged listing URL.
- Consolidated three duplicate listing-audit helpers into one shared AutoGrid360 audit boundary and centralized the repeated system-admin guard plus exact case-insensitive host-user lookup.
- Removed the internal one-seller `ImportResult` compatibility adapter. Seller-scoped restore now returns the canonical `RestoreResult`, and restore construction consumes the already-validated lifecycle source mapping directly instead of rebuilding the same field map manually.
- Simplified restore image-primary selection and filesystem cleanup while preserving bundle/image validation and caller-owned transaction behavior.
- Renamed the historical `return_active_listing_to_pending` helper to `return_public_listing_to_pending` so its name matches its Active/Sale Pending behavior, and removed an unreachable lifecycle fallthrough branch.
- Reduced maintenance bookkeeping by centralizing mail-result accounting and passing one resolved audit-policy boolean through the existing private phases instead of a lazy callback.
- Removed several one-use/repeated plumbing helpers without weakening authorization, ownership, transaction rollback, upload/archive validation, or other trust-boundary checks.
- No schema or migration changes are introduced by this cleanup. Entering the pass, the user-confirmed green baseline was **344 AutoGrid360 tests, 15 warnings, 257 subtests** and **374 Flask-AAS tests, 13 warnings, 22 subtests**; a fresh local regression is required after applying this production cleanup.

## 2026-08-14 — Full-fidelity inventory backup/restore and deterministic demo archive tooling

- Reframed canonical AutoGrid360 inventory portability as backup/restore rather than forced re-moderation. Normal restore now preserves listing lifecycle state, publication/expiration/sold/aging timestamps, featured state, view count, creation/update history, portable IDs, listing/vehicle content, and image associations after validation.
- Kept an explicit **Reset restored listings to Draft** mode for intentional content ingestion; lifecycle reset is no longer the default meaning of import.
- Seller-owned export remains available for the seller's own inventory. Added persisted **Allow sellers to restore inventory bundles** policy, disabled by default because a trusted restore bundle can recreate Active/Sale Pending/Sold state; administrators and CLI operators always retain restore capability.
- Added administrator full multi-seller inventory backup/restore spanning AutoGrid360 sellers and seller presentation profiles while continuing to exclude Flask-AAS passwords, account email/contact data, sessions, MFA material, host roles, and other authentication state.
- Added automatic same-username restore mapping plus explicit `source=destination` mappings for renamed Flask-AAS accounts. Every source seller must map to an existing distinct destination user; AutoGrid360 does not create host accounts during restore.
- Added site-scope bundle validation for seller declarations/ownership, duplicate portable identities, safe archive paths, destination reference resolution, lifecycle timestamp consistency, bounded images, seller mappings, and transactional/filesystem rollback.
- Added `inventory export-all` and `inventory restore` operator commands while retaining the single-seller `inventory export` / `inventory import` workflow.
- Added standalone `scripts/demo.py` to generate valid seller-scoped or multi-seller AutoGrid360 backup ZIPs for density/release QA. It never writes database rows directly, uses deterministic portable IDs and lifecycle mixes, can generate local synthetic QA JPEGs, and can optionally normalize a fixed project-owned image directory into the archive.
- This adds one still-unfrozen Alpha settings column (`allow_seller_inventory_import`). A new provisional local migration is required before exercising the setting against an existing development database; migration history remains disposable until the first Alpha schema freeze.
- Entering this work, the user-confirmed fully green baseline was **370 AutoGrid360 tests, 16 warnings, 295 subtests** and **374 Flask-AAS tests, 13 warnings, 22 subtests**. This backup/restore checkpoint requires a fresh local regression after application.

## 2026-08-14 — Listing-status ribbon refinement and Alpha release checkpoint

- Refined Sale Pending/Sold image overlays into one shared **upper-left** folded-ribbon component. **PENDING** uses `#F07818` orange and **SOLD** uses `#BD1550`; both states use the same diagonal geometry and placement.
- Kept ribbon rendering presentation-only and state-driven. Stored listing JPEGs/thumbnails are never rewritten, so Sold/Sale Pending -> Active immediately removes the overlay without image regeneration or cache/file cleanup.
- Kept the rotated ribbon element on the original generator geometry and equalized the two states with a centered inner label footprint instead of forcing a width on the transformed element. The Pending label is slightly horizontally condensed to preserve comfortable left/right whitespace while Sold retains its natural letterforms.
- Render ribbons only when an actual listing image is present. The no-image placeholder remains a plain fallback, and image-editor thumbnails remain clean source assets without lifecycle decoration.
- Generated and applied provisional local migration `4910b17c1c79` from `d64c560e6a00` for `Listing.sold_at` plus the Sale Pending/Sold visibility and sold-retention settings. The Alpha migration history remains intentionally **unfrozen**.
- User-confirmed fully green lifecycle baseline before the ribbon-only presentation assertions: **367 AutoGrid360 tests passed, 14 warnings, 275 subtests passed in 61.01s**. The enclosing Flask-AAS suite remained **374 passed, 13 warnings, 22 subtests passed in 67.82s**.
- The subsequent ribbon-enabled AutoGrid360 run initially reached **366 passed, 2 failed, 14 warnings, 282 subtests passed in 60.78s**. Both failures were fixture-only assertions that expected an image ribbon on listings with no image; after correcting those fixtures and completing the later gallery/lightbox plus same-origin JavaScript hardening, the user-confirmed AutoGrid360 suite reached **370 passed, 16 warnings, 295 subtests**.
- Clarified the remaining Alpha gate: a deterministic demo-data generator is useful tooling but is **not** itself a release blocker. Representative-density public/seller/admin shakedown remains required regardless of how the test inventory is created; after that, freeze migration #1, perform a true greenfield install/deployment, finish Railway/release/support documentation, and make the initial public commit/push.

## 2026-08-14 — Reversible Sale Pending/Sold lifecycle and public-visibility policy

- Added a distinct `sale_pending` lifecycle state for buyer/deal progress; moderation `pending` remains exclusively **Pending Review** and is never exposed publicly.
- Made seller availability transitions reversible: Active can move to Sale Pending or Sold, Sale Pending can return to Active or become Sold, and Sold can return to Active or Sale Pending when a deal falls through or a status was chosen incorrectly.
- Added administrator-directed lifecycle status changes across Draft, Pending Review, Active, Sale Pending, Sold, Expired, and Removed while routing every transition through lifecycle services instead of raw status assignment, preserving publication/expiration/sold bookkeeping and normal audit events.
- Applied configured seller-edit re-review to both Active and Sale Pending inventory so moving a listing into the deal-pending state does not create a moderation bypass.
- Added **Show Sale Pending listings publicly** and **Show Sold listings publicly** AutoGrid360 settings, both enabled by default. Active remains public; Pending Review, Draft, Expired, and Removed remain private.
- Kept Sale Pending buyer-contact available when that state is public, while Sold inventory remains non-contactable even when retained publicly.
- Public inventory/search and seller pages now use the site public-visibility policy and rank Active first, Sale Pending second, and Sold last before the selected inventory sort. Advanced Search facets derive from that same currently public inventory.
- Kept the RSS feed focused on available inventory: Active is always included, Sale Pending is included when publicly enabled, and Sold is excluded. The sitemap remains Active-only/indexable; public Sale Pending/Sold detail uses noindex semantics.
- Added `Listing.sold_at` plus configurable sold-retention policy (default 90 days, `0` = indefinite). Scheduled maintenance soft-removes Sold listings after that independent retention period without reusing expired-listing warning/removal notices.
- Extended expiration handling to Sale Pending so an Active -> Sale Pending transition retains the same publication deadline rather than escaping normal listing aging.
- Updated public/print/seller/admin presentation with explicit **Sale Pending**, **Sold**, and **Pending Review** labels and added the new lifecycle controls to the existing Manage Listing workflow.
- Added a shared upper-left folded image ribbon for marketplace and listing-management presentation: **PENDING** uses `#F07818` orange and **SOLD** uses `#BD1550`; both use the same diagonal geometry and are rendered from listing state without modifying stored image files. Image-editor thumbnails remain clean assets without lifecycle overlays.
- This changes the still-unfrozen Alpha schema (`sold_at` and three AutoGrid360 settings fields) but does **not** freeze migration history. Existing development databases need a new provisional local migration or recreation before exercising this lifecycle checkpoint.
- User-confirmed regression after the lifecycle migration and follow-up expectation fixes (before the later ribbon-specific assertions): **367 AutoGrid360 tests passed with 275 subtests**, and the enclosing Flask-AAS suite remained **374 passed with 22 subtests**.

## 2026-08-14 — Configurable listing-image storage checkpoint

- Added persisted `AutoGrid360Settings.listing_images_path` storage configuration with a fresh default of `static/images/listings`.
- Matched the Flask-AAS user-image path contract: relative listing-image paths resolve from the Flask application root, absolute paths are used as configured, and AutoGrid360 appends no hidden storage suffix.
- Kept `AUTOGRID360_IMAGE_ROOT` as a pre-settings-row deployment seed/fallback only; once the AutoGrid360 settings row exists, the persisted database value is authoritative.
- Moved new listing-image storage keys to `<listing_id>/<generated>.jpg` and `<listing_id>/<generated>_thumb.jpg`, so the complete configured listing directory remains the real storage root rather than producing a duplicated `.../listings/listings/...` hierarchy.
- Preserved containment checks for generated storage keys so `..`, symlink resolution, or malformed internal keys cannot escape the configured root.
- Kept normalized image writes atomic and kept upload/import/delete/serving behavior on the same central path resolver.
- Exposed the storage path through **AutoGrid360 Administration → Settings** with explicit guidance that changing the path does not move existing files.
- Documented the exposure/deployment tradeoff: a path beneath Flask `static/` is public-capable presentation storage and may be served directly by Flask or the front-end web server; the selected directory must be writable and persistent when required, and operators that require route-gated media can select a non-static absolute directory instead.
- This changes the still-unfrozen AutoGrid360 settings schema but does **not** freeze or publish migration history. Existing development databases must apply a provisional local migration or be recreated before using the updated model.

The latest user-confirmed regression baseline entering this change remains **340 passed with 241 subtests passed**. A new complete AutoGrid360 regression run is required after application.

## 2026-08-13 — Alpha navigation, inventory UX, and route-hardening checkpoint

Completed the next focused AutoGrid360 Alpha shakedown without changing the plugin database schema or expanding the Flask-AAS Plugin API boundary.

### Public inventory and Advanced Search

- Added a compact fixed-size photo column to the canonical public inventory table. The primary listing image is preferred, the first image is used as fallback, and a bounded no-photo placeholder is shown when needed; thumbnails link to the public listing detail.
- Changed Advanced Search categorical controls to reflect values that can actually be found in **current active inventory** instead of exposing the entire packaged reference universe.
- Make, model, vehicle type, drivetrain, condition, transmission, features, seller, country, and zone/subdivision choices now derive from active inventory; models remain make-scoped and represented unlisted/free-text model values remain searchable.
- Kept model year as an inclusive **range boundary**, not an exact categorical facet. The year selector therefore remains contiguous between the oldest represented active year and the current model-year range even when an intermediate year has no active listing.
- Kept price, postal/radius, and distance-unit inputs as range/origin controls rather than incorrectly treating them as inventory facets.

### Listing create/edit and management presentation

- Consolidated Create, Edit, and administrator create-on-behalf vehicle fields through one shared template so the controlled choices cannot drift.
- Normalized model-year, condition, fuel, transmission, door-count, and drivetrain inputs while retaining explicit older/other or unlisted escape hatches where marketplace data cannot be exhaustively enumerated.
- Kept AWD and 4WD distinct and added Four Wheel Drive to the seeded drivetrain references.
- Simplified **My Listings** by removing redundant top-page Create Listing and Seller Profile actions; those destinations remain in application navigation.
- Moved low-frequency Import / Export below the listing collection under **Inventory Tools**.
- Placed **Mark Sold** and **Remove** in one normal lifecycle action row instead of vertically stacking them.
- Kept the long, straightforward Create/Edit form and intentionally avoided a broad multi-column/component redesign.

### Navigation normalization

- Split ordinary authenticated application navigation into **My AutoGrid360** (`My Listings`, `Create Listing`, `Seller Profile`) and **AutoGrid360** (`Inventory`, `Advanced Search`, `Payment Calculator`).
- Added one complete **AutoGrid360 Admin** navigation section for site administrators wherever AutoGrid360 navigation is present: `Dashboard`, `Inventory`, `Pending Review`, `Sellers`, `Create Seller Listing`, `Reference Data`, and `Settings`.
- Preserved the same grouping in the sidebarless fallback so application destinations remain available when the host sidebar position is `none`.
- Kept the Flask-AAS Admin Home host-only; AutoGrid360 does not inject its application navigation into the separate host administrative dashboard shell.
- Corrected the manually registered listing-route test fixture to provide the manifest-derived AutoGrid360 navigation label, preventing blank test-only navigation headings without hard-coding the product name into production templates.

### Route-rate-limit hardening

AutoGrid360 now applies explicit proxy-aware Flask-AAS limiter policy where domain cost or abuse characteristics justify a tighter limit than the host global ceiling:

- public inventory/search/detail/seller/print surfaces: `120/minute`;
- geographic lookup: `120/minute`;
- contact seller POST: existing `10/hour`;
- seller profile and listing create/edit mutations: `10/minute`;
- listing lifecycle mutations and administrator listing/seller/reference/settings mutations: generally `10/minute`;
- image upload: `10/minute`;
- image primary/reorder operations: `30/minute`;
- image delete: `20/minute`;
- inventory export: `10/minute`;
- inventory import: `3/minute`;
- expiration maintenance action: `3/minute`.

Mixed GET/POST forms consume their tighter mutation budget only on POST. Cheap/static operations such as the payment calculator, feed/sitemap generation, and ordinary image-file GET delivery continue to use the host global limiter where an additional application-specific ceiling is not justified.

### Validation checkpoint

Latest user-confirmed complete regression run after this work:

```text
340 passed, 241 subtests passed in 55.56s
```

The enclosing Flask-AAS suite also passes:

```text
374 passed, 13 warnings, 22 subtests passed in 69.03s
```

The next AutoGrid360 Alpha sequence is **finish remaining concrete page-level UI/usability cleanup -> add deterministic demo inventory/media loading -> stress public/seller/admin presentation with representative inventory density -> freeze the first durable migration -> greenfield deployment/Railway/release preparation -> initial Git commit/push**.

`OPENAUTO_parity.md` tracks the remaining Alpha capability and release work. Flask-AAS host changes discovered while building AutoGrid360 are recorded in the Flask-AAS changelog rather than duplicated here.

## 2026-08-11 — AutoGrid360 v1 Alpha implementation and shakedown checkpoint

Established the first substantial AutoGrid360 implementation on the Flask-AAS Plugin API v1 host and brought the plugin through the current private-Alpha architecture, integration, and bare-bones UI shakedown checkpoint.

### Plugin and ownership boundary

- Implemented AutoGrid360 as a first-class Flask-AAS application plugin with its own routes, models, forms, templates, static assets, settings, CLI behavior, data, tests, and independent migration history.
- Kept Flask-AAS as the owner of authentication, user identity, passwords, email, account activation/approval, sessions, MFA, host roles, audit infrastructure, outbound mail, CAPTCHA, and plugin lifecycle.
- Kept AutoGrid360 responsible for seller marketplace presentation, vehicles, listings, images, listing lifecycle, public inventory/search, inquiries, plugin settings/admin, automotive/postal data, geography, and application-specific maintenance behavior.
- Kept `admin` as the only Flask-AAS role with hard-coded cross-seller AutoGrid360 authority; other host roles receive no implicit AutoGrid360 privileges.
- Kept dealer/team organizations, delegated staff authorization, memberships, invitations, billing, and dealer-location management outside Alpha.

### Seller and listing lifecycle

- Added AutoGrid360-owned seller presentation profiles linked to the canonical user; `SellerProfile` now retains only `display_name`, `company_name`, and its identity/timestamps and does not duplicate account contact or location data.
- Kept canonical account location on Flask-AAS `User`; when host user-location support is enabled, public AutoGrid360 seller pages may expose only coarse city/zone/country while keeping account street address, postal code, phone, and email out of the seller surface.
- Added **Use my profile location** on listing creation when canonical account location is available; values are copied into the listing rather than dynamically linked so later profile edits do not move existing inventory.
- Added authenticated seller inventory management with draft creation, editing, submission, public activation, sold state, expiration, relisting, soft removal, and owner/admin authorization boundaries.
- Added configurable approval and published-listing re-review policy.
- Added an administrator pending-review queue and cross-seller inventory management.
- Added immutable first-publication tracking plus current publication/expiration-cycle timestamps.
- Added automatic active-listing expiration with configurable lifetime and warning lead time.
- Added expired-listing retention, pre-removal warning, soft removal, and day-of-removal notification behavior.
- Added unchanged-expired direct relisting and changed-expired reapproval behavior while preserving the original first-listed date.
- Added best-effort administrator email notification when seller-driven activity creates new pending-review work.

### Vehicle data and reference data

- Added normalized vehicle/listing persistence covering title, make, model, body/type, doors, color, mileage, year, condition, engine, transmission, drivetrain, MPG, fuel type, price, description, VIN, stock number, canonical Flask-AAS ISO country/zone codes, city/locality, postal code, and selected features.
- Replaced AutoGrid360-local country/state semantics with the canonical Flask-AAS ISO 3166-1 Country / ISO 3166-2 Zone catalog, persisting portable alpha-2 country codes and full subdivision codes such as `US-IL`; AutoGrid360 does not maintain a second country/state reference map.
- Added canonical make-scoped vehicle models with an explicit unlisted/free-form model fallback; seller input never creates canonical model rows automatically.
- Added stable-key automotive reference data for makes, models, vehicle types, drivetrains, and selectable features.
- Normalized seller Create/Edit controls for model year, condition, transmission, fuel type, and door count: ordinary model years use a 2000-through-next-model-year selector with an explicit validated older/other fallback; categorical values are constrained to useful marketplace choices; and uncommon door counts retain an explicit numeric fallback.
- Added Four Wheel Drive as a distinct drivetrain reference value rather than collapsing 4WD into AWD.
- Added editable JSON bootstrap data with insert-missing, idempotent seeding that preserves runtime IDs, disabled rows, and administrator customizations.
- Exposed optional packaged **Automotive Reference Data** and **Postal Data** through the generic Flask-AAS Applications administration contract, keeping dataset state non-blocking and keeping server-side load/refresh controls with the host/site administrator rather than future dealer administrators.
- Verified the real plugin lifecycle on a fresh database: enable the plugin loader, enable AutoGrid360, reload into `NEEDS_MIGRATION`, initialize the AutoGrid360 schema, reload into `ACTIVE`, then initialize/refresh declared application datasets.
- Removed packaged automotive seeding from the AutoGrid360 Reference Data web page; that page remains for live reference-value administration while web-based server dataset initialization is centralized on the Flask-AAS application card.
- Added basic VIN normalization/validation: optional, uppercase-normalized, exactly 17 characters when present, and restricted to the standard VIN character alphabet excluding `I`, `O`, and `Q`. Authenticity/provider-backed VIN validation remains future work.

### Listing images

- Added multi-image upload, ordering, primary-image selection, deletion, and no-image fallback behavior.
- Added bounded Pillow-based normalization to display JPEGs and thumbnails.
- Added configurable maximum image count.
- Applied the same published-listing re-review policy to seller image changes.
- Preserved image associations through inventory export/import while revalidating imported images through the ordinary image pipeline.

### Public inventory, search, and geography

- Made `/autogrid360/` the single canonical public inventory/results surface: no filters means all active inventory, while semantic criteria produce the corresponding filtered result set using the same cards, sorting, and pagination.
- Kept `/autogrid360/listings/search` as an **Advanced Search** criteria builder only; applying the form redirects into the canonical inventory/results route instead of rendering a second results implementation beneath the form.
- Reworked the canonical public inventory results surface into a sortable column table: make, model, year, and price sort directly from their headers, while page-size changes use lightweight result links instead of a top-of-page sorting form. Empty inventory omits irrelevant sort/page-size controls.
- Integrated public inventory URLs with the Flask-AAS **Fancy URLs** setting. When enabled, semantic search criteria are encoded into deterministic path segments and pagination uses `/page/<n>`; display state such as `sort` and `per_page` remains query-string state. Query-style inventory searches redirect to their fancy canonical equivalent when representable safely.
- Added pagination with active criteria/sort state preserved and selectable 10/20/50/100 page sizes.
- Added filters for price, vehicle type, make, make-scoped model, year, condition, transmission, drivetrain, seller, selected features, and canonical ISO country/zone.
- Added sorting by make, model, year, and price with deterministic null handling.
- Canonicalized case-insensitive seller filters back to the stored public username so seller search URLs do not multiply by casing variants.
- Added dependent make-to-model selection with server-side validation.
- Added normalized postal geography with optional synchronized country datasets and SQLite/PostgreSQL-portable radius search.
- Added 10/25/50/100/250 distance choices with `Auto`, miles, and kilometers presentation; `Auto` uses miles for US/GB searches and kilometers for other loaded countries.
- Kept postal datasets optional: manual city/locality remains valid when no postal lookup is available.
- Added hybrid listing locality behavior: canonical ISO Country/Zone choices drive listing geography, while known AutoGrid360 postal data can populate a blank city or map postal subdivision metadata to an ISO zone; geographic/radius calculations use the resolved postal centroid rather than display text.
- Renamed the public progressive-location API from `/autogrid360/postal/lookup` to `/autogrid360/geo/lookup` and updated listing forms/JavaScript/tests to use the geographic endpoint name; the obsolete postal route is intentionally not retained as a private-Alpha compatibility alias.
- Intentionally omitted listing-level street-address storage for modern privacy reasons. Individual sellers expose approximate locality only; future dealer/business locations belong to dealer profile/location data and may later be selected per listing.
- Added a reproducible postal-data updater and normalized artifact contract; U.S. postal data is the practical default and other country datasets can be supplied using the same schema.

### Buyer-facing utilities

- Added listing-specific seller inquiries with anonymous/authenticated sender handling, abuse controls, host mail delivery, and privacy-conscious audit metadata.
- Added share-by-email through the buyer's local mail client rather than an anonymous server-side relay.
- Added printable active/sold listing pages that do not increment the normal public view counter.
- Added a public payment calculator with listing-price prefill, down payment, amount financed, APR, 1–10 year terms, monthly/biweekly/weekly payment frequencies, zero-interest handling, and optional amortization schedule.
- Added links to current external vehicle-history/safety resources for full-length VINs without relying on undocumented deep-link behavior.
- Added configurable currency symbol, decimal separator, thousands separator, and distinct currency code handling.

### Inventory portability

- Added versioned ZIP export/import as first-class Alpha inventory portability functionality.
- Added stable AutoGrid360-owned listing UUIDs independent from database IDs.
- Exported seller presentation metadata, listing/vehicle data, stable automotive reference keys, portable ISO country/zone codes, and associated images without exporting Flask-AAS passwords, sessions, MFA state, host roles, or other identity secrets. The private-Alpha inventory bundle remains **version 1**; its schema is intentionally allowed to evolve in place until the migration/release contract is frozen. Imported location codes are validated against destination host references.
- At this earlier checkpoint, imports were forced back to `draft`; the later backup/restore checkpoint above supersedes that behavior with lifecycle-preserving restore plus an explicit Draft-reset option.
- Added explicit source-seller to destination Flask-AAS user mapping.
- Added import validation for duplicate portable identities, unsafe ZIP paths, malformed or oversized bundles, unsupported reference keys, and invalid image payloads.
- Added transaction/filesystem rollback behavior and atomic export construction.
- Added city/locality and canonical country/zone values to the private-Alpha portable listing contract without introducing artificial bundle-version churn before a public compatibility boundary exists.
- Left third-party/dealer CSV feed adapters for post-Alpha work.

### Publishing and SEO

- Added canonical slugged listing URLs with compatibility-detail redirects and stale-slug canonicalization.
- Added listing-specific title/description metadata, canonical/Open Graph/Twitter metadata, and Schema.org Product/Vehicle/Offer structured data.
- Added index/noindex policy for active, sold, filtered, print, contact, and utility surfaces.
- Added AutoGrid360 XML sitemap generation for indexable application routes, active listings, and sellers with active inventory.
- Added RSS 2.0 active-inventory feeds, including seller-filtered feeds with stable portable listing UUIDs as GUIDs.
- Preserved Flask-AAS host theme integration for ordinary AutoGrid360 pages while keeping AutoGrid360 CSS application-specific and theme-neutral.

### Maintenance and audit behavior

- Added deterministic AutoGrid360 maintenance execution through the plugin CLI rather than embedding a scheduler or exposing a public cron endpoint.
- Added warning/expiration/aging/removal processing designed to be idempotent and safe for repeated operator scheduling.
- Kept seller notification markers retryable by recording them only after successful queueing through the Flask-AAS mail service.
- Kept AutoGrid360 audit metadata limited to operational identifiers/state rather than inquiry bodies, secrets, or unnecessary personal data.

### Administration and settings

- Added plugin-owned administration for dashboard counts, inventory, pending review, seller profiles, listing creation/reassignment, AutoGrid360 settings, and automotive reference data.
- Centralized AutoGrid360 administration on one shared admin base/sidebar so Dashboard, Inventory, Pending Review, Sellers, Create Seller Listing, Reference Data, and Settings appear consistently and in the same order on every admin page.
- Added configurable publication approval, re-review, expiration, expiration warning, expired retention, expired-removal warning, image-count, pagination, currency, and feed-limit behavior.
- Kept generic registration, account approval, SMTP, CAPTCHA, authentication, and other host settings in Flask-AAS rather than duplicating them inside AutoGrid360.
- Confirmed Flask-AAS maintenance-mode behavior is not an AutoGrid360 Alpha dependency; the host setting remains separate unfinished/deferred Flask-AAS work.

### Package and template cleanup

- Reorganized root implementation modules into an explicit `services/` package while keeping plugin/CLI entry surfaces at the package root.
- Renamed service modules for clearer intent: `postal.py` to `services/geo.py`, `payment.py` to `services/paycalc.py`, and `portable.py` to `services/transfer.py`.
- Removed confirmed dead compatibility helpers and stale imports after explicit reference tracing.
- Removed dead/unreferenced template stubs and added structural template tests for host-theme inheritance and AutoGrid360 stylesheet loading.
- Renamed the plugin stylesheet from `static/autogrid360.css` to generic `static/style.css`; the plugin path identifies ownership and avoids baking the AutoGrid360 product name into a filename that a future deployment/theme may replace.
- Normalized AutoGrid360 forms around the existing Flask-AAS bare-bones form primitives (`fieldset`/`legend`, `.form-group`, `.checkbox-inline`, `.form-control`, and `.form-error`) and removed plugin CSS that was unnecessarily overriding generic field widths, textarea behavior, fieldset presentation, checkbox alignment, and error spacing.
- Kept AutoGrid360 CSS focused on application-specific concerns such as listing/image grids, search/result layouts, pagination, vehicle features, admin tables, and marketplace presentation; deeper visual design remains intentionally separate from the functional default-theme cleanup.
- Kept the plugin package free of listing-level street-address infrastructure and speculative dealer/team abstractions before Alpha.

### Validation checkpoint

- At that earlier checkpoint, before the later sortable-column/navigation/rate-limit work, the clean regression baseline was **321 passed, 177 subtests passed** for AutoGrid360, with the full Flask-AAS host suite at **371 passed, 22 subtests passed**. The newer 2026-08-13 checkpoint above supersedes these counts.
- Fresh-database/plugin lifecycle shakedown has been exercised through schema initialization and `ACTIVE` runtime state, including optional application-data initialization.
- Private-Alpha migrations and internal bundle/API contracts remain intentionally mutable/disposable while schema intent is still moving; migration history and compatibility promises will be frozen only when development slows enough to make those contracts durable.
- The current UI goal is functional consistency with the minimal Flask-AAS CSS first; a deeper visual/layout design pass will build incrementally on that stable bare-bones baseline rather than layering more ad-hoc overrides onto inconsistent markup.

### Explicitly deferred / later design work

- deterministic demo-data generation for representative 100 / 1,000 / 10,000 / 100,000-listing datasets, reusable image pools, pagination/search profiling, and later import/export/performance pressure testing;
- deeper public/admin visual design and theme work after the current bare-bones CSS/form/navigation normalization is complete;
- optional plugin-to-host dynamic sitemap contribution for parameterized public URLs; AutoGrid360 keeps its own application sitemap for now and the host sitemap remains generic;
- multi-tenant/site/custom-domain presentation, tenant-scoped administration, and the distinction between deployment, site/tenant, application, organization/dealer, and user authority;
- dealer/business organizations, staff memberships, invitations, delegated authorization, and multiple dealership locations with per-listing location selection;
- optional post-Alpha featured/promoted-inventory behavior; Alpha uses the canonical `/autogrid360/` all-active inventory/results surface rather than a separate featured homepage;
- full internationalization/localization and additional language coverage;
- additional themes;
- seller/profile images;
- third-party/dealer CSV feeds;
- provider- or dataset-backed VIN decoding/authenticity validation;
- generic marketplace/listing-schema frameworks beyond the automotive Alpha domain;
- mobile applications and AI/photo-based vehicle identification/autofill.
