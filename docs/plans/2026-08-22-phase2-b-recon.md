# Phase 2 B 类侦察决策 (2026-08-22)

> **Scope**: Phase 2 B 类——Evidence Gate 价值验证 + `query_type` 字段/参数清理
>
> **本文件不执行修改**，只固化为侦察结果与决策依据。任何 runtime 修改必须另开执行计划。

## 1. Decision Summary (current status)

| 项目 | 状态 | 下一步 | 备注 |
| --- | --- | --- | --- |
| **B1 Evidence Gate** 价值验证 | **VALUE VALIDATION pending** | 10-15 题 **technical smoke test**（不是价值评测） | 通过后才进 65 题 ablation |
| **B2 query_type** field (`EvidenceTable.query_type` / `organize(query_type=...)`) | **DELETE candidate, 高置信度** | 实施阶段全仓验证无 caller 后独立删除 | 见 §6.1 |
| **B2 query_type** 参数（pipeline.py 透传） | **DELETE candidate, 高置信度** | 跟随 field 删除 | 见 §6.1 |
| **B2 unused weighting code** (`compute_chunk_importance` / `rerank_chunks`) | **DELETE candidate, 待零 caller 验证** | 实施阶段二次确认无 internal-only 调用后删除 | 见 §6.1 |
| **B2 `query_type` concept**（"问题分类"作为产品/系统概念） | **KEEP AS CONCEPT** | 不为 dead field 提前设计新 ontology | 见 §6.4 |

**B 类核心结论**：**Evidence Gate 与 `query_type` 在运行时完全独立**——Gate 决策只读 `coverage` + `temporal_consistent`，与 `query_type` 无任何路径耦合。因此 B1 与 B2 可以**分别决策、分别提交、分别回滚**。

## 2. Evidence Gate 当前 I/O 契约

### 2.1 Gate 决策输入（只读 2 字段）

[app/core/evidence.py:106-122](app/core/evidence.py#L106-L122) `evidence_gate_should_refuse`：

```python
def evidence_gate_should_refuse(result: EvidenceResult, threshold: float) -> bool:
    if threshold <= 0: return False
    if not result.temporal_consistent: return True   # 字段 1
    if result.coverage < threshold: return True      # 字段 2
    return False
```

**只读 `EvidenceResult` 的 2 个字段**：`coverage` + `temporal_consistent`。**不读 query_type**。

### 2.2 上游数据流

[app/core/pipeline.py:435-454](app/core/pipeline.py#L435-L454)：

```
unique_chunks (retrieval 结果)
  ↓
evidence_organizer.organize(query, sub_question_chunks, query_type="complex"/"simple")
  ↓
EvidenceTable (slots, conflicts)
  ↓
build_evidence_result(table)
  ↓
EvidenceResult(coverage, temporal_consistent, conflicts, sources, coverage_by_year)
  ↓
evidence_gate_should_refuse(result, threshold) → True/False
```

### 2.3 关键字段来源

| Gate 读取字段 | 来源 | 受 query_type 影响？ |
| --- | --- | --- |
| `coverage` | `covered_slots / total_slots`，clip [0,1]；每个子问题是否拿到 chunk | **NO**（slots 构造只依赖 sub_question_chunks，与 query_type 无关） |
| `temporal_consistent` | `len(table.conflicts) == 0`，由 `ConflictDetector.detect()` 产出（仅当 `has_multiple_docs`） | **NO**（ConflictDetector 不读 query_type） |

→ **Gate 决策完全独立于 `query_type`**。

## 3. `query_type` 三套 ontology — 当前状态

### 3.1 词汇表

| 来源 | 词汇 | 维度 |
| --- | --- | --- |
| [app/core/evidence.py:384](app/core/evidence.py#L384) 默认值 / [pipeline.py:440](app/core/pipeline.py#L440) 调用 | `"complex" / "simple"` | complexity |
| [app/core/evidence.py:476, 490](app/core/evidence.py#L476) 分支 | `"comparison" / "summary"` | task type |
| [tests/unit/test_evidence.py:28, 48](tests/unit/test_evidence.py#L28) | `"comparison" / "summary"` | task type（仅测试） |
| [docs/plans/2026-08-19-cross-doc-rag-improvement.md:93](docs/plans/2026-08-19-cross-doc-rag-improvement.md#L93) | `"comparison" / "summary" / "single"` | task type（历史计划） |

### 3.2 生产路径消费方

| 消费者 | 状态 |
| --- | --- |
| `EvidenceOrganizer.organize` | 写入 `EvidenceTable.query_type` |
| `build_evidence_result` | 不读 query_type |
| `evidence_gate_should_refuse` | 不读 query_type |
| `ConflictDetector.detect` | 不读 query_type |
| `EvidenceOrganizer.compute_chunk_importance` | **会读 query_type**——但全仓 0 caller |
| `EvidenceOrganizer.rerank_chunks` | **会读 query_type**——但全仓 0 caller |

→ **生产路径上 query_type 是 dead-in-storage**：写入后从未被任何生产代码读取。  
→ **分支不一致问题**：`compute_chunk_importance` 检查 `"comparison" / "summary"`，但 pipeline 写入的是 `"complex" / "simple"`——即使该函数被调用，分支也永不触发。

### 3.3 Historical context (informational)

历史 plan 提到"控制证据表呈现顺序"与"接通时序一致性"——**从未实现**。此仅作 context，不作决策证据。

## 4. B1 ↔ B2 独立性（核心结论）

### 4.1 数据流依赖图

```
                    retrieval
                       ↓
                sub_question_chunks
                       ↓
            ┌───── EvidenceOrganizer.organize (query_type="complex"/"simple")
            ↓                                    ↓
     EvidenceTable                       query_type field
   (slots, conflicts)                    (写入后 dead-in-storage)
            ↓
     build_evidence_result
            ↓
     EvidenceResult(coverage, temporal_consistent, ...)
            ↓
     evidence_gate_should_refuse  ←  完全不读 query_type
```

### 4.2 推论

- **Gate 决策完全独立于 `query_type` ontology 决策**
- B1（Gate 价值验证）不需要先解决 B2（query_type ontology）
- B2（删除 query_type）不需要先做 Gate 实验
- **两个 B 子任务可以独立 commit、独立 eval、独立 revert**

## 5. B1 — Evidence Gate 价值验证

### 5.1 Stage 1: Technical smoke test (10-15 题)

**目的不是判断 Gate 有没有价值**——只验证 Gate 能正常工作。

| 验证项 | 通过标准 |
| --- | --- |
| Gate 在 `evidence_gate_enabled=True` 时被触发 | pipeline.py:435 路径被执行 |
| 真的产生 `evidence_refused` SSE 状态事件 | SSE 流出现 `evidence_refused` |
| `degraded` 事件 payload 正确 | `reason="evidence_gate_refused"` |
| `threshold` 参数真的生效 | 不同 threshold 下覆盖率拒绝结果不同 |
| `coverage` 计算正确 | smoke 测试题手工核对 coverage 值 |
| `temporal_consistent` 计算正确 | 同一题两块相互冲突的数据产生 conflicts |
| refuse 路径不抛异常 | 全程无 traceback |
| fallback 路径正常（gate 异常时降级） | 强制 mock 异常确认 `evidence_gate.failed_falling_through` log 出现 |

**通过 Stage 1 后才进入 Stage 2**。

### 5.2 Stage 2: Value ablation (65 题全集)

| 对比配置 | 说明 |
| --- | --- |
| `gate off`（默认） | 当前 baseline |
| `gate on, threshold=0.5` | 宽松 |
| `gate on, threshold=0.7` | 当前默认值 |
| `gate on, threshold=0.9` | 严格 |

### 5.3 指标矩阵

**核心指标**（决定 Gate 命运）：

| 指标 | 定义 | 解读 |
| --- | --- | --- |
| Generation accuracy | 是否更准确 | gate 拒绝的错误答案数 vs gate 允许的错误答案数 |
| Refusal rate | `refused / total` | gate 触发的频率 |
| False refusal rate | `(本该给出但被拒) / 本该给出的题数` | 误拒代价 |
| Refusal precision | `正确拒答 / 所有拒答` | 拒答判断的准确性 |
| Refusal recall | `正确拒答 / 所有应拒答` | 拒答覆盖度 |

**辅助指标**（诊断用，不主导决策）：

| 指标 | 用途 |
| --- | --- |
| Latency p50/p95 | Evidence 计算成本 |
| SSE event trace | gate 是否真的工作（status + degraded + done） |
| Citation quality | 当前 Gate 不直接判 citation，列为观察项 |

### 5.4 Outcome → decision 映射

| outcome | decision |
| --- | --- |
| 准确率↑ + false refusal 可接受 | **REFACTOR + ENABLE** |
| 仅 0.5 有收益 | **KEEP + 调参文档化** |
| 0.7/0.9 有但 0.5 没收益（阈值敏感） | **KEEP + 加自适应阈值探索** |
| 全部组合无收益 | **DELETE**（移除 evidence.py + gate 开关 + query_type 整套） |

> **DELETE 是合理结局**——不要默认"现成能力必须启用"。4 个阈值都证伪时，删除就是最理性的工程决定。

## 6. B2 — `query_type` 清理

### 6.1 三层分级删除判断

| 子项 | 决策 | 置信度 | 证据 |
| --- | --- | --- | --- |
| `EvidenceTable.query_type` field | **DELETE** | 高 | 生产路径 0 reader；分支永不触发 |
| `organize(query_type=...)` 参数 + 默认值 + pipeline.py:440 透传 | **DELETE** | 高 | 同上 + 跟随 field 删除 |
| `compute_chunk_importance` / `rerank_chunks` 整套加权算法 | **DELETE candidate** | 中-高 | 0 caller 已确认；实施阶段再做一次 grep 验证无 internal-only 调用 |

**不合并为同一个判断**——field 死 ≠ 辅助算法死。两者证据强度不同。

### 6.2 Required changes (execution checklist)

**B2.a — field/parameters 删除**：
- [ ] `app/core/evidence.py:384` 删除 `query_type` 参数与默认 "complex"
- [ ] `app/core/evidence.py:416` 删除 `EvidenceTable.query_type` 字段
- [ ] `app/core/pipeline.py:440` 删除 query_type 透传

**B2.b — weighting code 删除**：
- [ ] `app/core/evidence.py:437-498` 删除 `compute_chunk_importance` 整方法
- [ ] `app/core/evidence.py:500-...` 删除 `rerank_chunks` 整方法（确认无 caller 后）

### 6.3 Validation (执行 B2 时)

```bash
# Baseline
grep -rn "query_type\|compute_chunk_importance\|rerank_chunks" app/ tests/ tools/ eval/

# 删除后必须 0 命中（除 history plan 文档与本 decision doc）：
grep -rn "EvidenceTable.query_type\|compute_chunk_importance" app/

# 运行时验证
pytest -q  # 维持 459 passed / 6 failed / 13 skipped
ruff check  # 退出 0
```

### 6.4 显式原则：不为 dead field 提前设计 ontology

> **不要因为 `query_type` 字段死了，就急着设计一个"更漂亮"的 ontology。**

当前代码根本**没有消费** `query_type`。"问题分类"作为概念本身**保留为未来可能性**，但只有在出现真实 consumer / 产品需求时才设计 schema。

**克制决策树**：

```
删除 query_type 实现
        ↓
保留 "问题分类" 作为未来概念
        ↓
仅当出现真实 consumer 时再设计 schema（query_complexity? query_intent? evidence_requirement?）
```

这比现在提前设计 `query_complexity / query_intent / evidence_requirement` 三字段方案**克制得多**。

### 6.5 Explicit non-changes

- 不在 B 类重新设计 ontology
- 不提前定义新的查询类型枚举
- 不删除 SYSTEM_PROMPT 中"复杂问题"概念（与 B2 无关，属于 A 类已决策的保留项）

## 7. Validation strategy

### 7.1 B1 Stage 1 (smoke test) 验证协议

| 验证项 | 命令 / 操作 |
| --- | --- |
| gate 真触发 | `EVIDENCE_GATE_ENABLED=true pytest tests/integration/test_pipeline_*.py -v` 看 SSE 流 |
| 拒绝路径 | 构造低 coverage 题（mock 一个空 sub_question_chunks）确认 refuse event |
| threshold 生效 | threshold=0.99 vs 0.01 对比同一题 refusal 差异 |
| temporal_consistent | mock 一个跨文档冲突数据，确认 conflicts 字段非空 |

### 7.2 B2 删除验证协议

见 §6.3。

### 7.3 Final acceptance (整体 B 类执行后)

- 全部 ablation 数据落盘于 `eval/evidence_gate_ablation_<timestamp>.json`
- 本 decision doc 更新 §1 状态表（VALUE VALIDATION → DONE / DEFER / DELETE 等）
- 下一阶段决策固化于新 plan doc

## 8. Explicit non-decisions

**本轮（侦察期）不做**：

- ✗ 不接通 Evidence Gate
- ✗ 不修改 `evidence_min_coverage` 阈值
- ✗ 不修改 `query_type` 字段
- ✗ 不删除 `query_type` 字段（属 B2 执行期）
- ✗ 不删除 `compute_chunk_importance` / `rerank_chunks`（属 B2.b 执行期）
- ✗ 不改 `EvidenceResult` schema
- ✗ 不跑 65 题正式 ablation
- ✗ 不重设计 query ontology

**当前状态（侦察完成）**：B1 待 Stage 1 smoke test 启动；B2 待实施期执行。

---

## 9. Method note（B 类侦察形成的可复用原则）

本次 B 类侦察的最重要成果不是发现了"Gate 与 query_type 独立"——而是形成了一套**可在 Phase 后续阶段复用的 Gate 价值评估方法**：

1. **不预设"现成能力值得启用"**——每个 Strategy 都必须经过"价值验证或 DELETE"二选一
2. **依赖关系先于功能实现**——B1 / B2 拆开证明独立的根因是数据流图清晰
3. **smoke test 与价值实验分离**——技术验证 ≠ 价值结论；混在一起会得到"Gate 工作了但不知是否有用"的二阶混淆
4. **DELETE 是合理结局**——4 种 outcome 都有明示映射
5. **field / concept 严格区分**——field DELETE 不等于 concept DELETE；不为 dead field 提前设计 ontology
6. **核心指标 / 辅助指标分离**——拒绝 precision/recall 不混进单一公式；citation quality 等次要项不主导决策

这套方法可复用于 Phase 2 C 类（`` 协议解耦）与 D 类（Alembic）。
