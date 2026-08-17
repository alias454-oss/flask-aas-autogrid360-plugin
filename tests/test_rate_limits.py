# app/plugins/autogrid360/tests/test_rate_limits.py

import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ROUTES_ROOT = PLUGIN_ROOT / "routes"


def _source(name: str) -> str:
    return (ROUTES_ROOT / name).read_text(encoding="utf-8")


def _assert_limit(testcase, source: str, function_name: str, limit: str, *, post_only=False):
    methods = r', methods=\["POST"\]' if post_only else ""
    pattern = (
        rf'@limiter\.limit\("{re.escape(limit)}"{methods}, key_func=get_client_ip\)'
        rf'\s+@login_required\s+def {re.escape(function_name)}\b'
    )
    testcase.assertRegex(source, pattern)


class AutoGrid360RateLimitContractTests(unittest.TestCase):
    def test_route_rate_limit_policy(self):
        account = _source("account.py")
        listings = _source("listings.py")
        images = _source("images.py")
        admin = _source("admin.py")
        reference = _source("reference.py")
        settings = _source("settings.py")
        public = _source("public.py")

        _assert_limit(self, account, "profile", "10 per minute", post_only=True)
        _assert_limit(self, account, "inventory_export", "10 per minute")
        _assert_limit(self, account, "inventory_import", "3 per minute")
        _assert_limit(self, listings, "create", "10 per minute", post_only=True)
        _assert_limit(self, listings, "edit", "10 per minute", post_only=True)
        for function_name in (
            "submit", "approve", "relist", "mark_sale_pending", "make_available",
            "mark_sold", "expire", "remove", "admin_status", "delete",
        ):
            with self.subTest(policy="listing mutation", function=function_name):
                _assert_limit(self, listings, function_name, "10 per minute")

        for function_name, limit in (
            ("upload", "10 per minute"),
            ("primary", "30 per minute"),
            ("move", "30 per minute"),
            ("delete", "20 per minute"),
        ):
            with self.subTest(policy="image mutation", function=function_name):
                _assert_limit(self, images, function_name, limit)

        _assert_limit(self, admin, "create_listing", "10 per minute", post_only=True)
        _assert_limit(self, admin, "assign_seller", "10 per minute")
        _assert_limit(self, admin, "seller_profile", "10 per minute", post_only=True)
        for function_name in ("values", "edit", "models", "edit_model"):
            with self.subTest(policy="reference edit", function=function_name):
                _assert_limit(self, reference, function_name, "10 per minute", post_only=True)
        for function_name in ("toggle", "toggle_model"):
            with self.subTest(policy="reference toggle", function=function_name):
                _assert_limit(self, reference, function_name, "10 per minute")
        _assert_limit(self, settings, "settings", "10 per minute", post_only=True)
        _assert_limit(self, settings, "expire_due", "3 per minute")

        for function_name in (
            "index", "search", "inventory_fancy", "seller_detail",
            "listing_public", "printable_listing",
        ):
            with self.subTest(policy="public browse", function=function_name):
                pattern = (
                    r'@limiter\.limit\("120 per minute", key_func=get_client_ip\)'
                    rf'\s+def {re.escape(function_name)}\b'
                )
                self.assertRegex(public, pattern)
        self.assertIn(
            '@limiter.limit("120 per minute", key_func=get_client_ip)\ndef geo_lookup',
            public,
        )
        self.assertIn(
            '@limiter.limit("10 per hour", methods=["POST"], key_func=get_client_ip)\ndef contact_seller',
            public,
        )
