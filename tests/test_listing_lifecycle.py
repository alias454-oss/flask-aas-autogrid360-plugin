# app/plugins/autogrid360/tests/test_listing_lifecycle.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.extensions import db
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REMOVED,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    AutoGrid360Settings,
    Listing,
    ListingImage,
    Vehicle,
)
from app.plugins.autogrid360.services.media import image_root as listing_image_root
from app.plugins.autogrid360.tests.listing_support import AutoGrid360ListingRouteTestCase


class AutoGrid360ListingLifecycleRouteTests(AutoGrid360ListingRouteTestCase):
    def test_listing_lifecycle_mutations_require_login(self):
        cases = (
            ("submit", STATUS_DRAFT),
            ("approve", STATUS_PENDING),
            ("sold", STATUS_ACTIVE),
            ("expire", STATUS_ACTIVE),
            ("remove", STATUS_ACTIVE),
            ("delete", STATUS_DRAFT),
        )

        for action, status in cases:
            with self.subTest(action=action):
                listing = self._create_listing()
                listing.status = status
                db.session.commit()

                response = self.app.test_client().post(
                    f"/autogrid360/listings/{listing.id}/{action}"
                )

                self.assertEqual(response.status_code, 302)
                self.assertIn("/host-login", response.headers["Location"])
                db.session.expire_all()
                unchanged = db.session.get(Listing, listing.id)
                self.assertIsNotNone(unchanged)
                self.assertEqual(unchanged.status, status)


    def test_non_owner_cannot_submit_listing(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_DRAFT)


    def test_owner_submits_draft_listing_for_approval(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail_response = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/submit"',
            detail_response.get_data(as_text=True),
        )

        response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)


    def test_pending_submission_notifies_configured_site_administrator(self):
        self.env_settings.admin_email = "review-admin@example.test"
        db.session.commit()
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.notifications.send_email",
            return_value="queued",
        ) as send_email:
            response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        send_email.assert_called_once()
        subject, recipient, body = send_email.call_args.args
        self.assertIn(listing.title, subject)
        self.assertEqual(recipient, "review-admin@example.test")
        self.assertIn("new listing submitted for approval", body)
        self.assertIn("/autogrid360/admin/pending", body)


    def test_direct_publication_does_not_notify_administrator(self):
        self.env_settings.admin_email = "review-admin@example.test"
        self._set_listing_policy(approval=False, rereview=False)
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch("app.plugins.autogrid360.services.notifications.send_email") as send_email:
            response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        send_email.assert_not_called()


    def test_admin_submission_does_not_notify_administrator(self):
        self.env_settings.admin_email = "review-admin@example.test"
        db.session.commit()
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.admin)

        with patch("app.plugins.autogrid360.services.notifications.send_email") as send_email:
            response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        send_email.assert_not_called()


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_lifecycle_mutations_reject_invalid_source_states(self, _audit_enabled):
        cases = (
            ("submit", self.seller, (STATUS_PENDING,)),
            (
                "sold",
                self.seller,
                (STATUS_DRAFT, STATUS_PENDING, STATUS_SOLD, STATUS_EXPIRED, STATUS_REMOVED),
            ),
            ("expire", self.admin, (STATUS_SOLD,)),
            ("remove", self.admin, (STATUS_DRAFT, STATUS_PENDING, STATUS_REMOVED)),
        )

        for action, actor, statuses in cases:
            client = self.app.test_client()
            self._login(client, actor)
            for status in statuses:
                with self.subTest(action=action, status=status):
                    listing = self._create_listing()
                    listing.status = status
                    db.session.commit()

                    response = client.post(
                        f"/autogrid360/listings/{listing.id}/{action}"
                    )

                    self.assertEqual(response.status_code, 409)
                    db.session.refresh(listing)
                    self.assertEqual(listing.status, status)


    def test_unprivileged_user_cannot_approve_listing(self):
        listing = self._create_listing()
        listing.status = STATUS_PENDING
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(f"/autogrid360/listings/{listing.id}/approve")

        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)


    def test_moderator_has_no_autogrid360_approval_authority(self):
        listing = self._create_listing()
        listing.status = STATUS_PENDING
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.moderator)

        detail_response = client.get(f"/autogrid360/listings/{listing.id}")
        response = client.post(f"/autogrid360/listings/{listing.id}/approve")

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)


    def test_admin_approves_pending_listing(self):
        listing = self._create_listing()
        listing.status = STATUS_PENDING
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/listings/{listing.id}/approve")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    def test_admin_approval_assigns_configured_expiration_deadline(self):
        self._set_listing_policy(
            approval=True,
            rereview=True,
            expiration=True,
            expiration_days=45,
            warning_days=5,
        )
        listing = self._create_listing()
        listing.status = STATUS_PENDING
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/listings/{listing.id}/approve")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertEqual(listing.expires_at - listing.published_at, timedelta(days=45))


    def test_moderator_approval_route_is_denied_before_state_evaluation(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.moderator)

        response = client.post(f"/autogrid360/listings/{listing.id}/approve")

        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_DRAFT)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_marks_active_listing_sold(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        published_at = db.func.now()
        listing.published_at = published_at
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/sold"',
            detail.get_data(as_text=True),
        )

        response = client.post(f"/autogrid360/listings/{listing.id}/sold")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SOLD)
        self.assertIsNotNone(listing.published_at)
        self.assertIsNotNone(listing.sold_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_can_move_listing_through_sale_pending_and_available_states(
        self, _audit_enabled
    ):
        now = datetime.now(timezone.utc)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.first_published_at = now - timedelta(days=2)
        listing.published_at = now - timedelta(days=2)
        listing.expires_at = now + timedelta(days=58)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        pending_response = client.post(
            f"/autogrid360/listings/{listing.id}/sale-pending"
        )
        self.assertEqual(pending_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SALE_PENDING)

        available_response = client.post(
            f"/autogrid360/listings/{listing.id}/available"
        )
        self.assertEqual(available_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNone(listing.sold_at)

        client.post(f"/autogrid360/listings/{listing.id}/sale-pending")
        sold_response = client.post(f"/autogrid360/listings/{listing.id}/sold")
        self.assertEqual(sold_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SOLD)
        self.assertIsNotNone(listing.sold_at)

        pending_again_response = client.post(
            f"/autogrid360/listings/{listing.id}/sale-pending"
        )
        self.assertEqual(pending_again_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SALE_PENDING)
        self.assertIsNone(listing.sold_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_can_make_sold_listing_available_again(self, _audit_enabled):
        now = datetime.now(timezone.utc)
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        listing.first_published_at = now - timedelta(days=10)
        listing.published_at = now - timedelta(days=10)
        listing.expires_at = now + timedelta(days=50)
        listing.sold_at = now - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/available")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNone(listing.sold_at)
        self.assertIsNotNone(listing.published_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_non_owner_cannot_change_sale_availability_state(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.other_user)

        self.assertEqual(
            client.post(f"/autogrid360/listings/{listing.id}/sale-pending").status_code,
            404,
        )
        listing.status = STATUS_SOLD
        listing.sold_at = datetime.now(timezone.utc)
        db.session.commit()
        self.assertEqual(
            client.post(f"/autogrid360/listings/{listing.id}/available").status_code,
            404,
        )


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_moderator_cannot_mark_another_sellers_listing_sold(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.moderator)

        response = client.post(f"/autogrid360/listings/{listing.id}/sold")

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_admin_marks_active_listing_sold(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/listings/{listing.id}/sold")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SOLD)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_non_owner_cannot_mark_listing_sold(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(f"/autogrid360/listings/{listing.id}/sold")

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    @patch("app.plugins.autogrid360.services.audit.log_action")
    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=True)
    def test_lifecycle_mutations_queue_status_audit_events(self, _audit_enabled, log_action):
        cases = (
            ("sold", self.seller, STATUS_ACTIVE, STATUS_SOLD),
            ("expire", self.admin, STATUS_ACTIVE, STATUS_EXPIRED),
            ("remove", self.admin, STATUS_SOLD, STATUS_REMOVED),
        )

        for action, actor, previous_status, new_status in cases:
            with self.subTest(action=action):
                listing = self._create_listing()
                listing.status = previous_status
                db.session.commit()
                client = self.app.test_client()
                self._login(client, actor)
                log_action.reset_mock()

                response = client.post(
                    f"/autogrid360/listings/{listing.id}/{action}"
                )

                self.assertEqual(response.status_code, 302)
                log_action.assert_called_once_with(
                    user_id=actor.id,
                    action="autogrid360_listing_status_changed",
                    target=f"listing:{listing.id}",
                    extra_data={
                        "listing_id": listing.id,
                        "seller_id": self.seller.id,
                        "previous_status": previous_status,
                        "new_status": new_status,
                    },
                )


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_admin_can_override_listing_lifecycle_status(self, _audit_enabled):
        now = datetime.now(timezone.utc)
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        listing.first_published_at = now - timedelta(days=5)
        listing.published_at = now - timedelta(days=5)
        listing.expires_at = now + timedelta(days=55)
        listing.sold_at = now - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        pending = client.post(
            f"/autogrid360/listings/{listing.id}/status",
            data={"status": STATUS_PENDING},
        )
        self.assertEqual(pending.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.sold_at)
        self.assertIsNone(listing.published_at)

        available = client.post(
            f"/autogrid360/listings/{listing.id}/status",
            data={"status": STATUS_ACTIVE},
        )
        self.assertEqual(available.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)

        sold = client.post(
            f"/autogrid360/listings/{listing.id}/status",
            data={"status": STATUS_SOLD},
        )
        self.assertEqual(sold.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_SOLD)
        self.assertIsNotNone(listing.sold_at)


    def test_non_admin_cannot_use_lifecycle_override(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/status",
            data={"status": STATUS_ACTIVE},
        )

        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_DRAFT)


    def test_sold_listing_is_public_in_inventory_and_detail_by_default(self):
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        listing.sold_at = datetime.now(timezone.utc)
        db.session.commit()
        self._create_image(listing, is_primary=True, token="sold-primary")
        client = self.app.test_client()

        inventory = client.get("/autogrid360/")
        public_detail = client.get(self.public_listing_path(listing))

        self.assertEqual(inventory.status_code, 200)
        inventory_body = inventory.get_data(as_text=True)
        self.assertIn(listing.title, inventory_body)
        self.assertIn("<strong>Sold</strong>", inventory_body)
        self.assertIn("autogrid360-status-ribbon--sold", inventory_body)
        self.assertIn(">Sold</span>", inventory_body)
        self.assertEqual(public_detail.status_code, 200)
        body = public_detail.get_data(as_text=True)
        self.assertIn(listing.title, body)
        self.assertIn("<strong>Sold</strong>", body)
        self.assertIn("autogrid360-status-ribbon--sold", body)
        self.assertIn(">Sold</span>", body)
        self.assertNotIn("Contact Seller", body)


    def test_public_inventory_orders_available_before_sale_pending_before_sold(self):
        active = self._create_active_inventory_listing(
            title="Available Civic", year=2020, make="Honda", model="Civic"
        )
        sale_pending = self._create_active_inventory_listing(
            title="Pending Accord", year=2021, make="Honda", model="Accord"
        )
        sold = self._create_active_inventory_listing(
            title="Sold Mustang", year=2022, make="Ford", model="Mustang"
        )
        sale_pending.status = STATUS_SALE_PENDING
        sold.status = STATUS_SOLD
        sold.sold_at = datetime.now(timezone.utc)
        db.session.commit()

        body = self.app.test_client().get(
            "/autogrid360/", query_string={"sort": "year_desc", "per_page": "10"}
        ).get_data(as_text=True)

        self.assertLess(body.index(active.title), body.index(sale_pending.title))
        self.assertLess(body.index(sale_pending.title), body.index(sold.title))


    def test_admin_can_hide_sold_inventory_from_all_public_surfaces(self):
        self._set_listing_policy(show_sold=False)
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        listing.sold_at = datetime.now(timezone.utc)
        db.session.commit()
        client = self.app.test_client()

        inventory = client.get("/autogrid360/")
        public_detail = client.get(self.public_listing_path(listing))

        self.assertNotIn(listing.title, inventory.get_data(as_text=True))
        self.assertEqual(public_detail.status_code, 404)


    def test_sale_pending_listing_is_public_and_contactable_by_default(self):
        listing = self._create_listing()
        listing.status = STATUS_SALE_PENDING
        db.session.commit()
        self._create_image(listing, is_primary=True, token="pending-primary")
        client = self.app.test_client()

        inventory = client.get("/autogrid360/")
        public_detail = client.get(self.public_listing_path(listing))
        contact = client.get(f"/autogrid360/listings/{listing.id}/contact")

        self.assertEqual(inventory.status_code, 200)
        inventory_body = inventory.get_data(as_text=True)
        self.assertIn(listing.title, inventory_body)
        self.assertIn("Sale Pending", inventory_body)
        self.assertIn("autogrid360-status-ribbon--pending", inventory_body)
        self.assertIn(">Pending</span>", inventory_body)
        self.assertEqual(public_detail.status_code, 200)
        public_body = public_detail.get_data(as_text=True)
        self.assertIn("<strong>Sale Pending</strong>", public_body)
        self.assertIn("autogrid360-status-ribbon--pending", public_body)
        self.assertIn(">Pending</span>", public_body)
        self.assertEqual(contact.status_code, 200)


    def test_admin_can_hide_sale_pending_inventory_from_public_surfaces(self):
        self._set_listing_policy(show_sale_pending=False)
        listing = self._create_listing()
        listing.status = STATUS_SALE_PENDING
        db.session.commit()
        client = self.app.test_client()

        inventory = client.get("/autogrid360/")
        public_detail = client.get(self.public_listing_path(listing))
        contact = client.get(f"/autogrid360/listings/{listing.id}/contact")

        self.assertNotIn(listing.title, inventory.get_data(as_text=True))
        self.assertEqual(public_detail.status_code, 404)
        self.assertEqual(contact.status_code, 404)


    def test_sold_listing_contact_route_is_not_available(self):
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        db.session.commit()
        client = self.app.test_client()

        get_response = client.get(f"/autogrid360/listings/{listing.id}/contact")
        post_response = client.post(
            f"/autogrid360/listings/{listing.id}/contact",
            data={
                "name": "Buyer",
                "email": "buyer@example.test",
                "message": "Is this still available?",
            },
        )

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_cannot_manually_expire_active_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        response = client.post(f"/autogrid360/listings/{listing.id}/expire")

        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(
            f'action="/autogrid360/listings/{listing.id}/expire"',
            detail.get_data(as_text=True),
        )
        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_moderator_has_no_autogrid360_expiration_authority(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.moderator)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        response = client.post(f"/autogrid360/listings/{listing.id}/expire")

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(response.status_code, 403)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_admin_expires_active_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(f"/autogrid360/listings/{listing.id}/expire")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_EXPIRED)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_moderator_cannot_remove_another_sellers_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.moderator)

        response = client.post(f"/autogrid360/listings/{listing.id}/remove")

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_admin_removes_sold_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_SOLD
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.admin)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/remove"',
            detail.get_data(as_text=True),
        )

        response = client.post(f"/autogrid360/listings/{listing.id}/remove")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_REMOVED)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_can_soft_remove_active_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/remove"',
            detail.get_data(as_text=True),
        )

        response = client.post(f"/autogrid360/listings/{listing.id}/remove")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_REMOVED)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_unrelated_user_cannot_soft_remove_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(f"/autogrid360/listings/{listing.id}/remove")

        self.assertEqual(response.status_code, 404)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    def test_expired_and_removed_listings_are_private_but_remain_in_my_listings(self):
        expired = self._create_listing()
        expired.title = "Expired inventory"
        expired.status = STATUS_EXPIRED
        removed = self._create_listing()
        removed.title = "Removed inventory"
        removed.status = STATUS_REMOVED
        db.session.commit()
        client = self.app.test_client()

        search = client.get("/autogrid360/").get_data(as_text=True)
        self.assertNotIn(expired.title, search)
        self.assertNotIn(removed.title, search)
        self.assertEqual(
            client.get(self.public_listing_path(expired)).status_code,
            404,
        )
        self.assertEqual(
            client.get(self.public_listing_path(removed)).status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/autogrid360/listings/{expired.id}/contact").status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/autogrid360/listings/{removed.id}/contact").status_code,
            404,
        )

        self._login(client, self.seller)
        mine = client.get("/autogrid360/listings/")
        body = mine.get_data(as_text=True)
        self.assertIn(expired.title, body)
        self.assertIn("Status:</strong> Expired", body)
        self.assertIn(removed.title, body)
        self.assertIn("Status:</strong> Removed", body)
        self.assertEqual(
            client.get(f"/autogrid360/listings/{expired.id}").status_code,
            200,
        )
        self.assertEqual(
            client.get(f"/autogrid360/listings/{removed.id}").status_code,
            200,
        )


    def test_active_owner_uses_soft_remove_instead_of_hard_delete(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        body = detail.get_data(as_text=True)

        self.assertNotIn(
            f'action="/autogrid360/listings/{listing.id}/delete"',
            body,
        )
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/remove"',
            body,
        )

        response = client.post(f"/autogrid360/listings/{listing.id}/delete")

        self.assertEqual(response.status_code, 409)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)


    def test_listing_detail_includes_delete_form(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get(f"/autogrid360/listings/{listing.id}")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/delete"',
            body,
        )
        self.assertIn('method="post"', body)
        self.assertIn('value="Delete Listing"', body)


    def test_non_owner_cannot_delete_listing(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(f"/autogrid360/listings/{listing.id}/delete")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Listing.query.count(), 1)
        self.assertEqual(Vehicle.query.count(), 1)


    def test_owner_deletes_listing_but_preserves_vehicle(self):
        listing = self._create_listing()
        listing_id = listing.id
        vehicle_id = listing.vehicle.id
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing_id}/delete")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/autogrid360/listings/"))
        self.assertIsNone(db.session.get(Listing, listing_id))
        self.assertIsNotNone(db.session.get(Vehicle, vehicle_id))
        self.assertEqual(Listing.query.count(), 0)
        self.assertEqual(Vehicle.query.count(), 1)


    def test_listing_delete_removes_associated_image_records_and_files(self):
        listing = self._create_listing()
        image = self._create_image(listing, is_primary=True, token="delete-with-listing")
        image_paths = [
            listing_image_root() / image.storage_key,
            listing_image_root() / image.thumbnail_key,
        ]
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/delete")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListingImage.query.count(), 0)
        self.assertTrue(all(not path.exists() for path in image_paths))


    def test_listing_detail_is_visible_to_owner_or_admin_only(self):
        listing = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="Seller-owned Civic",
        )
        db.session.add(listing)
        db.session.commit()

        owner_client = self.app.test_client()
        self._login(owner_client, self.seller)
        self.assertEqual(
            owner_client.get(f"/autogrid360/listings/{listing.id}").status_code,
            200,
        )

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        self.assertEqual(
            admin_client.get(f"/autogrid360/listings/{listing.id}").status_code,
            200,
        )

        moderator_client = self.app.test_client()
        self._login(moderator_client, self.moderator)
        self.assertEqual(
            moderator_client.get(f"/autogrid360/listings/{listing.id}").status_code,
            404,
        )


    def test_disabled_approval_publishes_draft_directly(self):
        self._set_listing_policy(approval=False, rereview=True)
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)
        public_response = self.app.test_client().get(
            self.public_listing_path(listing)
        )
        self.assertEqual(public_response.status_code, 200)


    def test_direct_publication_assigns_configured_expiration_deadline(self):
        self._set_listing_policy(
            approval=False,
            rereview=True,
            expiration=True,
            expiration_days=30,
            warning_days=5,
        )
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/submit")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)
        self.assertIsNotNone(listing.expires_at)
        self.assertEqual(listing.expires_at - listing.published_at, timedelta(days=30))


    def test_disabled_approval_changes_submit_label_to_publish(self):
        self._set_listing_policy(approval=False, rereview=True)
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        body = client.get(f"/autogrid360/listings/{listing.id}").get_data(as_text=True)

        self.assertIn('value="Publish Listing"', body)
        self.assertNotIn('value="Submit for Approval"', body)


    def test_active_text_edit_returns_to_pending_when_rereview_enabled(self):
        self._set_listing_policy(approval=True, rereview=True)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Moderated edit"),
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)


    def test_active_text_rereview_notifies_configured_site_administrator(self):
        self.env_settings.admin_email = "review-admin@example.test"
        self._set_listing_policy(approval=True, rereview=True)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.notifications.send_email",
            return_value="queued",
        ) as send_email:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/edit",
                data=self._listing_form_data(title="Moderated edit notification"),
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        send_email.assert_called_once()
        self.assertIn("seller edit requires re-review", send_email.call_args.args[2])


    def test_active_text_edit_stays_live_when_rereview_disabled(self):
        self._set_listing_policy(approval=True, rereview=False)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Live edit"),
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    def test_active_text_edit_stays_live_when_approval_disabled(self):
        self._set_listing_policy(approval=False, rereview=True)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Direct-post edit"),
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    def test_active_image_change_stays_live_when_rereview_disabled(self):
        self._set_listing_policy(approval=True, rereview=False)
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.published_at = db.func.now()
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file()]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNotNone(listing.published_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_relists_unchanged_expired_listing_directly_with_fresh_lifetime(self, _audit_enabled):
        self._set_listing_policy(
            approval=True,
            expiration=True,
            expiration_days=60,
        )
        listing = self._create_listing()
        first_published = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        listing.status = STATUS_EXPIRED
        listing.first_published_at = first_published
        listing.published_at = first_published
        listing.expires_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/relist")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        normalized_first_published = listing.first_published_at
        if normalized_first_published.tzinfo is None:
            normalized_first_published = normalized_first_published.replace(
                tzinfo=timezone.utc
            )
        self.assertEqual(normalized_first_published, first_published)
        self.assertIsNotNone(listing.published_at)
        self.assertGreater(listing.published_at.replace(tzinfo=timezone.utc) if listing.published_at.tzinfo is None else listing.published_at, first_published)
        self.assertIsNotNone(listing.expires_at)
        self.assertIsNone(listing.expired_at)
        self.assertIsNone(listing.expired_removal_warning_sent_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_changed_expired_listing_returns_to_approval_on_relist(self, _audit_enabled):
        self._set_listing_policy(approval=True, expiration=True)
        listing = self._create_listing()
        listing.status = STATUS_EXPIRED
        listing.first_published_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        listing.published_at = listing.first_published_at
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        edit_response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(title="Changed after expiration"),
        )
        self.assertEqual(edit_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_EXPIRED)
        self.assertIsNotNone(listing.expired_edited_at)

        relist_response = client.post(f"/autogrid360/listings/{listing.id}/relist")

        self.assertEqual(relist_response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)
        self.assertIsNone(listing.expires_at)
        self.assertIsNone(listing.expired_at)
        self.assertIsNone(listing.expired_edited_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_changed_expired_relist_notifies_configured_site_administrator(
        self,
        _audit_enabled,
    ):
        self.env_settings.admin_email = "review-admin@example.test"
        self._set_listing_policy(approval=True, expiration=True)
        listing = self._create_listing()
        listing.status = STATUS_EXPIRED
        listing.first_published_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        listing.published_at = listing.first_published_at
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=2)
        listing.expired_edited_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.services.notifications.send_email",
            return_value="queued",
        ) as send_email:
            response = client.post(f"/autogrid360/listings/{listing.id}/relist")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        send_email.assert_called_once()
        self.assertIn(
            "changed expired listing submitted for re-approval",
            send_email.call_args.args[2],
        )


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_changed_expired_listing_reactivates_when_approval_is_disabled(self, _audit_enabled):
        self._set_listing_policy(approval=False, expiration=True, expiration_days=45)
        listing = self._create_listing()
        listing.status = STATUS_EXPIRED
        listing.first_published_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        listing.published_at = listing.first_published_at
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=2)
        listing.expired_edited_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(f"/autogrid360/listings/{listing.id}/relist")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertEqual(
            listing.first_published_at.replace(tzinfo=timezone.utc) if listing.first_published_at.tzinfo is None else listing.first_published_at,
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(listing.expires_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_owner_can_soft_remove_expired_listing(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_EXPIRED
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertIn(
            f'action="/autogrid360/listings/{listing.id}/remove"',
            detail.get_data(as_text=True),
        )
        response = client.post(f"/autogrid360/listings/{listing.id}/remove")

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_REMOVED)
        self.assertIsNone(listing.aged_out_at)


    @patch("app.plugins.autogrid360.services.audit.audit_activity_enabled", return_value=False)
    def test_expired_image_change_marks_listing_changed_before_relist(self, _audit_enabled):
        listing = self._create_listing()
        listing.status = STATUS_EXPIRED
        listing.expired_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file("expired-update.jpg")]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_EXPIRED)
        self.assertIsNotNone(listing.expired_edited_at)


    def test_expired_removal_warning_must_precede_retention_deadline(self):
        client = self.app.test_client()
        self._login(client, self.admin)

        response = client.post(
            "/autogrid360/admin/settings",
            data=self._settings_form_data(
                expired_retention_days="30",
                expired_removal_warning_days="30",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Expired removal warning lead time must be less than the retention period.",
            response.get_data(as_text=True),
        )
        self.assertIsNone(db.session.get(AutoGrid360Settings, 1))
