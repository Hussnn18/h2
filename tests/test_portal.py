"""Smoke test: the portal and each of its pages run without errors (reads only, saves nothing)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "portal" / "app.py")


@pytest.fixture(autouse=True)
def test_data(generated_db, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(generated_db))
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)


def _page(name):
    from portal import views
    getattr(views, name)()


def test_portal_opens_on_overview():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    assert "Overview" in at.title[0].value


@pytest.mark.parametrize("page", ["overview", "offers_page", "rules_page", "customer_page"])
def test_page_renders(page):
    at = AppTest.from_function(_page, args=(page,), default_timeout=120).run()
    assert not at.exception, at.exception
    assert at.title, "page rendered no title"
