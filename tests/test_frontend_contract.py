# -*- coding: utf-8 -*-
"""Contratos estáticos de UX que evitam regressões no front-end sem rede."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def _tag_with_id(element_id: str) -> str:
    match = re.search(rf"<[^>]+\bid=[\"']{re.escape(element_id)}[\"'][^>]*>", INDEX)
    assert match, f"elemento #{element_id} não encontrado"
    return match.group(0)


def test_mobile_viewport_and_skip_link_are_present():
    assert "width=device-width, initial-scale=1, viewport-fit=cover" in INDEX
    assert 'class="skip-link" href="#main-content"' in INDEX
    assert 'id="main-content" tabindex="-1"' in INDEX


def test_frontend_assets_share_the_same_cache_version():
    assert "style.css') }}?v=8" in INDEX
    assert "app.js') }}?v=8" in INDEX


def test_search_uses_editorial_hero_and_progressive_refinement():
    assert 'class="form-section search-hero"' in INDEX
    assert '<details class="search-refine" id="searchRefine">' in INDEX
    assert "<summary>" in INDEX
    assert 'class="search-refine-body"' in INDEX


def test_onboarding_is_started_only_by_user_action():
    assert "getElementById('tourBtn').addEventListener('click', () => showStep(0))" in APP
    assert "setTimeout(() => startTour" not in APP


def test_primary_inputs_have_accessible_names():
    input_ids = (
        "searchQuery",
        "directorInput",
        "actorInput",
        "similarSearch",
        "exploreDirectorInput",
        "pickSearch",
        "ratingsFile",
        "newListName",
    )
    for element_id in input_ids:
        assert "aria-label=" in _tag_with_id(element_id), f"#{element_id} sem nome acessível"


def test_async_status_regions_are_announced():
    tags = re.findall(r'<[^>]+class="[^"]*\bstatus\b[^"]*"[^>]*>', INDEX)
    assert tags
    for tag in tags:
        assert 'role="status"' in tag
        assert 'aria-live="polite"' in tag


def test_modal_contract_supports_focus_and_escape():
    for modal_id in ("authModal", "similarModal", "movieModal"):
        assert 'aria-hidden="true"' in _tag_with_id(modal_id)
    assert INDEX.count('role="dialog" aria-modal="true"') == 3
    assert "function openOverlay(" in APP
    assert "function closeOverlay(" in APP
    assert "e.key !== 'Tab'" in APP
    assert "e.key !== 'Escape'" in APP


def test_responsive_touch_and_safe_area_tokens_exist():
    assert "--touch-target: 44px" in STYLE
    assert "[hidden] { display: none !important; }" in STYLE
    assert "100dvh" in STYLE
    assert "env(safe-area-inset-bottom)" in STYLE
    assert "@media (max-width: 768px)" in STYLE
    assert "prefers-reduced-motion: reduce" in STYLE


def test_clickable_movie_cards_use_native_buttons():
    assert '<button class="poster-wrap movie-open"' in APP
    assert '<button class="movie-title movie-open"' in APP
    assert 'role="slider"' in APP
    assert "function portalNavMenu(" in APP
