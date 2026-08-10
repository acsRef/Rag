# Plans 索引

> 永久索引，plans 的唯一入口。不要按日期找 plan，从这里进。
> 状态机：`进行中 → 已完成 → 已归档`；另有 `暂缓`（已批准但搁置）与 `只读评审`。
> 命名：`YYYY-MM-DD-<topic-slug>.md`；同 topic 跨天加 `-v2`，不改日期。

## 进行中

（暂无）

## 已完成

- [2026-08-10-schema-faq-mcp](2026-08-10-schema-faq-mcp.md) — Schema FAQ MCP（Phase A）commit `94c4930`；追加跨进程 token 缓存 commit `e2ff172`（`mcp_server/token_cache.py`，根治登录 429，`RagentClient._login` 命中共享缓存不再登录，401 自动失效重登） — Schema FAQ MCP（Phase A）：独立 FAQ 知识库 + `ingest_faq`/`search_faq`/`list_faq_docs` 工具 + `faq_seed.json` 20 条种子；`render_faq_doc` 单 chunk 渲染；全量 211 passed 无回归
- [2026-08-07-dict-mcp-server](2026-08-07-dict-mcp-server.md) — 数据字典 MCP 桥（A1-A8 全部完成）：A1 检索契约模型 / A2 嵌入降级 helper / A3 `/api/v1/retrieve` 端点 / A4 Markdown 渲染 / A5 PG 只读自省 / A6 HTTP 客户端 / A7 MCP stdio 服务装配；70 例定向测试 + 全量 195 passed 无回归；既有隐患 `app/api/kb.py::delete_kb` `DocRoleAccess` import 缺失登记为 follow-up（不动）

- [2026-08-05-resilience-round2](2026-08-05-resilience-round2.md) — 二轮审查韧性修复（完成，193 passed）：429 不打熔断（RateLimitError）、熔断器线程安全、rerank 按 loop 重建、retrieval 同步 DB 解阻、摘要锁前移、标签空白容忍、重索引部分失败保旧索引（B1–B6 + D1/D2/D3/D6/D7）；DB 降级加固：熔断计数/穿透兜底/故障与无内容分流//health 探针（DB-1…DB-4）

- [2026-08-04-audit-followups](2026-08-04-audit-followups.md) — 全栈审查遗留修复（16 项）：P0 增量更新丢复用 chunk 问题向量（replace_chunks 差量化）/邻居扩展适配 hash chunk id（created_at 全序）/前端 XSS（v-html 前置消毒）；P1 意图路由名称归一 + 畸形守卫、diag id 路径穿越白名单、熔断兜底文案流式、工作空间 restricted 默认 + owner 旁路 ACL、SSE 进度按用户过滤 + 丢旧 + 节流；P2 事件循环解阻、tsquery 消毒、embedding 32 分片、chunker 元素装箱 + 64 字符重叠、死代码清理、消息 id 序（分支 fix/audit-followups，+31 单测 +9 集成用例，152 passed）

- [2026-08-02-test-infrastructure](2026-08-02-test-infrastructure.md) — 测试基建：pytest 离线单测（42 例锁 4 个已知 bug）+ integration 摄入/跨文档/检索全链路（自制 fixture 文档 + 确定性 fake 层），`ragent_test` 测试库隔离（commit: ad3ac46，分支 feat/test-infrastructure）
- [2026-08-02-security-p0](2026-08-02-security-p0.md) — 安全 P0：诊断遥测 admin 鉴权 + 删静态挂载、会话 IDOR、银联卡漏报 + 重叠掩码去重、白名单永久开洞、登录时序拉平、注册限流（commits: 297f9bf..b8cefbe，分支 fix/security-p0）
- [2026-08-02-memory-overhaul](2026-08-02-memory-overhaul.md) — 记忆机制改造：id 水位修丢消息 bug、摘要异步化、窗口化查询、失败退避、自动标题、结构化摘要（commits: 335d007..d315b34，分支 fix/memory-overhaul）
- [2026-08-02-cross-doc-retrieval-overhaul](2026-08-02-cross-doc-retrieval-overhaul.md) — 跨文档检索改造：三通道量纲统一、公平排序映射、channel 3 独立发现、document_id 补全、bulk 上限、事件循环解阻、rerank 不丢候选、MMR 真余弦、引用编号一致（commits: 4d9dcef..062d42b，分支 fix/cross-doc-overhaul）
- [2026-08-02-llm-gateway-convergence](2026-08-02-llm-gateway-convergence.md) — LLM 调用收敛：客户端单次化去双层重试、HALF_OPEN 单 probe、退避封顶、JSON 契约、限流器钳制、门控正则、rewrite 守卫、prompt 对齐、`_trim_history` 顺序、vision 主循环复用 + 小图过滤（分支 fix/llm-gateway-convergence）
- [2026-08-02-tag-stream-parser](2026-08-02-tag-stream-parser.md) — 标签流解析器：抽取 TagStreamParser 纯类（12 例单测），修 <answer> 标签泄漏、跨 token 断标签泄漏、展示/持久化不一致（分支 fix/tag-stream-parser）
- [2026-08-02-ingestion-correctness](2026-08-02-ingestion-correctness.md) — 摄入正确性：questions 构造点对齐、Document 行先行（FK xfail 转正）、失败可重试、全失败保旧索引、稳定 chunk id + 孤儿清理、无 H3 尺寸闭环、get_image 修复（分支 fix/ingestion-correctness）
- [2026-08-02-audit-residual-fixes](2026-08-02-audit-residual-fixes.md) — 审查遗留修复：搜索异常可见性、微秒溢出、意图路由复活（KB 名称 + 默认全库）、rewritten 兜底 + no_context 信号、PII 中文排除词、坏正则告警、限流硬化（XFF + 上界）、默认口令读配置、黑名单精确匹配、前端 degraded 事件（分支 fix/residual-fixes）
- [2026-08-02-live-e2e-validation](2026-08-02-live-e2e-validation.md) — 真实链路端到端验证：16 轮长对话记忆不变式（有界滞后）、平安一季报 PDF 摄入检索、5 份复杂表格文档跨文档检索——13 例 live 全数通过；期间修复 TagStreamParser 死循环、摘要排水式收敛、vision 多模态模型固定

## 已归档

（暂无）

## 暂缓 / 待细化

以下主题来自 2026-08-02 全栈审查（记忆机制 / 链路设计 / 代码质量），按优先级排队，逐份细化为正式 plan：

（全部主题已细化并完成，见上方「已完成」区。）