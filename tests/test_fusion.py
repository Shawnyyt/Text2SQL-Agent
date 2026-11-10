"""CP6：RRF 融合。"""

import pytest

from text2sql.retrieval.fusion import rrf


def test_scores_follow_reciprocal_rank():
    res = dict(rrf([["a", "b"], ["b", "c"]], k=60))
    assert res["a"] == pytest.approx(1 / 61)
    assert res["b"] == pytest.approx(1 / 62 + 1 / 61)  # 两路都出现，分数累加
    assert res["c"] == pytest.approx(1 / 62)


def test_ordering_and_deterministic_tie_break():
    # x 与 y 分数相同（各出现一次，一个第 1 一个第 2），按首次出现顺序 x 在前
    order = [doc for doc, _ in rrf([["x", "y"], ["y", "x"]])]
    assert order == ["x", "y"]


def test_duplicate_inside_one_ranking_counts_once():
    res = dict(rrf([["a", "a"]]))
    assert res == {"a": pytest.approx(1 / 61)}


def test_empty_rankings():
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_k_controls_flatness():
    small_k = dict(rrf([["a", "b"]], k=1))
    big_k = dict(rrf([["a", "b"]], k=1000))
    assert small_k["a"] / small_k["b"] > big_k["a"] / big_k["b"]
