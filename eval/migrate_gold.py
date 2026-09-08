"""Migrate verified gold into rag_testset.json — 统一 single source of truth.

Per user decision D:
- rag_testset.json 是唯一 gold 来源
- 15 题 verified gold (Q26/Q55 修正 + 13 题确认) 合并进去
- schema 升级: 参考答案 / gold_evidence / gold_judgment / gold_source
- 其余 50 题标记 gold_source="original_unverified" (未验证, 待后续校准)

原文件在 git history 中保留 (bb43497^), 不需要备份副本。
"""

import json
from pathlib import Path

TESTSET_PATH = Path("eval/sany_annual_reports/rag_testset.json")
VERIFIED_PATH = Path("eval/sany_annual_reports/human_verified_15.json")

GOLD_SOURCE_VERIFIED = "human_verified_2026-08-23"
GOLD_SOURCE_ORIGINAL = "original_unverified"


def main():
    ds = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))
    verified = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
    ver_by_id = {q["id"]: q for q in verified["questions"]}

    migrated = 0
    marked = 0
    corrections = []

    # 只有这两题是真正的 gold 修正（PDF 原文证实原 gold 错误）
    # 其余 13 题 human 验证 = 原 gold 正确，保留原文，只补 evidence/judgment/source
    TRUE_CORRECTIONS = {"Q26", "Q55"}

    for q in ds["题目"]:
        qid = q["id"]
        if qid in ver_by_id:
            v = ver_by_id[qid]
            old_gold = q.get("参考答案", "")
            new_gold = v["verified_gold"]

            if qid in TRUE_CORRECTIONS:
                # 真修正：覆盖 gold + 记录 diff
                q["参考答案"] = new_gold
                corrections.append(
                    {
                        "id": qid,
                        "old": old_gold[:80],
                        "new": new_gold[:80],
                    }
                )
                q["gold_correction_note"] = v.get("correction_note", "")
            else:
                # human 验证原 gold 正确 → 保留原文（避免降级：原 gold 往往比
                # 关键词搜索改写更详细，如 Q11 单位说明 / Q51 研发投入合计口径）
                pass

            # Schema 升级: 补 3 个新字段（所有 verified 题都有）
            q["gold_evidence"] = v["gold_evidence"]
            q["gold_judgment"] = v["gold_judgment"]
            q["gold_source"] = GOLD_SOURCE_VERIFIED
            migrated += 1
        else:
            # 未验证的 50 题 — 只加来源标记, 不动 gold
            q.setdefault("gold_evidence", [])
            q.setdefault("gold_judgment", "")
            q["gold_source"] = GOLD_SOURCE_ORIGINAL
            marked += 1

    # 更新元数据
    ds["字段说明"]["gold_evidence"] = "PDF 原文证据片段列表 [{year, keyword, snippet}]"
    ds["字段说明"]["gold_judgment"] = "answerable / partial_answerable / not_answerable"
    ds["字段说明"]["gold_source"] = (
        f"{GOLD_SOURCE_VERIFIED} (已人工+PDF验证) | {GOLD_SOURCE_ORIGINAL} (原始标注未验证)"
    )

    TESTSET_PATH.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Migrated {migrated} verified questions")
    print(f"Marked   {marked} original questions as {GOLD_SOURCE_ORIGINAL}")
    print()
    print("Gold text corrections:")
    for c in corrections:
        print(f"  {c['id']}:")
        print(f"    old: {c['old']}")
        print(f"    new: {c['new']}")

    # Sanity checks
    print()
    print("Sanity checks:")
    data = json.loads(TESTSET_PATH.read_text(encoding="utf-8"))
    n_total = len(data["题目"])
    n_verified = sum(1 for q in data["题目"] if q.get("gold_source") == GOLD_SOURCE_VERIFIED)
    n_with_evidence = sum(1 for q in data["题目"] if q.get("gold_evidence"))
    print(f"  total={n_total}, verified={n_verified}, with_evidence={n_with_evidence}")

    # 验证 Q26/Q55 新 gold 已生效；其余题 gold 未被意外改动
    by_id = {q["id"]: q for q in data["题目"]}
    assert "0.80" in by_id["Q26"]["参考答案"], "Q26 gold not updated!"
    assert "86.4" in by_id["Q55"]["参考答案"], "Q55 gold not updated!"
    assert by_id["Q26"]["gold_source"] == GOLD_SOURCE_VERIFIED
    assert "千元" in by_id["Q11"]["参考答案"], "Q11 gold degraded (should keep 千元 unit answer)!"
    assert "61.01" in by_id["Q51"]["参考答案"] or "61.01" in str(
        by_id["Q51"].get("gold_evidence", "")
    ), "Q51 gold degraded!"
    assert by_id["Q56"]["参考答案"].startswith("三份年报中"), "Q56 gold changed unexpectedly!"
    print("  Q26 gold contains '0.80' ✓")
    print("  Q55 gold contains '86.4' ✓")
    print("  Q11 keeps 千元 unit answer ✓")
    print("  Q51 keeps 61.01 (研发投入合计口径) ✓")
    print("  Q56 gold unchanged ✓")


if __name__ == "__main__":
    main()
