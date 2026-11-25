"""Offline tests for SSR embedded-state extraction (no browser, no network)."""

import json

from mediavault.ssr_state import (
    _decode,
    balanced_object,
    extract_kuaishou_photo,
    extract_xhs_note,
)


def test_balanced_object_basic_and_nested():
    html = 'prefix window.__X__ = {"a": 1, "b": {"c": 2}}; suffix'
    assert balanced_object(html, "__X__") == '{"a": 1, "b": {"c": 2}}'


def test_balanced_object_ignores_braces_inside_strings():
    html = 'm = {"text": "a } b { c", "ok": true}'
    assert balanced_object(html, "m =") == '{"text": "a } b { c", "ok": true}'


def test_balanced_object_handles_escaped_quote():
    html = 'm = {"q": "he said \\"hi\\" {}"}'
    assert balanced_object(html, "m =") == '{"q": "he said \\"hi\\" {}"}'


def test_balanced_object_missing_marker_and_brace():
    assert balanced_object("nothing here", "__X__") is None
    assert balanced_object("__X__ = ;", "__X__") is None


def test_balanced_object_unbalanced_returns_none():
    html = 'm = {"a": {'
    assert balanced_object(html, "m =") is None


def test_decode_tolerates_undefined_and_rejects_garbage():
    html = 's = {"a": undefined, "b": 1}'
    lit = balanced_object(html, "s =")
    assert _decode(lit) == {"a": None, "b": 1}
    assert _decode("not-json") is None
    assert _decode("[1,2]") is None  # only dict state is accepted
    assert _decode(None) is None


XHS_STATE = {
    "note": {"noteDetailMap": {"k1": {"note": {
        "noteId": "6af1", "title": "胶片",
        "imageList": [{"urlDefault": "https://ci/a.jpg"}],
    }}}},
    "user": {},
}
XHS_HTML = "window.__INITIAL_STATE__=" + json.dumps(XHS_STATE)


def test_extract_xhs_note_hydrated():
    note = extract_xhs_note(XHS_HTML)
    assert note["noteId"] == "6af1" and note["title"] == "胶片"


def test_extract_xhs_note_shell_pages_return_none():
    assert extract_xhs_note("no state at all") is None
    assert extract_xhs_note('__INITIAL_STATE__={"note":{"noteDetailMap":{}}}') is None
    assert extract_xhs_note('__INITIAL_STATE__={"note":{}}') is None
    assert extract_xhs_note('__INITIAL_STATE__={"note":{"noteDetailMap":{"k":{}}}}') is None
    assert extract_xhs_note("__INITIAL_STATE__={broken json") is None


KS_STATE = {"defaultClient": {
    "ROOT_QUERY": {"x": 1},
    "VisionVideoDetailPhoto:3x": {
        "photoId": "3x", "caption": "hi", "photoUrl": "https://cdn/p.mp4",
    },
}}
KS_HTML = "window.__APOLLO_STATE__=" + json.dumps(KS_STATE)


def test_extract_kuaishou_photo_hydrated():
    photo = extract_kuaishou_photo(KS_HTML)
    assert photo["photoId"] == "3x" and photo["photoUrl"] == "https://cdn/p.mp4"


def test_extract_kuaishou_photo_shell_returns_none():
    assert extract_kuaishou_photo("nope") is None
    assert extract_kuaishou_photo('__APOLLO_STATE__={"defaultClient":{"ROOT_QUERY":{}}}') is None
    assert extract_kuaishou_photo('__APOLLO_STATE__={}') is None
    assert extract_kuaishou_photo("__APOLLO_STATE__={broken") is None

def test_extract_kuaishou_photo_non_dict_cache_returns_none():
    # defaultClient present but not an object -> defensive None, never raises
    html = 'window.__APOLLO_STATE__=' + json.dumps({"defaultClient": None})
    assert extract_kuaishou_photo(html) is None