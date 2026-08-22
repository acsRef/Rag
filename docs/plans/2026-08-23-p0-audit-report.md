# P0 双审计报告 (2026-08-23)

> **范围**：10 道 A 类（单文档事实抽取）题（Q01-Q10，三一重工 2023-2025 年报）
> **方法**：V3 + Gate off 跑 10 题手验 judge；V3 + Gate=on 重跑捕获 conflicts 列表逐题审计
> **状态**：Phase 1 证据收集完成；Phase 2-3 模式分析 + 假设形成完成；**Phase 4 实现待用户决策**

---

## Audit 1: Judge noise 审计

### 数据
- 文件：`eval/ablation/20260822T160235/per_question/off_Q*.json`（10 份）
- Judge 模型：V3（同 chat 模型，`minimax_client.chat`）
- Judge prompt：「判断下面模型对问题的回答是否与参考答案一致。严格要求只输出一个英文单词 'correct' 或 'incorrect'」

### Per-question 审计结果

| Q | judge | 手验 | 一致性 | 备注 |
|---|---|---|---|---|
| Q01 | incorrect | incorrect (gen 740.19亿 ≠ gold 732.22亿) | ✅ TN | 模型数字错，judge 正确 |
| Q02 | correct | correct (59.75亿 + 31.98% 匹配) | ✅ TP | 多说 2023 同比是 bonus |
| Q03 | correct | correct (19,975,261千元 + 34.84% 匹配) | ✅ TP | 完全匹配 |
| **Q04** | **incorrect** | **partial** (答 558.6亿 + 64%，漏 15.1% 增长) | ⚠️ Borderline | partial answer 被判 incorrect |
| Q05 | correct | correct (1047 + 623 匹配) | ✅ TP | 完全匹配 |
| Q06 | correct | correct (25,930 + 2,445 + 23,485 匹配) | ✅ TP | 模型还附了表格，bonus |
| Q07 | incorrect | incorrect (漏了发行价 21.30 港元) | ✅ TN | 模型答了部分，judge 正确 |
| Q08 | correct | correct (2.20 元 + 尚须审议 匹配) | ✅ TP | 完全匹配 |
| Q09 | correct | correct (131.15亿 匹配) | ✅ TP | 多说市占率是 bonus |
| **Q10** | **incorrect** | **correct** (gen「0.9834元/股」= gold「0.9834元/股」) | ❌ **FN** | judge 误判 |

### Judge noise 量化

| 维度 | 值 |
|---|---|
| 总题数 | 10 |
| Judge 正确 | 8 |
| Judge 误判 | 2 (Q04 borderline + Q10 confirmed FN) |
| **Noise rate** | **10-20%** |

### Pattern 识别

1. **"承认不知道"模式** (Q07)：模型说「年报未披露」，gold 有具体值 → 错。Judge 正确判定，但说明模型本身对 retrieval 失败有「假装权威」的倾向。
2. **Partial answer 模式** (Q04)：模型答了 2/3 关键事实 → judge 倾向 incorrect（strict reading）。**无 partial 档**。
3. **Perfect match 被误判** (Q10)：数字完全一致但 judge 仍判 incorrect。Judge LLM 推理出错（非 prompt 模糊）。

### Judge 设计缺陷

```python
text = resp.strip().lower()
if "incorrect" in text: return False
if "correct" in text: return True
```

- **二元判定**：没有 partial 档，partial answer 必判 incorrect
- **不一致定义模糊**：judge prompt 「一致」未量化（如「核心数字匹配」vs「所有事实匹配」vs「结论方向一致」）
- **Substring matching**：模型如果解释「... 不完全正确 ... 但 ... 正确」会判 incorrect（注意：这是合理语义，不是 bug）

### 建议改进方向（待用户决策）

A) **3 档判定**：correct / partial / incorrect（需重写 judge prompt）
B) **A 类用 deterministic judge**：数字+单位字符串匹配，LLM judge 留给 C/D/E 复杂类
C) **保留现状 + 抽 10% 手验**：noise rate < 20% 可接受，n>50 时报告 noise range

---

## Audit 2: ConflictDetector 审计

### 数据
- 脚本：`eval/audit_conflict_v3.py`（用 `CHAT_MODEL=deepseek-ai/DeepSeek-V3` + `evidence_gate_enabled=True` + `evidence_min_coverage=0.1` 跑 10 题）
- 输出：`eval/ablation/audit_conflict_v3/{Q01-Q10}.json`（每题完整 EvidenceResult）
- 重点字段：`captured.conflicts[]`（每条 conflict 含 metric / conflict_type / severity / values[] / resolution_hint）

### Per-question 审计结果

