# ConflictKey 数据契约 Spec (Phase 4-A)

> **范围**：定义 `ConflictKey` 5 元组数据结构与 `ConflictKeyExtractor` 接口契约
>
> **不写 detector/extractor 实现** — 实现见 Phase 4-B
>
> **目标读者**：Phase 4-B 实现者 / Reviewer / 后续维护者

---

## 1. 设计目标

替代当前 `app/core/evidence.py` 中 `_normalize_metric` 的单字段归一化逻辑，从单维度 (`metric`) 升级为 5 维度比较。

**根本问题**（来自 P0 审计）：
- 当前 detector 把「挖掘机械:销售收入276.36亿」与「起重机械:销售收入130亿」归为同一 metric（`销售收入`），触发假阳性
- 真正的 comparison key 应区分 entity（产品线）+ period（年份）+ scope（合并/母公司）

**设计原则**：
1. **契约优先**：先把数据形状写清楚，再写实现
2. **Best-effort**：失败字段填 `UNKNOWN`，caller 必须跳过
3. **Pure 模块**：本模块不依赖 LLM/DB/网络
4. **可演进**：未来可以加 LLM extractor，不动数据结构

---

## 2. ConflictKey 数据结构

### 2.1 5 元组字段

| 字段 | 类型 | 取值范围 | 提取失败时 |
|---|---|---|---|
| `entity` | `str` | "挖掘机械" / "混凝土机械" / "起重机械" / "公司整体" / "未知" | `"未知"` |
| `metric` | `str` | "营业收入" / "净利润" / "毛利率" / "销售收入" / "未知" | `"未知"` |
| `period` | `str` | "2023年" / "2024年" / "2024Q1" / chunk.year / "" | `""` |
| `unit` | `str` | "亿元" / "万元" / "%" / "个百分点" / "股" / "未知" | `"未知"` |
| `scope` | `str` | "合并报表" / "母公司" / "国际" / "国内" / "海外" / "公司整体" | `"公司整体"` |

### 2.2 Sentinels（模块级常量）

```python
UNKNOWN = "未知"        # 不可识别
DEFAULT_SCOPE = "公司整体"  # 无具体 scope 时的兜底
```

### 2.3 比较语义

```python
def matches_except_value(self, other: ConflictKey) -> bool:
    """True iff all 5 fields match → may conflict (if values differ)."""
```

只有当 5 字段全部相同，values 不同时才是 **TRUE conflict**（high severity）。

任何字段不同 → **NOT a conflict**（不同事物，不可比）。

### 2.4 Frozen + Hashable

`@dataclass(frozen=True)` 使 ConflictKey 不可变 + 可哈希 → 可作为 `dict` key / `set` 元素。

```python
keys_by_entity = defaultdict(set)  # 未来可按 entity 分组
for mv in all_metric_values:
    keys_by_entity[mv.key.entity].add(mv.key)
```

---

## 3. ConflictKeyExtractor Protocol 接口

### 3.1 接口契约

```python
class ConflictKeyExtractor(Protocol):
    def extract(
        self,
        chunk: RetrievedChunk,
        value_span: tuple[int, int],
    ) -> ConflictKey:
        ...
```

### 3.2 输入输出契约

| 输入 | 含义 |
|---|---|
| `chunk` | RetrievedChunk with `.text`, `.year`, `.document_id`, `.section_path` 等 metadata |
| `value_span` | `(start, end)` 字符偏移，标记 chunk.text 中数值的边界 |

| 输出 | 含义 |
|---|---|
| `ConflictKey` | 5 元组 best-effort 提取结果 |

### 3.3 Best-effort 语义

**所有字段都必须返回**（即使失败也要返回 sentinel），caller 通过 sentinel 判断是否参与 conflict check：

| 字段 | 失败兜底 | caller 应跳过条件 |
|---|---|---|
| `entity` | `"未知"` | 是 → 跳过 |
| `metric` | `"未知"` | 是 → 跳过 |
| `period` | `""`（空字符串） | 是 → 跳过 |
| `unit` | `"未知"` | 是 → 跳过（不同 unit 的值可能可比，但本期先跳过） |
| `scope` | `"公司整体"` | 否（默认值，不跳过） |

**跳过规则**（caller 实现，4-B 写）：
```python
def is_comparable(key: ConflictKey) -> bool:
    return (
        key.entity != UNKNOWN
        and key.metric != UNKNOWN
        and key.period != ""
        and key.unit != UNKNOWN
    )
```

### 3.4 Pure Function 约束

- 无副作用
- 不调用 LLM（本期 regex-based；LLM-based 留作 4-B 后续）
- 不读写 DB
- 不依赖全局状态（除非配置参数）

---

## 4. 字段提取规则（接口契约，仅文档化）

> **本期只定义规则，不写实现**。Phase 4-B 实现 regex / LLM extractor。

### 4.1 entity 提取

**目标**：从 chunk 文本中识别出具体的实体（产品线/部门/区域）。

| 模式 | 例子 |
|---|---|
| `X 机械` | 挖掘机械 / 混凝土机械 / 起重机械 / 路面机械 / 桩工机械 |
| `X 业务` | 国际业务 / 国内业务 |
| `X 区域` | 亚洲 / 欧洲 / 美洲 |
| 默认 | 公司整体 |

**失败兜底**：`"未知"`

### 4.2 metric 提取

**目标**：识别指标名（营业收入/净利润等）。

**改进方向**（取代现有 `_METRIC_PREFIX_PATTERN`）：
- 必须包含 entity 区分（不能光匹配「收入」后缀）
- 列出所有可能的 metric 模式

**典型 metric 清单**：
- 营业收入 / 营业总收入 / 销售收入（**注意区分**！）
- 净利润 / 归属于上市公司股东的净利润 / 归属母公司净利润
- 总资产 / 归属于上市公司股东的净资产
- 经营现金流 / 经营活动产生的现金流量净额
- 毛利率 / 净利率 / 海外业务毛利率
- 销售商品收到的现金 / 回款率
- 研发费用 / 研发投入
- 市场份额 / 销量 / 产量

**失败兜底**：`"未知"`

### 4.3 period 提取

**优先级**：
1. `chunk.year`（已存在，结构化字段，最可靠）
2. 文本 regex 兜底：`r"(20\d{2})\s*年(?:第?[一二三四]?季度|Q[1-4])?"`

**失败兜底**：`""`（空字符串 = 不可比）

### 4.4 unit 提取

**目标**：识别数值单位。

**支持的 unit**：
- 金额：亿元 / 万元 / 百万元 / 千万 / 百万 / 十亿 / 万亿
- 比率：% / 个百分点
- 数量：股 / 万股 / 亿股
- 其他：（未来扩展）

**失败兜底**：`"未知"`

### 4.5 scope 提取

**优先级**：
1. chunk.section_path 前 30 字符匹配关键词：
   - "合并" / "合并报表" → "合并报表"
   - "母公司" → "母公司"
   - "国际" / "海外" → "国际" 或 "海外"
   - "国内" → "国内"
2. 默认："公司整体"

**失败兜底**：`DEFAULT_SCOPE = "公司整体"`

---

## 5. 边界 Case 与 Test Fixtures

### 5.1 必须 PASS 的边界 case

| # | 输入 (chunk.text 摘要) | 期望 entity | 期望 metric | 期望 period | 期望 unit | 期望 scope | 期望 comparable? |
|---|---|---|---|---|---|---|---|
| 1 | "挖掘机械:销售收入276.36亿元" | 挖掘机械 | 销售收入 | chunk.year | 亿元 | 公司整体 | ✅ |
| 2 | "起重机械:销售收入131.15亿元" | 起重机械 | 销售收入 | chunk.year | 亿元 | 公司整体 | ✅ |
| 3 | "归属于上市公司股东的净利润59.75亿元" | 公司整体 | 归属于上市公司股东的净利润 | chunk.year | 亿元 | 公司整体 | ✅ |
| 4 | "本期毛利率27.72%" | 公司整体 | 毛利率 | chunk.year | % | 公司整体 | ✅ |
| 5 | "占营业收入的80.29%" | 公司整体 | 占营业收入 | chunk.year | % | 公司整体（合并报表）| ✅ |
| 6 | "8,289,375 股" | 公司整体 | 未知（无法从前缀判断）| chunk.year | 股 | 公司整体 | ❌（metric=未知）|
| 7 | "5.9%" (无前缀) | 公司整体 | 未知 | chunk.year | % | 公司整体 | ❌（metric=未知）|
| 8 | "同比增长31.98%" | 公司整体 | 同比增长 | chunk.year | % | 公司整体 | ✅ |
| 9 | chunk.year = "" | 任意 | 任意 | "" | 任意 | 任意 | ❌（period=空）|
| 10 | chunk.section_path 含 "母公司" | 任意 | 任意 | chunk.year | 任意 | 母公司 | ✅ |

### 5.2 关键 conflict / non-conflict case（用于 Phase 4-B detector 单测）

| # | key1 | key2 | values 关系 | 期望 conflict? |
|---|---|---|---|---|
| C1 | (挖掘机械, 销售收入, 2023年, 亿元, 公司整体) | (起重机械, 销售收入, 2023年, 亿元, 公司整体) | 276.36 vs 130 | ❌（不同 entity）|
| C2 | (公司整体, 销售收入, 2023年, 亿元, 公司整体) | (公司整体, 销售收入, 2024年, 亿元, 公司整体) | 100 vs 120 | ❌（不同 period）|
| C3 | (公司整体, 营业收入, 2023年, 亿元, 合并报表) | (公司整体, 营业收入, 2023年, 亿元, 母公司) | 800 vs 750 | ❌（不同 scope）|
| C4 | (公司整体, 营业收入, 2023年, 亿元, 合并报表) | (公司整体, 营业收入, 2023年, 亿元, 合并报表) | 800 vs 805 | ✅ TRUE conflict (high severity) |
| C5 | (公司整体, 占比, 2023年, %, 公司整体) | (公司整体, 占比, 2023年, %, 公司整体) | 80.29 vs 75.06 | ✅ TRUE conflict |
| C6 | (公司整体, 增长, 2023年, %, 公司整体) | (公司整体, 增长, 2024年, %, 公司整体) | 5.9 vs 12.15 | ❌（不同 period → year_mismatch）|

