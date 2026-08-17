# app/plugins/autogrid360/tests/test_maintenance.py
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from flask import Flask

from app.core.extensions import db
from app.models import User
from app.plugins.autogrid360.cli import cli
from app.plugins.autogrid360.services.maintenance import run_scheduled_maintenance
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_REMOVED,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    CATEGORY_MAKE,
    Listing,
    AutoGrid360Settings,
    ReferenceValue,
    SellerProfile,
    Vehicle,
    VehicleModel,
    vehicle_features,
)
from app.plugins.autogrid360.services.reference import (
    reference_by_key,
    seed_reference_data,
    vehicle_model_by_key,
)


class AutoGrid360MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "autogrid360-maintenance.db"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="autogrid360-maintenance-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.metadata.create_all(
            bind=db.engine,
            tables=[
                User.__table__,
                AutoGrid360Settings.__table__,
                SellerProfile.__table__,
                ReferenceValue.__table__,
                VehicleModel.__table__,
                Vehicle.__table__,
                vehicle_features,
                Listing.__table__,
            ],
        )
        seed_reference_data()
        self.seller = User(
            username="maintenance-seller",
            email="maintenance-seller@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(self.seller)
        db.session.add(
            AutoGrid360Settings(
                id=1,
                enable_listing_expiration=True,
                listing_expiration_days=60,
                expiration_warning_days=7,
                expired_retention_days=30,
                expired_removal_warning_days=7,
            )
        )
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.metadata.drop_all(
            bind=db.engine,
            tables=[
                Listing.__table__,
                vehicle_features,
                Vehicle.__table__,
                VehicleModel.__table__,
                ReferenceValue.__table__,
                SellerProfile.__table__,
                AutoGrid360Settings.__table__,
                User.__table__,
            ],
        )
        db.engine.dispose()
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _listing(
        self,
        *,
        title,
        expires_at,
        status=STATUS_ACTIVE,
        warned_at=None,
        expired_at=None,
        removal_warned_at=None,
        aged_out_at=None,
        aged_out_notice_at=None,
        sold_at=None,
    ):
        make = reference_by_key(CATEGORY_MAKE, "Honda", active_only=False)
        model = vehicle_model_by_key(make, "Civic", active_only=False)
        listing = Listing(
            seller=self.seller,
            vehicle=Vehicle(make_ref=make, model_ref=model),
            title=title,
            status=status,
            published_at=expires_at - timedelta(days=60),
            expires_at=expires_at,
            expiration_warning_sent_at=warned_at,
            expired_at=expired_at,
            expired_removal_warning_sent_at=removal_warned_at,
            aged_out_at=aged_out_at,
            aged_out_notice_sent_at=aged_out_notice_at,
            sold_at=sold_at,
        )
        db.session.add(listing)
        db.session.commit()
        return listing

    def test_maintenance_queues_one_warning_and_expires_due_rows_once(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        warning = self._listing(title="Warning Civic", expires_at=now + timedelta(days=3))
        due = self._listing(title="Due Civic", expires_at=now - timedelta(minutes=1))

        with patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            return_value="queued",
        ) as send_email, patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.maintenance.log_action") as log_action:
            first = run_scheduled_maintenance(now=now)
            db.session.commit()
            second = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(first.warnings_queued, 1)
        self.assertEqual(first.expired, 1)
        self.assertEqual(second.warnings_queued, 0)
        self.assertEqual(second.expired, 0)
        send_email.assert_called_once()
        subject, recipient, body = send_email.call_args.args
        self.assertIn("Warning Civic", subject)
        self.assertEqual(recipient, self.seller.email)
        self.assertIn("Scheduled expiration", body)
        db.session.refresh(warning)
        db.session.refresh(due)
        self.assertIsNotNone(warning.expiration_warning_sent_at)
        self.assertEqual(due.status, STATUS_EXPIRED)
        self.assertEqual(log_action.call_count, 2)
        self.assertEqual(
            [call.kwargs["action"] for call in log_action.call_args_list],
            [
                "autogrid360_listing_expiration_warning_queued",
                "autogrid360_listing_status_changed",
            ],
        )

    def test_sale_pending_inventory_receives_expiration_maintenance(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        warning = self._listing(
            title="Pending Warning Civic",
            expires_at=now + timedelta(days=3),
            status=STATUS_SALE_PENDING,
        )
        due = self._listing(
            title="Pending Due Civic",
            expires_at=now - timedelta(minutes=1),
            status=STATUS_SALE_PENDING,
        )

        with patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            return_value="queued",
        ), patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.maintenance.log_action") as log_action:
            result = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(result.warnings_queued, 1)
        self.assertEqual(result.expired, 1)
        db.session.refresh(warning)
        db.session.refresh(due)
        self.assertIsNotNone(warning.expiration_warning_sent_at)
        self.assertEqual(due.status, STATUS_EXPIRED)
        status_events = [
            call for call in log_action.call_args_list
            if call.kwargs["action"] == "autogrid360_listing_status_changed"
        ]
        self.assertEqual(len(status_events), 1)
        self.assertEqual(
            status_events[0].kwargs["extra_data"]["previous_status"],
            STATUS_SALE_PENDING,
        )

    def test_unavailable_warning_delivery_remains_retryable(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        listing = self._listing(title="Retry Civic", expires_at=now + timedelta(days=2))

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            side_effect=["disabled", "failed", "queued"],
        ):
            disabled = run_scheduled_maintenance(now=now)
            db.session.commit()
            db.session.refresh(listing)
            self.assertIsNone(listing.expiration_warning_sent_at)

            failed = run_scheduled_maintenance(now=now)
            db.session.commit()
            db.session.refresh(listing)
            self.assertIsNone(listing.expiration_warning_sent_at)

            queued = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(disabled.warnings_disabled, 1)
        self.assertEqual(failed.warnings_failed, 1)
        self.assertEqual(queued.warnings_queued, 1)
        db.session.refresh(listing)
        self.assertIsNotNone(listing.expiration_warning_sent_at)

    def test_zero_warning_days_disables_email_but_not_expiration(self):
        settings = db.session.get(AutoGrid360Settings, 1)
        settings.expiration_warning_days = 0
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        due = self._listing(title="Due Civic", expires_at=now - timedelta(minutes=1))
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch("app.plugins.autogrid360.services.maintenance.send_email") as send_email:
            result = run_scheduled_maintenance(now=now)
            db.session.commit()

        send_email.assert_not_called()
        self.assertEqual(result.warnings_queued, 0)
        self.assertEqual(result.expired, 1)
        db.session.refresh(due)
        self.assertEqual(due.status, STATUS_EXPIRED)

    def test_nonactive_and_already_warned_listings_are_not_rewarned(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        self._listing(
            title="Already warned",
            expires_at=now + timedelta(days=2),
            warned_at=now - timedelta(days=1),
        )
        self._listing(
            title="Sold listing",
            expires_at=now + timedelta(days=2),
            status=STATUS_SOLD,
        )

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch("app.plugins.autogrid360.services.maintenance.send_email") as send_email:
            result = run_scheduled_maintenance(now=now)

        send_email.assert_not_called()
        self.assertEqual(result.warnings_queued, 0)
        self.assertEqual(result.expired, 0)

    def test_maintenance_cli_runs_warning_and_expiration_workflow(self):
        now = datetime.now(timezone.utc)
        warning = self._listing(title="CLI Warning", expires_at=now + timedelta(days=2))
        due = self._listing(title="CLI Due", expires_at=now - timedelta(minutes=1))

        with patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            return_value="queued",
        ), patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ):
            result = CliRunner().invoke(cli, ["maintenance"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("warnings_queued=1", result.output)
        self.assertIn("expired=1", result.output)
        db.session.refresh(warning)
        db.session.refresh(due)
        self.assertIsNotNone(warning.expiration_warning_sent_at)
        self.assertEqual(due.status, STATUS_EXPIRED)

    def test_expired_inventory_warns_then_ages_out_and_notifies_once(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        warning = self._listing(
            title="Aging Civic",
            expires_at=now - timedelta(days=25),
            status=STATUS_EXPIRED,
            expired_at=now - timedelta(days=25),
        )
        due = self._listing(
            title="Remove Accord",
            expires_at=now - timedelta(days=31),
            status=STATUS_EXPIRED,
            expired_at=now - timedelta(days=31),
        )

        with patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            return_value="queued",
        ) as send_email, patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.maintenance.log_action") as log_action:
            first = run_scheduled_maintenance(now=now)
            db.session.commit()
            second = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(first.removal_warnings_queued, 1)
        self.assertEqual(first.removed, 1)
        self.assertEqual(first.removal_notices_queued, 1)
        self.assertEqual(second.removal_warnings_queued, 0)
        self.assertEqual(second.removed, 0)
        self.assertEqual(second.removal_notices_queued, 0)
        self.assertEqual(send_email.call_count, 2)
        db.session.refresh(warning)
        db.session.refresh(due)
        self.assertIsNotNone(warning.expired_removal_warning_sent_at)
        self.assertEqual(due.status, STATUS_REMOVED)
        normalized_aged_out_at = due.aged_out_at
        if normalized_aged_out_at.tzinfo is None:
            normalized_aged_out_at = normalized_aged_out_at.replace(tzinfo=timezone.utc)
        self.assertEqual(normalized_aged_out_at, now)
        self.assertIsNotNone(due.aged_out_notice_sent_at)
        self.assertEqual(
            [call.kwargs["action"] for call in log_action.call_args_list],
            [
                "autogrid360_listing_removal_warning_queued",
                "autogrid360_listing_status_changed",
                "autogrid360_listing_aged_out_notice_queued",
            ],
        )

    def test_failed_aged_out_notice_remains_retryable_after_soft_removal(self):
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        listing = self._listing(
            title="Retry removal notice",
            expires_at=now - timedelta(days=31),
            status=STATUS_EXPIRED,
            expired_at=now - timedelta(days=31),
        )

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            side_effect=["disabled", "failed", "queued"],
        ):
            first = run_scheduled_maintenance(now=now)
            db.session.commit()
            second = run_scheduled_maintenance(now=now + timedelta(hours=1))
            db.session.commit()
            third = run_scheduled_maintenance(now=now + timedelta(hours=2))
            db.session.commit()

        self.assertEqual(first.removed, 1)
        self.assertEqual(first.removal_notices_disabled, 1)
        self.assertEqual(second.removed, 0)
        self.assertEqual(second.removal_notices_failed, 1)
        self.assertEqual(third.removal_notices_queued, 1)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_REMOVED)
        self.assertIsNotNone(listing.aged_out_notice_sent_at)

    def test_expired_retention_runs_even_when_automatic_expiration_is_disabled(self):
        settings = db.session.get(AutoGrid360Settings, 1)
        settings.enable_listing_expiration = False
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        listing = self._listing(
            title="Manual expired Civic",
            expires_at=now - timedelta(days=31),
            status=STATUS_EXPIRED,
            expired_at=now - timedelta(days=31),
        )
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.services.maintenance.send_email",
            return_value="queued",
        ):
            result = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertFalse(result.expiration_enabled)
        self.assertEqual(result.expired, 0)
        self.assertEqual(result.removed, 1)
        self.assertEqual(result.removal_notices_queued, 1)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_REMOVED)

    def test_sold_retention_soft_removes_due_inventory_without_expired_notice(self):
        settings = db.session.get(AutoGrid360Settings, 1)
        settings.sold_retention_days = 90
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        due = self._listing(
            title="Old Sold Civic",
            expires_at=now + timedelta(days=10),
            status=STATUS_SOLD,
            sold_at=now - timedelta(days=91),
        )
        recent = self._listing(
            title="Recent Sold Civic",
            expires_at=now + timedelta(days=10),
            status=STATUS_SOLD,
            sold_at=now - timedelta(days=89),
        )
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=True,
        ), patch(
            "app.plugins.autogrid360.services.maintenance.send_email"
        ) as send_email, patch(
            "app.plugins.autogrid360.services.maintenance.log_action"
        ) as log_action:
            result = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(result.removed, 1)
        self.assertEqual(result.removal_notices_queued, 0)
        send_email.assert_not_called()
        db.session.refresh(due)
        db.session.refresh(recent)
        self.assertEqual(due.status, STATUS_REMOVED)
        self.assertIsNone(due.aged_out_at)
        self.assertEqual(recent.status, STATUS_SOLD)
        status_events = [
            call for call in log_action.call_args_list
            if call.kwargs["action"] == "autogrid360_listing_status_changed"
        ]
        self.assertEqual(len(status_events), 1)
        self.assertEqual(status_events[0].kwargs["extra_data"]["previous_status"], STATUS_SOLD)
        self.assertEqual(status_events[0].kwargs["extra_data"]["new_status"], STATUS_REMOVED)

    def test_zero_sold_retention_keeps_sold_inventory_indefinitely(self):
        settings = db.session.get(AutoGrid360Settings, 1)
        settings.sold_retention_days = 0
        now = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
        listing = self._listing(
            title="Permanent Sold Civic",
            expires_at=now + timedelta(days=10),
            status=STATUS_SOLD,
            sold_at=now - timedelta(days=3650),
        )
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.services.maintenance.audit_activity_enabled",
            return_value=False,
        ), patch("app.plugins.autogrid360.services.maintenance.send_email") as send_email:
            result = run_scheduled_maintenance(now=now)
            db.session.commit()

        self.assertEqual(result.removed, 0)
        send_email.assert_not_called()
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SOLD)

    def test_maintenance_cli_reports_disabled_expiration_without_side_effects(self):
        settings = db.session.get(AutoGrid360Settings, 1)
        settings.enable_listing_expiration = False
        db.session.commit()
        now = datetime.now(timezone.utc)
        listing = self._listing(title="Disabled Civic", expires_at=now - timedelta(days=1))

        with patch("app.plugins.autogrid360.services.maintenance.send_email") as send_email:
            result = CliRunner().invoke(cli, ["maintenance"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("listing expiration is disabled", result.output)
        send_email.assert_not_called()
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