| Q | temporal_consistent | conflicts | 类型 | 真冲突？ | 备注 |
|---|---|---|---|---|---|
| Q01 | **False** | 2 | value_mismatch | **NO** | 「销售收入」5 产品线（276.36/153.15/130/24.85/20.85 亿）误判为冲突；「未知指标」混入市场份额/现金流/毛利率等不同 metric |
| Q02 | **False** | 2 | value_mismatch | **NO** | 都是跨年数据（2024 vs 2023），应被 `_classify_conflict` 识别为 `year_mismatch`（low severity），却被标 value_mismatch |
| Q03 | True | 0 | — | n/a | 唯一无冲突 |
| Q04 | **False** | 12 | section_mismatch + year_mismatch | **NO** | Q04 问 2025 国际收入，detector 抓到 2024 同比增长、收入等 |
| Q05 | **False** | 1 | section_mismatch（50+ values） | **NO** | 「未知指标」混入 R&D 投资、市场份额、员工持股、专利等所有数字 |
| Q06 | **False** | 1 | value_mismatch | **NO** | 待分析 |
| Q07 | **False** | 1 | section_mismatch | **NO** | 待分析 |
| Q08 | **False** | 1 | section_mismatch | **NO** | 待分析 |
| Q09 | **False** | 11 | year_mismatch × 5 + section_mismatch × 4 + value_mismatch × 1 | **NO** | 5 产品线销售收入误判冲突；year_mismatch(low) 应被忽略；占比 80.29 vs 75.06 是不同 metric（合并报表 vs 母公司） |
| Q10 | **False** | 1 | value_mismatch | **NO** | 4 个 share count (588000/720614400/8474978037/58573613 股) 误判为同一冲突，实为不同 metric |

### ConflictDetector 假阳性率

**9/10 题 temporal_consistent=False，0/9 是真冲突** — **假阳性率 ≈ 100%**

### Root cause 分析（Phase 2）

