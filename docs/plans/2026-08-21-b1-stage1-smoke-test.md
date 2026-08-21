# B1 Stage 1 — Evidence Gate 确定性技术场景 smoke test (2026-08-21)

> **Phase 2 B 类执行第一阶段**（详见 [docs/plans/2026-08-22-phase2-b-recon.md](2026-08-22-phase2-b-recon.md) §5.1）

## 1. Scope

构造**确定性固定技术场景**验证 Evidence Gate 代码路径、SSE 事件序列、fallback 行为。**不消耗 LLM API、不跑自然语言题、不评估生成质量**。Stage 1 全部通过后才进入 Stage 2（65 题价值 ablation）。

## 2. 6 个技术场景（来自 recon §5.1）

| # | 场景 | 验证 |
| --- | --- | --- |
| 1 | 正常通过 | coverage 充分 + 无冲突 → gate 不拒答 |
| 2 | 低 coverage → refuse | coverage < threshold → `evidence_refused` 事件 |
| 3 | temporal conflict → refuse | temporal_consistent=False → 拒答（无论 coverage） |
| 4 | threshold 边界 | 严格小于关系；threshold=0 永不拒答 |
| 5 | gate exception → fallback | organizer 抛异常 → 降级 + 日志记录 |
| 6 | SSE event sequence | refuse 路径事件顺序固定 |

## 3. 实现

**新文件**：`tests/unit/test_evidence_gate_smoke.py`

**测试组合**（单元 + 集成）：

| 测试 | 类型 | 覆盖场景 |
| --- | --- | --- |
| `test_gate_normal_high_coverage_no_conflict` | 单元（直接调 `evidence_gate_should_refuse`） | #1 |
| `test_gate_low_coverage_refuses` | 单元 | #2 |
| `test_gate_temporal_conflict_refuses_even_high_coverage` | 单元 | #3 |
| `test_gate_threshold_zero_never_refuses` | 单元 | #4 |
| `test_gate_threshold_strict_inequality_at_boundary` | 单元（多 threshold 值） | #4 |
| `test_pipeline_low_coverage_emits_evidence_refused_sse` | 集成（mock pipeline.execute） | #2 + #6 |
| `test_pipeline_temporal_conflict_refuse_reason_text` | 集成 | #3 + #6 |
| `test_pipeline_gate_exception_falls_through` | 集成（mock raise + caplog） | #5 |
| `test_pipeline_refuse_event_order_status_degraded_done` | 集成（断言顺序） | #6 |

**复用现有模式**：[tests/unit/test_evidence_gate_regression.py](tests/unit/test_evidence_gate_regression.py) 的 mock 装配（DB / retrieval / organizer / build_evidence_result）。

## 4. 验证协议

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_evidence_gate_smoke.py -v
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q   # 全量 unit 基线守住
D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/ tools/ eval/ tests/
```

**通过标准**：
- 新测试全部 PASS
- 全量 unit `400 passed` 基线守住（加新测试后变 400+N passed，但 failed 集合不变）
- ruff 退出码 0

## 5. Out of scope (本轮不做)

- Stage 2：65 题价值 ablation（待 Stage 1 全过后另开 plan）
- B2：query_type 三层 DELETE（独立 plan）
- 任何 evidence_min_coverage 默认值修改
- 任何 LLM 调用 / 真实 API
- 任何 EvidenceGate 行为改进（仅验证现状）

## 6. 与现有 regression test 的关系

| 文件 | 职责 |
| --- | --- |
| `tests/unit/test_evidence_gate_regression.py` | **Phase 1 logger bug 回归锁**——固定 evidence_gate logger 存在 + refuse 路径不抛异常 |
| `tests/unit/test_evidence_gate_smoke.py`（本次新增） | **Phase 2 B1 Stage 1 技术场景**——覆盖 6 个技术验收场景 |

两文件职责不重叠：前者锁历史 bug 修复，后者验证 Gate 功能完整性。
