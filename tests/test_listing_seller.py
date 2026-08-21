# app/plugins/autogrid360/tests/test_listing_seller.py
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from app.core.extensions import db
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_SOLD,
    AutoGrid360Settings,
    Listing,
    ListingImage,
    SellerProfile,
    Vehicle,
    VehicleModel,
)
from app.plugins.autogrid360.services.media import image_root as listing_image_root
from app.plugins.autogrid360.tests.listing_support import AutoGrid360ListingRouteTestCase


class AutoGrid360SellerListingRouteTests(AutoGrid360ListingRouteTestCase):
    def test_seller_pages_require_login(self):
        listing = self._create_listing()
        paths = (
            "/autogrid360/listings/",
            "/autogrid360/listings/create",
            f"/autogrid360/listings/{listing.id}/edit",
            "/autogrid360/account/profile",
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.app.test_client().get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/host-login", response.headers["Location"])


    def test_my_listings_shows_only_current_users_listings(self):
        owned = self._create_listing()
        other = Listing(
            seller=self.other_user,
            vehicle=self._vehicle(make="Ford", model="Focus"),
            title="Other user's Focus",
        )
        db.session.add(other)
        db.session.commit()

        client = self.app.test_client()
        self._login(client, self.seller)
        response = client.get("/autogrid360/listings/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("My Listings", body)
        self.assertIn(owned.title, body)
        self.assertIn("Honda Civic", body)
        self.assertNotIn(other.title, body)
        self.assertIn(f'/autogrid360/listings/{owned.id}', body)
        self.assertIn("Create Listing", body)


    def test_my_listings_renders_primary_thumbnail(self):
        listing = self._create_listing()
        primary = self._create_image(listing, position=0, is_primary=True)

        client = self.app.test_client()
        self._login(client, self.seller)
        response = client.get("/autogrid360/listings/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            f"/autogrid360/listings/{listing.id}/images/{primary.id}/thumb",
            body,
        )


    def test_my_listings_empty_state_links_to_create(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/listings/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("You do not have any listings yet.", body)
        self.assertIn('/autogrid360/listings/create', body)


    def test_authenticated_user_can_open_create_form(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/listings/create")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Create Listing", body)
        self.assertIn("Listing Title", body)
        self.assertIn("Make", body)
        self.assertIn("Model", body)
        self.assertIn("Country", body)
        self.assertIn("Region / Subdivision", body)
        self.assertIn('data-location-country="true"', body)
        self.assertIn('data-location-zone="true"', body)
        self.assertIn("United States", body)
        self.assertIn("City / Locality", body)
        self.assertNotIn("Street Address", body)
        self.assertIn("Save Draft", body)
        self.assertRegex(
            body,
            r'<input(?=[^>]*\bid="price")(?=[^>]*\btype="text")[^>]*>',
        )
        self.assertIn('data-currency-preview="price-preview"', body)
        self.assertIn('id="price-preview"', body)
        self.assertIn('data-currency-symbol="$"', body)
        self.assertIn('data-currency-decimal-separator="."', body)
        self.assertIn('data-currency-thousands-separator=","', body)
        self.assertIn('/autogrid360/static/currency.js', body)


    def test_create_accepts_human_formatted_price(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(price="$ 32,565.00"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Listing.query.one().price, Decimal("32565.00"))


    def test_create_uses_configured_currency_separators_for_price(self):
        db.session.add(
            AutoGrid360Settings(
                id=1,
                currency_code="EUR",
                currency_symbol="€",
                currency_decimal_separator=",",
                currency_thousands_separator=".",
            )
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        get_response = client.get("/autogrid360/listings/create")
        get_body = get_response.get_data(as_text=True)
        self.assertEqual(get_response.status_code, 200)
        self.assertIn('data-currency-symbol="€"', get_body)
        self.assertIn('data-currency-decimal-separator=","', get_body)
        self.assertIn('data-currency-thousands-separator="."', get_body)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(price="€32.565,75"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Listing.query.one().price, Decimal("32565.75"))


    def test_create_rejects_malformed_price_with_visible_field_error(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(price="$8,95,0"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter a valid price.", response.get_data(as_text=True))
        self.assertEqual(Listing.query.count(), 0)


    def test_create_rejects_price_above_database_range(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(price="10000000000.00"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Price must be between 0 and 9999999999.99.",
            response.get_data(as_text=True),
        )
        self.assertEqual(Listing.query.count(), 0)


    def test_create_form_uses_controlled_vehicle_editor_fields(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/listings/create")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        latest_model_year = datetime.now(timezone.utc).year + 1
        self.assertIn('id="year" name="year"', body)
        self.assertIn(
            f'<option value="{latest_model_year}">{latest_model_year}</option>',
            body,
        )
        self.assertIn('<option value="2000">2000</option>', body)
        self.assertIn(
            '<option value="__other__">Older / Other...</option>',
            body,
        )
        self.assertIn('id="year_other"', body)
        self.assertIn('placeholder="YYYY"', body)
        self.assertIn('id="condition" name="condition"', body)
        self.assertIn(
            '<option value="Certified Pre-Owned">Certified Pre-Owned</option>',
            body,
        )
        self.assertIn('id="transmission" name="transmission"', body)
        self.assertIn('<option value="Dual-Clutch">Dual-Clutch</option>', body)
        self.assertIn('id="fuel_type" name="fuel_type"', body)
        self.assertIn('<option value="Electric">Electric</option>', body)
        self.assertIn('<option value="Propane / LPG">Propane / LPG</option>', body)
        self.assertIn('id="doors" name="doors"', body)
        self.assertIn('id="doors_other"', body)
        self.assertIn('Four Wheel Drive', body)
        self.assertIn('/autogrid360/static/editor.js', body)


    def test_create_accepts_fallback_year_doors_and_controlled_values(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(
                year="__other__",
                year_other="1998",
                doors="__other__",
                doors_other="1",
                condition="Project",
                transmission="Manual",
                fuel_type="Propane / LPG",
                drivetrain="4WD",
            ),
        )

        self.assertEqual(response.status_code, 302)
        vehicle = Vehicle.query.one()
        self.assertEqual(vehicle.year, 1998)
        self.assertEqual(vehicle.doors, 1)
        self.assertEqual(vehicle.condition, "Project")
        self.assertEqual(vehicle.transmission, "Manual")
        self.assertEqual(vehicle.fuel_type, "Propane / LPG")
        self.assertEqual(vehicle.drivetrain, "Four Wheel Drive")


    def test_create_rejects_invalid_fallback_model_year(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(
                year="__other__",
                year_other="98",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertEqual(Vehicle.query.count(), 0)
        self.assertIn(
            "Enter a four-digit model year (YYYY).",
            response.get_data(as_text=True),
        )


    def test_invalid_create_does_not_persist_partial_records(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data={
                "title": "Incomplete listing",
                "make": "Ford",
                "model": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertEqual(Vehicle.query.count(), 0)
        self.assertIn("This field is required.", response.get_data(as_text=True))


    def test_create_rejects_zone_from_another_country(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(
                country_code="CA",
                zone_code="US-IL",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertIn("Not a valid choice", response.get_data(as_text=True))


    def test_authenticated_user_creates_draft_listing_and_vehicle(self):
        postal = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="IL",
            latitude=42.2967,
            longitude=-89.6212,
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data={
                "title": "Nice little Ford",
                "price": "32565.00",
                "description": "First listing created through the AutoGrid360 web flow.",
                "year": "2005",
                "make": "Ford",
                "model": "ford:ka-sport",
                "model_other": "",
                "vehicle_type": "Convertible",
                "mileage": "89989",
                "vin": "1FTCR10T7KUB59290",
                "country_code": "US",
                "zone_code": "US-IL",
                "postal_code": "61032",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Listing.query.count(), 1)
        self.assertEqual(Vehicle.query.count(), 1)

        listing = Listing.query.one()
        self.assertEqual(listing.seller_id, self.seller.id)
        self.assertEqual(listing.status, STATUS_DRAFT)
        self.assertEqual(listing.title, "Nice little Ford")
        self.assertEqual(listing.price, Decimal("32565.00"))
        self.assertEqual(listing.vehicle.make, "Ford")
        self.assertEqual(listing.vehicle.model, "KA Sport")
        self.assertIsNotNone(listing.vehicle.model_id)
        self.assertIsNone(listing.vehicle.model_text)
        self.assertEqual(listing.city, "Freeport")
        self.assertEqual(listing.postal_location_id, postal.id)
        self.assertIn(
            f"/autogrid360/listings/{listing.id}",
            response.headers["Location"],
        )


    def test_create_autofills_city_and_zone_from_known_postal_location(self):
        postal = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="Illinois",
            latitude=42.2967,
            longitude=-89.6212,
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(city="", zone_code=""),
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.one()
        self.assertEqual(listing.city, "Freeport")
        self.assertEqual(listing.zone_code, "US-IL")
        self.assertEqual(listing.postal_location_id, postal.id)


    def test_create_preserves_seller_city_override_when_postal_location_resolves(self):
        postal = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="Illinois",
            latitude=42.2967,
            longitude=-89.6212,
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(city="Lake Summerset"),
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.one()
        self.assertEqual(listing.city, "Lake Summerset")
        self.assertEqual(listing.postal_location_id, postal.id)


    def test_create_accepts_manual_city_when_postal_data_is_unavailable(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(city="Freeport", postal_code="99999"),
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.one()
        self.assertEqual(listing.city, "Freeport")
        self.assertIsNone(listing.postal_location_id)


    def test_create_requires_manual_city_when_geo_lookup_cannot_supply_one(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(city="", postal_code="99999"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertIn(
            "Enter a city/locality, or provide a postal code that resolves to one.",
            response.get_data(as_text=True),
        )


    def test_public_geo_lookup_returns_locality_without_private_address_data(self):
        self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="Illinois",
            latitude=42.2967,
            longitude=-89.6212,
        )
        db.session.commit()

        response = self.app.test_client().get(
            "/autogrid360/geo/lookup?country=United+States&postal_code=61032"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["city"], "Freeport")
        self.assertEqual(payload["zone_code"], "US-IL")
        self.assertNotIn("address", payload)
        self.assertNotIn("street", payload)


    def test_public_geo_lookup_returns_not_found_for_unknown_code(self):
        response = self.app.test_client().get(
            "/autogrid360/geo/lookup?country=US&postal_code=99999"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"found": False})


    def test_public_listing_displays_iso_location_labels(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Freeport, Illinois 61032 · United States", response.get_data(as_text=True))


    def test_create_normalizes_lowercase_vin(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data=self._listing_form_data(
                title="Lowercase VIN Civic",
                vin="2hgfb2f50dh000001",
            ),
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.one()
        self.assertEqual(listing.vehicle.vin, "2HGFB2F50DH000001")


    def test_create_rejects_structurally_invalid_vins(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        cases = (
            ("1HGCM82633A00435", "VIN must be exactly 17 characters."),
            ("1HGCM82633A00I35Q", "I, O, and Q are not allowed"),
        )

        for vin, error in cases:
            with self.subTest(vin=vin):
                response = client.post(
                    "/autogrid360/listings/create",
                    data=self._listing_form_data(title="Invalid VIN Civic", vin=vin),
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(Listing.query.count(), 0)
                self.assertIn(error, response.get_data(as_text=True))


    def test_authenticated_user_can_create_unlisted_model_without_polluting_model_defaults(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        model_count = VehicleModel.query.count()

        response = client.post(
            "/autogrid360/listings/create",
            data={
                "title": "Coachbuilt Ford",
                "make": "Ford",
                "model": "__other__",
                "model_other": "One-Off Coachbuilt Special",
                "city": "Freeport",
            },
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.query.one()
        self.assertIsNone(listing.vehicle.model_id)
        self.assertEqual(listing.vehicle.model_text, "One-Off Coachbuilt Special")
        self.assertEqual(listing.vehicle.model, "One-Off Coachbuilt Special")
        self.assertEqual(VehicleModel.query.count(), model_count)


    def test_create_requires_text_for_other_unlisted_model(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data={
                "title": "Unnamed special",
                "make": "Ford",
                "model": "__other__",
                "model_other": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertIn("Enter the unlisted vehicle model.", response.get_data(as_text=True))


    def test_create_rejects_model_that_belongs_to_different_make(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            "/autogrid360/listings/create",
            data={
                "title": "Impossible combination",
                "make": "Honda",
                "model": "ford:mustang",
                "model_other": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Listing.query.count(), 0)
        self.assertEqual(Vehicle.query.count(), 0)
        self.assertIn(
            "The selected model does not belong to the selected make.",
            response.get_data(as_text=True),
        )


    def test_edit_preserves_custom_city_when_postal_code_changes(self):
        listing = self._create_listing()
        listing.city = "Custom Locality"
        destination = self._postal_location(
            country_code="US",
            postal_code="61101",
            locality="Rockford",
            region="Illinois",
            latitude=42.2711,
            longitude=-89.0940,
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data=self._listing_form_data(
                city="Custom Locality",
                postal_code="61101",
            ),
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.city, "Custom Locality")
        self.assertEqual(listing.postal_location_id, destination.id)


    def test_owner_can_open_populated_edit_form(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get(f"/autogrid360/listings/{listing.id}/edit")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Edit Listing", body)
        self.assertIn('value="Seller-owned Civic"', body)
        self.assertIn('value="honda"', body)
        self.assertIn('>Honda</option>', body)
        self.assertIn('value="honda:civic"', body)
        self.assertIn('data-make="honda"', body)
        self.assertIn('value="Save Changes"', body)


    def test_edit_populates_fallback_controls_for_older_year_and_door_count(self):
        listing = self._create_listing()
        listing.vehicle.year = 1998
        listing.vehicle.doors = 1
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get(f"/autogrid360/listings/{listing.id}/edit")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            '<option selected value="__other__">Older / Other...</option>',
            body,
        )
        self.assertIn('id="year_other"', body)
        self.assertIn('value="1998"', body)
        self.assertIn('id="doors_other"', body)
        self.assertIn('value="1"', body)


    def test_non_owner_cannot_edit_listing(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.other_user)

        get_response = client.get(f"/autogrid360/listings/{listing.id}/edit")
        post_response = client.post(
            f"/autogrid360/listings/{listing.id}/edit",
            data={
                "title": "Hijacked listing",
                "make": "Honda",
                "model": "honda:civic",
                "model_other": "",
            },
        )

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)


    def test_valid_edit_updates_listing_and_vehicle(self):
        listing = self._create_listing()
        listing_id = listing.id
        vehicle_id = listing.vehicle.id
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing_id}/edit",
            data={
                "title": "Updated Civic",
                "price": "8750.00",
                "description": "Updated description",
                "year": "2013",
                "make": "Honda",
                "model": "honda:civic",
                "model_other": "",
                "trim": "EX",
                "vehicle_type": "Sedan",
                "doors": "4",
                "exterior_color": "Blue",
                "mileage": "84500",
                "condition": "Used",
                "engine": "1.8L",
                "transmission": "Automatic",
                "drivetrain": "FWD",
                "mpg": "31",
                "fuel_type": "Gasoline",
                "vin": "2HGFB2F50DH000001",
                "stock_number": "OA-1001",
                "country_code": "US",
                "zone_code": "US-IL",
                "postal_code": "61032",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/autogrid360/listings/{listing_id}",
            response.headers["Location"],
        )
        self.assertEqual(Listing.query.count(), 1)
        self.assertEqual(Vehicle.query.count(), 1)

        db.session.expire_all()
        updated_listing = db.session.get(Listing, listing_id)
        updated_vehicle = db.session.get(Vehicle, vehicle_id)
        self.assertEqual(updated_listing.title, "Updated Civic")
        self.assertEqual(updated_listing.price, Decimal("8750.00"))
        self.assertEqual(updated_listing.description, "Updated description")
        self.assertEqual(updated_listing.status, STATUS_DRAFT)
        self.assertEqual(updated_listing.country_code, "US")
        self.assertEqual(updated_listing.zone_code, "US-IL")
        self.assertEqual(updated_vehicle.year, 2013)
        self.assertEqual(updated_vehicle.model, "Civic")
        self.assertIsNotNone(updated_vehicle.model_id)
        self.assertIsNone(updated_vehicle.model_text)
        self.assertEqual(updated_vehicle.trim, "EX")
        self.assertEqual(updated_vehicle.mileage, 84500)
        self.assertEqual(updated_vehicle.vin, "2HGFB2F50DH000001")


    def test_invalid_edit_leaves_listing_and_vehicle_unchanged(self):
        listing = self._create_listing()
        listing_id = listing.id
        vehicle_id = listing.vehicle.id
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing_id}/edit",
            data={
                "title": "Should not persist",
                "make": "Honda",
                "model": "",
                "mileage": "100000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("This field is required.", response.get_data(as_text=True))

        db.session.expire_all()
        unchanged_listing = db.session.get(Listing, listing_id)
        unchanged_vehicle = db.session.get(Vehicle, vehicle_id)
        self.assertEqual(unchanged_listing.title, "Seller-owned Civic")
        self.assertEqual(unchanged_listing.price, Decimal("9250.00"))
        self.assertEqual(unchanged_listing.description, "Original description")
        self.assertEqual(unchanged_listing.status, STATUS_DRAFT)
        self.assertEqual(unchanged_vehicle.model, "Civic")
        self.assertEqual(unchanged_vehicle.mileage, 82000)


    def test_listing_image_upload_requires_flask_aas_login(self):
        listing = self._create_listing()

        response = self.app.test_client().post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file()]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/host-login", response.headers["Location"])
        self.assertEqual(ListingImage.query.count(), 0)


    def test_seller_can_create_and_update_autogrid360_profile(self):
        client = self.app.test_client()
        self._login(client, self.seller)
        original_email = self.seller.email

        create_response = client.post(
            "/autogrid360/account/profile",
            data=self._seller_profile_form_data(
                company_name="  Jane\nMotors  ",
            ),
        )

        self.assertEqual(create_response.status_code, 302)
        profile = SellerProfile.query.filter_by(user_id=self.seller.id).one()
        self.assertEqual(profile.company_name, "Jane Motors")
        self.assertEqual(self.seller.email, original_email)

        update_response = client.post(
            "/autogrid360/account/profile",
            data=self._seller_profile_form_data(
                company_name="Updated Motors",
            ),
        )

        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(SellerProfile.query.filter_by(user_id=self.seller.id).count(), 1)
        db.session.refresh(profile)
        self.assertEqual(profile.company_name, "Updated Motors")


    def test_seller_profile_does_not_duplicate_account_contact_or_location_fields(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/account/profile")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for field_name in (
            "phone",
            "alternate_phone",
            "fax",
            "address",
            "city",
            "zone_code",
            "postal_code",
            "country_code",
        ):
            self.assertNotIn(f'name="{field_name}"', body)


    def test_seller_profile_audit_records_changed_field_names_not_values(self):
        client = self.app.test_client()
        self._login(client, self.seller)

        with patch(
            "app.plugins.autogrid360.routes.account.audit_activity_enabled",
            return_value=True,
        ), patch("app.plugins.autogrid360.routes.account.log_action") as log_action:
            response = client.post(
                "/autogrid360/account/profile",
                data=self._seller_profile_form_data(
                    company_name="Sensitive Seller Name",
                ),
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        extra_data = log_action.call_args.kwargs["extra_data"]
        self.assertIn("company_name", extra_data["changed_fields"])
        self.assertNotIn("Sensitive Seller Name", repr(extra_data))
        self.assertNotIn(self.seller.email, repr(extra_data))


    def test_public_seller_page_uses_enabled_account_location_and_public_inventory(self):
        self.seller.phone = "815-555-0100"
        self.seller.address = "123 Private Street"
        self.seller.city = "Profiletown"
        self.seller.zone_code = "US-IL"
        self.seller.postal_code = "62701"
        self.seller.country_code = "US"
        profile = SellerProfile(
            user_id=self.seller.id,
            display_name="Jane Seller",
            company_name="Jane Motors",
        )
        active = self._create_listing()
        active.status = STATUS_ACTIVE
        active.title = "Public seller inventory"
        sold = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Ford", model="Sold Car"),
            title="Sold seller inventory",
            status=STATUS_SOLD,
        )
        draft = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Ford", model="Draft Car"),
            title="Draft seller inventory",
            status=STATUS_DRAFT,
        )
        other = Listing(
            seller=self.other_user,
            vehicle=self._vehicle(make="Ford", model="Other Car"),
            title="Other seller inventory",
            status=STATUS_ACTIVE,
        )
        db.session.add_all([profile, sold, draft, other])
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Jane Motors", body)
        self.assertIn("Jane Seller", body)
        self.assertIn("Profiletown", body)
        self.assertIn("Illinois", body)
        self.assertIn("United States", body)
        self.assertIn(active.title, body)
        self.assertIn(sold.title, body)
        self.assertIn("Sold", body)
        self.assertNotIn(draft.title, body)
        self.assertNotIn(other.title, body)
        self.assertNotIn(self.seller.email, body)
        self.assertNotIn("123 Private Street", body)
        self.assertNotIn("815-555-0100", body)
        self.assertNotIn("62701", body)


    def test_public_seller_page_hides_account_location_when_location_is_disabled(self):
        self.seller.city = "Profiletown"
        self.seller.zone_code = "US-IL"
        self.seller.country_code = "US"
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        self._set_user_location_enabled(False)

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Profiletown", response.get_data(as_text=True))


    def test_create_listing_offers_profile_location_copy_when_enabled(self):
        self.seller.country_code = "US"
        self.seller.zone_code = "US-IL"
        self.seller.city = "Profiletown"
        self.seller.postal_code = "62701"
        db.session.commit()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/listings/create")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Use my profile location", body)
        self.assertIn('data-country-code="US"', body)
        self.assertIn('data-zone-code="US-IL"', body)
        self.assertIn('data-postal-code="62701"', body)
        self.assertIn('data-city="Profiletown"', body)


    def test_create_listing_hides_profile_location_copy_when_location_is_disabled(self):
        self.seller.country_code = "US"
        self.seller.zone_code = "US-IL"
        self.seller.city = "Profiletown"
        self.seller.postal_code = "62701"
        db.session.commit()
        self._set_user_location_enabled(False)
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.get("/autogrid360/listings/create")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Use my profile location", response.get_data(as_text=True))


    def test_public_seller_page_falls_back_to_username_when_profile_missing(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.seller.username, response.get_data(as_text=True))


    def test_public_seller_page_reuses_canonical_host_profile_image(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        self.seller.image = "0123456789abcdef0123456789abcdef.webp"
        db.session.commit()

        rendered_image = "data:image/webp;base64,ZmFrZS1hdmF0YXI="
        with patch(
            "app.plugins.autogrid360.routes.public.profile_image_data_uri",
            return_value=rendered_image,
        ) as profile_image_data_uri:
            response = self.app.test_client().get(
                f"/autogrid360/sellers/{self.seller.username}"
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        profile_image_data_uri.assert_called_once_with(self.seller.image)
        self.assertIn('class="autogrid360-seller-profile-image"', body)
        self.assertIn(rendered_image, body)
        self.assertIn(f"Profile image for {self.seller.username}", body)


    def test_public_seller_page_omits_missing_host_profile_image_cleanly(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        self.seller.image = "0123456789abcdef0123456789abcdef.webp"
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.routes.public.profile_image_data_uri",
            return_value=None,
        ):
            response = self.app.test_client().get(
                f"/autogrid360/sellers/{self.seller.username}"
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn("autogrid360-seller-profile-image", body)
        self.assertIn(self.seller.username, body)


    def test_public_seller_page_does_not_enumerate_host_only_users(self):
        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.other_user.username}"
        )

        self.assertEqual(response.status_code, 404)


    def test_public_profile_can_exist_without_active_inventory(self):
        db.session.add(
            SellerProfile(
                user_id=self.seller.id,
                display_name="Seller Without Inventory",
            )
        )
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Seller Without Inventory", body)
        self.assertIn("no public listings", body.lower())


    def test_public_seller_profile_autoescapes_profile_text(self):
        db.session.add(
            SellerProfile(
                user_id=self.seller.id,
                company_name="<script>alert(1)</script>",
            )
        )
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)


    def test_public_listing_detail_links_to_seller_profile_with_public_label(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.add(
            SellerProfile(
                user_id=self.seller.id,
                company_name="Linked Seller Motors",
            )
        )
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Linked Seller Motors", body)
        self.assertIn(
            f'/autogrid360/sellers/{self.seller.username}',
            body,
        )
        self.assertNotIn(self.seller.email, body)


    def test_public_seller_inventory_paginates_active_listings(self):
        self.app.config["AUTOGRID360_LISTINGS_PER_PAGE"] = 2
        for index in range(3):
            db.session.add(
                Listing(
                    seller=self.seller,
                    vehicle=self._vehicle(make="Honda", model=f"Model {index}"),
                    title=f"Seller page listing {index}",
                    status=STATUS_ACTIVE,
                )
            )
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/sellers/{self.seller.username}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Page 1 of 2", body)
        self.assertIn(
            f"/autogrid360/sellers/{self.seller.username}?page=2",
            body.replace("&amp;", "&"),
        )


    def test_non_owner_cannot_upload_listing_images(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [self._image_file()]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(ListingImage.query.count(), 0)


    def test_owner_uploads_multiple_images_with_primary_and_order(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={
                "images": [
                    self._image_file("front.jpg", (200, 0, 0)),
                    self._image_file("rear.jpg", (0, 0, 200)),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        images = ListingImage.query.order_by(ListingImage.position).all()
        self.assertEqual(len(images), 2)
        self.assertEqual([image.position for image in images], [0, 1])
        self.assertTrue(images[0].is_primary)
        self.assertFalse(images[1].is_primary)
        self.assertEqual(images[0].original_filename, "front.jpg")
        self.assertTrue(images[0].storage_key.startswith(f"{listing.id}/"))
        self.assertTrue(images[0].thumbnail_key.startswith(f"{listing.id}/"))
        self.assertFalse(images[0].storage_key.startswith("listings/"))
        image_root = listing_image_root()
        for image in images:
            display = image_root / image.storage_key
            thumbnail = image_root / image.thumbnail_key
            self.assertTrue(display.is_file())
            self.assertTrue(thumbnail.is_file())
            self.assertEqual(display.read_bytes()[:2], b"\xff\xd8")
            self.assertEqual(thumbnail.read_bytes()[:2], b"\xff\xd8")


    def test_invalid_image_upload_creates_no_records_or_files(self):
        listing = self._create_listing()
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={"images": [(BytesIO(b"not an image"), "fake.jpg")]},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListingImage.query.count(), 0)
        image_root = listing_image_root()
        self.assertFalse(any(image_root.rglob("*.jpg")) if image_root.exists() else False)


    def test_listing_image_limit_is_enforced_before_storage(self):
        listing = self._create_listing()
        self.app.config["AUTOGRID360_MAX_LISTING_IMAGES"] = 1
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images",
            data={
                "images": [
                    self._image_file("one.jpg"),
                    self._image_file("two.jpg"),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListingImage.query.count(), 0)


    def test_owner_can_change_primary_listing_image(self):
        listing = self._create_listing()
        first = self._create_image(listing, position=0, is_primary=True, token="first")
        second = self._create_image(listing, position=1, is_primary=False, token="second")
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images/{second.id}/primary"
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith(
                f"/autogrid360/listings/{listing.id}/edit"
            )
        )
        db.session.refresh(first)
        db.session.refresh(second)
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)


    def test_owner_can_reorder_listing_images(self):
        listing = self._create_listing()
        first = self._create_image(listing, position=0, is_primary=True, token="first")
        second = self._create_image(listing, position=1, token="second")
        third = self._create_image(listing, position=2, token="third")
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images/{third.id}/move",
            data={"direction": "up"},
        )

        self.assertEqual(response.status_code, 302)
        ordered = ListingImage.query.order_by(ListingImage.position).all()
        self.assertEqual([image.id for image in ordered], [first.id, third.id, second.id])
        self.assertEqual([image.position for image in ordered], [0, 1, 2])


    def test_non_owner_cannot_delete_listing_image(self):
        listing = self._create_listing()
        image = self._create_image(listing, is_primary=True)
        client = self.app.test_client()
        self._login(client, self.other_user)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images/{image.id}/delete"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(db.session.get(ListingImage, image.id))


    def test_deleting_primary_image_promotes_next_and_removes_files(self):
        listing = self._create_listing()
        first = self._create_image(listing, position=0, is_primary=True, token="first")
        second = self._create_image(listing, position=1, token="second")
        third = self._create_image(listing, position=2, token="third")
        first_paths = [
            listing_image_root() / first.storage_key,
            listing_image_root() / first.thumbnail_key,
        ]
        client = self.app.test_client()
        self._login(client, self.seller)

        response = client.post(
            f"/autogrid360/listings/{listing.id}/images/{first.id}/delete"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(ListingImage, first.id))
        db.session.refresh(second)
        db.session.refresh(third)
        self.assertTrue(second.is_primary)
        self.assertEqual([second.position, third.position], [0, 1])
        self.assertTrue(all(not path.exists() for path in first_paths))


    def test_active_listing_image_change_returns_listing_to_pending(self):
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
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)


    def test_active_image_rereview_notifies_configured_site_administrator(self):
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
                f"/autogrid360/listings/{listing.id}/images",
                data={"images": [self._image_file()]},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(listing)
        self.assertEqual(listing.status, STATUS_PENDING)
        send_email.assert_called_once()
        self.assertIn(
            "listing image change requires re-review",
            send_email.call_args.args[2],
        )


    def test_private_listing_image_is_visible_to_owner_or_admin_only(self):
        listing = self._create_listing()
        image = self._create_image(listing, is_primary=True)
        image_root = listing_image_root()
        display_path = image_root / image.storage_key
        display_path.write_bytes(b"\xff\xd8private-image")

        owner_client = self.app.test_client()
        self._login(owner_client, self.seller)
        owner_response = owner_client.get(
            f"/autogrid360/listings/{listing.id}/images/{image.id}/display"
        )

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        admin_response = admin_client.get(
            f"/autogrid360/listings/{listing.id}/images/{image.id}/display"
        )

        moderator_client = self.app.test_client()
        self._login(moderator_client, self.moderator)
        moderator_response = moderator_client.get(
            f"/autogrid360/listings/{listing.id}/images/{image.id}/display"
        )

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(moderator_response.status_code, 404)


    def test_listing_edit_owns_image_management_and_detail_is_preview_only(self):
        listing = self._create_listing()
        self._create_image(listing, position=0, is_primary=True, token="primary")
        self._create_image(listing, position=1, token="secondary")
        client = self.app.test_client()
        self._login(client, self.seller)

        detail = client.get(f"/autogrid360/listings/{listing.id}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.get_data(as_text=True)
        self.assertIn('class="autogrid360-image-carousel"', detail_body)
        self.assertNotIn("Upload Images", detail_body)
        self.assertNotIn("Delete image", detail_body)
        self.assertNotIn("Make primary", detail_body)

        edit = client.get(f"/autogrid360/listings/{listing.id}/edit")
        self.assertEqual(edit.status_code, 200)
        edit_body = edit.get_data(as_text=True)
        self.assertIn("Listing Images", edit_body)
        self.assertIn("Upload Images", edit_body)
        self.assertIn(">Delete</button>", edit_body)
        self.assertIn("Set primary", edit_body)
        self.assertIn("Manage Listing", edit_body)
        self.assertNotIn(">View Listing</a>", edit_body)
        self.assertIn(
            f'href="/autogrid360/listings/{listing.id}"',
            edit_body,
        )
        self.assertNotIn("View Public Listing", edit_body)
        self.assertIn('<fieldset class="autogrid360-image-management">', edit_body)

        listing.status = STATUS_ACTIVE
        db.session.commit()
        public_edit = client.get(f"/autogrid360/listings/{listing.id}/edit")
        self.assertEqual(public_edit.status_code, 200)
        public_edit_body = public_edit.get_data(as_text=True)
        self.assertIn("Manage Listing", public_edit_body)
        self.assertIn("View Public Listing", public_edit_body)
        self.assertIn(
            f'href="/autogrid360/listings/{listing.id}/2012-honda-civic"',
            public_edit_body,
        )