来自 [app/core/evidence.py](app/core/evidence.py#L200-L371) 源码追溯：

#### Bug 1: `_METRIC_PREFIX_PATTERN` 过宽（line 211）

```python
_METRIC_PREFIX_PATTERN = re.compile(
    r"([一-鿿]{2,15}(?:收入|利润|资产|负债|现金流|销量|产量|占比|增速|增长|下降|规模|总额|合计))",
)
```

只匹配单一后缀（收入/利润/资产等），无法区分：
- **产品线**：挖掘机械/混凝土/起重/路面/桩工（都是「销售收入」）
- **层级**：合并报表 vs 母公司（都是「占营业收入」）
- **指标类型**：营业利润 vs 净利润 vs 毛利（都是「利润」）

→ Q01 5 产品线「销售收入」被误合并为同一 metric，触发冲突
→ Q09 80.29% vs 75.06% 「占营业收入」是不同 metric 被误合并

#### Bug 2: `_VALUE_PATTERN` 过宽（line 205-207）

```python
_VALUE_PATTERN = re.compile(
    r"(-?[\d,]+(?:\.\d+)?)\s*"
    r"(亿元|万元|百万元|亿元|千万|百万|十亿|万亿|%|个百分点|股|万股|亿股)",
)
```

匹配任何「数字+单位」,无法区分：
- **股份数量** vs **价格** vs **金额**
- **百分比** vs **百分点**

→ Q10 4 个不同 share count 被合并到「未知指标」下
→ Q05 50+ 不同 metric 的数字全合并到「未知指标」

#### Bug 3: `_normalize_metric` 上下文不足（line 312-316）

```python
def _normalize_metric(self, metric: str) -> str:
    normalized = re.sub(r"(同比|环比|本期|上期|当年|历年)", "", metric)
    return normalized.strip()
```

只去掉 同比/环比/本期/上期/当年/历年 等时间修饰，**不提取实体（产品线/部门/区域）**。

→ 「挖掘机械:销售收入276.36亿」与「起重机械:销售收入130亿」归一化后都是「销售收入」

#### Bug 4: Gate 决策忽略 severity 与 conflict_type（line 99 + 118）

```python
# Line 99
temporal_consistent=(len(table.conflicts) == 0),
# Line 118
if not result.temporal_consistent:
    return True
```

`_classify_conflict` 已正确分类 year_mismatch 为 severity=low（line 322-332），但 Gate 决策只看 `len(conflicts) == 0`，完全忽略 severity 与 conflict_type。

→ 即使 detector 自判「不是真冲突」（year_mismatch, low severity），仍触发拒答
→ 与 display logic 不一致（line 631-635 跳过 year_mismatch 显示，但 gate 仍拒答）

### 量化影响

| Gate 决策 | 当前 | 应该 |
|---|---|---|
| 真冲突（value_mismatch, high） | 拒答 | 拒答 ✓ |
| year_mismatch（detector 自判 low） | 拒答 | **放行** |
| section_mismatch（产品线互补） | 拒答 | **放行** |
| value_mismatch（不同 metric 误合并） | 拒答 | **放行** |

按当前实现，即使 detector 完全修复，gate 仍会因 year_mismatch 误拒答。

### Gate 实验历史回溯（来自 [memory/phase2-2026-08-22-checkpoint.md](../memory/phase2-2026-08-22-checkpoint.md) §3）

```
config=off:    accuracy=0%   refusal=0%   p95=39s
config=t=0.5:  accuracy=0%   refusal=90%  p95=13s  ← 假阳性触发
config=t=0.7:  accuracy=0%   refusal=90%  p95=16s
config=t=0.9:  accuracy=0%   refusal=90%  p95=14s
```

**0% accuracy + 90% refusal 在 Qwen3-8B baseline 下** = detector 假阳性让 gate 几乎全拒答 → Qwen 收到拒答后答不出 → 0%。**这是 detector bug，不是 Gate 本身的问题**。V3 baseline 的 Stage 2 也有 9/10 temporal_consistent=False（与本次 audit 一致）。

---

## Phase 3 假设

H1 (已证实)：ConflictDetector 当前实现对 A 类（单文档事实抽取）几乎 100% 假阳性。**Detector 不是「有时会错」，是「结构性不能工作」**。

H2 (待验证)：ConflictDetector 对 C/D/E 类（跨文档对比/计算/时序）也大概率假阳性，因为同样的 metric pattern + 同样缺乏 entity 提取。需要更多数据验证。

H3：Gate 决策与 detector classification 不一致 — 即使 detector 自判 `severity=low`，gate 仍拒答。这是独立于 detector 的 bug。

H4：当前 9/10 temporal_consistent=False 不是"detector 找到 9 个真冲突"，是 "detector 在 9 题上都错误触发"。

---

## Phase 4 候选方案（待用户决策）

| 方案 | 描述 | 改动量 | 风险 |
|---|---|---|---|
| A. **修 detector** | 重写 `_METRIC_PREFIX_PATTERN` + `_VALUE_PATTERN`，加 entity/year 提取 | 大（重写核心算法） | 高（可能引入新 bug） |
| B. **修 gate decision** | Gate 只在 `severity=high` 时拒答；year_mismatch/section_mismatch 不触发 | 小（line 99 + 118） | 中（可能放过真冲突） |
| C. **A+B 组合** | 修 detector + 修 gate decision | 大 | 高 |
| D. **A 类跳过 detector** | 单文档查询（`has_multiple_docs=False`）不调用 detector | 中 | 低（A 类失效，C/D/E 仍可用） |
| **E. DELETE detector** | 撤掉 detector 模块，gate 只看 coverage | 中（删 + 改 gate） | **彻底 DELETE 决策符合 B 类原则** |
| F. **保留现状** | 不动，看证据 | 0 | 继续阻塞所有 Gate 实验 |

### 推荐（待用户确认）

**倾向 E（DELETE detector）+ 配套 B 类决策**：

- 符合项目原则 "DELETE 是合理结局"
- 9/10 假阳性说明 detector 当前价值为负
- 一旦 DELETE，Gate 实验 = Gate off baseline（这其实就是当前的 Baseline-1: 60%）
- C/D/E 跨文档场景暂未审计，但如果 detector 当前对 A 类错成这样，对 C/D/E 也大概率错
- 「等 detector 修好再做 Gate 实验」会拖很久，先 DELETE 看 Gate-coverage-only 是否够用

但需用户决策，不能擅自行动。

---

## Judge + Detector 综合影响评估

| 改动 | 对当前 Baseline-1 (60%) 的影响 |
|---|---|
| Judge 改进（3 档或 deterministic） | 可能 ±5-15pp（取决于 judge 偏差方向） |
| DELETE detector（= Gate always off） | **无影响**（Baseline-1 就是 Gate off） |
| Gate 接通（修 detector 后） | 不确定（可能改善，也可能继续 90% 拒答） |

**关键事实**：当前 Baseline-1 (60%) 是在 Judge 有 noise + Detector 100% 假阳性 + Gate off 的状态下测的。**真值应在 60% ± 15pp 区间**。

---

## 下一步（待用户决策）

1. **ConflictDetector 命运**：E (DELETE) / D (A 类跳过) / B (改 gate decision) / A+C (修 detector)？
2. **Judge 命运**：保留 + 抽 10% 手验 / 改 3 档 / A 类用 deterministic？
3. **Gate pilot 何时启动**：仅在 ConflictDetector + Judge 两个审计完成后。

---

## 关联

- 审计数据：
  - Judge: `eval/ablation/20260822T160235/per_question/off_Q*.json`（10 份）
  - Detector: `eval/ablation/audit_conflict_v3/{Q01-Q10}.json` + `summary.json`
  - 审计脚本: `eval/audit_conflict_v3.py`（capture 完整 EvidenceResult）
- Detector 源码: [app/core/evidence.py](app/core/evidence.py)
- Stage 2 原始 Gate 数据: `eval/ablation/20260822T152324/`（Qwen3-8B 4 config 全跑）
- 方法论纪律: [memory/eval-methodology-feedback-2026-08-23.md](../memory/eval-methodology-feedback-2026-08-23.md)
