"""HTTP client for imagic desktop license activation."""

from __future__ import annotations

import hashlib
import json
import platform
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request


class LicenseClientError(RuntimeError):
    """Raised when the license API rejects or cannot process a request."""


def get_device_id() -> str:
    raw = "|".join([
        platform.node() or "unknown-node",
        platform.system() or "unknown-os",
        platform.machine() or "unknown-machine",
        str(uuid.getnode()),
        str(Path.home()),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_device_name() -> str:
    return platform.node() or f"{platform.system()} desktop"


class DesktopLicenseClient:
    def __init__(self, base_url: str, timeout_s: float = 8.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise LicenseClientError("License server URL is not configured.")
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(detail) if detail else {}
            except json.JSONDecodeError:
                parsed = {}
            raise LicenseClientError(parsed.get("detail") or f"HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise LicenseClientError(f"Could not reach license server: {exc.reason}") from exc

        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise LicenseClientError("License server returned invalid JSON.") from exc

    def activate(self, license_key: str) -> dict[str, Any]:
        return self._post_json(
            "/api/licenses/activate",
            {
                "license_key": license_key,
                "device_id": get_device_id(),
                "device_name": get_device_name(),
            },
        )

    def validate(self, activation_token: str) -> dict[str, Any]:
        return self._post_json(
            "/api/licenses/validate",
            {
                "activation_token": activation_token,
                "device_id": get_device_id(),
            },
        )

    def check_for_update(self, current_version: str) -> dict[str, Any] | None:
        """Check if a newer version is available.

        Returns a dict with 'latest_version' and 'download_url' if an update
        is available, or None if already up to date (or on error).
        """
        if not self.enabled:
            return None
        try:
            req = request.Request(
                f"{self._base_url}/api/desktop/latest-version",
                method="GET",
            )
            with request.urlopen(req, timeout=self._timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
            latest = data.get("latest_version", "")
            if latest and _version_tuple(latest) > _version_tuple(current_version):
                return data
        except Exception:
            pass
        return None


def _version_tuple(version_string: str) -> tuple[int, ...]:
    """Parse '1.2.3' into (1, 2, 3) for comparison."""
    parts = []
    for segment in version_string.strip().split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)