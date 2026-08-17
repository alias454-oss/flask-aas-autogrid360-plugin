# app/plugins/autogrid360/plugin.py
"""Flask-AAS Plugin API v1 adapter for AutoGrid360."""

from pathlib import Path
from typing import Any

from app.plugins import ApplicationPlugin, PluginConfiguration, load_plugin_manifest
from app.plugins.navigation import register_plugin_navigation
from app.plugins.autogrid360 import models as autogrid360_models  # noqa: F401 - register model metadata
from app.plugins.autogrid360.cli import cli as autogrid360_cli
from app.plugins.autogrid360.routes import BLUEPRINTS
from app.plugins.autogrid360.services.data import admin_datasets, run_admin_dataset_action


class AutoGrid360Plugin(ApplicationPlugin):
    """AutoGrid360 application plugin."""

    manifest = load_plugin_manifest(Path(__file__).with_name("plugin.toml"))
    plugin_id = manifest.plugin_id
    name = manifest.name
    version = manifest.version
    api_version = manifest.api_version

    def validate_config(self) -> PluginConfiguration:
        """The initial scaffold has no required runtime configuration."""

        return PluginConfiguration(configured=True)

    def clear_secrets(self) -> None:
        """The initial scaffold owns no persisted secrets."""

        return None

    def get_cli(self):
        """Return the AutoGrid360-owned CLI surface."""

        return autogrid360_cli

    def get_admin_datasets(self):
        """Expose optional packaged application datasets to host administrators."""

        return admin_datasets()

    def run_admin_dataset_action(self, dataset_key):
        """Run one host-authorized AutoGrid360 dataset action."""

        return run_admin_dataset_action(dataset_key)

    def register(self, app: Any) -> None:
        """Register AutoGrid360 Flask surfaces with the host application."""

        for blueprint in BLUEPRINTS:
            app.register_blueprint(blueprint)

        register_plugin_navigation(
            app,
            plugin_id=self.plugin_id,
            label=self.manifest.navigation_label or self.name,
            endpoint="autogrid360.index",
        )

        @app.context_processor
        def autogrid360_sidebar_context():
            """Expose AutoGrid360 public navigation to the shared host sidebar."""

            return {
                "sidebar_extra_template": f"{self.plugin_id}/includes/public_nav.html",
                "autogrid360_navigation_label": self.manifest.navigation_label or self.name,
            }


plugin = AutoGrid360Plugin()
