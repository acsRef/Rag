"""锁定 R1 意图 prompt 具备强 JSON/禁思考约束。

烟测实锤：DeepSeek-R1 把路由任务当通用问答，输出推理轨迹/长段 markdown，3/3 不吐 JSON
→ robust_json_parse 失败 → 空 matches → 全库回退。此测试锁定 prompt 必须有强格式约束。
"""

from app.core.intent import INTENT_CLASSIFIER_PROMPT


def test_prompt_forbids_thinking_and_explanation():
    # R1 是推理模型：必须明确禁止输出思考过程/解释
    assert "不要" in INTENT_CLASSIFIER_PROMPT
    assert "思考" in INTENT_CLASSIFIER_PROMPT
    assert "JSON" in INTENT_CLASSIFIER_PROMPT


def test_prompt_has_strict_json_only_example():
    # 必须有"整个回复就是 JSON"的强声明，无任何额外文本
    assert "整个回复" in INTENT_CLASSIFIER_PROMPT
    assert "matches" in INTENT_CLASSIFIER_PROMPT
