# services/data.py
"""AutoGrid360 declarations for host-managed application datasets."""

from app.plugins import PluginDataset, PluginDatasetActionResult
from app.plugins.autogrid360.models import PostalLocation, ReferenceValue, VehicleModel
from app.plugins.autogrid360.services.geo import available_postal_countries, sync_postal_data
from app.plugins.autogrid360.services.reference import DATA_ROOT, REFERENCE_FILES, seed_reference_data


AUTOMOTIVE_DATASET = "automotive_reference"
POSTAL_DATASET = "postal"


def _automotive_assets_available() -> bool:
    return all((DATA_ROOT / filename).is_file() for filename in REFERENCE_FILES.values())


def admin_datasets() -> tuple[PluginDataset, ...]:
    """Describe packaged AutoGrid360 datasets without making them readiness requirements."""

    datasets: list[PluginDataset] = []

    if _automotive_assets_available():
        reference_count = ReferenceValue.query.count()
        model_count = VehicleModel.query.count()
        datasets.append(
            PluginDataset(
                key=AUTOMOTIVE_DATASET,
                label="Automotive Reference Data",
                description=(
                    "Packaged makes, models, vehicle types, drivetrains, and features. "
                    "Loading is additive and preserves existing database identities and edits."
                ),
                status=(
                    f"{reference_count:,} reference values and "
                    f"{model_count:,} vehicle models in the database."
                ),
                action_label=(
                    "Load"
                    if reference_count == 0 and model_count == 0
                    else "Reload"
                ),
            )
        )

    countries = available_postal_countries()
    if countries:
        active_count = PostalLocation.query.filter(
            PostalLocation.country_code.in_(countries),
            PostalLocation.active.is_(True),
        ).count()
        country_list = ", ".join(countries)
        datasets.append(
            PluginDataset(
                key=POSTAL_DATASET,
                label="Postal Data",
                description=(
                    "Packaged postal-centroid data used for optional locality lookup and "
                    "radius search. AutoGrid360 remains usable without loading it."
                ),
                status=(
                    f"{active_count:,} active database records across packaged "
                    f"dataset(s): {country_list}."
                ),
                action_label="Load" if active_count == 0 else "Reload",
            )
        )

    return tuple(datasets)


def run_admin_dataset_action(dataset_key: str) -> PluginDatasetActionResult:
    """Execute one host-dispatched AutoGrid360 dataset operation without committing."""

    if dataset_key == AUTOMOTIVE_DATASET:
        if not _automotive_assets_available():
            raise RuntimeError("Packaged automotive reference data is unavailable")
        inserted = seed_reference_data()
        if inserted:
            message = f"AutoGrid360 automotive reference data loaded: added {inserted:,} records."
        else:
            message = "AutoGrid360 automotive reference data is already populated; no records added."
        return PluginDatasetActionResult(message=message)

    if dataset_key == POSTAL_DATASET:
        countries = available_postal_countries()
        if not countries:
            raise RuntimeError("No packaged postal datasets are available")
        result = sync_postal_data(countries=countries)
        return PluginDatasetActionResult(
            message=(
                "AutoGrid360 postal data synchronized: "
                f"inserted={result.inserted:,}, updated={result.updated:,}, "
                f"reactivated={result.reactivated:,}, deactivated={result.deactivated:,}, "
                f"active={result.total_active:,}."
            )
        )

    raise KeyError(f"Unknown AutoGrid360 dataset {dataset_key!r}")
