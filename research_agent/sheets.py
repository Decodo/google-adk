"""Tools that log the research to a Google Sheet."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import gspread
from gspread.utils import ValueInputOption

CREDENTIALS_ENV_VAR = "GOOGLE_SERVICE_ACCOUNT_FILE"
SHEET_ID_ENV_VAR = "GOOGLE_SHEET_ID"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

RESULTS_TAB = "Search results"
RESULTS_HEADER = ["Logged at (UTC)", "Query", "Rank", "Title", "Link", "Description"]
RESULT_KEYS = ("rank", "title", "link", "description")
ANALYSIS_TAB = "Analysis"
ANALYSIS_HEADER = ["Logged at (UTC)", "Query", "Analysis"]

logger = logging.getLogger(__name__)


class MissingSetting(Exception):
    pass


def _setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(name)
    return value


def _worksheet(title: str, header: list[str]) -> gspread.Worksheet:
    client = gspread.service_account(filename=_setting(CREDENTIALS_ENV_VAR), scopes=[SHEETS_SCOPE])
    spreadsheet = client.open_by_key(_setting(SHEET_ID_ENV_VAR))
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(header))
        worksheet.append_row(header, value_input_option=ValueInputOption.raw)
        return worksheet


def _append(title: str, header: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    try:
        # RAW keeps scraped text as text: a title starting with "=" must not become a formula.
        _worksheet(title, header).append_rows(rows, value_input_option=ValueInputOption.raw)
    except MissingSetting as missing:
        return {"error": f"Set the {missing} environment variable to log to Google Sheets."}
    except Exception as error:
        # Error text from Google can carry request details, so only its type reaches the model.
        logger.exception("Could not write to the Google Sheet")
        return {"error": f"Could not write to the Google Sheet ({type(error).__name__}). Details are in the log."}
    return {"logged_rows": len(rows), "tab": title}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def log_search_results(query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Appends search results to the "Search results" tab of the Google Sheet.

    Args:
        query: The search query the results came from.
        results: The organic results of the web search, each with rank, title, link and description.
    """
    now = _now()
    rows = [[now, query, *(item.get(key, "") for key in RESULT_KEYS)] for item in results if isinstance(item, dict)]
    return await asyncio.to_thread(_append, RESULTS_TAB, RESULTS_HEADER, rows)


async def log_analysis(query: str, analysis: str) -> dict[str, Any]:
    """Appends your written analysis of a query's results to the "Analysis" tab of the Google Sheet.

    Args:
        query: The search query that was analysed.
        analysis: The analysis, as plain text.
    """
    return await asyncio.to_thread(_append, ANALYSIS_TAB, ANALYSIS_HEADER, [[_now(), query, analysis]])
