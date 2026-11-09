import asyncio
import logging
import os
import socket
import uuid
from typing import Any

import httpx


logger = logging.getLogger("service_registry_client")


class RegistryClient:
    """
    Registers a microservice with the central Service Registry.

    Responsibilities:
    - Initial registration
    - Periodic heartbeats
    - Re-registration after lease expiration
    - Retry after registry failures
    - Graceful deregistration
    """

    def __init__(
        self,
        service_id: str,
        endpoint: str,
        registry_url: str,
        api_key: str,
        heartbeat_interval: float = 5.0,
        metadata: dict[str, Any] | None = None,
    ):
        self.service_id = service_id
        self.endpoint = endpoint.rstrip("/")
        self.registry_url = registry_url.rstrip("/")
        self.api_key = api_key
        self.heartbeat_interval = heartbeat_interval
        self.metadata = metadata or {}

        hostname = socket.gethostname()
        unique_suffix = uuid.uuid4().hex[:12]

        self.instance_id = (
            f"{hostname}-{unique_suffix}"
        )

        self._http: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._registered = False
        self._stopping = False

    async def start(self) -> None:
        """Start the background registration and heartbeat loop."""

        if self._task is not None:
            return

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            headers={
                "X-Registry-API-Key": self.api_key,
            },
        )

        self._stopping = False

        self._task = asyncio.create_task(
            self._maintain_registration()
        )

        logger.info(
            "Registry client started for %s (%s)",
            self.service_id,
            self.instance_id,
        )

    async def stop(self) -> None:
        """Stop heartbeats and attempt graceful deregistration."""

        self._stopping = True

        if self._task is not None:
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

            self._task = None

        if self._registered and self._http is not None:
            try:
                response = await self._http.delete(
                    f"{self.registry_url}/v1/instances/"
                    f"{self.instance_id}"
                )

                if response.status_code == 404:
                    logger.info(
                        "Instance was already absent from registry: %s",
                        self.instance_id,
                    )
                else:
                    response.raise_for_status()

                    logger.info(
                        "Deregistered instance %s",
                        self.instance_id,
                    )

            except httpx.HTTPError:
                logger.exception(
                    "Could not deregister %s during shutdown",
                    self.instance_id,
                )

        self._registered = False

        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _register(self) -> None:
        """Register this instance with the central registry."""

        if self._http is None:
            return

        payload = {
            "service_id": self.service_id,
            "instance_id": self.instance_id,
            "endpoint": self.endpoint,
            "version": "0.1.0",
            "metadata": self.metadata,
        }

        response = await self._http.post(
            f"{self.registry_url}/v1/instances/register",
            json=payload,
        )

        response.raise_for_status()

        self._registered = True

        logger.info(
            "Registered %s at %s",
            self.instance_id,
            self.endpoint,
        )

    async def _heartbeat(self) -> bool:
        """
        Send a heartbeat.

        Returns False if the registry reports that the instance
        no longer has a valid registration.
        """

        if self._http is None:
            return False

        response = await self._http.post(
            f"{self.registry_url}/v1/instances/"
            f"{self.instance_id}/heartbeat"
        )

        if response.status_code == 404:
            logger.warning(
                "Registration expired or disappeared for %s",
                self.instance_id,
            )

            self._registered = False
            return False

        response.raise_for_status()

        logger.debug(
            "Heartbeat successful for %s",
            self.instance_id,
        )

        return True

    async def _maintain_registration(self) -> None:
        """Maintain registration with exponential retry backoff."""

        retry_delay = 1.0
        maximum_retry_delay = 10.0

        while not self._stopping:
            try:
                if not self._registered:
                    await self._register()
                else:
                    await self._heartbeat()

                retry_delay = 1.0

                await asyncio.sleep(
                    self.heartbeat_interval
                )

            except asyncio.CancelledError:
                raise

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Registry returned HTTP %s for %s: %s",
                    exc.response.status_code,
                    self.service_id,
                    exc.response.text,
                )

                # A 4xx other than 404 is generally a configuration
                # or request problem. Keep retrying, but back off.
                await asyncio.sleep(retry_delay)

                retry_delay = min(
                    retry_delay * 2,
                    maximum_retry_delay,
                )

            except httpx.HTTPError:
                logger.exception(
                    "Registry communication failed for %s",
                    self.service_id,
                )

                await asyncio.sleep(retry_delay)

                retry_delay = min(
                    retry_delay * 2,
                    maximum_retry_delay,
                )

            except Exception:
                logger.exception(
                    "Unexpected registration error for %s",
                    self.service_id,
                )

                await asyncio.sleep(retry_delay)

                retry_delay = min(
                    retry_delay * 2,
                    maximum_retry_delay,
                )