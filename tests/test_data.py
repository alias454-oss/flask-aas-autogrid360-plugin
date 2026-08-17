# tests/test_data.py
import csv
from pathlib import Path
import tempfile
import unittest

from flask import Flask

from app.core.extensions import db
from app.plugins.autogrid360.models import PostalLocation, ReferenceValue, VehicleModel
from app.plugins.autogrid360.plugin import plugin
from app.plugins.autogrid360.services.data import (
    AUTOMOTIVE_DATASET,
    POSTAL_DATASET,
    admin_datasets,
    run_admin_dataset_action,
)


class AutoGrid360ApplicationDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.postal_root = root / "geography"
        self.postal_root.mkdir()

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{root / 'application-data.db'}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AUTOGRID360_POSTAL_DATA_ROOT=str(self.postal_root),
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.metadata.create_all(
            bind=db.engine,
            tables=[
                ReferenceValue.__table__,
                VehicleModel.__table__,
                PostalLocation.__table__,
            ],
        )

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.metadata.drop_all(
            bind=db.engine,
            tables=[
                PostalLocation.__table__,
                VehicleModel.__table__,
                ReferenceValue.__table__,
            ],
        )
        db.engine.dispose()
        self.context.pop()
        self.temp_dir.cleanup()

    def _write_us_postal_artifact(self):
        path = self.postal_root / "us_postal_codes.csv"
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
            writer.writerow(
                {
                    "country_code": "US",
                    "postal_code": "61032",
                    "locality": "Freeport",
                    "region": "Illinois",
                    "region_code": "IL",
                    "county": "Stephenson",
                    "latitude": "42.2967",
                    "longitude": "-89.6212",
                    "accuracy": "4",
                    "source": "test",
                }
            )

    def test_packaged_automotive_data_is_declared_without_affecting_readiness(self):
        datasets = admin_datasets()

        self.assertTrue(plugin.validate_config().configured)
        self.assertEqual([item.key for item in datasets], [AUTOMOTIVE_DATASET])
        self.assertIn("0 reference values", datasets[0].status)
        self.assertEqual(datasets[0].action_label, "Load")

    def test_postal_dataset_is_declared_only_when_packaged_artifact_exists(self):
        self._write_us_postal_artifact()

        datasets = {item.key: item for item in admin_datasets()}

        self.assertIn(POSTAL_DATASET, datasets)
        self.assertIn("0 active database records", datasets[POSTAL_DATASET].status)
        self.assertEqual(datasets[POSTAL_DATASET].action_label, "Load")
        self.assertTrue(plugin.validate_config().configured)

    def test_automotive_dataset_action_reuses_additive_reference_seeder(self):
        result = run_admin_dataset_action(AUTOMOTIVE_DATASET)

        self.assertGreater(ReferenceValue.query.count(), 0)
        self.assertGreater(VehicleModel.query.count(), 0)
        self.assertIn("automotive reference data loaded", result.message)

        datasets = {item.key: item for item in admin_datasets()}
        self.assertEqual(datasets[AUTOMOTIVE_DATASET].action_label, "Reload")

        second = run_admin_dataset_action(AUTOMOTIVE_DATASET)
        self.assertIn("no records added", second.message)

    def test_postal_dataset_action_reuses_postal_sync_and_refreshes_status(self):
        self._write_us_postal_artifact()

        result = run_admin_dataset_action(POSTAL_DATASET)
        datasets = {item.key: item for item in admin_datasets()}

        self.assertEqual(PostalLocation.query.count(), 1)
        self.assertIn("active=1", result.message)
        self.assertIn("1 active database records", datasets[POSTAL_DATASET].status)
        self.assertEqual(datasets[POSTAL_DATASET].action_label, "Reload")
        self.assertTrue(plugin.validate_config().configured)

    def test_unknown_dataset_action_fails_closed(self):
        with self.assertRaises(KeyError):
            run_admin_dataset_action("not-declared")


if __name__ == "__main__":
    unittest.main()
