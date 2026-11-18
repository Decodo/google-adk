from __future__ import annotations

import logging
import os

from decodo import (
    BundledSchema,
    DecodoClient,
    DecodoConfig,
    DecodoSchema,
    RemoteSchema,
    RemoteSchemaLoadOptions,
    WebScrapingApi,
    WebScrapingApiConfig,
)

API_KEY_ENV_VAR = "DECODO_API_KEY"
INTEGRATION_HEADER = "google-adk-python"
SCHEMA_TTL_MS = 24 * 60 * 60 * 1000

logger = logging.getLogger(__name__)


def resolve_api_key(api_key: str | None = None) -> str:
    key = (api_key or os.environ.get(API_KEY_ENV_VAR) or "").strip()
    if not key:
        raise ValueError(f"Pass api_key or set the {API_KEY_ENV_VAR} environment variable.")
    if not key.isascii() or not key.isprintable():
        raise ValueError(f"{API_KEY_ENV_VAR} contains characters an HTTP header cannot carry.")
    return key


def load_schema() -> DecodoSchema:
    try:
        return RemoteSchema.load(RemoteSchemaLoadOptions(ttl_ms=SCHEMA_TTL_MS))
    except Exception as error:
        logger.warning("Could not load the published Decodo schema (%s); using the bundled one.", error)
        return BundledSchema.shared


def build_api(api_key: str, schema: DecodoSchema) -> WebScrapingApi:
    config = DecodoConfig(
        web_scraping_api=WebScrapingApiConfig(api_key=api_key, integration_header=INTEGRATION_HEADER),
        schema=schema,
    )
    return DecodoClient(config).web_scraping_api
