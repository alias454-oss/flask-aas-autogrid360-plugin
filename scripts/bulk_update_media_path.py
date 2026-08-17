#!/usr/bin/env python3
"""
Repair local SQLite media paths after moving runtime images out of app/static.

Run from the Flask-AAS repository root.

Changes:
- env_settings.users_stored_path -> "uploads/users"
- plugin_autogrid360_settings.listing_images_path -> "uploads/listings"

If the AutoGrid360 singleton settings row does not exist yet, create the normal
row with id=1 and let the database apply the model-defined server defaults for
all other settings.

A timestamped backup of the SQLite database is created before modification.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


AUTOGRID360_SETTINGS_ID = 1


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Update Flask-AAS and AutoGrid360 local media paths."
    )
    parser.add_argument(
        "--db",
        default="instance/dev.db",
        help="SQLite database path relative to repo root (default: instance/dev.db).",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Flask-AAS repository root (default: current directory).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a timestamped database backup.",
    )
    return parser.parse_args()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return whether a table exists in the SQLite database."""

    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def fetch_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: str,
) -> list[sqlite3.Row]:
    """Return selected rows from a settings table."""

    return conn.execute(f"SELECT {columns} FROM {table} ORDER BY id").fetchall()


def require_singleton(
    conn: sqlite3.Connection,
    table: str,
    columns: str,
) -> sqlite3.Row:
    """Return the single settings row or fail rather than updating ambiguously."""

    rows = fetch_rows(conn, table, columns)
    if len(rows) != 1:
        raise RuntimeError(
            f"Expected exactly one row in {table}; found {len(rows)}."
        )
    return rows[0]


def main() -> int:
    """Update persisted media paths for the local demo database."""

    args = parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (repo_root / db_path).resolve()

    users_dir = (repo_root / "uploads" / "users").resolve()
    listings_dir = (repo_root / "uploads" / "listings").resolve()

    if not db_path.is_file():
        print(f"ERROR: SQLite database not found: {db_path}", file=sys.stderr)
        return 2

    if not users_dir.is_dir():
        print(f"ERROR: Profile image directory not found: {users_dir}", file=sys.stderr)
        return 2

    if not listings_dir.is_dir():
        print(
            f"ERROR: AutoGrid360 listing image directory not found: {listings_dir}",
            file=sys.stderr,
        )
        return 2

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = db_path.with_name(f"{db_path.name}.{stamp}.bak")
        shutil.copy2(db_path, backup_path)
        print(f"Backup: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        if not table_exists(conn, "env_settings"):
            raise RuntimeError("Missing env_settings table.")

        if not table_exists(conn, "plugin_autogrid360_settings"):
            raise RuntimeError("Missing plugin_autogrid360_settings table.")

        env = require_singleton(
            conn,
            "env_settings",
            "id, users_stored_path",
        )

        autogrid_rows = fetch_rows(
            conn,
            "plugin_autogrid360_settings",
            "id, listing_images_path",
        )
        if len(autogrid_rows) > 1:
            raise RuntimeError(
                "Expected at most one row in plugin_autogrid360_settings; "
                f"found {len(autogrid_rows)}."
            )

        new_users_path = "uploads/users"
        new_listings_path = "uploads/listings"

        print()
        print("Current values:")
        print(f"  users_stored_path:   {env['users_stored_path']}")
        if autogrid_rows:
            print(
                "  listing_images_path: "
                f"{autogrid_rows[0]['listing_images_path']}"
            )
        else:
            print(
                "  listing_images_path: <no persisted AutoGrid360 settings row>"
            )

        print()
        print("New values:")
        print(f"  users_stored_path:   {new_users_path}")
        print(f"  listing_images_path: {new_listings_path}")
        print()

        conn.execute("BEGIN IMMEDIATE")

        core_result = conn.execute(
            """
            UPDATE env_settings
            SET users_stored_path = ?
            WHERE id = ?
            """,
            (new_users_path, env["id"]),
        )
        if core_result.rowcount != 1:
            raise RuntimeError(
                f"Expected to update one env_settings row; updated {core_result.rowcount}."
            )

        if autogrid_rows:
            plugin_result = conn.execute(
                """
                UPDATE plugin_autogrid360_settings
                SET listing_images_path = ?
                WHERE id = ?
                """,
                (new_listings_path, autogrid_rows[0]["id"]),
            )
            if plugin_result.rowcount != 1:
                raise RuntimeError(
                    "Expected to update one plugin_autogrid360_settings row; "
                    f"updated {plugin_result.rowcount}."
                )
        else:
            # The plugin intentionally has no persisted settings row until its
            # settings are first saved. Creating id=1 here is the normal
            # singleton shape; every omitted column receives its model-defined
            # database server default.
            conn.execute(
                """
                INSERT INTO plugin_autogrid360_settings (
                    id,
                    listing_images_path
                )
                VALUES (?, ?)
                """,
                (AUTOGRID360_SETTINGS_ID, new_listings_path),
            )
            print(
                "Created AutoGrid360 settings singleton id=1 using "
                "database defaults for all other settings."
            )

        conn.commit()

        verify_env = require_singleton(
            conn,
            "env_settings",
            "id, users_stored_path",
        )
        verify_autogrid = require_singleton(
            conn,
            "plugin_autogrid360_settings",
            "id, listing_images_path",
        )

        print()
        print("Updated successfully:")
        print(f"  users_stored_path:   {verify_env['users_stored_path']}")
        print(f"  listing_images_path: {verify_autogrid['listing_images_path']}")
        print()
        print(f"Profile images: {users_dir}")
        print(f"Listing images: {listings_dir}")

        return 0

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())