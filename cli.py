# app/plugins/autogrid360/cli.py
"""Plugin-owned CLI commands for AutoGrid360."""

import logging
from pathlib import Path

import click

from app.core.extensions import db
from app.core.trackers import audit_activity_enabled, log_action, log_action_isolated
from app.plugins.migrations import PluginMigrationError, PluginMigrationManager
from app.plugins.autogrid360.services.auth import user_by_username
from app.plugins.autogrid360.services.maintenance import run_scheduled_maintenance
from app.plugins.autogrid360.services.transfer import (
    InventoryBundleError,
    cleanup_restore_files,
    export_inventory_bundle,
    export_site_inventory_bundle,
    import_inventory_bundle,
    inspect_inventory_bundle,
    parse_seller_mapping_entries,
    resolve_restore_seller_mapping,
    restore_inventory_bundle,
)


logger = logging.getLogger(__name__)


@click.group()
def cli():
    """AutoGrid360 commands."""


def _migration_manager() -> PluginMigrationManager:
    # Import lazily because plugin.py imports this CLI module.
    from app.plugins.autogrid360.plugin import plugin

    return PluginMigrationManager(plugin.manifest)


@cli.command("status")
def status():
    """Show the current AutoGrid360 persistence state."""

    try:
        manager = _migration_manager()
        current = manager.current_revision()
        head = manager.head_revision()
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("AutoGrid360 plugin is available.")
    click.echo(f"schema={'current' if head and current == head else 'needs-migration'}")
    click.echo(f"schema_revision={current or '<base>'}")
    click.echo(f"schema_head={head or '<none>'}")


@cli.group("reference")
def reference_commands():
    """Manage AutoGrid360 controlled reference data."""


@reference_commands.command("seed")
def reference_seed():
    """Add missing values from the editable automotive JSON defaults."""

    from app.plugins.autogrid360.services.reference import ReferenceDataError, seed_reference_data

    try:
        inserted = seed_reference_data()
        db.session.commit()
    except ReferenceDataError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    except Exception:
        db.session.rollback()
        raise

    click.echo(f"AutoGrid360 reference defaults added={inserted}")


@cli.group("postal")
def postal_commands():
    """Manage generated postal-centroid reference data."""


@postal_commands.command("sync")
@click.option(
    "--country",
    "countries",
    multiple=True,
    type=str,
    help="Sync one ISO alpha-2 country artifact; repeat for multiple countries. Defaults to US.",
)
def postal_sync(countries: tuple[str, ...]):
    """Synchronize normalized postal artifacts into the AutoGrid360 database."""

    from app.plugins.autogrid360.services.geo import PostalDataError, sync_postal_data

    selected = tuple(country.upper() for country in countries) or None
    try:
        result = sync_postal_data(countries=selected)
        db.session.commit()
    except PostalDataError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    except Exception:
        db.session.rollback()
        raise

    click.echo(
        "AutoGrid360 postal data synchronized: "
        f"inserted={result.inserted} "
        f"updated={result.updated} "
        f"reactivated={result.reactivated} "
        f"deactivated={result.deactivated} "
        f"active={result.total_active}"
    )


@cli.command("maintenance")
def scheduled_maintenance():
    """Run listing expiration, expired retention, and sold retention once."""

    try:
        result = run_scheduled_maintenance()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("AutoGrid360 scheduled maintenance failed")
        raise click.ClickException(
            "AutoGrid360 maintenance failed. Check application logs."
        ) from exc

    expiration_state = (
        "enabled" if result.expiration_enabled else "disabled"
    )
    click.echo(
        "AutoGrid360 maintenance completed: "
        f"listing expiration is {expiration_state}; "
        f"warnings_queued={result.warnings_queued} "
        f"warnings_disabled={result.warnings_disabled} "
        f"warnings_failed={result.warnings_failed} "
        f"expired={result.expired} "
        f"removal_warnings_queued={result.removal_warnings_queued} "
        f"removal_warnings_disabled={result.removal_warnings_disabled} "
        f"removal_warnings_failed={result.removal_warnings_failed} "
        f"removed={result.removed} "
        f"removal_notices_queued={result.removal_notices_queued} "
        f"removal_notices_disabled={result.removal_notices_disabled} "
        f"removal_notices_failed={result.removal_notices_failed}"
    )


@cli.group("inventory")
def inventory_commands():
    """Back up or restore canonical AutoGrid360 inventory bundles."""


@inventory_commands.command("export")
@click.option("--seller", "seller_username", required=True, help="Seller username to export.")
@click.option("--force", is_flag=True, help="Replace an existing destination bundle.")
@click.argument("destination", type=click.Path(path_type=Path, dir_okay=False))
def inventory_export(seller_username: str, force: bool, destination: Path):
    """Export one seller's AutoGrid360 inventory to DESTINATION."""

    seller = user_by_username(seller_username)
    if seller is None:
        raise click.ClickException("No Flask-AAS user has that username.")
    if destination.exists() and not force:
        raise click.ClickException("Destination already exists; use --force to replace it.")

    try:
        result = export_inventory_bundle(seller, destination)
    except (InventoryBundleError, OSError) as exc:
        logger.exception("AutoGrid360 operator inventory export failed seller_id=%s", seller.id)
        raise click.ClickException(str(exc)) from exc

    if audit_activity_enabled():
        log_action_isolated(
            user_id=None,
            action="autogrid360_inventory_exported_cli",
            target=f"seller:{seller.id}",
            extra_data={
                "seller_id": seller.id,
                "listing_count": result.listings_exported,
                "image_count": result.images_exported,
            },
        )

    click.echo(
        "AutoGrid360 inventory exported: "
        f"seller={seller.username} "
        f"listings={result.listings_exported} "
        f"images={result.images_exported} "
        f"destination={destination}"
    )


