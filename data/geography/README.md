# AutoGrid360 postal geography artifacts

AutoGrid360 radius search consumes normalized CSV artifacts from this directory. The
runtime database does not download postal data itself.

## Default dataset: United States

The default maintainer/update path is U.S.-only. From the AutoGrid360 repository
root:

```bash
python scripts/update_postal_codes.py
python scripts/update_postal_codes.py --check
```

Those commands are equivalent to `--country US`. They generate or verify:

```text
us_postal_codes.csv
sources.json
```

The generated artifacts are intended to be committed with AutoGrid360 so ordinary
installations do not depend on GeoNames being reachable at runtime.

Synchronize the default dataset into an AutoGrid360 installation with:

```bash
python manage.py plugin run autogrid360 postal sync
```

With no `--country`, runtime synchronization also defaults to `US`.

## Optional built-in UK dataset

UK outward postcode districts are supported but are not required by a U.S.-only
installation:

```bash
python scripts/update_postal_codes.py --country GB
python scripts/update_postal_codes.py --country GB --check
python manage.py plugin run autogrid360 postal sync --country GB
```

`--all` remains a maintainer convenience that generates both built-in datasets:

```bash
python scripts/update_postal_codes.py --all
```

The initial built-in source is the GeoNames postal-code export under Creative
Commons Attribution 4.0. The US artifact uses five-digit ZIP centroids. The GB
artifact uses outward postcode districts (for example `B15`), matching the
intentionally coarser public GeoNames UK dataset and avoiding address-level
precision for private-seller radius search.

## Normalized artifact contract

Operators may supply another country's postal/coordinate data without changing
the AutoGrid360 schema or distance engine. Use an ISO 3166-1 alpha-2 country code
and name the file:

```text
<lowercase-country-code>_postal_codes.csv
```

Examples:

```text
ca_postal_codes.csv
za_postal_codes.csv
```

The CSV header must contain exactly these columns (column order is not
significant):

```text
country_code,postal_code,locality,region,region_code,county,latitude,longitude,accuracy,source
```

Requirements:

- `country_code`: two-letter ISO code, such as `CA` or `ZA`.
- `postal_code`: canonical search token for that country's artifact, maximum 20
  characters. AutoGrid360 uppercases it and removes whitespace for generic
  countries.
- `latitude` / `longitude`: required WGS84 coordinates.
- `locality`, `region`, `region_code`, `county`: optional display/reference
  metadata.
- `accuracy`: optional integer source accuracy indicator.
- `source`: short provenance label; `geonames` is used by the built-in updater.

Then synchronize the operator-supplied artifact explicitly:

```bash
python manage.py plugin run autogrid360 postal sync --country CA
python manage.py plugin run autogrid360 postal sync --country ZA
```

For countries without a built-in normalizer, listing country values should use
the same ISO alpha-2 code and buyer postal input should match the artifact's
chosen postal granularity. Country-specific aliases or reductions can be added
later without changing `PostalLocation` or the radius calculation.

All synchronized country artifacts share the same `PostalLocation` table. The country selected in public search identifies the search-origin postal namespace; it does not restrict result listings to that country. Cross-border radius results are therefore possible when multiple country datasets are loaded. Distance calculations normalize to kilometers internally, while the buyer-facing unit may be miles or kilometers.

Postal synchronization updates upstream-owned coordinates/metadata and marks
identifiers missing from a refreshed country artifact inactive rather than
hard-deleting them. Historical listing foreign keys therefore remain valid.
