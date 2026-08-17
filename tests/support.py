# app/plugins/autogrid360/tests/support.py
"""Shared AutoGrid360 test fixtures that mirror stable Flask-AAS host references."""

from app.core.extensions import db
from app.models import Country, Zone


LOCATION_TABLES = (Country.__table__, Zone.__table__)


def seed_location_references() -> None:
    """Seed a minimal Country/Zone catalog for isolated AutoGrid360 tests."""

    countries = {
        "US": Country(name="United States", iso_code_2="US", iso_code_3="USA", active=True),
        "CA": Country(name="Canada", iso_code_2="CA", iso_code_3="CAN", active=True),
        "GB": Country(name="United Kingdom", iso_code_2="GB", iso_code_3="GBR", active=True),
    }
    db.session.add_all(countries.values())
    db.session.flush()

    zones = [
        Zone(
            country_id=countries["US"].country_id,
            code="US-IL",
            name="Illinois",
            type="State",
            active=True,
        ),
        Zone(
            country_id=countries["US"].country_id,
            code="US-WI",
            name="Wisconsin",
            type="State",
            active=True,
        ),
        Zone(
            country_id=countries["CA"].country_id,
            code="CA-ON",
            name="Ontario",
            type="Province",
            active=True,
        ),
        Zone(
            country_id=countries["GB"].country_id,
            code="GB-ENG",
            name="England",
            type="Country",
            active=True,
        ),
    ]
    db.session.add_all(zones)
    db.session.flush()
    england = next(zone for zone in zones if zone.code == "GB-ENG")
    db.session.add(
        Zone(
            country_id=countries["GB"].country_id,
            code="GB-WIL",
            name="Wiltshire",
            type="Unitary authority",
            parent_zone_id=england.zone_id,
            active=True,
        )
    )
    db.session.flush()
