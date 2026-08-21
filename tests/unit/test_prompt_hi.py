"""锁定 SYSTEM_PROMPT 的错误前提纠偏（H类）与拒答边界（I类）强化指令。

背景：H类（错误前提纠偏）60%→53.3%、I类（拒答边界）仍用错误数据糊弄，根因是
prompt 里的相关规则太弱（多为 checklist 里的「是否」软问句）。本测试锁定强指令存在。
"""
from app.core.prompt import SYSTEM_PROMPT


def test_h_class_premise_nullification_instructions_present():
    # H类：必须要求「开头就明确否定不成立的前提」，不能只给数据不否定
    assert "错误前提纠偏" in SYSTEM_PROMPT
    assert "开头第一句就明确否定前提" in SYSTEM_PROMPT
    assert "不许顺着用户的前提作答" in SYSTEM_PROMPT


def test_h_class_has_concrete_counterexample():
    # 有一个具体的反例约束（连续三年研发投入）
    assert "连续三年加大" in SYSTEM_PROMPT


def test_i_class_refusal_covers_all_edge_categories():
    # I类拒答边界必须覆盖：市值/单一国家/未来目标/别家公司/未披露项
    assert "拒答边界" in SYSTEM_PROMPT
    assert "市值" in SYSTEM_PROMPT
    assert "单一国家" in SYSTEM_PROMPT
    assert "未来预测" in SYSTEM_PROMPT
    assert "知识库之外" in SYSTEM_PROMPT


def test_i_class_forbids_synthesis_substitution():
    # 拒答时必须拒绝，不能用其他数据推导/估算/相近数据替代
    assert "不要用其他数据推导" in SYSTEM_PROMPT
    assert "拒答" in SYSTEM_PROMPT


def test_no_duplicate_information_sufficiency_section():
    # 之前编辑引入过重复块，锁定不回归
    assert SYSTEM_PROMPT.count("信息充分度决策") == 1
