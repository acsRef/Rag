> 状态: 进行中（分支 fix/resilience-round2）

# 二轮审查韧性修复（resilience-round2）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪。每个修复先落测试（纯逻辑 → `tests/unit`，DB 行为 → `tests/integration`），再改实现；完成后全量回归。

**Goal:** 修复二轮代码审查确认的 6 个 bug（B1–B6）、5 个高价值设计隐患（D1/D2/D3/D6/D7），以及降级覆盖面分析暴露的 **DB 侧韧性缺口**（无熔断/无健康探针、检索空结果不区分「真无内容」与「DB 故障」、pipeline/memory 的 DB 异常硬穿透 SSE 流）。核心主题：**熔断治理纪律**（429 不得打穿熔断器）、**事件循环不被同步 DB 阻塞**、**并发竞态**（摘要锁序、熔断器线程安全）、**标签解析对模型输出漂移的容忍度**、**重索引不得静默丢内容**、**DB 故障可见可降级**。

**Architecture:** 全部落在既有模块内，无 schema 变更、无新依赖。`base.py` 新增 `RateLimitError(TemporaryError)` 类型承担「可重试但不计熔断失败」语义（对齐 AGENTS §8 与 rerank.py 既有正确行为）；`tag_parser.py` 标记集扩展为空白容忍变体集，保持「最长标记前缀缓冲」契约不变。

**Tech Stack:** pytest（unit 离线 / integration ragent_test）。

---

## Context

二轮审查逐条核实结果（均已对当前代码确认）：

| # | 问题 | 核实 |
|---|------|------|
| B1 | `retrieval.py:94/:152` 在 async retrieve 内同步调 `list_kb_ids()` | ✅ 与 pipeline.py:167（已包 to_thread）不一致 |
| B2 | 429 被计为熔断失败：`classify_llm_error` 429→TemporaryError，`embed()`/`chat()`/`chat_stream()` 的 `if not isinstance(typed, PermanentError): _on_failure()` 在限流时触发熔断 | ✅ 另发现 `embed_single_chunk:139/:146`、`_try_batch_with_retry:210/:217` 同样错误（429 耗尽后、4xx 分支都在计失败）。rerank.py 是对的 |
| B3 | `rerank.py:_get_client` 单 client 缓存无 loop-id 跟踪，跨 loop 调用会 "Event loop is closed" | ✅ embedding/chat 均有按 loop 重建逻辑，rerank 缺失 |
| B4 | `_summarize_once` 先读 summary/watermark 拼 prompt，之后才拿每会话锁 → 并发任务基于同一旧快照重复摘要同区间 | ✅ 锁应前移到读取之前 |
| B5 | TagStreamParser 只认 `" \n<think>"` 精确前缀，模型输出 `\n<think>`/`\n\n<tool_call>` 时思考泄漏进正文 | ✅ 需空白容忍（0-2 空白前缀） |
| B6 | CircuitBreaker/ProviderHealth 无锁，ingestion 工作线程与主循环并发改全局单例 → HALF_OPEN 单 probe 保证失效 | ✅ |
| D1 | sub_questions 数量 LLM 控制无封顶，gather 无上限并发 | rewrite 出口加 `max_sub_questions` 上限 |
| D2 | pipeline 每个子问题的 status 事件在 gather 前一次性全发，时序误导 | 改为单条并行提示 |
| D3 | 重索引部分新块 embedding 失败：失败块被跳过，差量 upsert 又把对应旧行删掉 → 静默丢内容 | 有旧索引时任一新块失败 → 整体保留旧索引 + failed（可重试） |
| D6 | 登录限流纯内存且无锁 | 加 threading.Lock（重启清零属架构项，见缓做） |
| D7 | `_trim_chunks` 只从尾部裁，单个巨型 chunk（跨文档注记块）可击穿总预算 | 兜底截断单块至预算内 |
| DB-1 | DB 无熔断/健康计数：`_search_kb` 吞异常成空结果，掩盖「DB 挂了还是真没内容」 | DB 失败/成功计入 `provider_health["postgres"]`，复用既有熔断器与 degraded 事件链 |
| DB-2 | pipeline:167 `list_kb_ids`（to_thread 但无 try/except）、memory 读取/add_message：DB 挂时异常穿透 SSE 生成器 → 断流 | 三处加兜底：list_kb_ids 失败退回请求指定 KB/空；history/summary 失败退化为空上下文；add_message 失败仅告警不断流 |
| DB-3 | 空结果不区分故障与无内容：DB 熔断中也走 no_context + LLM 幻觉路径 | 检索为空且 postgres 处于降级 → 发 error 事件「知识库服务暂时不可用」并终止，不调 LLM |
| DB-4 | `/health` 不反映 DB 状态 | 加 `SELECT 1` 探针，返回 `{"status":"ok"/"degraded","db":true/false}` |

