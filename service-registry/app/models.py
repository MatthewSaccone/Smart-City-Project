from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

SERVICE_ID_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"
INSTANCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"

class InstanceStatus(str, Enum):
    healthy = "healthy"
    unavailable = "unavailable"

class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(pattern=SERVICE_ID_PATTERN)
    instance_id: str = Field(pattern=INSTANCE_ID_PATTERN)
    endpoint: HttpUrl
    version: str = Field(default="0.1.0", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_small_and_secret_free(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json
        encoded = json.dumps(value).lower()
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("metadata must not exceed 4096 bytes")
        forbidden = ("password", "secret", "token", "api_key", "apikey", "credential")
        if any(word in encoded for word in forbidden):
            raise ValueError("metadata must not contain credentials or secrets")
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
