# Architecture Decision Summary

> 主要技术决策、理由与 trade-off，按主题聚合。详细过程与实验数据见 [docs/plans/README.md](plans/README.md) 中的对应计划文档。

## D1. 混合检索：三路召回 + RRF 融合

- **决策**：向量（Qwen3-Embedding-8B@1024）+ BM25（jieba + PG ts_rank，OR-tsquery）+ 候选问题通道三路召回，RRF 融合；可选两阶段（文档级 pre-retrieve → 块级）。
- **理由**：向量擅长语义、BM25 擅长术语与编号精确匹配（财报数字/表格列名），问题通道弥补"提问措辞 ≠ 原文措辞"；OR-tsquery 避免 "什么/表示" 等噪声词导致 AND 查询零命中，relaxed 兜底进一步降噪。
- **Trade-off**：三路带来融合调参成本（RRF 常数、问题通道权重 0.15）；问题通道摄入期需 LLM 生成 + 独立向量（存储/算力成本），8 组 ablation 显示其对三一语料 recall 无显著收益，故 **Baseline 评测在 channel OFF 下锁定**，保留为可开关实验项。
- **关联**：`docs/plans/2026-08-23-rag-v2-implementation-plan.md`、`docs/plans/2026-08-23-baseline-ablation.md`

## D2. 表格感知摄入：双表示 + 元数据列（Issue #2 第一期）

- **决策**：`chunks.text` 保留原始 Markdown（生成用）；`embedding_text / search_text` 走 `build_retrieval_text()` 逐行归一化为自包含语句（"`{首列}：{表头2}为{值2}`"）；`chunk_type / table_headers / table_meta` 落库；小表格（≤4 行）入库时转自然语言，大表格保留 Markdown（列对齐对数字理解至关重要）。
- **理由**：裸表格整块 embedding 噪声大、列-值对应关系在检索后易丢失；归一化文本让每行成为独立语义单元。
- **Trade-off**：双重文本存储（体积 ×2）；一期只做"表示"不改检索策略——`chunk_type` 驱动的 table-only 检索/重排明确划为二期（未实现，见已知限制）；ablation 显示对 65 题整体有增益（B3 73.8%），但 C 类仍弱。
- **关联**：`docs/plans/2026-08-23-embedding-migration-table-aware-design.md`、`2026-08-24-baseline-3-locked.md`

## D3. Embedding 配置迁移：多代际共存 + 显式版本墙

- **决策**：`embedding_model / embedding_dimension` 单源配置（settings → client → DB）；`chunks.embedding_version` 标记 corpus 代际（v1 4096 → v2/v3 1024），检索按 `current_embedding_version` 过滤——**新 chunk 的版本必须在构造点取自 settings，禁止写死字面量**（曾经写死 1 导致新 chunk 对检索不可见）。
- **理由**：混版本语料是迁移期间的常态，显式版本墙防"新旧向量混算余弦"。
- **Trade-off**：代际推进后旧版 chunk 对检索不可见（必须重摄）；提供了 `tools/migrate_vector_dimension.py` + `reembed_v2.py` 一次性迁移工具。
- **关联**：`docs/plans/2026-08-23-baseline-2-locked.md`（迁移全程记录）

## D4. 摄入元数据可靠性：批量 + 重试 + 降级不丢块

- **决策**：LLM 元数据（title/summary/questions）按 **25 chunks/批**、每批 ≤3 次重试（指数退避）、显式键回显映射（禁止位置对位 zip）；失败批降级为空元数据，**不得 abort 或丢块**。
- **理由**：两度事故——整文档一次调用超时丢 41 chunks（Baseline-4 INVALID 根因）；截断 JSON 静默产生 99% 无 questions 语料。
- **Trade-off**：极端情况下元数据覆盖率 < 100%（title/summary/questions 为空）→ `tools/index_integrity_report.py` 提供覆盖率显式断言（`--expect-questions zero|positive`），把静默错配变成显式 FAIL。
- **关联**：`docs/plans/2026-08-24-baseline-4-candidate-invalid.md`

## D5. 增量更新：内容 hash 复用 + 稳定 chunk id

- **决策**：chunk id = `doc_id + content_hash[:10]`（重复内容加序号后缀）；重索引时未变更 chunk 复用 embedding/元数据/问题向量；embedding 部分失败 + 已有旧索引 → 保留旧索引置 failed（正确性优先于可用性）。
- **理由**：稳定 id 使重索引不产生孤儿问题行（旧 zip 错位曾把问题挂到别的 chunk）；复用省 LLM/embedding 成本（429 风险）。
- **Trade-off**：重试必须真正重索引（防 unchanged 短路卡死）；content_hash 相同的块变更检测依赖解析管线确定性。
- **关联**：`docs/plans/2026-08-02-ingestion-correctness.md`

