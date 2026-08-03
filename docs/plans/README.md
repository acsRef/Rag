# Plans 索引

> 永久索引，plans 的唯一入口。不要按日期找 plan，从这里进。
> 状态机：`进行中 → 已完成 → 已归档`；另有 `暂缓`（已批准但搁置）与 `只读评审`。
> 命名：`YYYY-MM-DD-<topic-slug>.md`；同 topic 跨天加 `-v2`，不改日期。

## 进行中

（暂无）

## 已完成

- [2026-08-02-test-infrastructure](2026-08-02-test-infrastructure.md) — 测试基建：pytest 离线单测（42 例锁 4 个已知 bug）+ integration 摄入/跨文档/检索全链路（自制 fixture 文档 + 确定性 fake 层），`ragent_test` 测试库隔离（commit: ad3ac46，分支 feat/test-infrastructure）
- [2026-08-02-security-p0](2026-08-02-security-p0.md) — 安全 P0：诊断遥测 admin 鉴权 + 删静态挂载、会话 IDOR、银联卡漏报 + 重叠掩码去重、白名单永久开洞、登录时序拉平、注册限流（commits: 297f9bf..b8cefbe，分支 fix/security-p0）
- [2026-08-02-memory-overhaul](2026-08-02-memory-overhaul.md) — 记忆机制改造：id 水位修丢消息 bug、摘要异步化、窗口化查询、失败退避、自动标题、结构化摘要（commits: 335d007..d315b34，分支 fix/memory-overhaul）
- [2026-08-02-cross-doc-retrieval-overhaul](2026-08-02-cross-doc-retrieval-overhaul.md) — 跨文档检索改造：三通道量纲统一、公平排序映射、channel 3 独立发现、document_id 补全、bulk 上限、事件循环解阻、rerank 不丢候选、MMR 真余弦、引用编号一致（commits: 4d9dcef..062d42b，分支 fix/cross-doc-overhaul）
- [2026-08-02-llm-gateway-convergence](2026-08-02-llm-gateway-convergence.md) — LLM 调用收敛：客户端单次化去双层重试、HALF_OPEN 单 probe、退避封顶、JSON 契约、限流器钳制、门控正则、rewrite 守卫、prompt 对齐、`_trim_history` 顺序、vision 主循环复用 + 小图过滤（分支 fix/llm-gateway-convergence）
- [2026-08-02-tag-stream-parser](2026-08-02-tag-stream-parser.md) — 标签流解析器：抽取 TagStreamParser 纯类（12 例单测），修 <answer> 标签泄漏、跨 token 断标签泄漏、展示/持久化不一致（分支 fix/tag-stream-parser）
- [2026-08-02-ingestion-correctness](2026-08-02-ingestion-correctness.md) — 摄入正确性：questions 构造点对齐、Document 行先行（FK xfail 转正）、失败可重试、全失败保旧索引、稳定 chunk id + 孤儿清理、无 H3 尺寸闭环、get_image 修复（分支 fix/ingestion-correctness）
- [2026-08-02-audit-residual-fixes](2026-08-02-audit-residual-fixes.md) — 审查遗留修复：搜索异常可见性、微秒溢出、意图路由复活（KB 名称 + 默认全库）、rewritten 兜底 + no_context 信号、PII 中文排除词、坏正则告警、限流硬化（XFF + 上界）、默认口令读配置、黑名单精确匹配、前端 degraded 事件（分支 fix/residual-fixes）

## 已归档

（暂无）

## 暂缓 / 待细化

以下主题来自 2026-08-02 全栈审查（记忆机制 / 链路设计 / 代码质量），按优先级排队，逐份细化为正式 plan：

（全部主题已细化并完成，见上方「已完成」区。）