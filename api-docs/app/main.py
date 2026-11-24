from __future__ import annotations

import asyncio
import copy
import re
import os
from typing import Any

import httpx
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response


SERVICES = {
    "s1": {"name": "Live Traffic Analytics", "url": "http://live-traffic:8080"},
    "s2": {"name": "Traffic Predictor", "url": "http://traffic-predictor:8080"},
    "s3": {"name": "Construction & Incident Reporting", "url": "http://construction-incidents:8080"},
    "s4": {"name": "Parking Analytics", "url": "http://parking-analytics:8080"},
    "s5": {"name": "Weather & Air Quality", "url": "http://weather-analytics:8080"},
    "s6": {"name": "Public Transit Analytics", "url": "http://transit-analytics:8080"},
    "s7": {"name": "Gasoline Price Analytics", "url": "http://gasoline-analytics:8080"},
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
COMPONENT_SECTIONS = {
    "schemas",
    "responses",
    "parameters",
    "examples",
    "requestBodies",
    "headers",
    "securitySchemes",
    "links",
    "callbacks",
}


app = FastAPI(
    title="Smart City Unified API",
    description="Unified API documentation for all seven Smart City microservices.",
    version="1.0.0",
    docs_url=None,
    openapi_url=None,
)


def rewrite_refs(value: Any, service_key: str) -> Any:
    """Namespace component references recursively."""
    if isinstance(value, dict):
        rewritten = {}

        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                match = re.match(r"^#/components/([^/]+)/(.+)$", item)

                if match:
                    section, component_name = match.groups()

                    if section in COMPONENT_SECTIONS:
                        item = (
                            f"#/components/{section}/"
                            f"{service_key.upper()}_{component_name}"
                        )

            rewritten[key] = rewrite_refs(item, service_key)

        return rewritten

    if isinstance(value, list):
        return [rewrite_refs(item, service_key) for item in value]

    return value


def namespace_components(spec: dict, service_key: str) -> dict:
    components = spec.get("components", {})
    namespaced_components = {}

    for section, entries in components.items():
        if section not in COMPONENT_SECTIONS or not isinstance(entries, dict):
            continue

        namespaced_components[section] = {
            f"{service_key.upper()}_{name}": rewrite_refs(
                definition, service_key
            )
            for name, definition in entries.items()
        }

    return namespaced_components


def namespace_security(operation: dict, service_key: str) -> None:
    if "security" not in operation:
        return

    rewritten_security = []

    for requirement in operation["security"]:
        rewritten_security.append({
            f"{service_key.upper()}_{scheme}": scopes
            for scheme, scopes in requirement.items()
        })

    operation["security"] = rewritten_security


async def fetch_spec(
    client: httpx.AsyncClient,
    service_key: str,
    service: dict,
) -> dict:
    response = await client.get(f"{service['url']}/openapi.json")
    response.raise_for_status()
    return response.json()


async def build_unified_spec() -> dict:
    timeout = httpx.Timeout(10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *[
                fetch_spec(client, key, service)
                for key, service in SERVICES.items()
            ],
            return_exceptions=True,
        )

    unified = {
        "openapi": "3.1.0",
        "info": {
            "title": "Smart City Unified API",
            "version": "1.0.0",
            "description": (
                "Unified documentation for the seven independently "
                "deployable Smart City microservices. Paths are prefixed "
                "with their service identifier."
            ),
        },
        "servers": [{"url": "/"}],
        "tags": [
            {"name": f"{key.upper()} - {service['name']}"}
            for key, service in SERVICES.items()
        ],
        "paths": {},
        "components": {},
    }

    unavailable = []

    for (service_key, service), result in zip(SERVICES.items(), results):
        if isinstance(result, Exception):
            unavailable.append(
                f"{service_key.upper()} ({service['name']})"
            )
            continue

        spec = result
        components = namespace_components(
            copy.deepcopy(spec), service_key
        )

        for section, entries in components.items():
            unified["components"].setdefault(section, {}).update(entries)

        for path, path_item in spec.get("paths", {}).items():
            rewritten_item = rewrite_refs(
                copy.deepcopy(path_item), service_key
            )

            for method, operation in rewritten_item.items():
                if method.lower() not in HTTP_METHODS:
                    continue

                operation["security"] = [] if path in {"/", "/health", "/ready"} and method in {"get", "head"} else [{"SmartCityOAuth": ["smart-city"]}]

                operation["tags"] = [
                    f"{service_key.upper()} - {service['name']}"
                ]

            unified["paths"][f"/{service_key}{path}"] = rewritten_item

    if unavailable:
        unified["info"]["description"] += (
            "\n\nCurrently unavailable services: "
            + ", ".join(unavailable)
            + ". Their specifications will be retried on the next request."
        )

    base = os.getenv("AUTHENTIK_PUBLIC_URL", "http://localhost:8088").rstrip("/")
    unified["components"]["securitySchemes"] = {"SmartCityOAuth": {
        "type": "oauth2", "flows": {"authorizationCode": {
            "authorizationUrl": base + "/application/o/authorize/",
            "tokenUrl": base + "/application/o/token/",
            "scopes": {"openid": "OpenID Connect", "smart-city": "Smart City API"}
        }}}}
    return unified


@app.get("/openapi.json", include_in_schema=False)
async def unified_openapi():
    try:
        return await build_unified_spec()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to build unified OpenAPI specification: {exc}",
        ) from exc
        
        
@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Smart City Unified API",
        oauth2_redirect_url="/docs/oauth2-redirect",
        init_oauth={"clientId": os.getenv("JWT_AUDIENCE", "smart-city-api"), "usePkceWithAuthorizationCodeGrant": True, "scopes": "openid smart-city"},
    )

@app.get("/docs/oauth2-redirect", include_in_schema=False)
async def oauth_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.api_route(
    "/{service_key}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_request(
    service_key: str,
    path: str,
    request: Request,
):
    service = SERVICES.get(service_key.lower())

    if service is None:
        raise HTTPException(status_code=404, detail="Unknown service")

    upstream_url = f"{service['url']}/{path}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "proxy-authorization",
            "cookie",
            "x-user-id",
            "x-roles",
            "x-permissions",
        } and not key.lower().startswith(("x-authentik-", "x-forwarded-user", "x-forwarded-email"))
    }

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.request(
                method=request.method,
                url=upstream_url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{service['name']} is unavailable",
        ) from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in {
            "content-length",
            "transfer-encoding",
            "connection",
            "content-encoding",
        }
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )