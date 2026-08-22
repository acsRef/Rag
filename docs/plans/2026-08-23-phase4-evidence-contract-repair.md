# Phase 4 — Evidence Consistency Contract Repair

> **目标**：让 Evidence Gate 信号可信 — 不预设 Gate 通过或 DELETE，先修 ConflictDetector 修复数据契约
>
> **背景**：[docs/plans/2026-08-23-p0-audit-report.md](2026-08-23-p0-audit-report.md) 证明 ConflictDetector ≈100% 假阳性
>
> **原则**：4-A 写 spec 不写代码；4-B/C 修代码 + 单测；4-D 小规模验证

---

## Phase 4-A: Conflict key 契约重定义（数据契约先行）

### 设计目标

定义 **comparison key 五元组**：

```python
ConflictKey(
    entity: str,    # 产品线 / 部门 / 区域 / "公司整体"
    metric: str,    # 营业收入 / 净利润 / 毛利率 / ...
    period: str,    # 2023年 / 2024年 / 2023Q1 / ...
    unit: str,      # 亿元 / 万元 / % / 个百分点 / 股
    scope: str,     # 合并报表 / 母公司 / 国际 / 国内 / 海外
)
```

**关键约束**：只有当 5 个字段全部相同时，才算「同一指标」，不同值才算 conflict。

### 模块设计

新增 `app/core/conflict_key.py`：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ConflictKey:
    """Comparison key for cross-source value comparison.

    Conflict semantics: same (entity, metric, period, unit, scope) but
    different values across chunks/documents/years = TRUE conflict.

    Any difference in these fields = NOT a conflict (different things).
    """
    entity: str
    metric: str
    period: str
    unit: str
    scope: str

    def matches_except_value(self, other: "ConflictKey") -> bool:
        """True if all 5 fields match except possibly value (which is not stored here)."""
        return (self.entity == other.entity
                and self.metric == other.metric
                and self.period == other.period
                and self.unit == other.unit
                and self.scope == other.scope)
```

新增 `ConflictKeyExtractor` 接口（**仅接口，不实现**）：

```python
class ConflictKeyExtractor(Protocol):
    def extract(self, chunk: RetrievedChunk, value_span: tuple[int, int]) -> ConflictKey:
        """Extract 5-tuple key from chunk around a numeric value span.

        Args:
            chunk: RetrievedChunk with text + metadata (year, document_id, ...)
            value_span: (start, end) char offsets of the value in chunk.text

        Returns:
            ConflictKey with all 5 fields populated (best-effort;
            unknown fields → "未知" or inferred)
        """
        ...
```

### 字段提取规则（接口契约，不是实现）

| 字段 | 提取方法 | 失败时 |
|---|---|---|
| `entity` | chunk.text 前 20 字符内的「X 机械」「X 业务」「公司」等模式 | "未知" |
| `metric` | 当前 `_METRIC_PREFIX_PATTERN` 升级版（必须包含 entity 区分） | "未知" |
| `period` | chunk.year（已存在） + 兜底正则提取 "2023 年" "2024Q1" | chunk.year |
| `unit` | `_VALUE_PATTERN` 抓的单位（必须保留） | "未知" |
| `scope` | chunk.section_path 的前 30 字符匹配 "合并报表" "母公司" "国际" "国内" | "公司整体" |

### 不变量（不写实现，先写 spec）

- **`scope` 必填**：默认 "公司整体"，但 metric 是百分比时（如「占营业收入」），`scope` 应区分合并 vs 母公司
- **`period` 必填**：必须有明确年份/季度；空字符串 = 不可比（不入 conflict check）
- **`entity` 必填**：空字符串 = 不可比

### 4-A 产出

1. `app/core/conflict_key.py`（仅 dataclass + Protocol 接口）
2. `docs/plans/2026-08-23-conflict-key-spec.md`（详细规则 + 边界 case 列表）
3. 测试基线不动（468/6/13 守口）

---

## Phase 4-B: Parser / normalizer 修复

### 改动范围

| 文件 | 改动 |
|---|---|
| `app/core/conflict_key.py` | 实现 `RegexConflictKeyExtractor`（规则式提取） |
| `app/core/evidence.py` | 重写 `_METRIC_PREFIX_PATTERN` + `_VALUE_PATTERN` + `_normalize_metric` + `ConflictDetector.detect()` |
| `tests/unit/test_conflict_detector.py` | 新文件，6+ 单测 |

### 单测清单（必须全部通过）

```python
# tests/unit/test_conflict_detector.py

def test_same_metric_same_entity_same_year_diff_value_is_conflict():
    # 三一重工-挖掘机械-销售收入-2023 = 100亿 vs 105亿 → conflict (high)
    ...

def test_different_entity_same_metric_same_year_is_not_conflict():
    # 三一-挖掘机械 vs 三一-起重机械 → no conflict (different entities)
    ...

def test_different_metric_same_chunk_is_not_conflict():
    # 营业收入 100亿 vs 净利润 50亿 → no conflict (different metrics)
    ...

def test_different_year_same_metric_is_not_conflict():
    # 2023年 100亿 vs 2024年 120亿 → no conflict (year_mismatch / different periods)
    ...

def test_different_scope_same_metric_is_not_conflict():
    # 合并报表 100亿 vs 母公司 80亿 → no conflict (different scopes)
    ...

def test_unknown_entity_falls_back_to_no_conflict():
    # metric "未知" → no conflict (skip)
    ...

def test_real_value_mismatch_in_same_section_detected():
    # 同一 chunk 内 "2023 年营业收入 100亿" 然后 "2023 年营业收入 105亿" → conflict
    ...
