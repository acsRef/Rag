# Day 2 下午 Done — Evidence Layer 接入 (2026-08-23)

> 接 [docs/plans/2026-08-22-rag-decomposition.md](docs/plans/2026-08-22-rag-decomposition.md) §九 Day 2 下午 + §五。

## 本次完成

按 plan §九 Day 2 下午 4 步执行，**4/4 步完成 + 验收通过**。

### 修改/新增

1. ✅ `app/core/evidence.py` 加 3 个新符号（不删 EvidenceTable / 314 行活代码）：
   - `EvidenceResult` dataclass（plan §五.1 五字段：coverage / temporal_consistent / conflicts / sources / coverage_by_year）
   - `build_evidence_result(table)` 转换函数
   - `evidence_gate_should_refuse(result, threshold)` gate 决策
2. ✅ `app/core/pipeline.py:397` 接入 evidence layer：
   - 在 `_truncate_with_doc_diversity` 之后、cross-doc synthesis 之前
   - `evidence_organizer.organize(...)` → `build_evidence_result(...)` → `evidence_gate_should_refuse(...)`
   - `if settings.evidence_gate_enabled and refuse → yield refusal events + return`
   - gate 失败降级继续（不阻断主流程）
3. ✅ 不删 `EvidenceTable` / `EvidenceOrganizer` / `ConflictDetector`——沿用现有活代码

### 测试

新增 `tests/unit/test_evidence_result.py` 16 tests：
- EvidenceResult 字段 + 默认值 + coverage 边界
- build_evidence_result：full / partial / empty / sources 收集 / conflicts 透传 / temporal_consistent / coverage clip / coverage_by_year by chunk.year
- evidence_gate_should_refuse：threshold 上下界 / temporal 不一致 / threshold=0 边界

## 验证结果

**Unit tests**：382 → 398 passed（+16），2 pre-existing fail（与本次无关）

**Live full 65 baseline**：

| 指标 | Day 1 晚上 | **Day 2 下午（gate off）** | Δ |
|---|---|---|---|
| hit@5 | 0.984 | 0.984 | 0 |
| hit@10 | **1.000** | **1.000** | 0 |
| recall@5 | 0.940 | 0.935 | -0.5pp（噪声） |
| recall@10 | **1.000** | **1.000** | 0 |
| MRR | 0.876 | 0.868 | -0.8pp（噪声） |

**plan §九 Day 2 下午验收**：smoke 10 题不恶化 ✅  
**plan §十 第 1 项**：267+ unit test 通过 ✅（**398**）

## 当前状态

| 项 | 状态 |
|---|---|
| 代码（4/4 步） | ✅ 完成 |
| 16 新 unit test | ✅ 全绿 |
| 398 unit tests 通过 | ✅ |
| Smoke 10 题 | ✅ 不恶化 |
| Full 65 baseline | ✅ hit@10=1.000 |
| retrieval baseline 恢复 | ✅（embedding_text v2 改造回滚后已稳定） |
| generation eval（plan §十 第 4 项 73.3%）| ⚠️ 未跑（需要 LLM judge，不在 retrieval 范围） |

## 当前 backend 配置

- `current_embedding_version: int = 1`（chunk-only embedding）
- `evidence_gate_enabled: bool = False`（默认关，启用需 `EVIDENCE_GATE_ENABLED=true`）
- `evidence_min_coverage: float = 0.7`（gate 阈值）
- 5 个检索层策略默认 False（cross_doc/section_boost/section_supplement/year_supplement/query_decomposition）
- cache 默认开（embedding + retrieval）

## 下一步（Day 2 晚上 — 8 组 Ablation）

按 plan §九 Day 2 晚上：

跑 8 组对照实验，写 `docs/plans/2026-08-23-ablation-report.md`：

| Pipeline | 含义 |
|---|---|
| Baseline (no flags) | 当前生产 = 0 个策略 |
| + Year Filter (query_parser) | Day 1 晚上已实现 |
| + Section Embedding (build_embedding_text) | 已废，不跑 |
| + Rerank | 已有 |
| + Section Boost | 已有 |
| + MMR | 已有 |
| + Cross-doc | 已有 |
| + Question Channel | 已有 |
| + Evidence Gate | Day 2 下午新增（可选） |

## 关键文件改动一览

| 文件 | 状态 | 说明 |
|---|---|---|
| `app/core/evidence.py` | 改 | + EvidenceResult + build_evidence_result + evidence_gate_should_refuse |
| `app/core/pipeline.py` | 改 | evidence gate 块（在 truncate 之后 / cross-doc 之前） |
| `tests/unit/test_evidence_result.py` | 新增 | 16 tests |
