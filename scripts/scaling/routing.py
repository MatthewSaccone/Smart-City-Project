"""Registry-aware round robin for the T009 local harness, not the application gateway."""

from datetime import datetime, timezone

import httpx


class RegistryRouter:
    def __init__(self, client: httpx.AsyncClient, registry_url: str, service_id: str):
        self.client = client
        self.registry_url = registry_url.rstrip("/")
        self.service_id = service_id
        self.instances = []
        self.draining = set()
        self.cursor = 0

    async def refresh(self):
        response = await self.client.get(f"{self.registry_url}/v1/services/{self.service_id}")
        if response.status_code == 503:
            self.instances = []
            return
        response.raise_for_status()
        body = response.json()
        if body["service_id"] != self.service_id:
            raise ValueError("Discovery returned a different service")
        self.instances = sorted(body["instances"], key=lambda item: item["instance_id"])

    def choose(self, now: datetime | None = None, excluded: set[str] | None = None):
        now = now or datetime.now(timezone.utc)
        candidates = [item for item in self.instances
                      if item["status"] == "healthy" and item["instance_id"] not in self.draining
                      and item["instance_id"] not in (excluded or set())
                      and datetime.fromisoformat(item["lease_expires_at"].replace("Z", "+00:00")) > now]
        if not candidates:
            raise LookupError("No eligible, unexpired instances")
        selected = candidates[self.cursor % len(candidates)]
        self.cursor += 1
        return selected

    async def get(self, path: str):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Use a service-relative path")
        excluded = set()
        for attempt in range(2):
            selected = self.choose(excluded=excluded)
            excluded.add(selected["instance_id"])
            try:
                response = await self.client.get(selected["endpoint"].rstrip("/") + path)
                if response.status_code >= 500:
                    response.raise_for_status()
                return selected["instance_id"], response
            except httpx.HTTPError:
                if attempt:
                    raise
                # Retry an idempotent GET once, on a different eligible instance.
        raise RuntimeError("Unreachable")