@inventory_commands.command("export-all")
@click.option("--force", is_flag=True, help="Replace an existing destination bundle.")
@click.argument("destination", type=click.Path(path_type=Path, dir_okay=False))
def inventory_export_all(force: bool, destination: Path):
    """Export a full AutoGrid360 multi-seller backup to DESTINATION."""

    if destination.exists() and not force:
        raise click.ClickException("Destination already exists; use --force to replace it.")

    try:
        result = export_site_inventory_bundle(destination)
    except (InventoryBundleError, OSError) as exc:
        logger.exception("AutoGrid360 operator full inventory export failed")
        raise click.ClickException(str(exc)) from exc

    if audit_activity_enabled():
        log_action_isolated(
            user_id=None,
            action="autogrid360_inventory_full_exported_cli",
            target="autogrid360_inventory:all",
            extra_data={
                "seller_count": result.sellers_exported,
                "listing_count": result.listings_exported,
                "image_count": result.images_exported,
            },
        )

    click.echo(
        "AutoGrid360 full inventory backup exported: "
        f"sellers={result.sellers_exported} "
        f"listings={result.listings_exported} "
        f"images={result.images_exported} "
        f"destination={destination}"
    )


@inventory_commands.command("import")
@click.option(
    "--seller",
    "seller_username",
    required=True,
    help="Destination Flask-AAS seller username.",
)
@click.option(
    "--as-draft",
    is_flag=True,
    help="Reset restored listings to Draft instead of preserving lifecycle state.",
)
@click.argument(
    "bundle",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
)
def inventory_import(seller_username: str, as_draft: bool, bundle: Path):
    """Restore one seller-scoped BUNDLE to a destination seller."""

    seller = user_by_username(seller_username)
    if seller is None:
        raise click.ClickException("No Flask-AAS user has that username.")

    result = None
    try:
        result = import_inventory_bundle(bundle, seller, as_draft=as_draft)
        if audit_activity_enabled():
            log_action(
                user_id=None,
                action="autogrid360_inventory_imported_cli",
                target=f"seller:{seller.id}",
                extra_data={
                    "seller_id": seller.id,
                    "source_seller_username": result.seller_mappings[0][0],
                    "listing_count": result.listings_imported,
                    "image_count": result.images_imported,
                    "seller_profile_created": bool(result.seller_profiles_created),
                    "as_draft": as_draft,
                },
            )
        db.session.commit()
    except InventoryBundleError as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        logger.exception("AutoGrid360 operator inventory import failed seller_id=%s", seller.id)
        raise click.ClickException(
            "AutoGrid360 inventory import failed. Check application logs."
        ) from exc

    click.echo(
        "AutoGrid360 inventory restored: "
        f"source_seller={result.seller_mappings[0][0]} "
        f"destination_seller={seller.username} "
        f"listings={result.listings_imported} "
        f"images={result.images_imported} "
        f"seller_profile_created={str(bool(result.seller_profiles_created)).lower()} "
        f"as_draft={str(as_draft).lower()}"
    )


@inventory_commands.command("restore")
@click.option(
    "--map",
    "seller_mappings",
    multiple=True,
    help="Optional source=destination seller mapping; repeat for renamed accounts.",
)
@click.option(
    "--as-draft",
    is_flag=True,
    help="Reset restored listings to Draft instead of preserving lifecycle state.",
)
@click.argument(
    "bundle",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
)
def inventory_restore(seller_mappings: tuple[str, ...], as_draft: bool, bundle: Path):
    """Restore a seller or full-site backup using destination Flask-AAS users."""

    result = None
    try:
        validated = inspect_inventory_bundle(bundle)
        overrides = parse_seller_mapping_entries(seller_mappings)
        mapping = resolve_restore_seller_mapping(validated, overrides)
        result = restore_inventory_bundle(bundle, mapping, as_draft=as_draft)
        if audit_activity_enabled():
            log_action(
                user_id=None,
                action="autogrid360_inventory_restored_cli",
                target="autogrid360_inventory:restore",
                extra_data={
                    "seller_count": result.sellers_restored,
                    "listing_count": result.listings_imported,
                    "image_count": result.images_imported,
                    "seller_profiles_created": result.seller_profiles_created,
                    "as_draft": as_draft,
                    "seller_mappings": [
                        {"source": source, "destination": destination}
                        for source, destination in result.seller_mappings
                    ],
                },
            )
        db.session.commit()
    except InventoryBundleError as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        logger.exception("AutoGrid360 operator inventory restore failed")
        raise click.ClickException(
            "AutoGrid360 inventory restore failed. Check application logs."
        ) from exc

    click.echo(
        "AutoGrid360 inventory backup restored: "
        f"sellers={result.sellers_restored} "
        f"listings={result.listings_imported} "
        f"images={result.images_imported} "
        f"profiles_created={result.seller_profiles_created} "
        f"as_draft={str(as_draft).lower()}"
    )
