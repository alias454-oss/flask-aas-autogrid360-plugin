# app/plugins/autogrid360/tests/test_listing_public.py
from datetime import datetime, timezone
from html import unescape
import json
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.core.extensions import db
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    CATEGORY_DRIVETRAIN,
    CATEGORY_VEHICLE_TYPE,
    AutoGrid360Settings,
    Listing,
)
from app.plugins.autogrid360.tests.listing_support import AutoGrid360ListingRouteTestCase


class AutoGrid360PublicListingRouteTests(AutoGrid360ListingRouteTestCase):
    def test_public_search_requires_no_login_and_shows_only_active_listings(self):
        active = self._create_listing()
        active.status = STATUS_ACTIVE
        active.title = "Public Civic"
        draft = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Ford", model="Focus"),
            title="Private draft Focus",
            status=STATUS_DRAFT,
        )
        pending = Listing(
            seller=self.seller,
            vehicle=self._vehicle(make="Toyota", model="Camry"),
            title="Private pending Camry",
            status=STATUS_PENDING,
        )
        db.session.add_all([draft, pending])
        db.session.commit()

        response = self.app.test_client().get("/autogrid360/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Public Civic", body)
        self.assertNotIn("Private draft Focus", body)
        self.assertNotIn("Private pending Camry", body)
        self.assertIn('id="inventory-results-controls"', body)
        self.assertIn(
            f"/autogrid360/listings/{active.id}/2012-honda-civic",
            body,
        )


    def test_public_inventory_uses_primary_fallback_and_no_photo_states(self):
        self.app.config["AUTOGRID360_LISTINGS_PER_PAGE"] = 10
        primary_listing = self._create_active_inventory_listing(
            title="Primary image listing", year=2020, make="Honda", model="Civic"
        )
        primary = self._create_image(
            primary_listing, position=1, is_primary=True, token="primary"
        )
        self._create_image(
            primary_listing, position=0, is_primary=False, token="first"
        )
        fallback_listing = self._create_active_inventory_listing(
            title="Fallback image listing", year=2020, make="Ford", model="Focus"
        )
        fallback = self._create_image(
            fallback_listing, position=0, is_primary=False, token="fallback"
        )
        self._create_active_inventory_listing(
            title="No image listing", year=2020, make="Toyota", model="Camry"
        )
        db.session.commit()

        response = self.app.test_client().get("/autogrid360/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            f"/autogrid360/listings/{primary_listing.id}/images/{primary.id}/thumb",
            body,
        )
        self.assertIn(
            f"/autogrid360/listings/{fallback_listing.id}/images/{fallback.id}/thumb",
            body,
        )
        self.assertIn('class="autogrid360-inventory-thumb"', body)
        self.assertIn('class="autogrid360-inventory-thumb-placeholder"', body)
        self.assertIn("No photo", body)


    def test_public_search_filters_us_inventory_by_postal_radius(self):
        origin = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="IL",
            latitude=42.2967,
            longitude=-89.6212,
        )
        near = self._postal_location(
            country_code="US",
            postal_code="61101",
            locality="Rockford",
            region="IL",
            latitude=42.2711,
            longitude=-89.0940,
        )
        far = self._postal_location(
            country_code="US",
            postal_code="60601",
            locality="Chicago",
            region="IL",
            latitude=41.8864,
            longitude=-87.6186,
        )
        local_listing = self._create_active_inventory_listing(
            title="Freeport truck", year=2020, make="Ford", model="F-150"
        )
        near_listing = self._create_active_inventory_listing(
            title="Rockford Civic", year=2021, make="Honda", model="Civic"
        )
        far_listing = self._create_active_inventory_listing(
            title="Chicago Corolla", year=2022, make="Toyota", model="Corolla"
        )
        local_listing.postal_location = origin
        near_listing.postal_location = near
        far_listing.postal_location = far
        db.session.commit()

        response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=61032&radius=50"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(local_listing.title, body)
        self.assertIn(near_listing.title, body)
        self.assertNotIn(far_listing.title, body)
        self.assertIn("miles away", body)
        self.assertIn(
            '/autogrid360/listings/search?postal_country=US&amp;postal_code=61032&amp;radius=50',
            body,
        )


    def test_public_search_accepts_full_uk_postcode_against_outward_district(self):
        b15 = self._postal_location(
            country_code="GB",
            postal_code="B15",
            locality="Birmingham",
            region="England",
            latitude=52.4628,
            longitude=-1.92701,
        )
        b1 = self._postal_location(
            country_code="GB",
            postal_code="B1",
            locality="Birmingham",
            region="England",
            latitude=52.4792,
            longitude=-1.91038,
        )
        m1 = self._postal_location(
            country_code="GB",
            postal_code="M1",
            locality="Manchester",
            region="England",
            latitude=53.4773,
            longitude=-2.2374,
        )
        local_listing = self._create_active_inventory_listing(
            title="Birmingham Mini", year=2020, make="Mini Cooper", model="Cooper"
        )
        nearby_listing = self._create_active_inventory_listing(
            title="Central Birmingham Mini", year=2021, make="Mini Cooper", model="Cooper"
        )
        far_listing = self._create_active_inventory_listing(
            title="Manchester Mini", year=2022, make="Mini Cooper", model="Cooper"
        )
        local_listing.postal_location = b15
        nearby_listing.postal_location = b1
        far_listing.postal_location = m1
        db.session.commit()

        response = self.app.test_client().get(
            "/autogrid360/?postal_country=GB&postal_code=B15+2TT&radius=25"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(local_listing.title, body)
        self.assertIn(nearby_listing.title, body)
        self.assertNotIn(far_listing.title, body)
        self.assertIn(
            '/autogrid360/listings/search?postal_country=GB&amp;postal_code=B15&amp;radius=25',
            body,
        )


    def test_public_search_unknown_postal_code_returns_no_matches_with_error(self):
        listing = self._create_active_inventory_listing(
            title="Existing inventory", year=2020, make="Honda", model="Civic"
        )

        response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=99999&radius=50"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(listing.title, body)
        self.assertIn("not available in the installed AutoGrid360 geography data", body)


    def test_public_radius_search_uses_site_unit_and_allows_buyer_override(self):
        origin = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="IL",
            latitude=42.0,
            longitude=-89.6,
        )
        outside_fifty_km = self._postal_location(
            country_code="US",
            postal_code="61101",
            locality="Rockford",
            region="IL",
            latitude=42.54,
            longitude=-89.6,
        )
        listing = self._create_active_inventory_listing(
            title="Unit-sensitive listing",
            year=2020,
            make="Honda",
            model="Civic",
        )
        listing.postal_location = outside_fifty_km
        db.session.add(AutoGrid360Settings(id=1, default_distance_unit="kilometers"))
        db.session.commit()

        kilometers_response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=61032&radius=50"
        )
        miles_response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=61032&radius=50&distance_unit=miles"
        )

        self.assertNotIn(
            listing.title,
            kilometers_response.get_data(as_text=True),
        )
        miles_body = miles_response.get_data(as_text=True)
        self.assertIn(listing.title, miles_body)
        self.assertIn("miles away", miles_body)


    def test_public_radius_search_renders_kilometer_distance_label(self):
        origin = self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="IL",
            latitude=42.0,
            longitude=-89.6,
        )
        nearby = self._postal_location(
            country_code="US",
            postal_code="61013",
            locality="Cedarville",
            region="IL",
            latitude=42.1,
            longitude=-89.6,
        )
        listing = self._create_active_inventory_listing(
            title="Kilometer-distance listing",
            year=2020,
            make="Honda",
            model="Civic",
        )
        listing.postal_location = nearby
        db.session.add(AutoGrid360Settings(id=1, default_distance_unit="kilometers"))
        db.session.commit()

        response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=61032&radius=50"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(listing.title, body)
        self.assertIn("km away", body)
        self.assertNotIn("miles away", body)


    def test_public_radius_search_can_cross_loaded_country_boundaries(self):
        self._postal_location(
            country_code="US",
            postal_code="61032",
            locality="Freeport",
            region="IL",
            latitude=42.0,
            longitude=-89.6,
        )
        nearby_foreign = self._postal_location(
            country_code="CA",
            postal_code="A1A1A1",
            locality="Cross Border",
            region="ON",
            latitude=42.2,
            longitude=-89.6,
        )
        listing = self._create_active_inventory_listing(
            title="Cross-border inventory",
            year=2021,
            make="Toyota",
            model="Corolla",
        )
        listing.postal_location = nearby_foreign
        db.session.commit()

        response = self.app.test_client().get(
            "/autogrid360/?postal_country=US&postal_code=61032&radius=50"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(listing.title, body)
        self.assertIn("miles", body)


    def test_public_search_filters_make_and_model_case_insensitively(self):
        civic = self._create_active_inventory_listing(
            title="Matching Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        accord = self._create_active_inventory_listing(
            title="Other Honda",
            year=2021,
            make="Honda",
            model="Accord",
        )
        toyota = self._create_active_inventory_listing(
            title="Other make",
            year=2022,
            make="Toyota",
            model="Corolla",
        )

        response = self.app.test_client().get(
            "/autogrid360/?make=hOnDa&model=cIv"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(civic.title, body)
        self.assertNotIn(accord.title, body)
        self.assertNotIn(toyota.title, body)


    def test_public_search_filters_structured_model_by_make_scoped_key(self):
        civic = self._create_active_inventory_listing(
            title="Structured Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        accord = self._create_active_inventory_listing(
            title="Structured Accord",
            year=2020,
            make="Honda",
            model="Accord",
        )
        mustang = self._create_active_inventory_listing(
            title="Structured Mustang",
            year=2020,
            make="Ford",
            model="Mustang",
        )

        response = self.app.test_client().get(
            "/autogrid360/?make=honda&model=honda:civic"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(civic.title, body)
        self.assertNotIn(accord.title, body)
        self.assertNotIn(mustang.title, body)
        self.assertIn(
            '/autogrid360/listings/search?make=honda&amp;model=honda:civic',
            body,
        )
        self.assertIn("Honda Civic Inventory", body)


    def test_public_search_filters_inclusive_year_range(self):
        old = self._create_active_inventory_listing(
            title="Older vehicle",
            year=2015,
            make="Honda",
            model="Civic",
        )
        middle = self._create_active_inventory_listing(
            title="Middle vehicle",
            year=2020,
            make="Honda",
            model="Accord",
        )
        new = self._create_active_inventory_listing(
            title="Newer vehicle",
            year=2024,
            make="Honda",
            model="Pilot",
        )

        response = self.app.test_client().get(
            "/autogrid360/?min_year=2020&max_year=2020"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn(old.title, body)
        self.assertIn(middle.title, body)
        self.assertNotIn(new.title, body)


    def test_public_search_filters_inclusive_price_range(self):
        low = self._create_active_inventory_listing(
            title="Low price",
            year=2020,
            make="Honda",
            model="Fit",
            price="5000.00",
        )
        middle = self._create_active_inventory_listing(
            title="Middle price",
            year=2020,
            make="Honda",
            model="Civic",
            price="10000.00",
        )
        high = self._create_active_inventory_listing(
            title="High price",
            year=2020,
            make="Honda",
            model="Pilot",
            price="20000.00",
        )
        no_price = self._create_active_inventory_listing(
            title="Call for price",
            year=2020,
            make="Honda",
            model="Prelude",
        )

        response = self.app.test_client().get(
            "/autogrid360/?min_price=10000&max_price=10000"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn(low.title, body)
        self.assertIn(middle.title, body)
        self.assertNotIn(high.title, body)
        self.assertNotIn(no_price.title, body)


    def test_public_search_filters_vehicle_type_and_zone(self):
        match = self._create_active_inventory_listing(
            title="Illinois SUV",
            year=2020,
            make="Honda",
            model="Pilot",
            vehicle_type="SUV",
            zone_code="US-IL",
        )
        wrong_zone = self._create_active_inventory_listing(
            title="Wisconsin SUV",
            year=2020,
            make="Honda",
            model="CR-V",
            vehicle_type="SUV",
            zone_code="US-WI",
        )
        wrong_type = self._create_active_inventory_listing(
            title="Illinois sedan",
            year=2020,
            make="Honda",
            model="Accord",
            vehicle_type="Sedan",
            zone_code="US-IL",
        )

        response = self.app.test_client().get(
            "/autogrid360/?vehicle_type=suv&country_code=US&zone_code=US-IL"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(match.title, body)
        self.assertNotIn(wrong_zone.title, body)
        self.assertNotIn(wrong_type.title, body)


    def test_public_search_filters_drivetrain_by_stable_reference_key(self):
        match = self._create_active_inventory_listing(
            title="Front wheel drive Civic",
            year=2020,
            make="Honda",
            model="Civic",
            drivetrain="FWD",
        )
        other = self._create_active_inventory_listing(
            title="All wheel drive Pilot",
            year=2020,
            make="Honda",
            model="Pilot",
            drivetrain="AWD",
        )

        response = self.app.test_client().get(
            "/autogrid360/?drivetrain=fwd"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(match.title, body)
        self.assertNotIn(other.title, body)
        self.assertIn('/autogrid360/listings/search?drivetrain=fwd', body)


    def test_public_search_filters_all_selected_features(self):
        match = self._create_active_inventory_listing(
            title="Comfort Civic",
            year=2020,
            make="Honda",
            model="Civic",
            features=["Air Conditioning", "Cruise Control"],
        )
        partial = self._create_active_inventory_listing(
            title="Air only Fit",
            year=2020,
            make="Honda",
            model="Fit",
            features=["Air Conditioning"],
        )
        other = self._create_active_inventory_listing(
            title="Heated Pilot",
            year=2020,
            make="Honda",
            model="Pilot",
            features=["Heated Seats"],
        )

        response = self.app.test_client().get(
            "/autogrid360/?feature=Air+Conditioning"
            "&feature=CRUISE_CONTROL"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(match.title, body)
        self.assertNotIn(partial.title, body)
        self.assertNotIn(other.title, body)
        self.assertIn(
            '/autogrid360/listings/search?feature=air-conditioning&amp;feature=cruise-control',
            body,
        )


    def test_public_search_filters_text_facets_case_insensitively(self):
        condition_match = self._create_active_inventory_listing(
            title="Used Civic",
            year=2020,
            make="Honda",
            model="Civic",
            condition="Certified Used",
        )
        condition_other = self._create_active_inventory_listing(
            title="New Accord",
            year=2020,
            make="Honda",
            model="Accord",
            condition="New",
        )
        transmission_match = self._create_active_inventory_listing(
            title="Automatic Pilot",
            year=2020,
            make="Honda",
            model="Pilot",
            transmission="Automatic",
        )
        transmission_other = self._create_active_inventory_listing(
            title="Manual Fit",
            year=2020,
            make="Honda",
            model="Fit",
            transmission="Manual",
        )

        cases = (
            ("condition=USED", condition_match.title, condition_other.title),
            ("transmission=AUTO", transmission_match.title, transmission_other.title),
        )
        client = self.app.test_client()
        for query, included, excluded in cases:
            with self.subTest(query=query):
                response = client.get(f"/autogrid360/?{query}")
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn(included, body)
                self.assertNotIn(excluded, body)
                self.assertIn(f"/autogrid360/listings/search?{query}", body)


    def test_public_search_filters_seller_by_public_username(self):
        own = self._create_active_inventory_listing(
            title="Primary seller Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        other = self._create_active_inventory_listing(
            title="Other seller Accord",
            year=2020,
            make="Honda",
            model="Accord",
            seller=self.other_user,
        )

        response = self.app.test_client().get(
            f"/autogrid360/?seller={self.other_user.username.upper()}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn(own.title, body)
        self.assertIn(other.title, body)
        self.assertIn(
            f'/autogrid360/listings/search?seller={self.other_user.username}',
            body,
        )
        self.assertIn(
            f'<link rel="canonical" href="http://localhost/autogrid360/?seller={self.other_user.username}">',
            body,
        )


    def test_public_search_sort_contracts(self):
        self.app.config["AUTOGRID360_LISTINGS_PER_PAGE"] = 50
        low = self._create_active_inventory_listing(
            title="Five thousand", year=2020, make="Honda", model="Fit", price="5000.00"
        )
        high = self._create_active_inventory_listing(
            title="Twenty thousand", year=2020, make="Honda", model="Pilot", price="20000.00"
        )
        no_price = self._create_active_inventory_listing(
            title="Call for price", year=2020, make="Honda", model="Prelude"
        )
        alpha = self._create_active_inventory_listing(
            title="Accord listing", year=2020, make="Honda", model="Accord"
        )
        middle = self._create_active_inventory_listing(
            title="Civic listing", year=2020, make="Honda", model="Civic"
        )
        omega = self._create_active_inventory_listing(
            title="Unlisted model listing", year=2020, make="Honda", model="Zeta Custom"
        )
        ford_z = self._create_active_inventory_listing(
            title="Ford Z model", year=2020, make="Ford", model="Zeta Custom"
        )
        ford_a = self._create_active_inventory_listing(
            title="Ford A model", year=2020, make="Ford", model="Aspire"
        )
        honda = self._create_active_inventory_listing(
            title="Honda model", year=2020, make="Honda", model="Civic"
        )
        old = self._create_active_inventory_listing(
            title="Old model year", year=2010, make="Honda", model="Civic"
        )
        new = self._create_active_inventory_listing(
            title="New model year", year=2024, make="Honda", model="Civic"
        )
        no_year = self._create_active_inventory_listing(
            title="Unknown model year", year=None, make="Honda", model="Civic"
        )

        client = self.app.test_client()
        bodies = {
            sort: client.get(f"/autogrid360/?sort={sort}").get_data(as_text=True)
            for sort in (
                "price_asc", "price_desc", "model_asc", "model_desc",
                "make_asc", "make_desc", "year_asc", "year_desc",
            )
        }

        self.assertLess(bodies["price_asc"].index(low.title), bodies["price_asc"].index(high.title))
        self.assertLess(bodies["price_asc"].index(high.title), bodies["price_asc"].index(no_price.title))
        self.assertLess(bodies["price_desc"].index(high.title), bodies["price_desc"].index(low.title))
        self.assertLess(bodies["price_desc"].index(low.title), bodies["price_desc"].index(no_price.title))
        self.assertLess(bodies["model_asc"].index(alpha.title), bodies["model_asc"].index(middle.title))
        self.assertLess(bodies["model_asc"].index(middle.title), bodies["model_asc"].index(omega.title))
        self.assertLess(bodies["model_desc"].index(omega.title), bodies["model_desc"].index(middle.title))
        self.assertLess(bodies["model_desc"].index(middle.title), bodies["model_desc"].index(alpha.title))
        self.assertLess(bodies["make_asc"].index(ford_a.title), bodies["make_asc"].index(ford_z.title))
        self.assertLess(bodies["make_asc"].index(ford_z.title), bodies["make_asc"].index(honda.title))
        self.assertLess(bodies["make_desc"].index(honda.title), bodies["make_desc"].index(ford_a.title))
        self.assertLess(bodies["make_desc"].index(ford_a.title), bodies["make_desc"].index(ford_z.title))
        self.assertLess(bodies["year_asc"].index(old.title), bodies["year_asc"].index(new.title))
        self.assertLess(bodies["year_asc"].index(new.title), bodies["year_asc"].index(no_year.title))
        self.assertLess(bodies["year_desc"].index(new.title), bodies["year_desc"].index(old.title))
        self.assertLess(bodies["year_desc"].index(old.title), bodies["year_desc"].index(no_year.title))


    def test_public_search_accepts_supported_page_size_and_ignores_invalid_size(self):
        for index in range(11):
            self._create_active_inventory_listing(
                title=f"Page size result {index}",
                year=2020,
                make="Honda",
                model="Civic",
            )

        client = self.app.test_client()
        expanded = client.get("/autogrid360/?per_page=10")
        expanded_body = expanded.get_data(as_text=True)
        next_match = re.search(r'href="([^"]+)">Next</a>', expanded_body)
        fallback = client.get("/autogrid360/?per_page=999")
        fallback_body = fallback.get_data(as_text=True)

        self.assertEqual(expanded.status_code, 200)
        self.assertEqual(expanded_body.count("Page size result"), 10)
        self.assertIn('aria-current="true">10</strong>', expanded_body)
        self.assertIsNotNone(next_match)
        next_query = parse_qs(urlparse(unescape(next_match.group(1))).query)
        self.assertEqual(next_query["per_page"], ["10"])
        self.assertEqual(next_query["page"], ["2"])
        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback_body.count("Page size result"), 2)
        self.assertIn('aria-current="true">Default (2)</strong>', fallback_body)


    def test_public_search_pagination_preserves_filters_and_sort(self):
        hondas = []
        for index, price in enumerate(("5000", "10000", "15000")):
            hondas.append(
                self._create_active_inventory_listing(
                    title=f"Honda result {index}",
                    year=2020 + index,
                    make="Honda",
                    model=f"Model {index}",
                    price=price,
                    drivetrain="FWD",
                    features=["Air Conditioning", "Cruise Control"],
                    condition="Used",
                    transmission="Automatic",
                )
            )
        ford = self._create_active_inventory_listing(
            title="Filtered Ford",
            year=2022,
            make="Ford",
            model="Focus",
            price="7500",
            drivetrain="AWD",
            features=["Air Conditioning"],
        )

        client = self.app.test_client()
        first = client.get(
            "/autogrid360/?make=Honda&drivetrain=FWD"
            "&feature=Air+Conditioning&feature=Cruise+Control"
            "&condition=Used&transmission=Automatic&seller=listing-seller"
            "&sort=model_asc"
        )
        first_body = first.get_data(as_text=True)
        match = re.search(r'href="([^"]+)">Next</a>', first_body)

        self.assertIsNotNone(match)
        next_url = unescape(match.group(1))
        query = parse_qs(urlparse(next_url).query)
        self.assertEqual(query["make"], ["honda"])
        self.assertEqual(query["drivetrain"], ["fwd"])
        self.assertEqual(
            query["feature"],
            ["air-conditioning", "cruise-control"],
        )
        self.assertEqual(query["condition"], ["Used"])
        self.assertEqual(query["transmission"], ["Automatic"])
        self.assertEqual(query["seller"], ["listing-seller"])
        self.assertEqual(query["sort"], ["model_asc"])
        self.assertEqual(query["page"], ["2"])

        second_body = client.get(next_url).get_data(as_text=True)
        self.assertIn(hondas[2].title, second_body)
        self.assertNotIn(ford.title, second_body)


    def test_public_search_sort_controls_preserve_active_selection(self):
        listing = self._create_active_inventory_listing(
            title="Sortable filtered Civic",
            year=2020,
            make="Honda",
            model="Civic",
            price="10000.00",
        )

        response = self.app.test_client().get(
            "/autogrid360/",
            query_string={
                "make": "honda",
                "sort": "make_asc",
                "per_page": "10",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(listing.title, body)
        self.assertIn('id="inventory-results-controls"', body)
        self.assertNotIn('id="sort" name="sort"', body)
        self.assertIn('aria-sort="ascending"', body)
        self.assertIn('Make ↑</a>', body)
        self.assertIn('aria-current="true">10</strong>', body)
        self.assertIn('>Newest</a>', body)


    def test_empty_public_inventory_does_not_render_sort_controls(self):
        body = self.app.test_client().get("/autogrid360/").get_data(as_text=True)

        self.assertIn("Vehicle Inventory", body)
        self.assertIn("Advanced Search", body)
        self.assertIn("No public listings are available right now.", body)
        self.assertNotIn('id="inventory-results-controls"', body)
        self.assertNotIn("Listings per page:", body)


    def test_public_inventory_navigation_tracks_authenticated_role(self):
        anonymous = self.app.test_client().get("/autogrid360/").get_data(as_text=True)
        self.assertNotIn("My Listings", anonymous)
        self.assertNotIn("AutoGrid360 Admin", anonymous)

        seller_client = self.app.test_client()
        self._login(seller_client, self.seller)
        seller_body = seller_client.get("/autogrid360/").get_data(as_text=True)
        self.assertIn("My Listings", seller_body)
        self.assertIn("Create Listing", seller_body)
        self.assertNotIn("AutoGrid360 Admin", seller_body)

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        admin_body = admin_client.get("/autogrid360/").get_data(as_text=True)
        for label in (
            "Dashboard", "Inventory", "Pending Review", "Sellers",
            "Create Seller Listing", "Reference Data", "Backup / Restore", "Settings",
        ):
            with self.subTest(label=label):
                self.assertIn(label, admin_body)


    def test_advanced_search_is_query_builder_without_result_list(self):
        listing = self._create_active_inventory_listing(
            title="Builder-only Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )

        body = self.app.test_client().get(
            "/autogrid360/listings/search"
        ).get_data(as_text=True)

        self.assertIn("Advanced Search", body)
        self.assertIn('action="/autogrid360/listings/search"', body)
        self.assertIn('name="apply" value="1"', body)
        self.assertIn("Search Inventory", body)
        self.assertNotIn('id="inventory-results-controls"', body)
        self.assertNotIn(listing.title, body)


    def test_advanced_search_facets_only_offer_active_inventory_values(self):
        self._create_active_inventory_listing(
            title="Available F-150",
            year=2020,
            make="Ford",
            model="F-150",
            vehicle_type="Pickup",
            drivetrain="RWD",
            features=["Air Conditioning"],
            condition="Used",
            transmission="Automatic",
            zone_code="US-IL",
        )

        unavailable = Listing(
            seller=self.other_user,
            vehicle=self._vehicle(
                year=2018,
                make="Honda",
                model="Civic",
                vehicle_type="SUV",
                drivetrain="AWD",
                features=["Heated Seats"],
                condition="New",
                transmission="Manual",
            ),
            title="Draft Civic",
            status=STATUS_DRAFT,
            country_code="CA",
            zone_code="CA-ON",
        )
        db.session.add(unavailable)
        db.session.commit()

        body = self.app.test_client().get(
            "/autogrid360/listings/search"
        ).get_data(as_text=True)

        self.assertIn('<option value="ford">Ford</option>', body)
        self.assertNotIn('<option value="honda">Honda</option>', body)
        self.assertIn('value="ford:f-150"', body)
        self.assertNotIn('value="honda:civic"', body)
        self.assertIn('<option value="pickup">Pickup</option>', body)
        self.assertNotIn('<option value="suv">SUV</option>', body)
        self.assertIn('<option value="rwd">Rear Wheel Drive</option>', body)
        self.assertNotIn('<option value="awd">All Wheel Drive</option>', body)
        self.assertIn('<option value="Used">Used</option>', body)
        self.assertNotIn('<option value="New">New</option>', body)
        self.assertIn('<option value="Automatic">Automatic</option>', body)
        self.assertNotIn('<option value="Manual">Manual</option>', body)
        self.assertIn('value="air-conditioning"', body)
        self.assertNotIn('value="heated-seats"', body)
        self.assertIn('<option value="listing-seller">listing-seller</option>', body)
        self.assertNotIn('<option value="other-seller">other-seller</option>', body)
        self.assertIn('<option value="US">United States</option>', body)
        self.assertNotIn('<option value="CA">Canada</option>', body)
        self.assertIn('value="US-IL"', body)
        self.assertNotIn('value="CA-ON"', body)


    def test_advanced_search_year_choices_cover_contiguous_active_inventory_range(self):
        self._create_active_inventory_listing(
            title="2020 F-150",
            year=2020,
            make="Ford",
            model="F-150",
        )
        unavailable = Listing(
            seller=self.seller,
            vehicle=self._vehicle(
                year=2015,
                make="Honda",
                model="Civic",
            ),
            title="Old draft Civic",
            status=STATUS_DRAFT,
        )
        db.session.add(unavailable)
        db.session.commit()

        body = self.app.test_client().get(
            "/autogrid360/listings/search"
        ).get_data(as_text=True)

        latest = datetime.now(timezone.utc).year
        self.assertIn('<label for="min_year">Start year</label>', body)
        self.assertIn('<label for="max_year">End year</label>', body)
        self.assertIn('<option value="">Through newest</option>', body)
        for year in range(2020, latest + 1):
            with self.subTest(year=year):
                self.assertGreaterEqual(body.count(f'<option value="{year}">'), 2)
        self.assertNotIn('<option value="2019">2019</option>', body)
        self.assertNotIn('<option value="2015">2015</option>', body)


    def test_advanced_search_submission_redirects_to_canonical_inventory(self):
        response = self.app.test_client().get(
            "/autogrid360/listings/search",
            query_string={
                "apply": "1",
                "make": "honda",
                "model": "honda:civic",
                "min_year": "2020",
                "max_year": "2020",
            },
        )

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(target.path, "/autogrid360/")
        self.assertEqual(
            parse_qs(target.query),
            {
                "make": ["honda"],
                "model": ["honda:civic"],
                "min_year": ["2020"],
                "max_year": ["2020"],
            },
        )


    def test_advanced_search_uses_compact_fancy_inventory_url_when_enabled(self):
        self._set_fancy_urls(True)

        response = self.app.test_client().get(
            "/autogrid360/listings/search",
            query_string={
                "apply": "1",
                "make": "honda",
                "model": "honda:civic",
                "min_year": "2020",
                "max_year": "2020",
                "country_code": "US",
                "zone_code": "US-IL",
                "drivetrain": "fwd",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            urlparse(response.headers["Location"]).path,
            "/autogrid360/2020/honda/civic/illinois/fwd",
        )


    def test_advanced_search_encodes_all_semantic_filters_in_fancy_path(self):
        self._set_fancy_urls(True)

        response = self.app.test_client().get(
            "/autogrid360/listings/search",
            query_string=[
                ("apply", "1"),
                ("min_year", "2018"),
                ("max_year", "2024"),
                ("make", "honda"),
                ("model", "honda:civic"),
                ("country_code", "US"),
                ("zone_code", "US-IL"),
                ("drivetrain", "fwd"),
                ("vehicle_type", "sedan"),
                ("condition", "Used"),
                ("transmission", "Automatic"),
                ("seller", "listing-seller"),
                ("max_price", "30000"),
                ("postal_country", "US"),
                ("postal_code", "61032"),
                ("radius", "50"),
                ("distance_unit", "miles"),
                ("feature", "air-conditioning"),
                ("feature", "cruise-control"),
            ],
        )

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(
            target.path,
            "/autogrid360/years/2018-2024/honda/civic/illinois/fwd"
            "/type/sedan/condition/Used/transmission/Automatic"
            "/seller/listing-seller/price/under-30000"
            "/near/us/61032/50/unit/miles"
            "/feature/air-conditioning/feature/cruise-control",
        )
        self.assertEqual(target.query, "")


    def test_query_style_inventory_redirects_to_fancy_path_when_enabled(self):
        self._set_fancy_urls(True)

        response = self.app.test_client().get(
            "/autogrid360/?min_year=2020&max_year=2020&make=honda"
            "&model=honda:civic&country_code=US&zone_code=US-IL"
            "&drivetrain=fwd&sort=price_asc"
        )

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(
            target.path,
            "/autogrid360/2020/honda/civic/illinois/fwd",
        )
        self.assertEqual(parse_qs(target.query), {"sort": ["price_asc"]})


    def test_query_style_seller_filter_uses_canonical_username_in_fancy_path(self):
        self._set_fancy_urls(True)
        self._create_active_inventory_listing(
            title="Canonical seller Accord",
            year=2020,
            make="Honda",
            model="Accord",
            seller=self.other_user,
        )

        response = self.app.test_client().get(
            f"/autogrid360/?seller={self.other_user.username.upper()}"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            urlparse(response.headers["Location"]).path,
            f"/autogrid360/seller/{self.other_user.username}",
        )


    def test_query_style_pagination_redirects_to_fancy_page_segment(self):
        self._set_fancy_urls(True)

        response = self.app.test_client().get(
            "/autogrid360/?page=2&sort=price_asc&per_page=50"
        )

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(target.path, "/autogrid360/page/2")
        self.assertEqual(
            parse_qs(target.query),
            {"sort": ["price_asc"], "per_page": ["50"]},
        )


    def test_fancy_inventory_path_renders_same_filtered_results(self):
        self._set_fancy_urls(True)
        match = self._create_active_inventory_listing(
            title="Fancy Civic",
            year=2020,
            make="Honda",
            model="Civic",
            drivetrain="FWD",
            zone_code="US-IL",
        )
        wrong_year = self._create_active_inventory_listing(
            title="Wrong year Civic",
            year=2021,
            make="Honda",
            model="Civic",
            drivetrain="FWD",
            zone_code="US-IL",
        )
        wrong_drive = self._create_active_inventory_listing(
            title="Wrong drive Civic",
            year=2020,
            make="Honda",
            model="Civic",
            drivetrain="AWD",
            zone_code="US-IL",
        )

        response = self.app.test_client().get(
            "/autogrid360/2020/honda/civic/illinois/fwd"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(match.title, body)
        self.assertNotIn(wrong_year.title, body)
        self.assertNotIn(wrong_drive.title, body)
        self.assertIn("2020 Honda Civic Inventory in Illinois", body)
        self.assertIn('content="index,follow"', body)


    def test_fancy_inventory_pagination_uses_page_path_and_preserves_sort_state(self):
        self._set_fancy_urls(True)
        for index in range(11):
            self._create_active_inventory_listing(
                title=f"Fancy page Civic {index}",
                year=2020,
                make="Honda",
                model="Civic",
                price=str(5000 + index * 1000),
                drivetrain="FWD",
                zone_code="US-IL",
            )

        first = self.app.test_client().get(
            "/autogrid360/2020/honda/civic/illinois/fwd"
            "?sort=price_asc&per_page=10"
        )
        body = first.get_data(as_text=True)
        match = re.search(r'href="([^"]+)">Next</a>', body)

        self.assertEqual(first.status_code, 200)
        self.assertIsNotNone(match)
        next_url = unescape(match.group(1))
        parsed = urlparse(next_url)
        self.assertEqual(
            parsed.path,
            "/autogrid360/2020/honda/civic/illinois/fwd/page/2",
        )
        self.assertEqual(
            parse_qs(parsed.query),
            {"sort": ["price_asc"], "per_page": ["10"]},
        )


    def test_fancy_inventory_path_redirects_to_plain_query_url_when_disabled(self):
        response = self.app.test_client().get(
            "/autogrid360/2020/honda/civic/illinois/fwd?sort=price_asc"
        )

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(target.path, "/autogrid360/")
        self.assertEqual(
            parse_qs(target.query),
            {
                "min_year": ["2020"],
                "max_year": ["2020"],
                "make": ["honda"],
                "model": ["honda:civic"],
                "country_code": ["US"],
                "zone_code": ["US-IL"],
                "drivetrain": ["fwd"],
                "sort": ["price_asc"],
            },
        )


    def test_public_search_ignores_invalid_numeric_filters_and_sort(self):
        listing = self._create_active_inventory_listing(
            title="Still visible",
            year=2020,
            make="Honda",
            model="Civic",
            price="10000.00",
        )

        response = self.app.test_client().get(
            "/autogrid360/?min_year=oops&max_year=2200"
            "&min_price=-10&max_price=NaN&sort=not-a-sort"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(listing.title, body)
        self.assertNotIn('id="sort" name="sort"', body)
        self.assertNotIn('>Newest</a>', body)
        self.assertIn('>Make</a>', body)
        self.assertIn('>Model</a>', body)
        self.assertIn('>Year</a>', body)
        self.assertIn('>Price</a>', body)


    def test_public_detail_shows_active_listing_without_login(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.vehicle.trim = "EX"
        listing.vehicle.vehicle_type_ref = self._reference(CATEGORY_VEHICLE_TYPE, "Sedan")
        listing.vehicle.transmission = "Automatic"
        listing.vehicle.drivetrain_ref = self._reference(CATEGORY_DRIVETRAIN, "FWD")
        listing.vehicle.fuel_type = "Gasoline"
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Seller-owned Civic", body)
        self.assertIn("2012 Honda Civic EX", body)
        self.assertIn("Sedan", body)
        self.assertIn("Automatic", body)
        self.assertIn("Front Wheel Drive", body)
        self.assertIn("Gasoline", body)
        self.assertIn(self.seller.username, body)
        self.assertNotIn(self.seller.email, body)


    def test_public_listing_owner_controls_are_owner_or_admin_only(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        path = self.public_listing_path(listing)
        edit_link = (
            f'href="/autogrid360/listings/{listing.id}/edit">Edit Listing</a>'
        )
        manage_link = (
            f'href="/autogrid360/listings/{listing.id}">Manage Listing</a>'
        )

        anonymous_body = self.app.test_client().get(path).get_data(as_text=True)
        self.assertNotIn(edit_link, anonymous_body)
        self.assertNotIn(manage_link, anonymous_body)

        unrelated_client = self.app.test_client()
        self._login(unrelated_client, self.other_user)
        unrelated_body = unrelated_client.get(path).get_data(as_text=True)
        self.assertNotIn(edit_link, unrelated_body)
        self.assertNotIn(manage_link, unrelated_body)

        owner_client = self.app.test_client()
        self._login(owner_client, self.seller)
        owner_body = owner_client.get(path).get_data(as_text=True)
        self.assertIn(edit_link, owner_body)
        self.assertIn(manage_link, owner_body)

        admin_client = self.app.test_client()
        self._login(admin_client, self.admin)
        admin_body = admin_client.get(path).get_data(as_text=True)
        self.assertIn(edit_link, admin_body)
        self.assertIn(manage_link, admin_body)


    def test_public_detail_hides_non_active_listing_states(self):
        for status in (STATUS_DRAFT, STATUS_PENDING):
            listing = self._create_listing()
            listing.status = status
            db.session.commit()

            response = self.app.test_client().get(
                self.public_listing_path(listing)
            )

            self.assertEqual(response.status_code, 404)
            db.session.delete(listing)
            db.session.commit()


    def test_public_detail_renders_image_gallery(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        primary = self._create_image(listing, position=0, is_primary=True, token="front")
        secondary = self._create_image(listing, position=1, is_primary=False, token="rear")
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            f"/autogrid360/listings/{listing.id}/images/{primary.id}/display",
            body,
        )
        self.assertIn(
            f"/autogrid360/listings/{listing.id}/images/{secondary.id}/thumb",
            body,
        )
        self.assertIn("data-autogrid360-gallery-main-link", body)
        self.assertIn("data-autogrid360-gallery-thumbnail", body)
        self.assertIn("data-autogrid360-image-lightbox", body)
        self.assertIn('aria-label="Close image"', body)
        self.assertIn("/autogrid360/static/gallery.js", body)


    def test_public_detail_increments_view_count(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        listing.view_count = 7
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        db.session.refresh(listing)
        self.assertEqual(listing.view_count, 8)


    def test_public_detail_links_to_contact_seller_without_exposing_email(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        response = self.app.test_client().get(
            self.public_listing_path(listing)
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Contact Seller", body)
        self.assertIn(f"/autogrid360/listings/{listing.id}/contact", body)
        self.assertNotIn(self.seller.email, body)


    def test_contact_seller_form_is_public_and_hides_seller_email(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/contact"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Contact Seller", body)
        self.assertIn(listing.title, body)
        self.assertIn("Send Inquiry", body)
        self.assertNotIn(self.seller.email, body)


    def test_contact_seller_hides_non_active_listing_states(self):
        for status in (STATUS_DRAFT, STATUS_PENDING):
            listing = self._create_listing()
            listing.status = status
            db.session.commit()

            client = self.app.test_client()
            get_response = client.get(f"/autogrid360/listings/{listing.id}/contact")
            post_response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(),
            )

            self.assertEqual(get_response.status_code, 404)
            self.assertEqual(post_response.status_code, 404)
            db.session.delete(listing)
            db.session.commit()


    def test_anonymous_inquiry_queues_only_to_listing_seller(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        env = SimpleNamespace(spam_check_enabled=False, spam_check_provider="local")

        with patch(
            "app.plugins.autogrid360.routes.public.get_cached_env_settings",
            return_value=env,
        ), patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.routes.public.send_email",
            return_value="queued",
        ) as send_email:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(),
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"/autogrid360/listings/{listing.id}/2012-honda-civic",
        )
        send_email.assert_called_once()
        subject, recipient, body = send_email.call_args.args
        self.assertEqual(subject, "AutoGrid360 inquiry: Availability")
        self.assertEqual(recipient, self.seller.email)
        self.assertNotEqual(recipient, self.other_user.email)
        self.assertIn(f"Listing ID: {listing.id}", body)
        self.assertIn(listing.title, body)
        self.assertIn("2012 Honda Civic", body)
        self.assertIn("Buyer Person", body)
        self.assertIn("buyer@example.com", body)
        self.assertIn("Anonymous visitor", body)
        self.assertIn("Is this vehicle still available?", body)
        self.assertIn(
            ("success", "Your inquiry was accepted for delivery."),
            self._flash_messages(client),
        )


    def test_authenticated_inquiry_uses_canonical_account_identity(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        self.other_user.email = "other-seller@example.com"
        db.session.commit()
        self._login(client, self.other_user)
        env = SimpleNamespace(spam_check_enabled=False, spam_check_provider="local")

        with patch(
            "app.plugins.autogrid360.routes.public.get_cached_env_settings",
            return_value=env,
        ), patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.routes.public.send_email",
            return_value="queued",
        ) as send_email:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(
                    name="Forged Name",
                    email="forged@example.test",
                ),
            )

        self.assertEqual(response.status_code, 302)
        _, recipient, body = send_email.call_args.args
        self.assertEqual(recipient, self.seller.email)
        self.assertIn(self.other_user.username, body)
        self.assertIn(self.other_user.email, body)
        self.assertNotIn("Forged Name", body)
        self.assertNotIn("forged@example.test", body)


    def test_inquiry_honeypot_does_not_send_email(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()

        with patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.routes.public.send_email"
        ) as send_email:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(nobot_check="filled by bot"),
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"/autogrid360/listings/{listing.id}/contact",
        )
        send_email.assert_not_called()


    def test_inquiry_spam_check_blocks_delivery(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        env = SimpleNamespace(spam_check_enabled=True, spam_check_provider="local")

        with patch(
            "app.plugins.autogrid360.routes.public.get_cached_env_settings",
            return_value=env,
        ), patch(
            "app.plugins.autogrid360.routes.public.check_spam",
            return_value=SimpleNamespace(passed=False, message="Blocked inquiry."),
        ), patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.routes.public.send_email"
        ) as send_email:
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(),
            )

        self.assertEqual(response.status_code, 302)
        send_email.assert_not_called()
        self.assertIn(
            ("danger", "Blocked inquiry."),
            self._flash_messages(client),
        )


    def test_unavailable_inquiry_delivery_does_not_report_success(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        env = SimpleNamespace(spam_check_enabled=False, spam_check_provider="local")

        with patch(
            "app.plugins.autogrid360.routes.public.get_cached_env_settings",
            return_value=env,
        ), patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.plugins.autogrid360.routes.public.send_email",
            return_value="disabled",
        ):
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(),
            )

        self.assertEqual(response.status_code, 302)
        flashes = self._flash_messages(client)
        self.assertIn(
            (
                "danger",
                "Seller contact is currently unavailable. Please try again later.",
            ),
            flashes,
        )
        self.assertFalse(any(category == "success" for category, _ in flashes))


    def test_invalid_inquiry_stays_on_form_and_does_not_send(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        with patch(
            "app.plugins.autogrid360.routes.public.send_email"
        ) as send_email:
            response = self.app.test_client().post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(message=""),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("This field is required.", response.get_data(as_text=True))
        send_email.assert_not_called()


    def test_inquiry_audit_redacts_sender_and_omits_message_content(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()
        client = self.app.test_client()
        env = SimpleNamespace(spam_check_enabled=False, spam_check_provider="local")

        with patch(
            "app.plugins.autogrid360.routes.public.get_cached_env_settings",
            return_value=env,
        ), patch(
            "app.plugins.autogrid360.routes.public.audit_activity_enabled",
            return_value=True,
        ), patch(
            "app.plugins.autogrid360.routes.public.log_action_isolated"
        ) as log_action, patch(
            "app.plugins.autogrid360.routes.public.send_email",
            return_value="queued",
        ):
            response = client.post(
                f"/autogrid360/listings/{listing.id}/contact",
                data=self._inquiry_form_data(
                    email="buyer@example.com",
                    message="Private inquiry content",
                ),
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_called_once()
        call = log_action.call_args
        self.assertEqual(call.kwargs["action"], "autogrid360_listing_inquiry")
        self.assertEqual(call.kwargs["target"], f"listing:{listing.id}")
        extra = call.kwargs["extra_data"]
        self.assertEqual(extra["listing_id"], listing.id)
        self.assertEqual(extra["seller_id"], self.seller.id)
        self.assertEqual(extra["sender_email"], "b***r@example.com")
        self.assertEqual(extra["status"], "queued")
        self.assertNotIn("buyer@example.com", str(extra))
        self.assertNotIn("Private inquiry content", str(extra))


    def test_management_detail_remains_private_for_active_listing(self):
        listing = self._create_listing()
        listing.status = STATUS_ACTIVE
        db.session.commit()

        anonymous = self.app.test_client().get(f"/autogrid360/listings/{listing.id}")
        client = self.app.test_client()
        self._login(client, self.other_user)
        other_user = client.get(f"/autogrid360/listings/{listing.id}")

        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/host-login", anonymous.headers["Location"])
        self.assertEqual(other_user.status_code, 404)


    def test_autogrid360_index_is_paginated_public_inventory(self):
        older = self._create_active_inventory_listing(
            title="Older index vehicle",
            year=2018,
            make="Ford",
            model="Focus",
        )
        newest = self._create_active_inventory_listing(
            title="Newest index vehicle",
            year=2020,
            make="Honda",
            model="Civic",
        )
        older.published_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        newest.published_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.session.commit()

        with patch.dict(
            self.app.config,
            {"AUTOGRID360_LISTINGS_PER_PAGE": 1},
        ):
            first = self.app.test_client().get("/autogrid360/")
            second = self.app.test_client().get("/autogrid360/?page=2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_body = first.get_data(as_text=True)
        second_body = second.get_data(as_text=True)
        self.assertIn("Vehicle Inventory", first_body)
        self.assertIn(newest.title, first_body)
        self.assertNotIn(older.title, first_body)
        self.assertIn('href="/autogrid360/?page=2"', first_body)
        self.assertIn(older.title, second_body)
        self.assertIn("Page 1 of 2", first_body)
        self.assertIn("Page 2 of 2", second_body)


    def test_public_search_links_to_search_engine_friendly_listing_url(self):
        listing = self._create_active_inventory_listing(
            title="Clean Civic listing",
            year=2020,
            make="Honda",
            model="Civic",
        )

        response = self.app.test_client().get("/autogrid360/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'/autogrid360/listings/{listing.id}/2020-honda-civic',
            body,
        )


    def test_canonical_listing_route_redirects_stale_slug(self):
        listing = self._create_active_inventory_listing(
            title="Canonical Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/wrong-slug"
        )

        self.assertEqual(response.status_code, 301)
        self.assertTrue(
            response.headers["Location"].endswith(
                f"/autogrid360/listings/{listing.id}/2020-honda-civic"
            )
        )


    def test_active_listing_renders_canonical_metadata_and_vehicle_jsonld(self):
        settings = AutoGrid360Settings(id=1, currency_code="CAD")
        db.session.add(settings)
        listing = self._create_active_inventory_listing(
            title="Low mileage Civic",
            year=2020,
            make="Honda",
            model="Civic",
            price="15995.00",
            condition="Used",
        )
        listing.description = "One owner Civic with service records and clean interior."
        listing.vehicle.vin = "1HGCM82633A004352"
        listing.vehicle.mileage = 41000
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/2020-honda-civic"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        canonical = f"http://localhost/autogrid360/listings/{listing.id}/2020-honda-civic"
        self.assertIn(f'<link rel="canonical" href="{canonical}">', body)
        self.assertIn(
            '<meta name="description" content="One owner Civic with service records and clean interior.">',
            body,
        )
        self.assertIn('<meta name="robots" content="index,follow">', body)
        match = re.search(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["@type"], ["Product", "Vehicle"])
        self.assertEqual(payload["brand"]["name"], "Honda")
        self.assertEqual(payload["model"], "Civic")
        self.assertEqual(payload["vehicleIdentificationNumber"], "1HGCM82633A004352")
        self.assertEqual(payload["offers"]["priceCurrency"], "CAD")
        self.assertEqual(payload["offers"]["availability"], "https://schema.org/InStock")
        self.assertEqual(payload["url"], canonical)


    def test_sold_listing_remains_public_but_is_noindex(self):
        listing = self._create_active_inventory_listing(
            title="Sold Civic",
            year=2020,
            make="Honda",
            model="Civic",
            price="12000.00",
        )
        listing.status = STATUS_SOLD
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/2020-honda-civic"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<meta name="robots" content="noindex,follow">', body)
        match = re.search(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
            body,
            re.DOTALL,
        )
        payload = json.loads(match.group(1))
        self.assertEqual(payload["offers"]["availability"], "https://schema.org/SoldOut")


    def test_filtered_query_inventory_is_noindex_and_self_canonical_when_fancy_disabled(self):
        response = self.app.test_client().get(
            "/autogrid360/?make=honda&min_year=2020"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<meta name="robots" content="noindex,follow">', body)
        self.assertIn(
            '<link rel="canonical" href="http://localhost/autogrid360/?make=honda&amp;min_year=2020">',
            body,
        )


    def test_autogrid360_sitemap_contains_only_active_inventory_urls(self):
        active = self._create_active_inventory_listing(
            title="Active Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        sold = self._create_active_inventory_listing(
            title="Sold Focus",
            year=2019,
            make="Ford",
            model="Focus",
        )
        sold.status = STATUS_SOLD
        draft = self._create_listing()
        db.session.commit()

        response = self.app.test_client().get("/autogrid360/sitemap.xml")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/xml")
        self.assertIn(
            f"http://localhost/autogrid360/listings/{active.id}/2020-honda-civic",
            body,
        )
        self.assertNotIn(
            f"http://localhost/autogrid360/listings/{sold.id}/2019-ford-focus",
            body,
        )
        self.assertNotIn(str(draft.portable_id), body)
        self.assertIn(
            "http://localhost/autogrid360/sellers/listing-seller",
            body,
        )
        self.assertNotIn("/autogrid360/feed.xml", body)
        self.assertNotIn("/autogrid360/listings/search", body)


    def test_inventory_feed_tracks_public_sale_state_policy(self):
        active = self._create_active_inventory_listing(
            title="Feed Civic", year=2021, make="Honda", model="Civic"
        )
        sale_pending = self._create_active_inventory_listing(
            title="Feed Pending Accord", year=2021, make="Honda", model="Accord"
        )
        sold = self._create_active_inventory_listing(
            title="Feed Sold Focus", year=2019, make="Ford", model="Focus"
        )
        sale_pending.status = STATUS_SALE_PENDING
        sold.status = STATUS_SOLD
        sold.sold_at = datetime.now(timezone.utc)
        db.session.commit()

        response = self.app.test_client().get("/autogrid360/feed.xml")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/rss+xml")
        self.assertIn(active.title, body)
        self.assertIn(sale_pending.title, body)
        self.assertNotIn(sold.title, body)
        self.assertIn(
            f"http://localhost/autogrid360/listings/{active.id}/2021-honda-civic",
            body,
        )
        self.assertIn(active.portable_id, body)

        self._set_listing_policy(show_sale_pending=False)
        hidden_body = self.app.test_client().get("/autogrid360/feed.xml").get_data(as_text=True)
        self.assertIn(active.title, hidden_body)
        self.assertNotIn(sale_pending.title, hidden_body)
        self.assertNotIn(sold.title, hidden_body)


    def test_inventory_feed_can_be_scoped_to_one_seller(self):
        own = self._create_active_inventory_listing(
            title="Seller Civic",
            year=2021,
            make="Honda",
            model="Civic",
            seller=self.seller,
        )
        other = self._create_active_inventory_listing(
            title="Other Focus",
            year=2021,
            make="Ford",
            model="Focus",
            seller=self.other_user,
        )

        response = self.app.test_client().get(
            f"/autogrid360/feed.xml?seller={self.seller.username}"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(own.title, body)
        self.assertNotIn(other.title, body)
        self.assertIn("listing-seller - AutoGrid360 Inventory", body)


    def test_inventory_feed_rejects_unknown_seller(self):
        response = self.app.test_client().get(
            "/autogrid360/feed.xml?seller=missing-seller"
        )

        self.assertEqual(response.status_code, 404)


    def test_printable_listing_is_noindex_and_points_to_canonical_detail(self):
        listing = self._create_active_inventory_listing(
            title="Printable Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/print"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('content="noindex,follow"', body)
        self.assertIn(
            f'<link rel="canonical" href="http://localhost/autogrid360/listings/{listing.id}/2020-honda-civic">',
            body,
        )


    def test_public_view_count_does_not_change_listing_content_timestamp(self):
        listing = self._create_active_inventory_listing(
            title="Timestamp Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        content_updated_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        listing.updated_at = content_updated_at
        db.session.commit()

        response = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/2020-honda-civic"
        )

        self.assertEqual(response.status_code, 200)
        db.session.refresh(listing)
        self.assertEqual(listing.view_count, 1)
        normalized_updated_at = listing.updated_at
        if normalized_updated_at.tzinfo is None:
            normalized_updated_at = normalized_updated_at.replace(tzinfo=timezone.utc)
        self.assertEqual(normalized_updated_at, content_updated_at)


    def test_public_listing_keeps_original_listed_date_after_reactivation(self):
        listing = self._create_active_inventory_listing(
            title="Original Date Civic",
            year=2020,
            make="Honda",
            model="Civic",
        )
        listing.first_published_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        listing.published_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        db.session.commit()

        body = self.app.test_client().get(
            f"/autogrid360/listings/{listing.id}/2020-honda-civic"
        ).get_data(as_text=True)

        self.assertIn("June 01, 2026", body)
        self.assertNotIn("August 10, 2026", body)
