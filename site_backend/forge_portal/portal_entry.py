from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) >= 3 else SCRIPT_ROOT
for candidate in (SCRIPT_ROOT, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from site_backend.forge_portal.api import PortalConfig, handle_request
    from site_backend.forge_portal.store import PortalStateStore
except ModuleNotFoundError:
    from api import PortalConfig, handle_request
    from store import PortalStateStore


def main() -> int:
    root = Path(__file__).resolve().parent
    state_root = Path(os.environ.get("FORGE_PORTAL_STATE_ROOT", root / "state")).resolve()
    method = os.environ.get("FORGE_PORTAL_REQUEST_METHOD", os.environ.get("REQUEST_METHOD", "GET"))
    path = os.environ.get("FORGE_PORTAL_REQUEST_PATH", "/")
    query_string = os.environ.get("QUERY_STRING", "")
    headers = {
        key[5:].replace("_", "-").lower(): value
        for key, value in os.environ.items()
        if key.startswith("HTTP_")
    }
    if "CONTENT_TYPE" in os.environ:
        headers["content-type"] = os.environ["CONTENT_TYPE"]

    raw_body = sys.stdin.buffer.read()
    store = PortalStateStore(state_root)
    try:
        response = handle_request(
            PortalConfig(
                state_root=state_root,
                manager_email=os.environ.get("FORGE_PORTAL_MANAGER_EMAIL", "larbilife@gmail.com"),
                cookie_path=os.environ.get("FORGE_PORTAL_COOKIE_PATH", "/FORGE/portal"),
                auth_session_days=int(os.environ.get("FORGE_PORTAL_SESSION_DAYS", "30")),
                app_base_url=os.environ.get("FORGE_PORTAL_APP_BASE_URL", "https://www.trenstudio.com/FORGE/portal"),
                debug_auth_tokens=os.environ.get("FORGE_PORTAL_DEBUG_AUTH_TOKENS", "0") == "1",
                google_client_id=os.environ.get("FORGE_GOOGLE_CLIENT_ID", ""),
                google_client_secret=os.environ.get("FORGE_GOOGLE_CLIENT_SECRET", ""),
                google_authorize_url=os.environ.get("FORGE_GOOGLE_AUTHORIZE_URL", "https://accounts.google.com/o/oauth2/v2/auth"),
                google_token_url=os.environ.get("FORGE_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token"),
                google_userinfo_url=os.environ.get("FORGE_GOOGLE_USERINFO_URL", "https://openidconnect.googleapis.com/v1/userinfo"),
            ),
            store,
            method=method.upper(),
            path=path,
            query_string=query_string,
            headers=headers,
            body=raw_body,
        )
    finally:
        store.close()

    status, response_headers, body = response.to_http()
    envelope = {
        "status": status,
        "headers": response_headers,
        "body": body.decode("utf-8"),
    }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
