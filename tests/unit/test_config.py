"""Module containing unit test fixtures."""

import unittest

from flaretrace.config import runtime_options


class RuntimeOptionsTests(unittest.TestCase):
    """Exercise runtime options tests with isolated application resources."""

    def test_local_defaults(self):
        """Verify local defaults."""
        options = runtime_options({})
        self.assertEqual(options["PUBLIC_BASE_URL"], "")
        self.assertEqual(options["SITE_ADDRESS"], "http://localhost")
        self.assertFalse(options["AUTO_TLS"])
        self.assertFalse(options["TRUST_PROXY"])

    def test_domain_http_and_automatic_tls(self):
        """Verify domain http and automatic tls."""
        plain = runtime_options({"DOMAIN": "Trace.Example.com."})
        self.assertEqual(plain["PUBLIC_BASE_URL"], "http://trace.example.com")
        self.assertTrue(plain["TRUST_PROXY"])
        secure = runtime_options({"DOMAIN": "trace.example.com", "AUTO_TLS": "1"})
        self.assertEqual(secure["PUBLIC_BASE_URL"], "https://trace.example.com")
        self.assertEqual(secure["SITE_ADDRESS"], "https://trace.example.com")

    def test_invalid_domain_and_tls_configuration(self):
        """Verify invalid domain and tls configuration."""
        for domain in (
            "https://example.com",
            "example.com/path",
            "example.com:443",
            "-bad.example",
            "bad..example",
            "example.com\n}",
        ):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                runtime_options({"DOMAIN": domain})
        for domain in ("", "localhost", "127.0.0.1"):
            with self.subTest(tls_domain=domain), self.assertRaises(ValueError):
                runtime_options({"DOMAIN": domain, "AUTO_TLS": "1"})
        with self.assertRaises(ValueError):
            runtime_options({"AUTO_TLS": "maybe"})

    def test_published_proxy_ports_are_used_in_shared_links(self):
        """Verify published proxy ports are used in shared links."""
        plain = runtime_options({"DOMAIN": "trace.example.com", "HTTP_PORT": "8080"})
        self.assertEqual(plain["PUBLIC_BASE_URL"], "http://trace.example.com:8080")
        secure = runtime_options(
            {"DOMAIN": "trace.example.com", "AUTO_TLS": "1", "HTTPS_PORT": "8443"}
        )
        self.assertEqual(secure["PUBLIC_BASE_URL"], "https://trace.example.com:8443")
        self.assertEqual(secure["SITE_ADDRESS"], "https://trace.example.com:8443")
        default = runtime_options({"DOMAIN": "trace.example.com", "HTTP_PORT": "80"})
        self.assertEqual(default["PUBLIC_BASE_URL"], "http://trace.example.com")

    def test_public_origin_and_legacy_ports_override_published_port_inference(self):
        """Verify public origin and legacy ports override published port inference."""
        explicit = runtime_options(
            {
                "DOMAIN": "trace.example.com",
                "AUTO_TLS": "1",
                "HTTPS_PORT": "8443",
                "PUBLIC_BASE_URL": "https://trace.example.com",
            }
        )
        self.assertEqual(explicit["PUBLIC_BASE_URL"], "https://trace.example.com")
        legacy = runtime_options(
            {"SITE_ADDRESS": "https://trace.example.com:9443", "HTTPS_PORT": "8443"}
        )
        self.assertEqual(legacy["PUBLIC_BASE_URL"], "https://trace.example.com:9443")

    def test_invalid_published_port(self):
        """Verify invalid published port."""
        for port in ("0", "65536", "-1", "8080:80", "no", ""):
            with self.subTest(port=port), self.assertRaises(ValueError):
                runtime_options({"DOMAIN": "trace.example.com", "HTTP_PORT": port})

    def test_legacy_site_address_and_explicit_override(self):
        """Verify legacy site address and explicit override."""
        legacy = runtime_options({"SITE_ADDRESS": "trace.example.com"})
        self.assertTrue(legacy["AUTO_TLS"])
        self.assertEqual(legacy["PUBLIC_BASE_URL"], "https://trace.example.com")
        plain = runtime_options({"SITE_ADDRESS": "http://trace.example.com:8080"})
        self.assertEqual(plain["PUBLIC_BASE_URL"], "http://trace.example.com:8080")
        self.assertFalse(plain["AUTO_TLS"])
        forced = runtime_options({"SITE_ADDRESS": "trace.example.com", "AUTO_TLS": "0"})
        self.assertEqual(forced["SITE_ADDRESS"], "http://trace.example.com")
        external = runtime_options(
            {
                "DOMAIN": "trace.example.com",
                "PUBLIC_BASE_URL": "https://external.example.com/",
            }
        )
        self.assertEqual(external["PUBLIC_BASE_URL"], "https://external.example.com")

    def test_proxy_trust_respects_binding_and_override(self):
        """Verify proxy trust respects binding and override."""
        self.assertFalse(
            runtime_options({"DOMAIN": "trace.example.com", "BIND_ADDRESS": "0.0.0.0"})[
                "TRUST_PROXY"
            ]
        )
        self.assertFalse(
            runtime_options({"DOMAIN": "trace.example.com", "TRUST_PROXY": "0"})[
                "TRUST_PROXY"
            ]
        )
        self.assertTrue(runtime_options({"TRUST_PROXY": "1"})["TRUST_PROXY"])

    def test_invalid_public_origin_and_boolean(self):
        """Verify invalid public origin and boolean."""
        for value in (
            "javascript:alert(1)",
            "https://user:password@example.com",
            "https://example.com/path",
            "https://example.com/?token=secret",
            "https://example.com:bad",
        ):
            with self.subTest(base=value), self.assertRaises(ValueError):
                runtime_options({"PUBLIC_BASE_URL": value})
        with self.assertRaises(ValueError):
            runtime_options(
                {
                    "DOMAIN": "trace.example.com",
                    "AUTO_TLS": "1",
                    "PUBLIC_BASE_URL": "http://trace.example.com",
                }
            )
        with self.assertRaises(ValueError):
            runtime_options({"TRUST_PROXY": "sometimes"})


if __name__ == "__main__":
    unittest.main()