```

### 验收门槛

- 7+ 单测全 PASS
- 测试基线守口（468/6/13 不变）
- Gate off 下 10 题 → temporal_consistent=False 应从 9/10 降至 ≤ 2/10
  - 验证脚本：`PYTHONPATH=. CHAT_MODEL=deepseek-ai/DeepSeek-V3 python eval/audit_conflict_v3.py`
  - 看 `eval/ablation/audit_conflict_v3/{Q01-Q10}.json` 的 `temporal_consistent` 字段

### 4-B 产出

1. `app/core/conflict_key.py` 实现（约 50 行）
2. `app/core/evidence.py` 修改（约 30 行 diff）
3. `tests/unit/test_conflict_detector.py` 新文件（约 100 行）
4. 3-4 个 commit（pattern 拆解 / 提取器 / 检测器 / 测试）

---

## Phase 4-C: Severity-aware Gate

### 改动契约

**当前**（[app/core/evidence.py:99](app/core/evidence.py#L99)）：
```python
temporal_consistent=(len(table.conflicts) == 0),
```

**修改后**：
```python
# 仅 high severity conflicts 触发 temporal_consistent=False
high_severity_conflicts = [c for c in table.conflicts if c.severity == "high"]
temporal_consistent=(len(high_severity_conflicts) == 0),
```

**当前**（[app/core/evidence.py:118](app/core/evidence.py#L118)）：
```python
if not result.temporal_consistent:
    return True
```

**保持不变** — gate 决策链路在 4-C 完成后自然正确（因为 temporal_consistent 已经过滤掉 medium/low）。

### Severity 分类（沿用 4-B 的 detector 输出）

| conflict_type | severity | 是否触发 gate |
|---|---|---|
| `value_mismatch` | high | ✅ 拒答 |
| `section_mismatch` | medium | ❌ 放行（warn in prompt） |
| `year_mismatch` | low | ❌ 放行 |

### 单测（`tests/unit/test_evidence_gate_severity.py`）

```python
def test_high_severity_conflict_triggers_refuse():
    # value_mismatch → gate refuse
    ...

def test_year_mismatch_does_not_trigger_refuse():
    # year_mismatch(low) → gate pass
    ...

def test_section_mismatch_does_not_trigger_refuse():
    # section_mismatch(medium) → gate pass
    ...

def test_multiple_year_mismatches_dont_trigger_refuse():
    # 5 个 year_mismatch → temporal_consistent=True → gate pass
    ...

def test_high_and_low_mixed_only_high_triggers_refuse():
    # 1 high + 5 low → temporal_consistent=False → gate refuse
    ...
```

### 4-C 产出

1. `app/core/evidence.py` line 99 修改（1 行）
2. `tests/unit/test_evidence_gate_severity.py` 新文件（约 80 行）
3. 测试基线守口
4. 1 个 commit

---

## Phase 4-D: 10q Gate pilot

### Config

```bash
PYTHONPATH=. CHAT_MODEL=deepseek-ai/DeepSeek-V3 \
  D:/miniConda/envs/rag/python.exe eval/ablation_evidence_gate.py \
  --limit 10 --configs off t05 t07 t09
```

4 个 config × 10 题 = 40 次执行。

### 通过门槛

| 指标 | 门槛 | 当前基线（gate off） |
|---|---|---|
| accuracy | ≥ 50% | Baseline-1 60% |
| false_refusal_rate | ≤ 20% | 0% (gate off) |
| refusal_rate | ≤ 30% | 0% (gate off) |
| latency_p95 overhead | < 50% vs gate off | n/a |

### 若未通过

**不要急着调 gate 阈值**。回 4-A 重新审视契约。

### 4-D 产出

1. `eval/ablation/2026MMDDHHMMSS/` 数据（gitignored）
2. 决策报告 `docs/plans/2026-08-23-phase4-d-gate-pilot-report.md`

---

## 与 P0 审计的关联

| 数据 | 用途 |
|---|---|
| `eval/ablation/audit_conflict_v3/Q01.json` | 4-B 修复前后对照（应是 9/10 FP → ≤ 2/10 FP） |
| `eval/ablation/audit_conflict_v3/Q09.json` | 4-B 修复验证（11 conflicts → 仅真实 high severity） |
| `eval/ablation/20260822T153446/` | 4-D 对照组（gate off baseline = 60%） |

## 测试基线

- Phase 4-A 不动测试（仅 spec）
- Phase 4-B/C 守口 `468 passed / 6 failed / 13 skipped`
- Phase 4-D 不跑 pytest（仅 ablation runner）

## 不变量

- 6 failed 预存失败集合禁止新增
- 每次 Phase 4 步骤独立 commit，独立 revert
- 4-A 不写 detector 实现代码（避免过早优化）
- 4-D 通过后才考虑 65 题全量

## 关键回滚点

- `app/core/conflict_key.py` 新模块（4-A）→ 删除即恢复
- `app/core/evidence.py` 修改（4-B + 4-C）→ 每次 commit 独立 revert
- 整体回滚：revert Phase 4 全部 commit 即可恢复 Baseline-1

## 关联

- P0 审计：[docs/plans/2026-08-23-p0-audit-report.md](2026-08-23-p0-audit-report.md)
- 方法论：[memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)
- Phase 4 memory：[memory/phase4-evidence-contract-repair.md](../../memory/phase4-evidence-contract-repair.md)
- Issue #1-A checkpoint：[memory/phase2-issue1a-checkpoint.md](../../memory/phase2-issue1a-checkpoint.md)
