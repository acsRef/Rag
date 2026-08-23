# P0 Gold 修正 + 三轴 Evaluator 重新校准 (2026-08-23)

> **触发**：Phase 4-D + P0 Judge Calibration 后的二次验证（PDF 原文交叉）发现 gold 本身有 2 题错（Q26/Q55），导致之前 93% judge-gold agreement 结论**误导**。
>
> **方法**：不依赖 RAG / Vector DB / Judge；直接读 PDF 提取文本（pymupdf4llm）+ 人工核对 + 修正。

---

## 1. Gold 修正清单（2 题）

### Q26 — 2025 全年分红

| 字段 | 原 Gold | 修正 Gold |
|---|---|---|
| `gold_answer` | "全年合计每10股4.9元，即每股0.49元" | "全年合计每股**0.80元**（含税）。中期分红 3.1元/10股（0.31元/股，已实施）+ 年度预案 4.9元/10股（0.49元/股，待股东会审议）" |
| `gold_evidence` | (无) | PDF 2025：「2025年半年度...向全体股东每 10 股派现金红利 3.1 元(含税)，共计派发现金红利 2,613,734,813.44 元，中期现金红利已于 2025 年 10 月 15 日发放」+ 年度预案 4.9 元/10 股 |
| `gold_judgment` | (无) | answerable |
| 错因 | 原 gold 只计年度分红，**漏中期 3.1元/10股** | — |

### Q55 — 2025 新能源收入

| 字段 | 原 Gold | 修正 Gold |
|---|---|---|
| `gold_answer` | "无法从年报中获得" | "2025 年新能源产品销售额 **86.4 亿元**，同比增长 **115%**" |
| `gold_evidence` | (无) | PDF 2025：「2025 年公司新能源产品销售额达 86.4 亿元人民币，同比提升 115%」 |
| `gold_judgment` | (无) | answerable |
| 错因 | 原 gold **错说** PDF 没有此数据（实际 PDF 原文明确披露）| — |

---

## 2. 新数据结构（per user spec）

每个 question 含：
```
id
category
question
original_gold       # 历史值（保留）
verified_gold       # 修正后
gold_evidence       # PDF 原文 snippets 列表
gold_judgment       # answerable / partial_answerable / not_answerable
gold_correction_note # 修正原因（仅 Q26/Q55）
rag_answer          # RAG 实际生成
judge_score         # LLM judge 0-3
judge_reason
human_label         # 4 字段 (correct/partial/refusal/citation)
verified_reality    # 基于 verified_gold 的 truth (correct/partial/wrong)
```

## 3. 三轴 Evaluator 评估（修正 gold 后）

### Metric 1: Judge ↔ Human Agreement = **14/15 = 93.3%**

| 不一致 | Judge | Human | Reality |
|---|---|---|---|
| Q33 | wrong | partial | wrong (RAG conflated 2024 adj with 2025 number)|

### Metric 2: Judge ↔ Reality Accuracy = **11/15 = 73.3%**

| 错判 | Judge | Reality |
|---|---|---|
| Q01 | partial | wrong (judge 太宽松, RAG 数字错) |
| Q11 | partial | wrong (judge 太宽松, RAG 拒答)|
| Q26 | wrong | **correct** (gold 错, RAG 实际对)|
| Q55 | wrong | **correct** (gold 错, RAG 实际对)|

### Metric 3: Shared Error Rate = **4/15 = 26.7%**

| 共同错 | Judge | Human | Reality | 错因 |
|---|---|---|---|---|
| Q12 | wrong | refusal_wrong | wrong | ✅ Real shared error (RAG false refusal)|
| Q17 | wrong | wrong | wrong | ✅ Real shared error (RAG hallucination)|
| Q26 | wrong | wrong | correct | ❌ Gold 错导致 shared error |
| Q55 | wrong | wrong | correct | ❌ Gold 错导致 shared error |

**修正 gold 后的真 shared error**: 2/15 = **13.3%** (Q12, Q17)
**修正 gold 后的真 accuracy**: 13/15 = **86.7%**

---

## 4. LOCK 决策撤回

| 之前 | 现在 |
|---|---|
| "93% agreement → LOCK judge" | "93% judge-gold agreement, 但存在 2 个 shared errors (源于 gold 错), verified accuracy ~87%, **暂不 LOCK evaluator**" |

**新决策**：judge 准确率 87%（修正后），仍优于人类 80%，**作为 baseline evaluator 暂时可用**，但需修复：
1. ✅ Q26/Q55 gold 已修
2. ⏳ 加 citation / numeric deterministic checks (P2)
3. ⏳ Q33 partial vs wrong 边界 rubric (P1)

---

## 5. 产出

- [`eval/sany_annual_reports/human_verified_15.json`](../../eval/sany_annual_reports/human_verified_15.json)
- [`eval/build_human_verified.py`](../../eval/build_human_verified.py)
- PDF 提取产物: `eval/sany_annual_reports/三一重工_20XX年年度报告.pymupdf4llm.md`

## 6. 关键方法论教训

### 单 agreement 不足以判断 evaluator correctness
之前 93% agreement 看似 LOCK，但 gold 错误导致 judge+human "共同错" 了 2 题。
**正解**: 至少看 3 个指标（Agreement / Accuracy / Shared Error）。

### Gold quality 至少与 evaluator quality 同等重要
测试集 gold 错 = 所有 calibration 错。
**正解**: 每题加 `gold_evidence` (PDF snippets) + `gold_judgment` (answerable / partial / not)。

### PaddleOCR-VL-1.5 验证更有信心
本次 PDF 原文交叉验证发现 gold 错 → 验证不只是发现 evaluator noise, 而是发现 **ground truth construction 本身有缺陷**。

## 7. 下一步（按你定的优先级）

| 优先级 | 任务 | 状态 |
|---|---|---|
| ✅ P0 | 修 Q26/Q55 gold | Done |
| ✅ P0 | 加 gold_evidence 结构 | Done |
| ✅ P0 | 重新计算 3 个 metric | Done |
| ⏳ P1 | Q33 partial vs wrong rubric 边界 | 待启动 |
| ⏳ P2 | 加 numeric / citation deterministic checks | 待启动 |
| ⏳ P2 | Issue #1-B modularization + tests | 待启动 |

## 8. 关联

- Phase 4 memory: [memory/phase4-evidence-contract-repair.md](../../memory/phase4-evidence-contract-repair.md)
- Method feedback: [memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)
- Multi-metric future ref: [memory/eval-multi-metric-feedback-2026-08-23.md](../../memory/eval-multi-metric-feedback-2026-08-23.md)
- Phase 4-D pilot: [docs/plans/2026-08-23-phase4-d-gate-pilot-report.md](../../docs/plans/2026-08-23-phase4-d-gate-pilot-report.md)
- Original testset: [eval/sany_annual_reports/rag_testset.json](../../eval/sany_annual_reports/rag_testset.json)（未动; 用户决定如何迁移）