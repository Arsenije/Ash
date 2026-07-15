"""Pure search machinery: RRF fusion, khora filter construction, Python re-filter."""

from __future__ import annotations

from server import _build_filter, _py_match, _rrf


# --- _rrf --------------------------------------------------------------------

def test_rrf_doc_in_both_rankings_beats_single_list_docs():
    recall = ["a", "b", "c"]
    graph = ["b", "d"]
    fused = _rrf([(recall, 1.0), (graph, 1.0)])
    order = [k for k, _ in fused]
    assert order[0] == "b"  # ranked by both lists → top
    assert set(order) == {"a", "b", "c", "d"}


def test_rrf_scores_sorted_descending_and_weighted():
    fused = _rrf([(["a"], 1.0), (["b"], 3.0)])
    scores = dict(fused)
    assert scores["b"] == 3.0 * scores["a"]  # same rank, 3x weight
    assert [k for k, _ in fused] == ["b", "a"]


def test_rrf_empty_input():
    assert _rrf([]) == []
    assert _rrf([([], 1.0)]) == []


def test_rrf_rank_decay():
    fused = dict(_rrf([(["a", "b"], 1.0)], k=60))
    assert fused["a"] == 1 / 60
    assert fused["b"] == 1 / 61


# --- _build_filter ------------------------------------------------------------

def test_build_filter_empty_is_none():
    assert _build_filter(None, None, [], [], None, None) is None


def test_build_filter_shapes():
    f = _build_filter("beach", "sunset", ["car", "dog"], ["summer"], "2024-01-01", "2024-12-31", "Budva", "Montenegro")
    assert f == {
        "metadata.custom.location": "beach",
        "metadata.custom.scene": "sunset",
        "metadata.custom.objects": {"$in": ["car", "dog"]},
        "metadata.custom.tags": {"$in": ["summer"]},
        "metadata.custom.occurred_at": {"$gte": "2024-01-01", "$lte": "2024-12-31"},
        "metadata.custom.gps_city": "Budva",
        "metadata.custom.gps_country": "Montenegro",
    }


def test_build_filter_open_ended_date_range():
    f = _build_filter(None, None, [], [], "2024-06-01", None)
    assert f == {"metadata.custom.occurred_at": {"$gte": "2024-06-01"}}


# --- _py_match ------------------------------------------------------------------

CUSTOM = {
    "location": "Beach",
    "scene": "Sunset",
    "objects": ["Palm Tree", "towel"],
    "tags": ["Summer"],
    "occurred_at": "2024-06-15T12:00:00+00:00",
    "gps_city": "Budva",
    "gps_country": "Montenegro",
}


def _match(**kw):
    args = {
        "location": None, "scene": None, "objects": [], "tags": [],
        "date_from": None, "date_to": None, "city": None, "country": None,
    }
    args.update(kw)
    return _py_match(CUSTOM, args["location"], args["scene"], args["objects"], args["tags"],
                     args["date_from"], args["date_to"], args["city"], args["country"])


def test_py_match_no_filters_matches():
    assert _match()


def test_py_match_is_case_insensitive():
    assert _match(location="beach")
    assert _match(scene="SUNSET")
    assert _match(objects=["palm tree"])
    assert _match(tags=["summer"])
    assert _match(city="budva")
    assert _match(country="montenegro")


def test_py_match_rejects_wrong_values():
    assert not _match(location="kitchen")
    assert not _match(objects=["bicycle"])
    assert not _match(city="Paris")


def test_py_match_multi_value_is_any_of():
    assert _match(objects=["bicycle", "towel"])  # one hit suffices


def test_py_match_date_range():
    assert _match(date_from="2024-06-01", date_to="2024-06-30")
    assert not _match(date_from="2024-07-01")
    assert not _match(date_to="2024-05-31")


def test_py_match_missing_fields_fail_closed():
    assert not _py_match({}, "beach", None, [], [], None, None)
    assert not _py_match({}, None, None, ["car"], [], None, None)
