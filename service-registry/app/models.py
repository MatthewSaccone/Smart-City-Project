
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

SERVICE_ID_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"
INSTANCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"

SENSITIVE_METADATA_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "credential",
    "credentials",
    "authorization",
    "privatekey",
    "accesskey",
    "clientsecret",
}


class InstanceStatus(str, Enum):
    healthy = "healthy"
    unavailable = "unavailable"


def contains_sensitive_key(value: Any) -> bool:
    """Recursively detect credential-like metadata field names."""
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())

            if normalized_key in SENSITIVE_METADATA_KEYS:
                return True

            if contains_sensitive_key(nested_value):
                return True

    elif isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)

    return False


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(pattern=SERVICE_ID_PATTERN)
    instance_id: str = Field(pattern=INSTANCE_ID_PATTERN)
    endpoint: HttpUrl
    version: str = Field(default="0.1.0", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, separators=(",", ":"))

        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("metadata must not exceed 4096 bytes")

        if contains_sensitive_key(value):
            raise ValueError(
                "metadata must not contain credential-related fields"
            )

        return value


class InstanceResponse(BaseModel):
    service_id: str
    instance_id: str
    endpoint: str
    version: str
    status: InstanceStatus
    registered_at: datetime
    last_heartbeat_at: datetime
    lease_expires_at: datetime
    metadata: dict[str, Any]


class DiscoveryResponse(BaseModel):
    service_id: str
    instances: list[InstanceResponse]
    discovered_at: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
