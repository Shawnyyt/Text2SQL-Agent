"""CP6：BM25 中文检索。"""

from text2sql.retrieval.bm25 import BM25Index, tokenize


def test_chinese_tokenization_keeps_words_drops_punctuation():
    tokens = tokenize("统计华东区的销售额，按商品分组！")
    assert "销售额" in tokens
    assert "华东区" in tokens or "华东" in tokens
    assert "，" not in tokens and "!" not in tokens
    assert all(t == t.lower() for t in tokens)


def test_ranking_prefers_matching_document():
    docs = {
        "sales": "销售流水 销售额 销售件数 商品",
        "users": "用户 登录 资产 客户 性别",
        "finance": "发票 应收 财务 供应商",
    }
    idx = BM25Index(docs)
    assert idx.search("销售额")[0][0] == "sales"
    assert idx.search("用户登录")[0][0] == "users"
    assert idx.search("发票应收")[0][0] == "finance"


def test_top_k_and_no_match():
    idx = BM25Index({"a": "销售额 金额", "b": "用户 登录"})
    assert len(idx.search("销售额 用户", top_k=1)) == 1
    assert idx.search("完全无关的词组") == []


def test_empty_inputs_are_safe():
    assert BM25Index({}).search("x") == []
    assert BM25Index({"a": "！！！"}).search("x") == []
    assert BM25Index({"a": "销售额"}).search("") == []
    assert len(BM25Index({"a": "销售额"})) == 1
