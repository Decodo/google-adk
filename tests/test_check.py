from __future__ import annotations

import httpx
import pytest
from conftest import API_KEY, FakeHttp, Ir, schema_of

from decodo_adk.check import api_key_problem
from decodo_adk.client import INTEGRATION_HEADER, build_api


@pytest.mark.parametrize(
    ("status", "accepted"),
    [(200, True), (400, True), (500, True), (613, True), (401, False), (403, False), (429, False)],
)
def test_key_is_accepted_unless_decodo_rejects_or_limits_it(
    ir: Ir, http: FakeHttp, status: int, accepted: bool
) -> None:
    http.answer(status, {"message": "m"})

    problem = api_key_problem(build_api(API_KEY, schema_of(ir)))

    assert (problem is None) is accepted
    assert http.calls[0]["json"] == ir["keyCheck"]["params"]
    assert http.calls[0]["headers"]["x-integration"] == INTEGRATION_HEADER


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("offline")])
def test_no_answer_proves_nothing(ir: Ir, http: FakeHttp, error: Exception) -> None:
    http.fail(error)

    assert api_key_problem(build_api(API_KEY, schema_of(ir))) is not None


def test_the_check_request_is_the_published_one(ir: Ir, http: FakeHttp) -> None:
    ir["keyCheck"]["params"]["url"] = "https://still-nowhere.decodo.com"
    http.answer(613, {"message": "m"})

    assert api_key_problem(build_api(API_KEY, schema_of(ir))) is None
    assert http.calls[0]["json"]["url"] == "https://still-nowhere.decodo.com"


def test_a_schema_without_a_key_check_is_reported_not_raised(ir: Ir, http: FakeHttp) -> None:
    del ir["keyCheck"]

    assert "could not be checked" in str(api_key_problem(build_api(API_KEY, schema_of(ir))))
    assert http.calls == []
