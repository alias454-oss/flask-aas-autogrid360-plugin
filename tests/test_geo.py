# app/plugins/autogrid360/tests/test_geo.py
from io import BytesIO
from pathlib import Path
import csv
import tempfile
import unittest
import zipfile

from click.testing import CliRunner
from flask import Flask

from app.core.extensions import db
from app.plugins.autogrid360.cli import cli
from app.plugins.autogrid360.models import PostalLocation
from app.plugins.autogrid360.services.geo import (
    PostalDataError,
    distance_from_kilometers,
    distance_to_kilometers,
    haversine_kilometers,
    normalize_country_code,
    normalize_postal_code,
    postal_location_by_code,
    resolve_distance_unit,
    sync_postal_data,
)
from app.plugins.autogrid360.scripts.update_postal_codes import (
    SourceArchive,
    normalize_archive,
    render_csv,
)


class AutoGrid360PostalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data_root = root / "geography"
        self.data_root.mkdir()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{root / 'postal.db'}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AUTOGRID360_POSTAL_DATA_ROOT=str(self.data_root),
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.metadata.create_all(bind=db.engine, tables=[PostalLocation.__table__])

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, tables=[PostalLocation.__table__])
        db.engine.dispose()
        self.context.pop()
        self.temp_dir.cleanup()

    def _write_artifact(self, country, rows):
        filename = f"{country.lower()}_postal_codes.csv"
        path = self.data_root / filename
        fields = [
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
        ]
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return path

    @staticmethod
    def _row(country, postal, locality, latitude, longitude, **overrides):
        row = {
            "country_code": country,
            "postal_code": postal,
            "locality": locality,
            "region": "Illinois" if country == "US" else "England",
            "region_code": "IL" if country == "US" else "ENG",
            "county": "Stephenson" if country == "US" else "Birmingham",
            "latitude": str(latitude),
            "longitude": str(longitude),
            "accuracy": "4",
            "source": "geonames",
        }
        row.update(overrides)
        return row

    def test_country_and_postal_normalization_supports_builtin_and_iso_codes(self):
        self.assertEqual(normalize_country_code("United States"), "US")
        self.assertEqual(normalize_country_code("USA"), "US")
        self.assertEqual(normalize_country_code("UK"), "GB")
        self.assertEqual(normalize_country_code("United Kingdom"), "GB")
        self.assertEqual(normalize_country_code("ca"), "CA")
        self.assertEqual(normalize_country_code("za"), "ZA")
        self.assertEqual(normalize_postal_code("US", "61032-1234"), "61032")
        self.assertEqual(normalize_postal_code("GB", "B15 2TT"), "B15")
        self.assertEqual(normalize_postal_code("GB", "ec1a 1bb"), "EC1A")
        self.assertEqual(normalize_postal_code("CA", "K1A 0B1"), "K1A0B1")
        self.assertEqual(normalize_postal_code("ZA", "8001"), "8001")
        self.assertIsNone(normalize_postal_code("US", "B15"))
        self.assertIsNone(normalize_country_code("Canada"))

    def test_postal_sync_updates_and_deactivates_without_repurposing_rows(self):
        self._write_artifact(
            "US",
            [
                self._row("US", "61032", "Freeport", 42.2967, -89.6212),
                self._row("US", "61101", "Rockford", 42.2711, -89.0940),
            ],
        )
        first = sync_postal_data(countries=["US"])
        db.session.commit()
        self.assertEqual(first.inserted, 2)
        self.assertEqual(first.total_active, 2)
        freeport = postal_location_by_code("US", "61032")
        freeport_id = freeport.id

        self._write_artifact(
            "US",
            [self._row("US", "61032", "Freeport", 42.3000, -89.6200)],
        )
        second = sync_postal_data(countries=["US"])
        db.session.commit()

        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.deactivated, 1)
        freeport = postal_location_by_code("US", "61032")
        self.assertEqual(freeport.id, freeport_id)
        self.assertAlmostEqual(freeport.latitude, 42.3)
        rockford = postal_location_by_code("US", "61101", active_only=False)
        self.assertFalse(rockford.active)

    def test_sync_requires_generated_artifact(self):
        with self.assertRaises(PostalDataError):
            sync_postal_data(countries=["US"])

    def test_sync_defaults_to_us_without_requiring_optional_gb_artifact(self):
        self._write_artifact(
            "US",
            [self._row("US", "61032", "Freeport", 42.2967, -89.6212)],
        )

        result = sync_postal_data()
        db.session.commit()

        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.total_active, 1)
        self.assertIsNotNone(postal_location_by_code("US", "61032"))

    def test_sync_accepts_operator_supplied_iso_country_artifact(self):
        self._write_artifact(
            "CA",
            [
                self._row(
                    "CA",
                    "K1A 0B1",
                    "Ottawa",
                    45.4201,
                    -75.7003,
                    region="Ontario",
                    region_code="ON",
                    county="Ottawa",
                    source="operator",
                )
            ],
        )

        result = sync_postal_data(countries=["CA"])
        db.session.commit()

        self.assertEqual(result.inserted, 1)
        location = postal_location_by_code("CA", "K1A 0B1")
        self.assertIsNotNone(location)
        self.assertEqual(location.postal_code, "K1A0B1")
        self.assertEqual(location.source, "operator")

    def test_postal_sync_cli_uses_generated_country_artifact(self):
        self._write_artifact(
            "US",
            [self._row("US", "61032", "Freeport", 42.2967, -89.6212)],
        )

        result = CliRunner().invoke(cli, ["postal", "sync", "--country", "US"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("inserted=1", result.output)
        self.assertIsNotNone(postal_location_by_code("US", "61032"))

    def test_distance_units_resolve_by_search_country_and_convert_through_kilometers(self):
        self.assertEqual(resolve_distance_unit("US", "auto"), "miles")
        self.assertEqual(resolve_distance_unit("GB", "auto"), "miles")
        self.assertEqual(resolve_distance_unit("CA", "auto"), "kilometers")
        self.assertEqual(resolve_distance_unit("ZA", "auto"), "kilometers")
        self.assertEqual(resolve_distance_unit("US", "kilometers"), "kilometers")
        kilometers = distance_to_kilometers(100, "miles")
        self.assertAlmostEqual(kilometers, 160.9344, places=4)
        self.assertAlmostEqual(
            distance_from_kilometers(kilometers, "miles"),
            100.0,
            places=6,
        )

    def test_haversine_distance_is_reasonable(self):
        distance = haversine_kilometers(42.2967, -89.6212, 42.2711, -89.0940)
        self.assertGreater(distance, 40)
        self.assertLess(distance, 50)

    def test_geonames_normalizer_collapses_duplicate_postal_rows_deterministically(self):
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "US.txt",
                "\n".join(
                    [
                        "US\t61032\tFreeport\tIllinois\tIL\tStephenson\t177\t\t\t"
                        "42.2967\t-89.6212\t4",
                        "US\t61032\tFreeport\tIllinois\tIL\tStephenson\t177\t\t\t"
                        "42.2973\t-89.6208\t6",
                        "US\t61101\tRockford\tIllinois\tIL\tWinnebago\t201\t\t\t"
                        "42.2711\t-89.0940\t4",
                    ]
                )
                + "\n",
            )
        source = SourceArchive(
            country_code="US",
            url="https://example.test/US.zip",
            payload=payload.getvalue(),
            last_modified="test",
        )
        rows = normalize_archive(source)

        self.assertEqual([row["postal_code"] for row in rows], ["61032", "61101"])
        self.assertEqual(rows[0]["locality"], "Freeport")
        self.assertEqual(rows[0]["accuracy"], "6")
        self.assertEqual(rows[0]["latitude"], "42.297")
        rendered = render_csv(rows).decode("utf-8")
        self.assertTrue(rendered.startswith("country_code,postal_code,"))

    def test_geonames_normalizer_ignores_ancillary_archive_files(self):
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "US.txt",
                "US\t61032\tFreeport\tIllinois\tIL\tStephenson\t177\t\t\t"
                "42.2967\t-89.6212\t4\n",
            )
            archive.writestr("readme.txt", "GeoNames postal data readme\n")

        rows = normalize_archive(
            SourceArchive("US", "https://example.test/US.zip", payload.getvalue(), None)
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["postal_code"], "61032")

    def test_geonames_gb_normalizer_preserves_outward_postcodes(self):
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "GB.txt",
                "GB\tB15\tBirmingham\tEngland\tENG\tBirmingham\t\t\t\t52.4628\t-1.92701\t4\n",
            )
        rows = normalize_archive(
            SourceArchive("GB", "https://example.test/GB.zip", payload.getvalue(), None)
        )
        self.assertEqual(rows[0]["postal_code"], "B15")
        self.assertEqual(rows[0]["country_code"], "GB")


if __name__ == "__main__":
    unittest.main()
