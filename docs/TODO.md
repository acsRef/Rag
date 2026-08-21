# TODO

> 只放当前待办。已完成工作/实验记录在 docs/plans/（索引：docs/plans/README.md）。

## P0 核心正确性
- [ ] H 类（错误前提纠偏）：需要"反面证据检索"，prompt 层已到瓶颈（53.3%）
- [ ] I 类（拒答边界）：年份一致性，波动大（73.3%，需 generation eval 复验）
- [ ] C 类（跨文档对比）：根因是每年内抓错 chunk，需按年定向语义检索

## P1 工程质量（Phase 2+，按序）
- [ ] Phase 2：complexity/sub_dependencies 契约重构（prompt contract migration，须在两轮评测之间执行）
- [ ] Phase 3：Evidence Gate 接通 + 数据契约重定义（text[:300] 截断随此处理）
- [ ] Phase 4：<think>/reasoning 协议解耦（单独设计任务）
- [ ] Phase 5：Alembic 迁移收敛 init_db() 的 ALTER TABLE
- [ ] Eval 结果版本化（experiment_id/git_sha/config snapshot）
- [ ] Prompt contract 单测 + RAG contract tests（year/permission/embedding_version 过滤等）

## P2 优化
- [ ] 策略单点 ablation 复验（question channel / MMR λ / rerank top_k）

## P3 未来
- [ ] 多轮对话上下文感知检索
- [ ] 表格结构化理解
- [ ] 用户反馈闭环

## 已知技术债
- retrieval.py 852 行，待 Phase 2+ 拆包（engine/hybrid/filter/rerank/diversity）
- init_db() 内联 ALTER TABLE ×20+（见 P1 Alembic 项）