## Design

### B2 熔断纪律：新增 RateLimitError 类型

`base.py`：`class RateLimitError(TemporaryError)` — 可重试（is-a TemporaryError，现有 retry 逻辑不变）但不计熔断失败。`classify_llm_error` 的 429 分支改返回它。各客户端：
- `embed()` / `chat()` / `chat_stream()`：`if not isinstance(typed, (PermanentError, RateLimitError)): _on_failure()`
- `embed_single_chunk` / `_try_batch_with_retry`：429 耗尽后不再 `_on_failure()`；APIStatusError 分支仅 `>=500` 才计失败（4xx 是永久错误）

### B6 熔断器线程安全

`CircuitBreaker` 增加 `threading.Lock`（dataclass field default_factory），`allow_request/on_success/on_failure/snapshot` 全部在锁内。`ProviderHealth.get` 的 dict 建锁保护。HALF_OPEN「恰好一个 probe」在多线程下恢复成立。

### B1 retrieval 同步 DB 解阻

`retrieve()` 两处 `pgvector_store.list_kb_ids()` → `await asyncio.to_thread(...)`。

### B3 rerank 客户端按 loop 重建

对齐 embedding/chat 的 `_client_loop_id` 模式：loop 变化时重建 `httpx.AsyncClient`。

### B4 摘要锁前移

`_summarize_once` 结构改为：退避检查 → **拿每会话锁（拿不到直接 False，排水语义不变）** → 读快照/拼 prompt/LLM/写库 → finally 释放。锁内 await 期间其他任务非阻塞拿锁失败即跳过，其覆盖区间由持锁者排水兜底（既有不变式保持）。

### B5 标签空白容忍

标记集扩展：`<think>`/`</think>` 前允许 0–2 个 `[ \t\r\n]` 空白（变体集 7×2+2=16 个），`_apply` 按 endswith 分类；`_partial_prefix_len` 对所有变体求最长真前缀后缀——「未闭合片段不泄漏」契约不变，纯文本尾部最多延迟 2 个空白字符。`THINK_OPEN/THINK_CLOSE` 常量保留（canonical 变体，测试与外部引用兼容）。

### D3 重索引部分失败保留旧索引

indexer 在持久化前增加守护：**有旧索引（`old_chunks_map` 非空）且任一新块 embedding 失败** → 不执行差量 upsert，状态 failed + 保留旧索引（重试可恢复，hash 复用生效）。新文档首摄的部分失败维持现行为（没有可丢的旧内容）。

### 其余小项

- **D1**：`settings.max_sub_questions: int = 4`，rewrite 守卫出口截断。
- **D2**：pipeline 子问题 status 循环删除，`len(sub_queries) > 1` 时发一条「正在并行检索 N 个子问题」。
- **D6**：`_check_rate_limit` 整体置 `threading.Lock` 内。
- **D7**：`_trim_chunks` 裁到单块仍超预算时，按 `budget*1.5` 字符截断该块文本（≤0 则弃块）。

### DB-1…DB-4：DB 降级加固

复用既有熔断基建，不引入新机制：

- **DB-1 熔断计数**：`_search_kb` 成功 → `provider_health.get("postgres").on_success()`，异常 → `on_failure()` 后照旧返回 `[]`（子查询容错不变）。`retrieve()` 入口先 `allow_request()`：熔断打开时直接记 `ctx.track_error("db", ...)` 返回 `[]`，不再撞 DB。postgres 进入 `provider_health` 后，pipeline 末尾既有的 `degraded` SSE 事件（前端已渲染）自动携带它——用户侧感知闭环。
- **DB-2 穿透兜底**：pipeline 三处包 try/except——`list_kb_ids` 失败退回 `req.knowledge_base_ids or []`；`get_history/get_summary` 失败退化为空上下文；`add_message` 失败仅 `logger.exception` 不断流。
- **DB-3 故障与无内容分流**：检索为空时，若 `provider_health.is_degraded()` 含 `"postgres"` → 发 `error` 事件「知识库服务暂时不可用，请稍后重试」+ done 终止（用户消息尝试入库、失败容忍），**不进 LLM 幻觉路径**；否则维持 no_context 现状。
- **DB-4 健康探针**：`/health` 执行 `SELECT 1`（经现有 engine，异常即失败），返回 `{"status": "ok"/"degraded", "db": bool}`。不做 DB 盲重试（pool_pre_ping 已处理 stale 连接）。

