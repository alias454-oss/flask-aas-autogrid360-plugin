# app/plugins/autogrid360/tests/test_templates.py

import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = PLUGIN_ROOT / "templates" / "autogrid360"


class AutoGrid360TemplateContractTests(unittest.TestCase):
    def test_templates_use_shared_bases_and_print_is_intentionally_standalone(self):
        standalone = Path("listings/print.html")

        for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
            relative = template.relative_to(TEMPLATE_ROOT)
            if relative.parts[0] == "includes" or relative == standalone:
                continue

            source = template.read_text(encoding="utf-8")
            with self.subTest(template=str(relative)):
                if relative.parts[0] == "admin" and relative.name != "base.html":
                    self.assertIn('{% extends "autogrid360/admin/base.html" %}', source)
                    self.assertNotIn('class="autogrid360-admin-nav"', source)
                else:
                    self.assertIn('{% extends "plugins/base.html" %}', source)
                    self.assertIn("{% block plugin_styles %}", source)
                    self.assertIn("style.css", source)

        print_source = (TEMPLATE_ROOT / standalone).read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", print_source)
        self.assertNotIn('{% extends "plugins/base.html" %}', print_source)
        self.assertIn("style.css", print_source)
        self.assertIn('class="autogrid360-print-body"', print_source)


    def test_user_facing_pages_attach_shared_autogrid360_sidebar(self):
        user_facing_pages = {
            Path("account/profile.html"),
            Path("account/transfer.html"),
            Path("index.html"),
            Path("listing.html"),
            Path("listings/contact.html"),
            Path("listings/create.html"),
            Path("listings/edit.html"),
            Path("listings/index.html"),
            Path("listings/public.html"),
            Path("listings/search.html"),
            Path("sellers/public.html"),
            Path("tools/payment.html"),
        }

        for relative in sorted(user_facing_pages):
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(template=str(relative)):
                self.assertIn(
                    '{% set sidebar_extra_template = "autogrid360/includes/public_nav.html" %}',
                    source,
                )
                self.assertIn(
                    '{% include "autogrid360/includes/public_nav_fallback.html" %}',
                    source,
                )

        nav = (TEMPLATE_ROOT / "includes" / "public_nav.html").read_text(
            encoding="utf-8"
        )
        seller_links = [
            ("autogrid360_listings.mine", "My Listings"),
            ("autogrid360_listings.create", "Create Listing"),
            ("autogrid360_account.profile", "Seller Profile"),
        ]
        marketplace_links = [
            ("autogrid360.index", "Inventory"),
            ("autogrid360.search", "Advanced Search"),
            ("autogrid360_tools.payment_calculator", "Payment Calculator"),
        ]
        self.assertIn("<h3>My {{ autogrid360_navigation_label }}</h3>", nav)
        self.assertIn("<h3>{{ autogrid360_navigation_label }}</h3>", nav)
        self.assertIn("{% if current_user.is_authenticated %}", nav)
        self.assertEqual(
            nav.count("<li><a href="),
            len(seller_links) + len(marketplace_links),
        )

        seller_heading = nav.index("<h3>My {{ autogrid360_navigation_label }}</h3>")
        marketplace_heading = nav.index("<h3>{{ autogrid360_navigation_label }}</h3>")
        self.assertLess(seller_heading, marketplace_heading)

        for endpoint, label in seller_links + marketplace_links:
            with self.subTest(endpoint=endpoint):
                self.assertIn(f"url_for('{endpoint}')", nav)
                self.assertIn(f">{label}</a></li>", nav)

        for endpoint, _label in seller_links:
            self.assertLess(nav.index(f"url_for('{endpoint}')"), marketplace_heading)
        for endpoint, _label in marketplace_links:
            self.assertGreater(nav.index(f"url_for('{endpoint}')"), marketplace_heading)

        self.assertIn(
            "{% if current_user.is_authenticated and current_user.is_admin %}", nav
        )
        self.assertIn(
            '{% include "autogrid360/includes/admin_nav.html" %}',
            nav,
        )
        self.assertNotIn(">{{ autogrid360_navigation_label }} Admin</a></li>", nav)

    def test_my_listings_keeps_primary_actions_in_navigation_and_inventory_tools_below_list(self):
        source = (TEMPLATE_ROOT / "listings" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("url_for('autogrid360_listings.create')", source)
        self.assertNotIn("url_for('autogrid360_account.profile')", source)
        self.assertIn('<section class="autogrid360-inventory-tools"', source)
        self.assertIn('<h2 id="inventory-tools-heading">Inventory Tools</h2>', source)
        self.assertIn("url_for('autogrid360_account.inventory_transfer')", source)
        self.assertGreater(
            source.index("autogrid360-inventory-tools"),
            source.index("autogrid360-listing-list"),
        )

    def test_public_navigation_fallback_only_renders_without_host_sidebar(self):
        fallback = (
            TEMPLATE_ROOT / "includes" / "public_nav_fallback.html"
        ).read_text(encoding="utf-8")

        self.assertIn("sidebar_position | default('right')", fallback)
        self.assertIn("not in ['left', 'right']", fallback)
        self.assertIn('class="autogrid360-inline-nav"', fallback)
        self.assertIn(
            '{% include "autogrid360/includes/public_nav.html" %}',
            fallback,
        )


    def test_data_entry_forms_follow_shared_fieldset_pattern(self):
        data_entry_templates = {
            Path("account/profile.html"),
            Path("account/transfer.html"),
            Path("admin/listing_create.html"),
            Path("admin/listings.html"),
            Path("admin/reference/edit.html"),
            Path("admin/reference/model_edit.html"),
            Path("admin/reference/models.html"),
            Path("admin/reference/values.html"),
            Path("admin/seller_profile.html"),
            Path("admin/sellers.html"),
            Path("admin/settings.html"),
            Path("listings/search.html"),
            Path("listings/contact.html"),
            Path("listings/create.html"),
            Path("listings/edit.html"),
            Path("tools/payment.html"),
        }

        for relative in sorted(data_entry_templates):
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(template=str(relative)):
                self.assertIn("<fieldset", source)
                self.assertIn("<legend", source)
                self.assertIn("form-group", source)
                self.assertNotIn("autogrid360-error", source)
                self.assertNotIn("autogrid360-form-help", source)


    def test_shared_admin_navigation_uses_host_sidebar_extension(self):
        admin_nav = (TEMPLATE_ROOT / "includes" / "admin_nav.html").read_text(
            encoding="utf-8"
        )
        public_nav = (TEMPLATE_ROOT / "includes" / "public_nav.html").read_text(
            encoding="utf-8"
        )
        expected_links = [
            ("autogrid360_admin.index", "Dashboard"),
            ("autogrid360_admin.inventory", "Inventory"),
            ("autogrid360_admin.pending", "Pending Review"),
            ("autogrid360_admin.sellers", "Sellers"),
            ("autogrid360_admin.create_listing", "Create Seller Listing"),
            ("autogrid360_reference.index", "Reference Data"),
            ("autogrid360_admin.backup_restore", "Backup / Restore"),
            ("autogrid360_settings.settings", "Settings"),
        ]

        self.assertIn("<h3>{{ autogrid360_navigation_label }} Admin</h3>", admin_nav)
        self.assertIn("<ul>", admin_nav)
        self.assertNotIn("<nav", admin_nav)
        self.assertNotIn('class="sidebar-content"', admin_nav)
        self.assertEqual(admin_nav.count("<li><a href="), len(expected_links))
        for endpoint, label in expected_links:
            with self.subTest(endpoint=endpoint):
                self.assertIn(f"url_for('{endpoint}')", admin_nav)
                self.assertIn(f">{label}</a></li>", admin_nav)

        self.assertIn(
            '{% include "autogrid360/includes/admin_nav.html" %}',
            public_nav,
        )
        self.assertIn(
            "{% if current_user.is_authenticated and current_user.is_admin %}",
            public_nav,
        )
        self.assertNotIn("autogrid360_admin_context", public_nav)

        admin_base = (TEMPLATE_ROOT / "admin" / "base.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('id="autogrid360-admin-sidebar"', admin_base)
        self.assertNotIn('class="sidebar"', admin_base)
        self.assertNotIn(
            '{% include "autogrid360/includes/admin_nav.html" %}',
            admin_base,
        )
        self.assertIn('id="autogrid360-admin-content"', admin_base)


    def test_backup_restore_and_demo_tooling_are_explicitly_bounded(self):
        admin_transfer = (TEMPLATE_ROOT / "admin" / "transfer.html").read_text(
            encoding="utf-8"
        )
        seller_transfer = (TEMPLATE_ROOT / "account" / "transfer.html").read_text(
            encoding="utf-8"
        )
        demo_script = (PLUGIN_ROOT / "scripts" / "demo_listing_generator.py").read_text(encoding="utf-8")
        transfer_forms = (PLUGIN_ROOT / "forms" / "transfer.py").read_text(
            encoding="utf-8"
        )
        my_listings = (TEMPLATE_ROOT / "listings" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("Download Full Inventory Backup", admin_transfer)
        self.assertIn("source=destination", admin_transfer)
        self.assertIn("seller_import_allowed", seller_transfer)
        self.assertIn("form.as_draft.label.text", admin_transfer)
        self.assertIn("Reset restored listings to Draft", transfer_forms)
        self.assertIn('BUNDLE_FORMAT = "autogrid360-inventory"', demo_script)
        self.assertIn('BUNDLE_VERSION = 1', demo_script)
        self.assertIn(">Backup / Restore</a>", my_listings)
        self.assertNotIn(">Import / Export</a>", my_listings)
        self.assertNotIn("requests.", demo_script)
        self.assertNotIn("urllib.request", demo_script)

    def test_autogrid360_uses_host_primitives_for_generic_presentation(self):
        stylesheet = (PLUGIN_ROOT / "static" / "style.css").read_text(encoding="utf-8")
        host_owned_selectors = [
            ".content-page",
            ".form-control",
            ".form-help",
            ".form-error",
            ".actions",
            ".panel",
            ".table-wrap",
            ".data-table",
            ".pagination",
        ]

        for selector in host_owned_selectors:
            with self.subTest(selector=selector):
                self.assertNotIn(selector, stylesheet)

        removed_autogrid360_primitives = [
            ".autogrid360-admin-table",
            ".autogrid360-admin-table-wrap",
            ".autogrid360-pagination",
        ]
        for selector in removed_autogrid360_primitives:
            with self.subTest(selector=selector):
                self.assertNotIn(selector, stylesheet)

        table_templates = [
            Path("admin/listings.html"),
            Path("admin/reference/models.html"),
            Path("admin/reference/values.html"),
            Path("admin/sellers.html"),
            Path("tools/payment.html"),
        ]
        for relative in table_templates:
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(template=str(relative)):
                self.assertIn('class="table-wrap"', source)
                self.assertIn('class="data-table"', source)

    def test_key_frontend_surfaces_keep_required_functional_anchors(self):
        expected = {
            Path("admin/base.html"): ['id="autogrid360-admin-content"'],
            Path("index.html"): ['autogrid360/includes/inventory_list.html'],
            Path("listings/search.html"): ['id="inventory-filters"', "filename='search.js'"],
            Path("includes/inventory_list.html"): [
                'id="inventory-results"',
                'id="inventory-results-controls"',
            ],
            Path("listings/create.html"): [
                'id="listing-editor"',
                'autogrid360/includes/listing_vehicle_fields.html',
                "filename='editor.js'",
            ],
            Path("listings/edit.html"): [
                'id="listing-editor"',
                'autogrid360/includes/listing_image_editor.html',
            ],
            Path("includes/listing_image_editor.html"): [
                'id="image-management-heading"',
                "autogrid360_images.upload",
                "autogrid360_images.primary",
                "autogrid360_images.move",
                "autogrid360_images.delete",
            ],
            Path("listings/index.html"): ['id="my-listings"'],
            Path("listing.html"): [
                'id="listing-management"',
                'id="listing-actions-heading"',
                'autogrid360/includes/listing_gallery.html',
                "filename='gallery.js'",
            ],
            Path("listings/public.html"): [
                'id="listing-detail"',
                'id="listing-gallery"',
                'id="seller-contact"',
                'autogrid360/includes/listing_gallery.html',
            ],
            Path("includes/listing_gallery.html"): [
                'data-autogrid360-gallery',
                'data-autogrid360-gallery-thumbnail',
                'data-autogrid360-image-lightbox',
            ],
            Path("tools/payment.html"): ['id="payment-calculator"'],
        }

        for relative, anchors in expected.items():
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            for anchor in anchors:
                with self.subTest(template=str(relative), anchor=anchor):
                    self.assertIn(anchor, source)

        public_listing = (TEMPLATE_ROOT / "listings" / "public.html").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            public_listing.index('autogrid360/includes/listing_gallery.html'),
            public_listing.index('id="listing-details-heading"'),
        )
        search = (TEMPLATE_ROOT / "listings" / "search.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('autogrid360/includes/inventory_list.html', search)


    def test_advanced_search_uses_selectable_inventory_facets(self):
        source = (TEMPLATE_ROOT / "listings" / "search.html").read_text(
            encoding="utf-8"
        )

        for field in (
            "make",
            "model",
            "min_year",
            "max_year",
            "vehicle_type",
            "drivetrain",
            "condition",
            "transmission",
            "seller",
            "country_code",
            "zone_code",
        ):
            with self.subTest(field=field):
                self.assertIn(f'<select class="form-control" id="{field}"', source)

        self.assertNotIn('id="condition" name="condition" type="text"', source)
        self.assertNotIn(
            'id="transmission" name="transmission" type="text"',
            source,
        )
        self.assertNotIn(
            "js/location_fields.js",
            source,
        )
        self.assertIn("filename='search.js'", source)

    def test_location_js_rejects_cross_origin_lookup_endpoints(self):
        script = (PLUGIN_ROOT / "static" / "location.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function sameOriginUrl(value)", script)
        self.assertIn("new URL(value, window.location.origin)", script)
        self.assertIn("url.origin !== window.location.origin", script)
        self.assertIn("const url = sameOriginUrl(endpoint);", script)
        self.assertIn("const url = sameOriginUrl(lookupUrl);", script)
        self.assertIn('credentials: "same-origin"', script)

    def test_listing_cards_expose_stable_theme_hooks(self):
        card_templates = [
            Path("listings/index.html"),
            Path("sellers/public.html"),
        ]
        hooks = [
            'autogrid360-listing-card',
            'class="autogrid360-listing-card__media"',
            'class="autogrid360-listing-card__body"',
            'autogrid360-listing-card__title',
            'autogrid360-listing-card__meta',
            'autogrid360-listing-card__price',
        ]

        for relative in card_templates:
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            for hook in hooks:
                with self.subTest(template=str(relative), hook=hook):
                    self.assertIn(hook, source)

    def test_sale_pending_and_sold_use_shared_upper_left_image_ribbon(self):
        ribbon = (
            TEMPLATE_ROOT / "includes" / "listing_status_ribbon.html"
        ).read_text(encoding="utf-8")
        stylesheet = (PLUGIN_ROOT / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn("listing.status == 'sale_pending'", ribbon)
        self.assertIn(">Pending</span></span>", ribbon)
        self.assertIn("listing.status == 'sold'", ribbon)
        self.assertIn(">Sold</span></span>", ribbon)
        self.assertIn('aria-hidden="true"', ribbon)

        for relative in (
            Path("includes/inventory_list.html"),
            Path("listings/index.html"),
            Path("sellers/public.html"),
        ):
            source = (TEMPLATE_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(template=str(relative)):
                self.assertIn('autogrid360/includes/listing_status_ribbon.html', source)
                self.assertIn("autogrid360-status-ribbon-frame", source)

        gallery = (TEMPLATE_ROOT / "includes" / "listing_gallery.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            gallery.count('autogrid360/includes/listing_status_ribbon.html'), 2
        )
        self.assertIn("autogrid360-status-ribbon-frame", gallery)

        ribbon_rule = stylesheet.split(".autogrid360-status-ribbon {", 1)[1].split("}", 1)[0]
        self.assertIn("left: 0;", ribbon_rule)
        self.assertIn("top: 0;", ribbon_rule)
        self.assertIn("rotate(-45deg)", ribbon_rule)
        self.assertIn(".autogrid360-status-ribbon--pending {", stylesheet)
        self.assertIn(".autogrid360-status-ribbon--sold {", stylesheet)

        image_editor = (
            TEMPLATE_ROOT / "includes" / "listing_image_editor.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("listing_status_ribbon.html", image_editor)


    def test_listing_gallery_progressively_enhances_thumbnail_selection_and_lightbox(self):
        gallery = (TEMPLATE_ROOT / "includes" / "listing_gallery.html").read_text(
            encoding="utf-8"
        )
        script = (PLUGIN_ROOT / "static" / "gallery.js").read_text(encoding="utf-8")
        stylesheet = (PLUGIN_ROOT / "static" / "style.css").read_text(encoding="utf-8")

        for anchor in (
            'data-autogrid360-gallery-main-link',
            'data-autogrid360-gallery-thumbnail',
            'data-display-url=',
            '<dialog class="autogrid360-image-lightbox"',
            'aria-label="Close image"',
        ):
            self.assertIn(anchor, gallery)
        self.assertEqual(
            gallery.count('autogrid360/includes/listing_status_ribbon.html'), 2
        )

        self.assertIn('setAttribute("aria-current", "true")', script)
        self.assertIn('lightbox.showModal();', script)
        self.assertIn('lightbox.close();', script)
        self.assertNotIn("mouseover", script)
        self.assertNotIn("mouseenter", script)
        self.assertIn('.autogrid360-image-lightbox::backdrop {', stylesheet)
        self.assertIn('.autogrid360-image-lightbox__close {', stylesheet)


    def test_templates_do_not_leak_host_theme_or_branding_implementation(self):
        for template in sorted(TEMPLATE_ROOT.rglob("*.html")):
            source = template.read_text(encoding="utf-8")
            with self.subTest(template=str(template.relative_to(TEMPLATE_ROOT))):
                self.assertNotIn("themes/default", source)
                self.assertNotIn("static/themes/", source)
                self.assertNotIn("Flask-AAS", source)


if __name__ == "__main__":
    unittest.main()
