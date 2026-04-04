from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from urllib import parse, request

from forge.config.settings import OperatorSettings
from forge.tools.credentials import CredentialResolver


@dataclass(slots=True)
class WordPressTarget:
    site_url: str
    resource_type: str
    status: str
    slug: str
    resource_id: str


class WordPressPublisher:
    """Publish mission outputs to WordPress through the WP REST API."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._resolver = CredentialResolver(settings)

    def publish(
        self,
        *,
        objective: str,
        content: str,
        site_url: str = "",
        title: str = "",
        slug: str = "",
        status: str = "",
        resource_type: str = "",
        resource_id: str = "",
    ) -> dict:
        target = self._resolve_target(
            objective=objective,
            site_url=site_url,
            slug=slug,
            status=status,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        username = self._resolver.resolve(
            label="WordPress username",
            env_names=["FORGE_WORDPRESS_USERNAME", "WORDPRESS_USERNAME", "WP_USERNAME"],
            soul_keys=["FORGE_WORDPRESS_USERNAME", "WORDPRESS_USERNAME", "WP_USERNAME"],
            required=True,
        )
        app_password = self._resolver.resolve(
            label="WordPress app password",
            env_names=["FORGE_WORDPRESS_APP_PASSWORD", "WORDPRESS_APP_PASSWORD", "WP_APP_PASSWORD"],
            soul_keys=["FORGE_WORDPRESS_APP_PASSWORD", "WORDPRESS_APP_PASSWORD", "WP_APP_PASSWORD"],
            required=True,
        )
        resolved_title = title.strip() or self._infer_title(objective, content)
        endpoint = self._endpoint(target)
        body = {
            "title": resolved_title,
            "content": content,
            "status": target.status,
        }
        if target.slug:
            body["slug"] = target.slug
        raw = self._request_json(endpoint, username=username, app_password=app_password, payload=body)
        response_id = str(raw.get("id", "")).strip()
        response_link = str(raw.get("link", "")).strip()
        response_status_value = str(raw.get("status", target.status)).strip()
        return {
            "status": "completed",
            "provider": "wordpress",
            "site_url": target.site_url,
            "resource_type": target.resource_type,
            "resource_id": response_id,
            "resource_status": response_status_value,
            "resource_link": response_link,
            "response_status": 200,
            "published_bytes": len(content.encode("utf-8")),
            "summary": f"Published WordPress {target.resource_type.rstrip('s')} `{resolved_title}` with status `{response_status_value}`.",
            "evidence": [
                f"site:{target.site_url}",
                f"type:{target.resource_type}",
                f"id:{response_id or 'unknown'}",
                f"status:{response_status_value}",
            ],
        }

    def _resolve_target(
        self,
        *,
        objective: str,
        site_url: str,
        slug: str,
        status: str,
        resource_type: str,
        resource_id: str,
    ) -> WordPressTarget:
        final_site = site_url.strip() or self._resolver.resolve(
            label="WordPress site URL",
            env_names=["FORGE_WORDPRESS_SITE_URL", "WORDPRESS_SITE_URL", "WP_SITE_URL"],
            soul_keys=["FORGE_WORDPRESS_SITE_URL", "WORDPRESS_SITE_URL", "WP_SITE_URL", "WORDPRESS_URL"],
            required=True,
        )
        final_type = resource_type.strip() or "posts"
        final_status = status.strip() or "publish"
        final_slug = slug.strip() or self._slugify(objective)
        return WordPressTarget(
            site_url=final_site.rstrip("/"),
            resource_type=final_type,
            status=final_status,
            slug=final_slug,
            resource_id=resource_id.strip(),
        )

    def _endpoint(self, target: WordPressTarget) -> str:
        base = f"{target.site_url}/wp-json/wp/v2/{target.resource_type}"
        if target.resource_id:
            return f"{base}/{parse.quote(target.resource_id)}"
        return base

    @staticmethod
    def _request_json(url: str, *, username: str, app_password: str, payload: dict) -> dict:
        token = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("ascii")
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "FORGE/1.1 wordpress-publisher",
            },
        )
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}

    @staticmethod
    def _infer_title(objective: str, content: str) -> str:
        first_heading = next(
            (
                line.lstrip("#").strip()
                for line in content.splitlines()
                if line.strip().startswith("#") and line.lstrip("#").strip()
            ),
            "",
        )
        return first_heading or objective[:120].strip() or "FORGE Mission"

    @staticmethod
    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
        slug = re.sub(r"-{2,}", "-", slug)
        return slug[:72] or "forge-mission"
