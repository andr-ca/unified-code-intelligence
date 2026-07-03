"""Grouped dashboard navigation: every route is reachable and the active group is highlighted."""

from __future__ import annotations

import pytest

from uci.api import views

ALL_ROUTES = [
    "/", "/understand", "/architecture", "/flows", "/onboarding",
    "/search", "/graph", "/metrics", "/gaps", "/build", "/enrich", "/db", "/projects", "/config",
]


@pytest.fixture(autouse=True)
def _reset_evals():
    views.configure(False)
    yield
    views.configure(False)


def test_every_route_is_present_in_nav():
    html = views.layout("T", "/", "<p>x</p>")
    for href in ALL_ROUTES:
        assert f'href="{href}"' in html


def test_group_menus_render():
    html = views.layout("T", "/", "<p>x</p>")
    for group in ("Understand", "Explore", "Analyze", "Data", "Settings"):
        assert f">{group}" in html            # a menu button label
    assert "menu-drop" in html                 # dropdown panels exist
    assert ">Guided Tour<" in html             # /understand relabeled inside the group


def test_active_item_and_group_highlighted():
    html = views.layout("T", "/enrich", "<p>x</p>")
    assert '<div class="menu active">' in html          # the Data group is active
    assert 'href="/enrich" class="active"' in html       # the item is marked active


def test_overview_is_standalone_active():
    html = views.layout("T", "/", "<p>x</p>")
    assert '<a href="/" class="active">Overview</a>' in html


def test_evals_only_when_configured():
    assert 'href="/evals"' not in views.layout("T", "/", "x")
    views.configure(True)
    html = views.layout("T", "/evals", "x")
    assert 'href="/evals"' in html                        # injected into the Data group
    assert '<div class="menu active">' in html            # Data group active for /evals