## D6. 检索策略默认关闭（代码齐备，env 单点可开）

- **决策**：6 个策略开关（cross_doc / section_boost / section_supplement / year_supplement / query_decomposition / evidence_gate）默认 `False`，env 可开。
- **理由**：8 组 ablation 显示对三一语料无 recall 收益、MRR 略降；保留代码与开关以便单点重评估（如换语料后）。
- **Trade-off**：策略单测需显式 monkeypatch 开关（如 `test_cross_doc_extras` / `test_supplement_appends`）——测试锁定的是"策略被打开时的行为"，而非运行时默认。
- **关联**：`docs/plans/2026-08-23-baseline-ablation.md`

## D7. Evidence Gate：代码级拒答保护，默认不激活

- **决策**：`evidence_gate_enabled=False`（`evidence_min_coverage=0.7`）；gate 在覆盖率 < 阈值或存在**高严重度**冲突时拒答（`evidence_refused` 状态 + `degraded` 事件），只对 high 级冲突触发（severity-aware，防同一文档口径修正被误杀）。
- **理由**：提示词层面的拒答边界（5 类明确拒绝 + 前提纠偏）仍然依赖模型行为；代码级 gate 提供确定性兜底，但需要独立校准（false-refusal 率）才能放心开启。
- **Trade-off**：关闭时回归到 prompt-only 行为；开启需要重跑 65 题基准验证不误杀（`eval/ablation_evidence_gate.py` 提供 pilot）。
- **关联**：`docs/plans/2026-08-23-phase4-evidence-contract-repair.md`

## D8. 跨文档检索：摄入期预计算，查询期零 LLM 成本

- **决策**：三通道——TF-IDF 关系边（摄入期构建关系矩阵，增量更新）、query 关键词召回、文档级 embedding 语义通道；权重映射到 RRF 量纲（旧 min(score, max_rrf) 压分沉底被截断，L1 回归修复）。
- **理由**：跨文档问题是 C 类根因之一；预计算让查询路径零 LLM/embedding 延迟。
- **Trade-off**：关系矩阵存储成本 + 增删文档时需维护；默认关闭（见 D6）。
- **关联**：`docs/plans/2026-08-02-cross-doc-retrieval-overhaul.md`

## D9. 评测体系：gold 唯一入口 + judge ≠ generator + 65 题为准

- **决策**：`eval/gold.py` 是 `rag_testset.json` 的唯一读取入口（含 schema 校验 + self-check CLI）；judge 用独立 model 参数（不与生成混淆）；15 题 verified 集仅作 regression gate，**正式锁定一律以 65 题 benchmark 为准**（15 题两轮被 ±50pp 单题翻转击穿，类别样本量无统计力）。
- **理由**：gold 曾两度出错（Q26 漏中期分红 / Q55 错判不可得），经人工勘误 + 15 题 verified 子集（gold_source 标记）后成为唯一数据源；judge 自证 87% 准确率。
- **Trade-off**：65 题全量约 30-60 分钟（含限速），迭代回合成本高；引用对齐仍缺自动化验证（人工标注含 citation 维度）。
- **关联**：`docs/plans/2026-08-23-gold-correction-report.md`、`2026-08-23-judge-rubric-v1.md`

## D10. 运行时工程约束

- **决策**：所有 LLM I/O 异步（async client + 熔断器按 provider 隔离，429/5xx 区分，4xx 永久错误不计数）；同步 DB 调用一律 `asyncio.to_thread`；错误分类降级（BM25-only / 跳过重排 / 备用响应）。
- **决策**：PII 三层检测（正则 → Luhn/mod-11 算法 → 上下文排除），策略 mask(partial)/mask(full)/reject/audit。
- **决策**：不做分布式追踪（不引入 trace_id/contextvars）——诊断子系统用**请求内** JSON 遥测（`/api/v1/diag` + 浏览器 viewer），避免全局上下文污染与运维复杂度。
- **决策**：前端零图标库（emoji + 内联 SVG）、Apple 设计语言、系统字体栈——面试/演示项目控制首屏体积与风格维护成本。
- **关联**：`docs/plans/2026-08-05-resilience-round2.md`、`2026-08-02-security-p0.md`
