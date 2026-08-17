#!/usr/bin/env python3
# app/plugins/autogrid360/scripts/update_postal_codes.py
"""Download GeoNames postal dumps and generate deterministic AutoGrid360 artifacts."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
import zipfile


GEONAMES_BASE_URL = "https://download.geonames.org/export/zip"
DEFAULT_COUNTRY = "US"
COUNTRIES = {
    "US": {
        "archive": "US.zip",
        "output": "us_postal_codes.csv",
    },
    "GB": {
        "archive": "GB.zip",
        "output": "gb_postal_codes.csv",
    },
}
OUTPUT_FIELDS = (
    "country_code",
    "postal_code",
    "locality",
    "region",
    "region_code",
    "county",
    "latitude",
    "longitude",
    "accuracy",
    "source",
)
USER_AGENT = "AutoGrid360-postal-updater/1.0 (+https://www.geonames.org/)"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


class PostalUpdateError(RuntimeError):
    """Raised when upstream postal data cannot be normalized safely."""


@dataclass(frozen=True)
class SourceArchive:
    country_code: str
    url: str
    payload: bytes
    last_modified: str | None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def _normalize_source_postal(country: str, value: str) -> str | None:
    cleaned = "".join(str(value or "").strip().upper().split())
    if country == "US":
        if len(cleaned) >= 5 and cleaned[:5].isdigit():
            return cleaned[:5]
        return None
    if country == "GB":
        if 2 <= len(cleaned) <= 4 and cleaned.isalnum():
            return cleaned
        if len(cleaned) >= 5:
            outward = cleaned[:-3]
            if 2 <= len(outward) <= 4 and outward.isalnum():
                return outward
        return None
    return None


def _bounded_text(value: str, maximum: int) -> str:
    cleaned = " ".join(str(value or "").replace("\x00", "").split())
    return cleaned[:maximum]


def _format_coordinate(value: float) -> str:
    rendered = f"{value:.6f}".rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _mode(values: list[str]) -> str:
    filtered = [value for value in values if value]
    if not filtered:
        return ""
    counts = Counter(filtered)
    highest = max(counts.values())
    return min(value for value, count in counts.items() if count == highest)


def download_archive(country: str) -> SourceArchive:
    """Download one country-specific GeoNames archive."""

    metadata = COUNTRIES[country]
    url = f"{GEONAMES_BASE_URL}/{metadata['archive']}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed HTTPS allowlist
        payload = response.read(MAX_ARCHIVE_BYTES + 1)
        last_modified = response.headers.get("Last-Modified")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise PostalUpdateError(f"GeoNames archive is unexpectedly large for {country}.")
    if not payload:
        raise PostalUpdateError(f"GeoNames returned an empty archive for {country}.")
    return SourceArchive(country, url, payload, last_modified)


def normalize_archive(archive: SourceArchive) -> list[dict[str, str]]:
    """Collapse one GeoNames archive to one representative centroid per postal code."""

    grouped: dict[str, list[dict]] = defaultdict(list)
    try:
        with zipfile.ZipFile(BytesIO(archive.payload), "r") as zipped:
            expected_name = f"{archive.country_code.upper()}.txt"
            data_members = [
                name
                for name in zipped.namelist()
                if not name.endswith("/")
                and Path(name).name.casefold() == expected_name.casefold()
            ]
            if len(data_members) != 1:
                raise PostalUpdateError(
                    f"Expected exactly one {expected_name} data file in "
                    f"{archive.country_code} archive; found {len(data_members)}."
                )
            with zipped.open(data_members[0], "r") as raw:
                reader = csv.reader(
                    TextIOWrapper(raw, encoding="utf-8"),
                    delimiter="\t",
                )
                for line_number, row in enumerate(reader, start=1):
                    if len(row) < 12:
                        raise PostalUpdateError(
                            f"GeoNames {archive.country_code} row {line_number} "
                            f"has {len(row)} columns."
                        )
                    if row[0].strip().upper() != archive.country_code:
                        raise PostalUpdateError(
                            f"GeoNames {archive.country_code} row {line_number} has wrong country."
                        )
                    code = _normalize_source_postal(archive.country_code, row[1])
                    if not code:
                        continue
                    try:
                        latitude = float(row[9])
                        longitude = float(row[10])
                        accuracy = int(row[11]) if row[11].strip() else None
                    except ValueError as exc:
                        raise PostalUpdateError(
                            f"GeoNames {archive.country_code} row {line_number} "
                            "has invalid coordinates."
                        ) from exc
                    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                        raise PostalUpdateError(
                            f"GeoNames {archive.country_code} row {line_number} "
                            "is outside coordinate bounds."
                        )
                    grouped[code].append(
                        {
                            "locality": _bounded_text(row[2], 180),
                            "region": _bounded_text(row[3], 100),
                            "region_code": _bounded_text(row[4], 20),
                            "county": _bounded_text(row[5], 100),
                            "latitude": latitude,
                            "longitude": longitude,
                            "accuracy": accuracy,
                        }
                    )
    except zipfile.BadZipFile as exc:
        raise PostalUpdateError(
            f"GeoNames returned an invalid ZIP archive for {archive.country_code}."
        ) from exc

    output: list[dict[str, str]] = []
    for code in sorted(grouped):
        rows = grouped[code]
        latitude = sum(row["latitude"] for row in rows) / len(rows)
        longitude = sum(row["longitude"] for row in rows) / len(rows)
        accuracies = [row["accuracy"] for row in rows if row["accuracy"] is not None]
        output.append(
            {
                "country_code": archive.country_code,
                "postal_code": code,
                "locality": _mode([row["locality"] for row in rows]),
                "region": _mode([row["region"] for row in rows]),
                "region_code": _mode([row["region_code"] for row in rows]),
                "county": _mode([row["county"] for row in rows]),
                "latitude": _format_coordinate(latitude),
                "longitude": _format_coordinate(longitude),
                "accuracy": str(max(accuracies)) if accuracies else "",
                "source": "geonames",
            }
        )
    if not output:
        raise PostalUpdateError(f"GeoNames {archive.country_code} produced no postal rows.")
    return output


def render_csv(rows: list[dict[str, str]]) -> bytes:
    """Render one deterministic UTF-8 CSV artifact."""

    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _render_sources(metadata: dict[str, dict]) -> bytes:
    payload = {
        "license": "Creative Commons Attribution 4.0",
        "provider": "GeoNames",
        "provider_url": "https://www.geonames.org/",
        "sources": metadata,
    }
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_artifacts(countries: list[str]) -> tuple[dict[str, bytes], dict[str, dict]]:
    """Download and normalize selected countries into artifact bytes."""

    artifacts: dict[str, bytes] = {}
    metadata: dict[str, dict] = {}
    for country in countries:
        archive = download_archive(country)
        rows = normalize_archive(archive)
        artifacts[COUNTRIES[country]["output"]] = render_csv(rows)
        metadata[country] = {
            "archive_sha256": archive.sha256,
            "last_modified": archive.last_modified,
            "record_count": len(rows),
            "url": archive.url,
        }
    return artifacts, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate AutoGrid360 postal reference artifacts from GeoNames."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--country",
        choices=sorted(COUNTRIES),
        help=f"Generate one built-in country dataset (default: {DEFAULT_COUNTRY}).",
    )
    group.add_argument("--all", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if generated artifacts differ from committed files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "geography",
    )
    args = parser.parse_args(argv)

    countries = sorted(COUNTRIES) if args.all else [args.country or DEFAULT_COUNTRY]
    try:
        artifacts, metadata = build_artifacts(countries)
    except (OSError, PostalUpdateError) as exc:
        print(f"postal update failed: {exc}", file=sys.stderr)
        return 2

    sources_path = args.output_dir / "sources.json"
    combined_sources: dict[str, dict] = {}
    if sources_path.is_file():
        try:
            existing_sources = json.loads(sources_path.read_text(encoding="utf-8"))
            combined_sources.update(existing_sources.get("sources", {}))
        except (OSError, json.JSONDecodeError):
            pass
    combined_sources.update(metadata)
    artifacts["sources.json"] = _render_sources(combined_sources)

    stale: list[str] = []
    for filename, payload in artifacts.items():
        destination = args.output_dir / filename
        if destination.is_file() and destination.read_bytes() == payload:
            continue
        stale.append(filename)
        if not args.check:
            _write_atomic(destination, payload)

    for country in countries:
        info = metadata[country]
        print(
            f"{country}: records={info['record_count']} "
            f"sha256={info['archive_sha256']}"
        )
    if args.check and stale:
        print("stale postal artifacts: " + ", ".join(sorted(stale)), file=sys.stderr)
        return 1
    if not args.check:
        print(f"postal artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
