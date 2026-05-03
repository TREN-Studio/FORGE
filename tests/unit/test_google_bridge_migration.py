from __future__ import annotations

import unittest
from pathlib import Path

from site_backend.forge_portal.api import (
    DEFAULT_GOOGLE_BRIDGE_URL,
    DEFAULT_GOOGLE_CLIENT_ID,
    PortalConfig,
    _google_bridge_location,
)


class GoogleBridgeMigrationTests(unittest.TestCase):
    def test_legacy_postgenius_bridge_is_replaced(self) -> None:
        config = PortalConfig(
            state_root=Path("."),
            google_client_id="google-client",
            google_bridge_url="https://postgeniuspro.com/forge-google-bridge/",
        )

        self.assertEqual(config.google_bridge_url, DEFAULT_GOOGLE_BRIDGE_URL)
        self.assertNotIn("postgeniuspro.com", _google_bridge_location(config, "state-token"))

    def test_default_bridge_is_trenstudio(self) -> None:
        config = PortalConfig(state_root=Path("."), google_client_id="google-client")

        self.assertEqual(config.google_bridge_url, "https://www.trenstudio.com/forge-auth/google-bridge/")
        self.assertEqual(config.google_oauth_mode, "bridge_id_token")

    def test_legacy_google_client_id_is_replaced(self) -> None:
        config = PortalConfig(
            state_root=Path("."),
            google_client_id="877623556231-20c2f7ts5u9kolvsmr4nd949q0tf2vhv.apps.googleusercontent.com",
        )

        self.assertEqual(config.google_client_id, DEFAULT_GOOGLE_CLIENT_ID)

    def test_new_bridge_file_has_no_legacy_domain(self) -> None:
        bridge_path = Path(__file__).resolve().parents[2] / "site_external" / "forge_auth" / "google-bridge" / "index.php"
        source = bridge_path.read_text(encoding="utf-8")

        self.assertIn("TREN Studio Auth Bridge", source)
        self.assertIn("https://www.trenstudio.com/FORGE/portal/api/index.php/auth/google/bridge-complete", source)
        self.assertNotIn("postgeniuspro.com", source)
        self.assertNotIn("Postgenius", source)


if __name__ == "__main__":
    unittest.main()
