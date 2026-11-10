"""Smoke test: every dashboard page renders without exceptions (needs data/ucl.sqlite)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ucl.config import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="no local database yet")
APP = str(Path(__file__).parent.parent / "app.py")


@pytest.mark.parametrize("page", ["Players", "Player detail", "Fixtures", "My team", "Transfers", "Captain", "Trends", "Model check"])
def test_page_renders(page, monkeypatch):
    monkeypatch.setenv("UCL_START_PAGE", page)
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception


def test_optimise_button(monkeypatch):
    monkeypatch.setenv("UCL_START_PAGE", "Transfers")
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert any("gain" in m.label for m in at.metric)
