# -*- coding: utf-8 -*-
"""Contratos estáticos de UX que evitam regressões no front-end sem rede."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
ADMIN = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
ADMIN_STYLE = (ROOT / "frontend" / "admin.css").read_text(encoding="utf-8")


def _tag_with_id(element_id: str) -> str:
    match = re.search(rf"<[^>]+\bid=[\"']{re.escape(element_id)}[\"'][^>]*>", INDEX)
    assert match, f"elemento #{element_id} não encontrado"
    return match.group(0)


def test_mobile_viewport_and_skip_link_are_present():
    assert "width=device-width, initial-scale=1, viewport-fit=cover" in INDEX
    assert 'class="skip-link" href="#main-content"' in INDEX
    assert 'id="main-content" tabindex="-1"' in INDEX


def test_frontend_assets_share_the_same_cache_version():
    assert "style.css') }}?v=9" in INDEX
    assert "app.js') }}?v=9" in INDEX
    assert "style.css') }}?v=9" in ADMIN


def test_search_uses_editorial_hero_and_stable_filter_dialog():
    assert 'class="form-section search-hero"' in INDEX
    assert 'id="filterOpenBtn"' in INDEX
    assert 'id="filterModal"' in INDEX
    assert 'aria-controls="filterModal"' in INDEX
    assert "function updateFilterSummary()" in APP


def test_product_guide_is_stable_and_does_not_move_the_page():
    assert 'id="guideModal"' in INDEX
    assert "const GUIDE_STEPS = [" in APP
    assert APP.count("eyebrow: '") == 7
    assert "tourReposition" not in APP
    assert "scrollIntoView({ block: 'center'" not in APP
    assert 'id="tourBtn"' not in INDEX


def test_search_moves_focus_to_results_after_submit():
    assert 'id="searchResults" class="results-anchor" tabindex="-1"' in INDEX
    assert "results.focus({ preventScroll: true })" in APP
    assert "results.scrollIntoView({" in APP


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
        tag = _tag_with_id(element_id)
        assert "aria-label=" in tag or f'for="{element_id}"' in INDEX, f"#{element_id} sem nome acessível"


def test_async_status_regions_are_announced():
    tags = re.findall(r'<[^>]+class="[^"]*\bstatus\b[^"]*"[^>]*>', INDEX)
    assert tags
    for tag in tags:
        assert 'role="status"' in tag
        assert 'aria-live="polite"' in tag


def test_modal_contract_supports_focus_and_escape():
    modal_ids = ("filterModal", "streamingModal", "guideModal", "authModal", "similarModal", "movieModal")
    for modal_id in modal_ids:
        assert 'aria-hidden="true"' in _tag_with_id(modal_id)
    assert INDEX.count('role="dialog" aria-modal="true"') == len(modal_ids)
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


def test_admin_has_responsive_navigation_and_account_cards():
    assert 'role="tablist" aria-label="Áreas do painel"' in ADMIN
    assert 'role="tabpanel" aria-labelledby="tabAccounts"' in ADMIN
    assert 'tabindex="0" role="button"' in ADMIN
    assert 'data-label="E-mail"' in ADMIN
    assert "@media (max-width: 760px)" in ADMIN_STYLE
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in ADMIN_STYLE