## Files to change

| 变更 | 路径 |
|---|---|
| Modify | `app/llm/base.py`、`app/llm/embedding.py`、`app/llm/chat.py`、`app/llm/rerank.py`、`app/core/retrieval.py`、`app/core/memory.py`、`app/core/tag_parser.py`、`app/core/pipeline.py`、`app/core/rewrite.py`、`app/core/prompt.py`、`app/ingestion/indexer.py`、`app/api/auth.py`、`app/config.py` |
| Create | `tests/unit/test_breaker_429.py`、`tests/unit/test_breaker_threads.py`、`tests/unit/test_rerank_client.py` |
| Modify | `tests/unit/test_tag_parser.py`（空白变体）、`tests/integration/test_ingestion.py`（D3 保留旧索引）、`tests/integration/test_memory_overhaul.py`（B4 并发不重复摘要）、`tests/integration/test_retrieval_e2e.py`（B1 回归） |

---

## Tasks

### Task 1: B2+B6 熔断治理（base.py 为核心）

- [ ] **Step 1**: 先写 `tests/unit/test_breaker_429.py`（429→RateLimitError 可重试不计失败；5xx 计失败；4xx 不计）与 `tests/unit/test_breaker_threads.py`（多线程 HALF_OPEN 恰一个 probe；ProviderHealth.get 并发单例）。
- [ ] **Step 2**: base.py 增类型 + 熔断器加锁；embedding/chat 四处 _on_failure 守卫修正；rerank 对照确认。
- [ ] **Step 3**: 单测绿 + 既有 test_llm_base/test_llm_gateway 回归绿，commit。

### Task 2: B3+B1 客户端/检索解阻

- [ ] **Step 1**: `tests/unit/test_rerank_client.py`（loop 变化触发重建）。
- [ ] **Step 2**: rerank `_get_client` 按 loop 重建；retrieval 两处 to_thread。
- [ ] **Step 3**: 单测 + retrieval e2e 回归绿，commit。

### Task 3: B4 摘要锁前移 + B5 标签容忍

- [ ] **Step 1**: 先写测试：integration 并发双 add_message 摘要区间不重叠（fake LLM 记录每次 prompt 的消息集合，断言互斥）；tag_parser 空白变体单测（`\n`/`\n\n`/裸标签/跨 token）。
- [ ] **Step 2**: memory 锁前移；tag_parser 变体集改造。
- [ ] **Step 3**: 测试绿（既有 12 例 tag_parser 单测 + memory 全套不得回归），commit。

### Task 4: D1/D2/D3/D6/D7 小项批

- [ ] **Step 1**: D3 integration 测试先行（重索引单块失败 → 旧索引原样 + failed + 重试恢复）。
- [ ] **Step 2**: 实现五项（D1 rewrite 截断+配置、D2 status 收敛、D3 守护、D6 限流锁、D7 单块截断）。
- [ ] **Step 3**: 测试绿，commit。

### Task 5: DB 降级加固批（DB-1…DB-4）

- [ ] **Step 1**: 测试先行：unit `_search_kb` 失败/成功计数 + 熔断打开时 retrieve 快速返回；integration DB 故障模拟 → pipeline 发 error 事件「知识库服务暂时不可用」、不调 LLM；history/summary 异常不断流。
- [ ] **Step 2**: 实现四项（熔断计数/入口闸门、三处穿透兜底、空结果故障分流、/health 探针）。
- [ ] **Step 3**: 测试绿 + 既有 security/retrieval 套件回归，commit。

### Task 6: 收尾

- [ ] **Step 1**: 全量 `pytest -q` + `python -c "import app.main"`。
- [ ] **Step 2**: plan 转已完成、README 索引更新、合并推送。

## Verification

| 验证项 | 命令 | 期望 |
|---|---|---|
| 全量套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | ≥152 passed + 新增用例全绿 |
| import 链 | `... -c "import app.main"` | 无异常 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| D4 摘要触发节流（每 N 条才查阈值） | 现有不变式测试锁定逐条触发语义；单次索引聚合成本可接受。改语义风险 > 收益 |
| D5 韧性状态外置（Redis/ARQ） | 架构项，已在 CLAUDE.md「Next up：异步文档处理」排队，需 Redis 引入决策 |
| D8 `_needs_decomposition` 正则调优 | 误触成本是一轮 LLM 改写；调优需评测数据支撑，归入「搜索质量评估框架」plan |
| live_llm 回归 | 本批全有离线覆盖；按需手动 |
