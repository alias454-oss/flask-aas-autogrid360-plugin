# app/plugins/autogrid360/tests/test_listing_admin.py
from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import zipfile

from sqlalchemy import event

from app.core.extensions import db
from app.plugins.autogrid360.forms.settings import AutoGrid360SettingsForm
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    AutoGrid360Settings,
    Listing,
    ListingImage,
    ReferenceValue,
    SellerProfile,
    VehicleModel,
)
from app.plugins.autogrid360.services.media import image_root as listing_image_root
from app.plugins.autogrid360.services.reference import vehicle_model_by_key
from app.plugins.autogrid360.tests.listing_support import AutoGrid360ListingRouteTestCase


class AutoGrid360AdminListingRouteTests(AutoGrid360ListingRouteTestCase):
    def test_admin_surfaces_require_system_admin(self):
        ford = self._reference(CATEGORY_MAKE, "Ford")
        seller_client = self.app.test_client()
        self._login(seller_client, self.seller)
        restricted = (
            ("get", "/autogrid360/admin/"),
            ("get", "/autogrid360/admin/settings"),
            ("post", "/autogrid360/admin/maintenance/expire-due"),
            ("get", "/autogrid360/admin/reference/"),
            ("get", "/autogrid360/admin/reference/makes"),
            ("get", f"/autogrid360/admin/reference/makes/{ford.id}/models"),
            ("post", "/autogrid360/admin/reference/seed"),
            ("get", "/autogrid360/admin/backup-restore"),
        )

        for method, path in restricted:
            with self.subTest(actor="seller", method=method, path=path):
                response = getattr(seller_client, method)(path)
                self.assertEqual(response.status_code, 403)

        moderator_client = self.app.test_client()
        self._login(moderator_client, self.moderator)
        self.assertEqual(moderator_client.get("/autogrid360/admin/").status_code, 403)
        self.assertIsNone(db.session.get(AutoGrid360Settings, 1))

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        dashboard = admin_client.get("/autogrid360/admin/")
        backup = admin_client.get("/autogrid360/admin/backup-restore")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("AutoGrid360 Administration", dashboard.get_data(as_text=True))
        self.assertEqual(backup.status_code, 200)
        self.assertIn("Download Full Inventory Backup", backup.get_data(as_text=True))


    def test_admin_dashboard_reports_inventory_and_pending_work(self):
        pending = self._create_listing()
        pending.title = "Needs administrator review"
        pending.status = STATUS_PENDING
        active = Listing(
            seller=self.other_user,
            vehicle=self._vehicle(make="Ford", model="Focus"),
            title="Other seller active",
            status=STATUS_ACTIVE,
        )
        db.session.add(active)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.get("/autogrid360/admin/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Needs administrator review", body)
        self.assertIn("Pending Review", body)
        self.assertIn("Overview", body)
        self.assertIn("Inventory", body)
        self.assertIn("<strong>4</strong><span>People</span>", body)
        self.assertIn("<strong>2</strong><span>Sellers</span>", body)
        self.assertIn("<strong>2</strong><span>Total Managed</span>", body)


    def test_pending_review_queue_only_shows_pending_and_quick_approve(self):
        pending = self._create_listing()
        pending.title = "Pending queue listing"
        pending.status = STATUS_PENDING
        active = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Toyota", model="Camry"),
            title="Already active listing",
            status=STATUS_ACTIVE,
        )
        db.session.add(active)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.get("/autogrid360/admin/pending")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Pending queue listing", body)
        self.assertNotIn("Already active listing", body)
        self.assertNotIn("Review Order", body)
        self.assertIn('aria-sort="ascending"', body)
        self.assertIn("Submitted ↑", body)
        self.assertIn('href="/autogrid360/admin/pending?sort=newest"', body)
        self.assertIn(
            f'action="/autogrid360/listings/{pending.id}/approve"',
            body,
        )
        self.assertIn(">Approve<", body)


    def test_pending_review_queue_sorts_oldest_by_default_and_newest_on_request(self):
        older = self._create_listing()
        older.title = "Older pending listing"
        older.status = STATUS_PENDING
        older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        newer = self._create_listing()
        newer.title = "Newer pending listing"
        newer.status = STATUS_PENDING
        newer.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.session.commit()

        client = self.app.test_client()
        self._login(client, self.admin)

        oldest_response = client.get("/autogrid360/admin/pending")
        newest_response = client.get(
            "/autogrid360/admin/pending",
            query_string={"sort": "newest"},
        )

        self.assertEqual(oldest_response.status_code, 200)
        self.assertEqual(newest_response.status_code, 200)
        oldest_body = oldest_response.get_data(as_text=True)
        newest_body = newest_response.get_data(as_text=True)
        self.assertLess(
            oldest_body.index("Older pending listing"),
            oldest_body.index("Newer pending listing"),
        )
        self.assertLess(
            newest_body.index("Newer pending listing"),
            newest_body.index("Older pending listing"),
        )
        self.assertIn('aria-sort="ascending"', oldest_body)
        self.assertIn("Submitted ↑", oldest_body)
        self.assertIn('href="/autogrid360/admin/pending?sort=newest"', oldest_body)
        self.assertIn('aria-sort="descending"', newest_body)
        self.assertIn("Submitted ↓", newest_body)
        self.assertIn('href="/autogrid360/admin/pending?sort=oldest"', newest_body)


    def test_pending_review_pagination_stays_on_pending_route_and_preserves_sort(self):
        self.app.config["AUTOGRID360_ADMIN_LISTINGS_PER_PAGE"] = 1
        first = self._create_listing()
        first.title = "First pending page"
        first.status = STATUS_PENDING
        first.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        second = self._create_listing()
        second.title = "Second pending page"
        second.status = STATUS_PENDING
        second.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.session.commit()

        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.get(
            "/autogrid360/admin/pending",
            query_string={"sort": "newest"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Second pending page", body)
        self.assertNotIn("First pending page", body)
        self.assertIn(
            'href="/autogrid360/admin/pending?page=2&amp;sort=newest"',
            body,
        )
        self.assertNotIn("/autogrid360/admin/listings?", body)


    def test_admin_inventory_filters_across_sellers_and_statuses(self):
        seller_listing = self._create_listing()
        seller_listing.title = "Seller draft Honda"
        other_listing = Listing(
            seller=self.other_user,
            vehicle=self._vehicle(make="Ford", model="Mustang"),
            title="Other seller Mustang",
            status=STATUS_ACTIVE,
        )
        db.session.add(other_listing)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.get(
            "/autogrid360/admin/listings",
            query_string={
                "status": STATUS_ACTIVE,
                "q": "Mustang",
                "seller": self.other_user.username,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Other seller Mustang", body)
        self.assertNotIn("Seller draft Honda", body)
        self.assertIn(self.other_user.username, body)


    def test_admin_inventory_submitted_header_toggles_global_sort_order(self):
        older = self._create_listing()
        older.title = "Older managed listing"
        older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        newer = self._create_listing()
        newer.title = "Newer managed listing"
        newer.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.session.commit()

        client = self.app.test_client()
        self._login(client, self.admin)

        newest_response = client.get("/autogrid360/admin/listings")
        oldest_response = client.get(
            "/autogrid360/admin/listings",
            query_string={"sort": "oldest"},
        )

        self.assertEqual(newest_response.status_code, 200)
        self.assertEqual(oldest_response.status_code, 200)
        newest_body = newest_response.get_data(as_text=True)
        oldest_body = oldest_response.get_data(as_text=True)
        self.assertLess(
            newest_body.index("Newer managed listing"),
            newest_body.index("Older managed listing"),
        )
        self.assertLess(
            oldest_body.index("Older managed listing"),
            oldest_body.index("Newer managed listing"),
        )
        self.assertIn('aria-sort="descending"', newest_body)
        self.assertIn("Submitted ↓", newest_body)
        self.assertIn(
            'href="/autogrid360/admin/listings?sort=oldest"',
            newest_body,
        )
        self.assertIn('aria-sort="ascending"', oldest_body)
        self.assertIn("Submitted ↑", oldest_body)
        self.assertIn(
            'href="/autogrid360/admin/listings?sort=newest"',
            oldest_body,
        )


    def test_admin_inventory_sort_preserves_filters_and_pagination(self):
        self.app.config["AUTOGRID360_ADMIN_LISTINGS_PER_PAGE"] = 1
        first = self._create_active_inventory_listing(
            title="First sortable Mustang",
            year=2018,
            make="Ford",
            model="Mustang",
            seller=self.other_user,
        )
        first.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = self._create_active_inventory_listing(
            title="Second sortable Mustang",
            year=2019,
            make="Ford",
            model="Mustang",
            seller=self.other_user,
        )
        second.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.session.commit()

        client = self.app.test_client()
        self._login(client, self.admin)
        response = client.get(
            "/autogrid360/admin/listings",
            query_string={
                "status": STATUS_ACTIVE,
                "q": "Mustang",
                "seller": self.other_user.username,
                "sort": "oldest",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("First sortable Mustang", body)
        self.assertNotIn("Second sortable Mustang", body)

        sort_match = re.search(
            r'<a href="([^"]+)">\s*Submitted ↑',
            body,
        )
        self.assertIsNotNone(sort_match)
        sort_url = urlparse(unescape(sort_match.group(1)))
        self.assertEqual(sort_url.path, "/autogrid360/admin/listings")
        self.assertEqual(
            parse_qs(sort_url.query),
            {
                "status": [STATUS_ACTIVE],
                "q": ["Mustang"],
                "seller": [self.other_user.username],
                "sort": ["newest"],
            },
        )

        next_match = re.search(r'<a href="([^"]+)">Next</a>', body)
        self.assertIsNotNone(next_match)
        next_url = urlparse(unescape(next_match.group(1)))
        self.assertEqual(next_url.path, "/autogrid360/admin/listings")
        self.assertEqual(
            parse_qs(next_url.query),
            {
                "status": [STATUS_ACTIVE],
                "q": ["Mustang"],
                "seller": [self.other_user.username],
                "sort": ["oldest"],
                "page": ["2"],
            },
        )
        self.assertIn(
            '<input type="hidden" name="sort" value="oldest">',
            body,
        )


    def test_admin_can_edit_another_sellers_active_listing_without_rereview(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Administrator corrected title"),
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.title, "Administrator corrected title")
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    def test_moderator_cannot_edit_another_sellers_listing(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.moderator)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Moderator edit attempt"),
        )

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.title, "Seller-owned Civic")


    def test_admin_can_manage_another_sellers_images_without_rereview(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file("admin-upload.jpg")]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListingImage.query.filter_by(listing_id=listing.id).count(), 1)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    def test_admin_can_reassign_listing_seller(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/listings/{listing.id}/seller",
            data={"seller_username": self.other_user.username},
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.seller_id, self.other_user.id)


    def test_admin_can_create_draft_for_selected_seller(self):
        client = self.app.test_client()
        self._login(client, self.admin)
        data = self._listing_form_data(title="Admin-created dealer inventory")
        data["seller_username"] = self.other_user.username

        response = client.post("/autogrid360/admin/listings/create", data=data)

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.filter_by(title="Admin-created dealer inventory").one()
        self.assertEqual(listing.seller_id, self.other_user.id)
        self.assertEqual(listing.status, STATUS_DRAFT)


    def test_admin_can_submit_another_sellers_draft_through_site_policy(self):
        self._set_listing_policy(approval=False, rereview=True)
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.admin)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(detail.status_code, 200)
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/submit"',
            detail.get_data(as_text=True),
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    def test_admin_can_hard_delete_another_sellers_draft(self):
        listing = self._create_listing()
        listing_id = listing.id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/listings/{listing.id}/delete")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/autogrid360/admin/listings", response.headers["Location"])
        self.assertIsNone(db.session.get(Listing, listing_id))


    def test_admin_can_manage_autogrid360_owned_seller_profile_data(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/sellers/{self.seller.id}/profile",
            data={
                "display_name": "Seller Display",
                "company_name": "Seller Company",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = SellerProfile.query.filter_by(user_id=self.seller.id).one()
        self.assertEqual(profile.company_name, "Seller Company")
        self.assertEqual(self.seller.email, "listing-seller@example.test")


    def test_admin_can_configure_listing_approval_and_rereview_policy(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                require_rereview_on_edit="y",
            ),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertFalse(settings.require_listing_approval)
        self.assertTrue(settings.require_rereview_on_edit)
        self.assertFalse(settings.enable_listing_expiration)
        self.assertEqual(settings.listing_expiration_days, 60)
        self.assertEqual(settings.expiration_warning_days, 7)
        self.assertTrue(settings.show_sale_pending_listings_publicly)
        self.assertTrue(settings.show_sold_listings_publicly)
        self.assertEqual(settings.sold_retention_days, 90)
        self.assertFalse(settings.allow_seller_inventory_import)


    def test_admin_can_configure_listing_expiration_policy(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                require_listing_approval="y",
                require_rereview_on_edit="y",
                enable_listing_expiration="y",
                listing_expiration_days="45",
                expiration_warning_days="5",
            ),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertTrue(settings.enable_listing_expiration)
        self.assertEqual(settings.listing_expiration_days, 45)
        self.assertEqual(settings.expiration_warning_days, 5)


    def test_admin_can_enable_seller_inventory_restore(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(allow_seller_inventory_import="y"),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertTrue(settings.allow_seller_inventory_import)


    def test_listing_expiration_warning_must_precede_expiration(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                enable_listing_expiration="y",
                listing_expiration_days="30",
                expiration_warning_days="30",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Expiration warning lead time must be less than the listing lifetime.",
            response.get_data(as_text=True),
        )
        self.assertIsNone(db.session.get(AutoGrid360Settings, 1))


    def test_image_pixel_limit_rejects_oversized_dimensions(self):
        self.app.config["AUTOGRID360_MAX_IMAGE_PIXELS"] = 4_000
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file()]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListingImage.query.count(), 0)
        self.assertTrue(
            any(
                "dimensions are too large" in message
                for _, message in self._flash_messages(client)
            )
        )


    def test_image_upload_total_request_limit_returns_413(self):
        self.app.config["AUTOGRID360_MAX_UPLOAD_REQUEST_BYTES"] = 100
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file()]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(ListingImage.query.count(), 0)


    def test_public_detail_uses_database_side_atomic_view_increment(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.view_count = 7
        db.session.commit()
        statements = []

        def capture_statement(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.app.test_client().get(
                self.public_listing_path(listing)
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(response.status_code, 200)
        db.session.refresh(listing)
        self.assertEqual(listing.view_count, 8)
        counter_updates = [
            statement.lower()
            for statement in statements
            if statement.lstrip().lower().startswith("update")
            and "view_count" in statement.lower()
        ]
        self.assertTrue(counter_updates)
        self.assertTrue(
            any("view_count +" in statement for statement in counter_updates),
            counter_updates,
        )


    def test_listing_create_audit_records_ids_without_listing_content(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        sensitive_description = "Private seller notes should never enter the audit event"

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(
                "/autogrid360/listings/create",
                data=self._listing_form_data(description=sensitive_description),
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_listing_created")
        extra_data = call.kwargs["extra_data"]
        self.assertIn("listing_id", extra_data)
        self.assertIn("seller_id", extra_data)
        self.assertIn("vehicle_id", extra_data)
        self.assertNotIn(sensitive_description, repr(extra_data))
        self.assertNotIn(self.seller.email, repr(extra_data))


    def test_listing_edit_audit_records_changed_field_names_not_values(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)
        sensitive_description = "Changed listing text must stay out of audit metadata"

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/edit",
                data=self._listing_form_data(description=sensitive_description),
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_listing_edited")
        extra_data = call.kwargs["extra_data"]
        self.assertIn("listing.description", extra_data["changed_fields"])
        self.assertIn("vehicle.trim", extra_data["changed_fields"])
        self.assertFalse(extra_data["returned_to_review"])
        self.assertNotIn(sensitive_description, repr(extra_data))
        self.assertNotIn(self.seller.email, repr(extra_data))


    def test_listing_delete_audit_preserves_metadata_without_deleted_content(self):
        listing = self._create_listing()
        listing_id = listing.id
        vehicle_id = listing.vehicle_id
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(f"/autogrid360/listings/{listing.id}/delete")

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_listing_deleted")
        self.assertEqual(
            call.kwargs["extra_data"],
            {
                "listing_id": listing_id,
                "seller_id": self.seller.id,
                "vehicle_id": vehicle_id,
                "previous_status": STATUS_DRAFT,
                "image_count": 0,
            },
        )


    def test_image_upload_audit_records_image_ids_not_filenames(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)
        filename = "private-seller-photo-name.jpg"

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/images",
                data={"images": [self._image_file(filename)]},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_listing_images_uploaded")
        extra_data = call.kwargs["extra_data"]
        self.assertEqual(extra_data["listing_id"], listing.id)
        self.assertEqual(extra_data["image_count"], 1)
        self.assertEqual(len(extra_data["image_ids"]), 1)
        self.assertNotIn(filename, repr(extra_data))


    def test_selecting_existing_primary_image_is_noop_without_rereview_or_audit(self):
        self._set_listing_policy(approval=True, rereview=True)
        listing = self._create_listing()
        image = self._create_image(listing, position=0, is_primary=True)
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/images/{image.id}/primary"
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)
        log_action.assert_not_called()


    def test_primary_image_change_audit_records_ids_only(self):
        listing = self._create_listing()
        first = self._create_image(listing, position=0, is_primary=True, token="first")
        second = self._create_image(listing, position=1, is_primary=False, token="second")
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/images/{second.id}/primary"
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        extra_data = log_action.call_args.kwargs["extra_data"]
        self.assertEqual(
            log_action.call_args.kwargs["action"],
            "autogrid360_listing_primary_image_changed",
        )
        self.assertEqual(extra_data["image_id"], second.id)
        self.assertEqual(extra_data["previous_primary_image_id"], first.id)
        self.assertFalse(extra_data["returned_to_review"])


    def test_image_reorder_and_delete_each_emit_metadata_audit_event(self):
        listing = self._create_listing()
        first = self._create_image(listing, position=0, is_primary=True, token="first")
        second = self._create_image(listing, position=1, is_primary=False, token="second")
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.audit.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.services.audit.log_action") as log_action:
            move_response = client.post(
                f"/autogrid360/listings/{listing.id}/images/{second.id}/move",
                data={"direction": "up"},
            )
            delete_response = client.post(
                f"/autogrid360/listings/{listing.id}/images/{first.id}/delete"
            )

        self.assertEqual(move_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)
        self.assertEqual(log_action.call_count, 2)
        self.assertEqual(
            [call.kwargs["action"] for call in log_action.call_args_list],
            [
                "autogrid360_listing_image_reordered",
                "autogrid360_listing_image_deleted",
            ],
        )
        reorder_data = log_action.call_args_list[0].kwargs["extra_data"]
        self.assertEqual(reorder_data["image_id"], second.id)
        self.assertEqual(reorder_data["previous_position"], 1)
        self.assertEqual(reorder_data["new_position"], 0)
        delete_data = log_action.call_args_list[1].kwargs["extra_data"]
        self.assertEqual(delete_data["image_id"], first.id)
        self.assertTrue(delete_data["was_primary"])


    def test_autogrid360_settings_audit_records_policy_values(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        with patch(
            "app.plugins.autogrid360.routes.settings.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.routes.settings.log_action") as log_action:
            response = client.post(
                "/autogrid360/admin/settings",
                data=self._settings_form_data(
                    require_listing_approval="y",
                    enable_listing_expiration="y",
                    listing_expiration_days="90",
                    expiration_warning_days="10",
                ),
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_settings_updated")
        self.assertEqual(
            call.kwargs["extra_data"]["current"],
            {
                "require_listing_approval": True,
                "require_rereview_on_edit": False,
                "enable_listing_expiration": True,
                "listing_expiration_days": 90,
                "expiration_warning_days": 10,
                "expired_retention_days": 30,
                "expired_removal_warning_days": 7,
                "show_sale_pending_listings_publicly": True,
                "show_sold_listings_publicly": True,
                "sold_retention_days": 90,
                "currency_code": "USD",
                "currency_symbol": "$",
                "currency_decimal_separator": ".",
                "currency_thousands_separator": ",",
                "default_distance_unit": "auto",
                "listing_images_path": self.app.config["AUTOGRID360_IMAGE_ROOT"],
                "allow_seller_inventory_import": False,
            },
        )


    def test_expiration_maintenance_expires_due_public_rows_once_and_audits_each(self):
        self._set_listing_policy(expiration=True, expiration_days=60, warning_days=7)
        now = datetime.now(timezone.utc)
        first = self._create_active_inventory_listing(
            title="Due Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        sale_pending = self._create_active_inventory_listing(
            title="Pending Accord",
            year=2021,
            make="Honda",
            model="Accord",
        )
        future = self._create_active_inventory_listing(
            title="Future Mustang",
            year=2022,
            make="Ford",
            model="Mustang",
        )
        sold = self._create_active_inventory_listing(
            title="Sold Focus",
            year=2018,
            make="Ford",
            model="Focus",
        )
        first.expires_at = now - timedelta(minutes=2)
        sale_pending.status = STATUS_SALE_PENDING
        sale_pending.expires_at = now - timedelta(minutes=1)
        future.expires_at = now + timedelta(days=1)
        sold.status = STATUS_SOLD
        sold.sold_at = now
        sold.expires_at = now - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        with patch(
            "app.plugins.autogrid360.routes.settings.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.routes.settings.log_action") as log_action:
            first_response = client.post("/autogrid360/admin/maintenance/expire-due")
            second_response = client.post("/autogrid360/admin/maintenance/expire-due")

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        db.session.refresh(first)
        db.session.refresh(sale_pending)
        db.session.refresh(future)
        db.session.refresh(sold)
        self.assertEqual(first.status, STATUS_EXPIRED)
        self.assertEqual(sale_pending.status, STATUS_EXPIRED)
        self.assertEqual(future.status, STATUS_ACTIVE)
        self.assertEqual(sold.status, STATUS_SOLD)
        self.assertEqual(log_action.call_count, 2)
        previous_statuses = {
            call.kwargs["extra_data"]["previous_status"]
            for call in log_action.call_args_list
        }
        self.assertEqual(previous_statuses, {STATUS_ACTIVE, STATUS_SALE_PENDING})
        for call in log_action.call_args_list:
            self.assertEqual(call.kwargs["action"], "autogrid360_listing_status_changed")
            self.assertEqual(call.kwargs["extra_data"]["new_status"], STATUS_EXPIRED)
            self.assertEqual(
                call.kwargs["extra_data"]["source"],
                "expiration_maintenance",
            )


    def test_reference_data_admin_lists_legacy_automotive_defaults(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.get("/autogrid360/admin/reference/makes")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Ford", body)
        self.assertIn("Chevrolet", body)
        self.assertIn("Stable key", body)
        self.assertIn("DB ID", body)


    def test_admin_added_reference_value_becomes_available_without_changing_seed_ids(self):
        ford_id = self._reference(CATEGORY_MAKE, "Ford").id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/reference/makes",
            data={"label": "Rivian", "sort_order": "75", "active": "y"},
        )

        self.assertEqual(response.status_code, 302)
        rivian = ReferenceValue.query.filter_by(
            category=CATEGORY_MAKE,
            key="rivian",
        ).one()
        self.assertNotEqual(rivian.id, ford_id)
        self.assertEqual(self._reference(CATEGORY_MAKE, "Ford").id, ford_id)

        self._login(client, self.seller)
        create = client.get("/autogrid360/listings/create")
        self.assertIn('value="rivian"', create.get_data(as_text=True))
        self.assertIn("Rivian", create.get_data(as_text=True))


    def test_admin_can_manage_models_for_newly_added_make(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        create_make = client.post(
            "/autogrid360/admin/reference/makes",
            data={"label": "Rivian", "sort_order": "75", "active": "y"},
        )
        self.assertEqual(create_make.status_code, 302)
        rivian = ReferenceValue.query.filter_by(
            category=CATEGORY_MAKE,
            key="rivian",
        ).one()

        makes_page = client.get("/autogrid360/admin/reference/makes")
        self.assertIn(
            f'href="/autogrid360/admin/reference/makes/{rivian.id}/models"',
            makes_page.get_data(as_text=True),
        )
        self.assertIn("Models (0)", makes_page.get_data(as_text=True))

        model_page = client.get(f"/autogrid360/admin/reference/makes/{rivian.id}/models")
        self.assertEqual(model_page.status_code, 200)
        self.assertIn("Rivian Models", model_page.get_data(as_text=True))
        self.assertIn(
            "No models have been configured for this make.",
            model_page.get_data(as_text=True),
        )

        response = client.post(
            f"/autogrid360/admin/reference/makes/{rivian.id}/models",
            data={"label": "R1T", "sort_order": "10", "active": "y"},
        )
        self.assertEqual(response.status_code, 302)
        model = VehicleModel.query.filter_by(
            make_id=rivian.id,
            key="r1t",
        ).one()
        self.assertEqual(model.label, "R1T")
        self.assertTrue(model.active)

        self._login(client, self.seller)
        create_body = client.get("/autogrid360/listings/create").get_data(as_text=True)
        self.assertIn('value="rivian:r1t"', create_body)
        self.assertIn("Rivian — R1T", create_body)


    def test_vehicle_model_edit_preserves_key_id_and_existing_vehicle_reference(self):
        listing = self._create_active_inventory_listing(
            title="Referenced Focus",
            year=2018,
            make="Ford",
            model="Focus",
        )
        ford = self._reference(CATEGORY_MAKE, "Ford")
        focus = vehicle_model_by_key(ford, "Focus", active_only=False)
        model_id = focus.id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/reference/makes/{ford.id}/models/{focus.id}/edit",
            data={
                "label": "Focus Classic",
                "sort_order": str(focus.sort_order),
                "active": "y",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing.vehicle)
        loaded = db.session.get(VehicleModel, model_id)
        self.assertEqual(loaded.id, model_id)
        self.assertEqual(loaded.key, "focus")
        self.assertEqual(loaded.label, "Focus Classic")
        self.assertEqual(listing.vehicle.model_id, model_id)
        self.assertEqual(listing.vehicle.model, "Focus Classic")


    def test_disabling_vehicle_model_preserves_listing_and_blocks_new_selection(self):
        listing = self._create_active_inventory_listing(
            title="Aging Focus",
            year=2019,
            make="Ford",
            model="Focus",
        )
        ford = self._reference(CATEGORY_MAKE, "Ford")
        focus = vehicle_model_by_key(ford, "Focus", active_only=False)
        model_id = focus.id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/reference/makes/{ford.id}/models/{focus.id}/toggle"
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(focus)
        db.session.refresh(listing.vehicle)
        self.assertFalse(focus.active)
        self.assertEqual(listing.vehicle.model_id, model_id)
        self.assertEqual(listing.vehicle.model, "Focus")

        self._login(client, self.seller)
        create_body = client.get("/autogrid360/listings/create").get_data(as_text=True)
        edit_body = client.get(
            f"/autogrid360/listings/{listing.id}/edit"
        ).get_data(as_text=True)
        public_body = client.get(
            self.public_listing_path(listing)
        ).get_data(as_text=True)

        self.assertNotIn('value="ford:focus"', create_body)
        self.assertIn("Ford — Focus (disabled)", edit_body)
        self.assertIn("Focus", public_body)


    def test_reference_label_edit_preserves_key_id_and_existing_listing_reference(self):
        listing = self._create_active_inventory_listing(
            title="Referenced Ford",
            year=2018,
            make="Ford",
            model="Focus",
        )
        ford = self._reference(CATEGORY_MAKE, "Ford")
        ford_id = ford.id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/reference/makes/{ford.id}/edit",
            data={
                "label": "Ford Motor Company",
                "sort_order": str(ford.sort_order),
                "active": "y",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing.vehicle)
        loaded = db.session.get(ReferenceValue, ford_id)
        self.assertEqual(loaded.id, ford_id)
        self.assertEqual(loaded.key, "ford")
        self.assertEqual(loaded.label, "Ford Motor Company")
        self.assertEqual(listing.vehicle.make_id, ford_id)
        self.assertEqual(listing.vehicle.make, "Ford Motor Company")


    def test_disabling_referenced_make_preserves_listing_and_blocks_new_selection(self):
        listing = self._create_active_inventory_listing(
            title="Aging Ford",
            year=2019,
            make="Ford",
            model="Fusion",
        )
        ford = self._reference(CATEGORY_MAKE, "Ford")
        ford_id = ford.id
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/admin/reference/makes/{ford.id}/toggle")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(ford)
        db.session.refresh(listing.vehicle)
        self.assertFalse(ford.active)
        self.assertEqual(listing.vehicle.make_id, ford_id)
        self.assertEqual(listing.vehicle.make, "Ford")

        self._login(client, self.seller)
        create_body = client.get("/autogrid360/listings/create").get_data(as_text=True)
        edit_body = client.get(
            f"/autogrid360/listings/{listing.id}/edit"
        ).get_data(as_text=True)
        public_body = client.get(
            self.public_listing_path(listing)
        ).get_data(as_text=True)

        self.assertNotIn('value="ford"', create_body)
        self.assertIn("Ford (disabled)", edit_body)
        self.assertIn("Ford", public_body)


    def test_listing_features_persist_and_render_publicly(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        data = self._listing_form_data(
            title="Feature Civic",
            features=["Air Conditioning", "Cruise Control"],
        )

        response = client.post("/autogrid360/listings/create", data=data)

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.filter_by(title="Feature Civic").one()
        self.assertEqual(
            [feature.label for feature in listing.vehicle.features],
            ["Air Conditioning", "Cruise Control"],
        )
        listing.status = STATUS_ACTIVE
        db.session.commit()

        public = self.app.test_client().get(
            self.public_listing_path(listing)
        )
        body = public.get_data(as_text=True)
        self.assertIn("Features / Options", body)
        self.assertIn("Air Conditioning", body)
        self.assertIn("Cruise Control", body)


    def test_feature_default_selection_is_admin_configurable_for_new_listings(self):
        feature = self._reference(CATEGORY_FEATURE, "Air Conditioning")
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            f"/autogrid360/admin/reference/features/{feature.id}/edit",
            data={
                "label": feature.label,
                "sort_order": str(feature.sort_order),
                "active": "y",
                "default_selected": "y",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(feature)
        self.assertTrue(feature.default_selected)

        self._login(client, self.seller)
        body = client.get("/autogrid360/listings/create").get_data(as_text=True)
        self.assertIn('value="air-conditioning"', body)
        self.assertIn("checked", body)


    def test_reference_admin_audit_uses_stable_metadata_not_submitted_label(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        with patch(
            "app.plugins.autogrid360.routes.reference.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.routes.reference.log_action") as log_action:
            response = client.post(
                "/autogrid360/admin/reference/features",
                data={"label": "Backup Camera", "sort_order": "50", "active": "y"},
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_reference_value_created")
        self.assertEqual(call.kwargs["extra_data"]["category"], CATEGORY_FEATURE)
        self.assertEqual(call.kwargs["extra_data"]["key"], "backup-camera")
        self.assertNotIn("Backup Camera", str(call.kwargs["extra_data"]))


    def test_public_listing_exposes_buyer_tools_and_current_vin_resources(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.vehicle.vin = "2HGFB2F50DH000001"
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"/autogrid360/tools/payment-calculator?listing_id={listing.id}",
            body,
        )
        self.assertIn(f"/autogrid360/listings/{listing.id}/print", body)
        self.assertIn("Share by Email", body)
        self.assertIn("mailto:?subject=", body)
        self.assertNotIn(self.seller.email, body)
        self.assertIn("CARFAX Vehicle History Reports", body)
        self.assertIn("NICB VINCheck", body)
        self.assertIn("NHTSA Recall Lookup", body)


    def test_vehicle_history_links_require_a_full_length_vin(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.vehicle.vin = "SHORTVIN"
        db.session.commit()

        body = self.app.test_client().get(
            self.public_listing_path(listing)
        ).get_data(as_text=True)

        self.assertNotIn("Vehicle History &amp; Safety Checks", body)
        self.assertNotIn("NICB VINCheck", body)


    def test_printable_listing_is_public_but_does_not_increment_views(self):
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        db.session.commit()
        self.assertEqual(listing.view_count, 0)

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/print"
        )
        db.session.refresh(listing)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(listing.view_count, 0)
        self.assertIn(listing.title, body)
        self.assertIn("<strong>Sold</strong>", body)
        self.assertIn("$9,250.00", body)
        self.assertIn("http://localhost/autogrid360/listings/", body)
        self.assertIn('name="robots" content="noindex,follow"', body)


    def test_printable_listing_rejects_nonpublic_lifecycle_states(self):
        listing = self._create_listing()

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/print"
        )

        self.assertEqual(response.status_code, 404)


    def test_payment_calculator_prefills_listing_price_and_calculates(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()

        get_response = client.get(
            f"/autogrid360/tools/payment-calculator?listing_id={listing.id}"
        )
        get_body = get_response.get_data(as_text=True)
        self.assertEqual(get_response.status_code, 200)
        self.assertIn(listing.title, get_body)
        self.assertIn('value="9250.00"', get_body)
        self.assertRegex(
            get_body,
            r'<input(?=[^>]*\bid="amount")(?=[^>]*\btype="text")[^>]*>',
        )
        self.assertRegex(
            get_body,
            r'<input(?=[^>]*\bid="down_payment")(?=[^>]*\btype="text")[^>]*>',
        )
        self.assertRegex(
            get_body,
            r'<input(?=[^>]*\bid="annual_interest_rate")(?=[^>]*\btype="number")[^>]*>',
        )
        self.assertIn('data-currency-preview="amount-preview"', get_body)
        self.assertIn('data-currency-preview="down-payment-preview"', get_body)
        self.assertIn('id="amount-preview"', get_body)
        self.assertIn('id="down-payment-preview"', get_body)
        self.assertNotRegex(
            get_body,
            r'<input(?=[^>]*\bid="annual_interest_rate")(?=[^>]*data-currency-preview)[^>]*>',
        )
        self.assertIn('/autogrid360/static/currency.js', get_body)

        post_response = client.post(
            "/autogrid360/tools/payment-calculator",
            data={
                "listing_id": str(listing.id),
                "amount": "20000.00",
                "down_payment": "2000.00",
                "annual_interest_rate": "6.000",
                "loan_years": "5",
                "frequency": "monthly",
                "show_schedule": "y",
            },
        )
        post_body = post_response.get_data(as_text=True)

        self.assertEqual(post_response.status_code, 200)
        self.assertIn("Amount financed", post_body)
        self.assertIn("$18,000.00", post_body)
        self.assertIn("$347.99", post_body)
        self.assertIn("60", post_body)
        self.assertIn("Amortization Schedule", post_body)


    def test_payment_calculator_accepts_human_formatted_currency_input(self):
        response = self.app.test_client().post(
            "/autogrid360/tools/payment-calculator",
            data={
                "amount": "$20,000.00",
                "down_payment": "$ 2,000.00",
                "annual_interest_rate": "6.000",
                "loan_years": "5",
                "frequency": "monthly",
            },
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Amount financed", body)
        self.assertIn("$18,000.00", body)
        self.assertIn("Estimated Payment", body)


    def test_payment_calculator_rejects_malformed_currency_input(self):
        response = self.app.test_client().post(
            "/autogrid360/tools/payment-calculator",
            data={
                "amount": "$20,00,0",
                "down_payment": "$2,000.00",
                "annual_interest_rate": "6.000",
                "loan_years": "5",
                "frequency": "monthly",
            },
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter a valid vehicle price / amount.", body)
        self.assertNotIn("Estimated Payment", body)


    def test_payment_calculator_rejects_down_payment_above_amount(self):
        response = self.app.test_client().post(
            "/autogrid360/tools/payment-calculator",
            data={
                "amount": "5000.00",
                "down_payment": "6000.00",
                "annual_interest_rate": "6.000",
                "loan_years": "5",
                "frequency": "monthly",
            },
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Down payment cannot exceed the vehicle price / amount.",
            body,
        )
        self.assertNotIn("Estimated Payment", body)


    def test_admin_can_configure_currency_display(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                currency_code="EUR",
                currency_symbol="€",
                currency_decimal_separator=",",
                currency_thousands_separator=".",
                default_distance_unit="kilometers",
            ),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertEqual(settings.currency_code, "EUR")
        self.assertEqual(settings.currency_symbol, "€")
        self.assertEqual(settings.currency_decimal_separator, ",")
        self.assertEqual(settings.currency_thousands_separator, ".")
        self.assertEqual(settings.default_distance_unit, "kilometers")


    def test_admin_can_configure_listing_image_storage_path(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                listing_images_path="uploads/custom-listings",
            ),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertEqual(
            settings.listing_images_path,
            "uploads/custom-listings",
        )
        self.assertEqual(
            listing_image_root(),
            (Path(self.app.root_path).parent / "uploads/custom-listings").resolve(),
        )


    def test_listing_image_storage_path_rejects_null_byte(self):
        form = AutoGrid360SettingsForm(
            data={
                "listing_images_path": "uploads/listings\x00escape",
            }
        )

        self.assertFalse(form.validate())
        self.assertIn(
            "Listing Images Path contains an invalid null byte.",
            form.listing_images_path.errors,
        )


    def test_configured_currency_format_is_used_on_public_listing(self):
        settings = AutoGrid360Settings(
            id=1,
            currency_symbol="€",
            currency_decimal_separator=",",
            currency_thousands_separator=".",
        )
        db.session.add(settings)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        body = self.app.test_client().get(
            self.public_listing_path(listing)
        ).get_data(as_text=True)

        self.assertIn("€9.250,00", body)


    def test_currency_separators_must_be_distinct(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                currency_symbol="$",
                currency_decimal_separator=",",
                currency_thousands_separator=",",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Thousands separator must differ from the decimal separator.",
            response.get_data(as_text=True),
        )
        self.assertIsNone(db.session.get(AutoGrid360Settings, 1))


    def test_currency_code_must_be_three_ascii_letters(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                currency_code="U$D",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Currency code must contain exactly three letters.",
            response.get_data(as_text=True),
        )
        self.assertIsNone(db.session.get(AutoGrid360Settings, 1))


    def test_admin_full_backup_and_restore_preserves_listing_state(self):
        listing = self._create_listing()
        now = datetime.now(timezone.utc)
        listing.status = STATUS_ACTIVE
        listing.created_at = now - timedelta(days=20)
        listing.first_published_at = now - timedelta(days=15)
        listing.published_at = now - timedelta(days=10)
        listing.expires_at = now + timedelta(days=50)
        listing.featured = True
        listing.view_count = 123
        portable_id = listing.portable_id
        db.session.commit()

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        export_response = admin_client.get("/autogrid360/admin/inventory-export-all")
        self.assertEqual(export_response.status_code, 200)
        bundle_bytes = export_response.get_data()
        with zipfile.ZipFile(BytesIO(bundle_bytes), "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["scope"], "site")
        self.assertEqual(manifest["listings"][0]["seller_username"], self.seller.username)
        export_response.close()

        db.session.delete(listing)
        db.session.commit()
        restore_response = admin_client.post(
            "/autogrid360/admin/inventory-restore",
            data={"bundle": (BytesIO(bundle_bytes), "site-backup.zip")},
            content_type="multipart/form-data",
        )

        self.assertEqual(restore_response.status_code, 302)
        restored = Listing.query.filter_by(portable_id=portable_id).one()
        self.assertEqual(restored.seller_id, self.seller.id)
        self.assertEqual(restored.status, STATUS_ACTIVE)
        self.assertTrue(restored.featured)
        self.assertEqual(restored.view_count, 123)
        self.assertIsNotNone(restored.published_at)
        self.assertIsNotNone(restored.expires_at)


    def test_inventory_export_downloads_canonical_bundle_for_current_seller(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        listing = self._create_listing()
        upload_response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file("portable.jpg")]},
            content_type="multipart/form-data",
        )
        self.assertEqual(upload_response.status_code, 302)

        response = client.get("/autogrid360/account/inventory-export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        with zipfile.ZipFile(BytesIO(response.get_data()), "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], "autogrid360-inventory")
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(manifest["seller"]["username"], self.seller.username)
            self.assertNotIn("email", manifest["seller"])
            self.assertEqual(len(manifest["listings"]), 1)
            self.assertEqual(
                manifest["listings"][0]["listing"]["title"],
                listing.title,
            )
            image_path = manifest["listings"][0]["images"][0]["path"]
            self.assertIn(image_path, archive.namelist())
        response.close()


    def test_inventory_restore_preserves_state_when_seller_import_enabled(self):
        source_listing = self._create_listing()
        now = datetime.now(timezone.utc)
        source_listing.status = STATUS_ACTIVE
        source_listing.featured = True
        source_listing.view_count = 44
        source_listing.created_at = now - timedelta(days=30)
        source_listing.first_published_at = now - timedelta(days=20)
        source_listing.published_at = now - timedelta(days=10)
        source_listing.expires_at = now + timedelta(days=50)
        settings = AutoGrid360Settings(
            id=1,
            allow_seller_inventory_import=True,
            listing_images_path=self.app.config["AUTOGRID360_IMAGE_ROOT"],
        )
        db.session.add(settings)
        db.session.commit()

        source_client = self.app.test_client()
        self._login(source_client, self.seller)
        export_response = source_client.get("/autogrid360/account/inventory-export")
        self.assertEqual(export_response.status_code, 200)
        bundle_bytes = export_response.get_data()
        export_response.close()

        db.session.delete(source_listing)
        db.session.commit()

        target_client = self.app.test_client()
        self._login(target_client, self.other_user)
        response = target_client.post(
            "/autogrid360/account/inventory-import",
            data={"bundle": (BytesIO(bundle_bytes), "inventory.zip")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        imported = Listing.query.filter_by(seller_id=self.other_user.id).one()
        self.assertEqual(imported.title, "Seller-owned Civic")
        self.assertEqual(imported.status, STATUS_ACTIVE)
        self.assertTrue(imported.featured)
        self.assertEqual(imported.view_count, 44)
        self.assertIsNotNone(imported.published_at)
        self.assertIsNotNone(imported.expires_at)


    def test_inventory_restore_is_disabled_for_sellers_by_default(self):
        source_listing = self._create_listing()
        source_client = self.app.test_client()
        self._login(source_client, self.seller)
        export_response = source_client.get("/autogrid360/account/inventory-export")
        bundle_bytes = export_response.get_data()
        export_response.close()
        db.session.delete(source_listing)
        db.session.commit()

        target_client = self.app.test_client()
        self._login(target_client, self.other_user)
        response = target_client.post(
            "/autogrid360/account/inventory-import",
            data={"bundle": (BytesIO(bundle_bytes), "inventory.zip")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Listing.query.filter_by(seller_id=self.other_user.id).count(), 0)


    def test_inventory_import_rejects_bundle_listing_already_present(self):
        settings = AutoGrid360Settings(
            id=1,
            allow_seller_inventory_import=True,
            listing_images_path=self.app.config["AUTOGRID360_IMAGE_ROOT"],
        )
        db.session.add(settings)
        db.session.commit()
        source_listing = self._create_listing()
        source_client = self.app.test_client()
        self._login(source_client, self.seller)
        export_response = source_client.get("/autogrid360/account/inventory-export")
        bundle_bytes = export_response.get_data()
        export_response.close()

        target_client = self.app.test_client()
        self._login(target_client, self.other_user)
        response = target_client.post(
            "/autogrid360/account/inventory-import",
            data={"bundle": (BytesIO(bundle_bytes), "inventory.zip")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Listing.query.filter_by(seller_id=self.other_user.id).count(), 0)
        self.assertIn(
            ("danger", "The bundle contains listing(s) that already exist in this installation."),
            self._flash_messages(target_client),
        )
        self.assertIsNotNone(db.session.get(Listing, source_listing.id))


    def test_admin_can_configure_public_sale_states_and_sold_retention(self):
        client = self.app.test_client()
        self._login(client, self.admin)
        data = self._settings_form_data(sold_retention_days="0")
        data.pop("show_sale_pending_listings_publicly")
        data.pop("show_sold_listings_publicly")

        response = client.post("/autogrid360/admin/settings", data=data)

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertFalse(settings.show_sale_pending_listings_publicly)
        self.assertFalse(settings.show_sold_listings_publicly)
        self.assertEqual(settings.sold_retention_days, 0)


    def test_admin_can_configure_expired_retention_policy(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                expired_retention_days="45",
                expired_removal_warning_days="10",
            ),
        )

        self.assertEqual(response.status_code, 302)
        settings = db.session.get(AutoGrid360Settings, 1)
        self.assertEqual(settings.expired_retention_days, 45)
        self.assertEqual(settings.expired_removal_warning_days, 10)