---

## 6. 不在 Phase 4-A 范围（Out of Scope）

| 项 | 推迟到 |
|---|---|
| `RegexConflictKeyExtractor` 实现 | Phase 4-B |
| `LLMConflictKeyExtractor` 实现（如果需要）| Phase 4-B 后续 |
| 修改 `_METRIC_PREFIX_PATTERN` / `_VALUE_PATTERN` / `_normalize_metric` | Phase 4-B |
| 修改 `ConflictDetector.detect()` | Phase 4-B |
| 新单测 `tests/unit/test_conflict_detector.py` | Phase 4-B |
| 修改 `temporal_consistent` 计算逻辑 | Phase 4-C |
| 修改 Gate 决策逻辑 | Phase 4-C |
| 10q Gate pilot | Phase 4-D |

---

## 7. Open Questions（Phase 4-B 实施前可能需要决策）

| # | 问题 | 建议默认 | 决策时机 |
|---|---|---|---|
| Q1 | entity 提取失败 → UNKNOWN 时，是否跳过整条 key？ | 跳过（保守，避免假阳性）| 4-B 实施时 |
| Q2 | period = "" 时是否真跳过？（可能错过跨年 conflict）| 跳过（默认不可比）| 4-B 实施时 |
| Q3 | scope 兜底 "公司整体" 是否合理？（会让合并/母公司误合并）| 4-B 评估覆盖度后再定 | 4-B 实施时 |
| Q4 | unit 标准化（"千" vs "千元" vs "千人民币"）需要提前？ | 4-B 简单 regex 标准化即可 | 4-B 实施时 |
| Q5 | 是否需要 unit conversion（亿元 vs 万元 转换）？ | 暂不做（增加复杂度）| 4-B 实施时 |

---

## 8. 与现有代码的集成点

### 8.1 `app/core/evidence.py` 集成点

| 位置 | 现状 | 4-A 关联 |
|---|---|---|
| `MetricValue` (line 176) | 无 key 字段 | 4-B 给 MetricValue 加 `key: ConflictKey` 字段 |
| `Conflict` (line 190) | `metric: str` | 4-B 改为 `key: ConflictKey`（或保留 metric + 加 key）|
| `ConflictDetector.detect()` (line 225) | 按 `_normalize_metric` 分组 | 4-B 改为按 `ConflictKey` 分组 |
| `_classify_conflict()` (line 318) | 按 docs/years/sections 分类 | 4-B 分类逻辑不变，但基于 ConflictKey |

### 8.2 `app/core/conflict_key.py` 集成

- 本期新建，仅 dataclass + Protocol
- 不被 evidence.py 引用（避免污染 4-B 实施空间）
- 4-B 实施时 evidence.py 改为 `from app.core.conflict_key import ConflictKey, ConflictKeyExtractor`

---

## 9. 验证门槛（Phase 4-A 验收）

| 项 | 验证方法 | 期望 |
|---|---|---|
| dataclass 正确性 | `tests/unit/test_conflict_key.py` 单测 | 全 PASS（≥ 6 个 case） |
| Protocol 正确性 | mypy / pyright 可选验证 | 通过 |
| 测试基线 | `pytest tests/unit -q` | 468 passed → ≥ 474 passed（+6+）+ 6 failed 不变 + 13 skipped 不变 |
| ruff | `ruff check` + `ruff format` | 全绿 |
| 不影响 evidence.py | `git diff app/core/evidence.py` | 无 diff |

---

## 10. 关键决策（已 sealed）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 5 元组结构 `(entity, metric, period, unit, scope)` | 解决 4 个 detector bug 的根本：单维度归一化不够 |
| D2 | `frozen=True` dataclass | 可哈希 → 可作 dict key |
| D3 | Protocol 而非 ABC | duck typing 更灵活；LLM extractor 可独立测试 |
| D4 | UNKNOWN 兜底值 | caller 可明确跳过 |
| D5 | DEFAULT_SCOPE = "公司整体" | 无具体 scope 时不强行分类 |
| D6 | unit 兜底 UNKNOWN 且 caller 跳过 | 避免不同 unit 误合并（本期先保守）|
| D7 | period 兜底空字符串且 caller 跳过 | 跨年数据不可比 |

---

## 11. 关联

- Phase 4 plan: [docs/plans/2026-08-23-phase4-evidence-contract-repair.md](2026-08-23-phase4-evidence-contract-repair.md) §Phase 4-A
- P0 审计: [docs/plans/2026-08-23-p0-audit-report.md](2026-08-23-p0-audit-report.md)
- 方法论: [memory/eval-methodology-feedback-2026-08-23.md](../../memory/eval-methodology-feedback-2026-08-23.md)
- Memory: [memory/phase4-evidence-contract-repair.md](../../memory/phase4-evidence-contract-repair.md)
- 实现文件: [app/core/conflict_key.py](../../app/core/conflict_key.py)
- 测试文件: [tests/unit/test_conflict_key.py](../../tests/unit/test_conflict_key.py)
