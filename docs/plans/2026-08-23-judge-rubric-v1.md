# Judge Rubric v1 — Partial vs Wrong 边界定义 (2026-08-23)

> **触发**: P0 calibration 中 Q33 出现 judge(1=wrong) vs human(partial) 分歧。
> Claim decomposition 证明 judge 对、human 太宽松 → 需要 operational rubric。
>
> **状态**: v1 固化。后续所有 judge prompt / 人工核对以本文件为准。

---

## 1. 三档定义（operational）

```text
Correct:
    核心结论与关键事实均一致。
    （数字、单位、口径、时间全部对；允许表述差异）

Partial:
    核心结论正确，但存在遗漏 / 数值不完整 / 部分维度错误。
    （方向对、主体数字对；缺次要细节或部分维度不准）

Wrong:
    核心答案错误，且错误 claim 构成主要结论。
    或：关键数字/口径/实体张冠李戴（misattribution）。
    或：声称"未披露/不存在"但实际存在（false refusal）。
    或：编造数据（hallucination）。
```

## 2. Claim Decomposition 流程（判定任何 borderline case 时执行）

```text
reference facts     ← 从 gold_evidence (PDF snippets) 提取
    ↓
RAG claims          ← 从 answer 逐句拆出可验证断言
    ↓
逐条标注            ← supported / missing / misattributed / contradictory
    ↓
套 rubric           ← 错误是否构成"主要结论"？
```

### 关键判据：「错误 claim 是否构成主要结论」

- 问题问什么，答案的主体就必须答什么
- 若答对的只是背景/原因/修饰，而**直接回答问题的部分错了** → Wrong
- 若直接回答的部分对，只是细节缺失 → Partial

## 3. Q33 作为 rubric 校准案例（已验证）

### Facts (from gold_evidence)
- F1: 2024年报披露 5,975,451 千元 (+31.98%)
- F2: 2025年报「调整后」= 5,955,567 千元
- F3: 2025年报「调整前」列 = 5,975,451（原始数据**有**披露）

### RAG Claims 判定
| Claim | 判定 |
|---|---|
| C1 追溯调整原因 | ✅ supported |
| C2 季度数据 4 个数 | ❌ misattributed（是 2025 年季度数据，非 2024 调整后）|
| C3 "全年合计 8,408,057（调整后值）" | ❌ core error（是 2025 全年数）|
| C4 "原始数据未披露" | ❌ false（调整前列有 5,975,451）|

### Rubric 输出
问题问「两个数是多少」→ RAG 给的两个数都错 → **Wrong**

**三方对照**：judge=wrong ✅ | human=partial ❌（被 C1 方向正确带偏）| reality=wrong

## 4. 其他 14 题的 rubric 一致性抽查

| Q | Judge | Human | Reality | Rubric 复核 |
|---|---|---|---|---|
| Q01 | partial(2) | wrong+partial | wrong | 数字错（740.19 vs 732.22）但接近 → judge partial 偏宽松但可辩护; human wrong 偏严。**边界 case，双方都可接受** |
| Q11 | partial(2) | wrong+partial | wrong | RAG 说"没找到单位"，实际表格明确是千元 → false refusal → 按 rubric 应 Wrong。judge 偏松 |
| Q12 | wrong(0) | refusal_wrong | wrong | false refusal → Wrong ✓ 一致 |
| Q26 | wrong(0)→reality correct | wrong | correct | gold 错导致；gold 已修 |
| Q33 | wrong(1) | partial | wrong | 本文档主案例 → Wrong ✓ judge 对 |

**发现**：judge 在 false-refusal 类（Q11）偏松给 partial，rubric 定义后应判 wrong。
这是 judge prompt 可改进点（P2，不立即改——保持 evaluator 冻结直到 LOCK）。

## 5. 与三轴指标的关系

Rubric 固化后：
- Metric 1 Agreement 不变（93.3%）
- Metric 2 Accuracy 计算口径更清晰（collapse 规则: correct/partial→right, wrong→wrong）
- Metric 3 Shared Error 需按 rubric 重算 human verdicts（Q11/Q01 human 标注偏严/偏松的影响）

**暂不重算**（避免 churn），LOCK evaluator v1 时统一按本 rubric 重标。

## 6. 后续动作

- [ ] B: numeric/citation deterministic checks（claim-level，非字符串匹配）
- [ ] C: evaluator modularization（rubric 进 `eval/judge/rubric`）
- [ ] LOCK 时：judge prompt 加入本 rubric 三档定义 + "错误是否构成主要结论"判据

## 7. 关联

- Gold correction report: docs/plans/2026-08-23-gold-correction-report.md
- human_verified_15.json: eval/sany_annual_reports/human_verified_15.json
- Methodology: memory/eval-methodology-feedback-2026-08-23.md
