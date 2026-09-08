# TODO

> 只放当前待办。已完成工作/实验记录在 docs/plans/（索引：docs/plans/README.md）。

**2026-09-07 封板**：RAG 核心功能收尾完成（见 docs/plans/2026-09-07-next-steps.md）。
以下为封板后明确不阻塞的后续实验项（Issue #3 Non-goal 列表之外不新增核心能力）。

## 后续实验（不阻塞，按需触发）
- [ ] Baseline-4 重跑：1381 chunks @ channel ON + coverage ≥90% 硬断言（metadata batching 已修复，前置 3R 控制组已就绪）
- [ ] 表格检索二期：chunk_type/table_headers 驱动的 table-only 检索/重排（一期元数据已落库）
- [ ] 引用 ↔ 来源对齐的自动化验证测试（当前只有 prompt 规则锁定 + 人工 citation 标注）
- [ ] Evidence Gate 校准后开启：用 eval/ablation_evidence_gate.py pilot 数据校准 false-refusal 后评估开启
- [ ] Prompt "补充通用知识" 条款与拒答边界的冲突消解（issue-1 audit 遗留，改动需重跑 65 题基线）
- [ ] 策略单点 ablation 复验（question channel / MMR λ / rerank top_k，换语料后）

## 已知技术债
- init_db() 内联 ALTER TABLE ×20+（收敛到 Alembic 迁移；约束：不改 app/store/db.py 模型）
- retrieval.py 已拆部分（_search_kb/_collect_results 并行化），仍有 800+ 行可继续按 engine/hybrid/filter/rerank/diversity 拆分
- 15 题 verified gate 无统计力的问题：后续如需回归 gate，考虑扩到 30+ 题样本
