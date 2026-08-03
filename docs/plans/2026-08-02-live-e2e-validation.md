> 状态: 已完成（commits: 7d96eb6 / e070b7e / b4d9ef0 等，分支 fix/residual-fixes→后续提交；13 例 live 全数通过）

# 真实链路端到端验证（live-e2e-validation）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪。本 plan 全部为**测试资产**，零业务代码改动（除非测试暴露新缺陷）。

**Goal:** 用真实 LLM/Embedding API 验证三条此前只有 fake 覆盖的链路：① 长对话记忆（16 轮对话，摘要水位/无丢失不变式）；② 单文档复杂真实 PDF（中国平安 2026 一季报，docling 解析 + 表格入库 + 金融术语检索）；③ 多文档复杂表格语料（5 份自制表格密集文档，跨文档关系 + 检索精度）。

**Architecture:** 全部落在 `tests/integration/`，`pytestmark = live_llm`，双重门槛：`RAGENT_LIVE_LLM=1` + `.env` 有真实 key（复用 `test_live_llm.py` 的 live_env 模式），缺失即 skip——离线环境套件不受影响。数据一律进 `ragent_test` 库，不碰开发库。平安 PDF 不进仓库（测试按路径引用，不存在则 skip）。

**Tech Stack:** pytest integration、真实 MiniMax M3（chat/vision）+ SiliconFlow embedding、docling PDF 解析。

---

## Context

前七份 plan 的自动化测试全部基于确定性 fake 层（hash 词袋向量、fake metadata），真实 API 只有 2 个冒烟用例。用户要求：
1. **长对话测试**——记忆机制改造（memory-overhaul）后从未在真实 LLM 下端到端验证：摘要触发、水位推进、窗口滑动、无丢失不变式。
2. **单文档用真实复杂 PDF**——`C:\Users\Lenovo\Downloads\中国平安：海外监管公告 - 中国平安保险（集团）股份有限公司2026年第一季度报告.pdf`（约 1MB，含大量财务报表表格），验证 docling 解析 → 表格清洗入库 → 金融术语检索。
3. **多文档自制、表格尽量多而复杂**——验证跨文档关系构建与检索精度的真实表现。

注意：docling 模型缓存为空，首次解析会触发模型下载（数分钟级），测试超时预算要足。

## Design

### 自制表格语料（tests/fixtures/docs-tables/，5 份）

| 文档 | 表格设计 | 跨文档钩子 |
|---|---|---|
| `sales_east_2024.md` | 3 张表：产品线×季度销售明细（含同比/环比/占比列）、渠道拆分表、季度汇总表 | 产品名（智享家Pro/安芯保/乐活贷）、渠道名 |
| `sales_south_2024.md` | 同构 3 张表（华南数据） | 同上产品名 → 与华东文档构成互补关系 |
| `product_cost_margin.md` | 产品成本毛利总表（单价/单位成本/毛利率/同比）+ 分季度毛利表 | 产品名 → 与销售文档交叉 |
| `kpi_summary_2024.md` | 全集团指标汇总（区域×指标矩阵）、达成率排名表 | 区域名 + 产品名聚合 |
| `channel_team_handbook.md` | 渠道-团队对照表、KPI 考核计分规则表 | 仅共享"渠道"词，作弱相关对照 |

表格复杂度要求：8+ 列、含 ¥/%/同比环比、多级表头风格的复合表、数字密集。数值避免 11 位 `1[3-9]` 开头数字串（防 PII 手机号规则误触）。

### 三个测试模块（均 live_llm 门控）

1. **`test_live_pdf_ingest.py`**（单文档）：
   - 常量引用平安 PDF 路径；文件不存在 → skip。
   - `document_indexer.index(document_id=None)`（Document 行先行后已合法）→ 断言 status == indexed、chunk_count ≥ 20、embedding 维度 4096。
   - 检索断言：`retrieval_engine.retrieve("中国平安2026年第一季度营业收入")` 与 `...净利润` 的 top-5 中均含该文档 chunk（断言 document_id 命中，不断言具体数值——报告内容不可预先断言）。
   - 表格解析痕迹断言：至少 1 个 chunk 的 text 含"营业收入"类财务词（从实际 chunk 中统计，允许 ≥1）。
2. **`test_live_table_docs.py`**（多文档）：
   - module 级 fixture：依次摄入 5 份表格文档（真实 embedding + 真实 metadata LLM）。
   - 关系断言：`get_doc_relations(华东)` 含 产品成本文档 或 华南文档 的边（TF-IDF 互补）。
   - 检索精度：`智享家Pro 2024年Q3 华东销售额` → top-3 含华东文档；`智享家Pro 毛利率 成本` → top-3 含成本文档；`华东 华南 销售额对比` → top-5 横跨 ≥2 个区域文档。
   - MMR 软约束抽查：结果中单文档 chunk ≤ 3。
3. **`test_live_long_conversation.py`**（长对话）：
   - 摄入 1 份表格文档作话题锚点（sales_east），随后走 `rag_pipeline.execute` 跑 **16 轮**真实对话：前几轮事实问答，中段引入代词问题（"它的Q3同比是多少""这个产品在南区的表现"）触发 rewrite + 摘要，后段追问早期细节。
   - 每轮收集 SSE 事件，断言有 token/answer 产出（或降级时有 error/no_context 事件）。
   - 记忆不变式（全部轮次结束后，await 后台摘要任务收敛）：
     - messages 行数 ≥ 30，无 status='streaming' 残留；
     - conversation.title 为首轮 query 前缀；
     - summary 非空、last_summarized_msg_id 非空；
     - **无丢失不变式**：按 memory 同款窗口游走算出 boundary_id，断言所有 id < boundary 的消息 id ≤ watermark；
     - get_history token 预算不超（估算累加 ≤ history_max_tokens）。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| RAGENT_LIVE_LLM 未设 / key 缺失 | 全部 skip，离线套件不受影响 |
| 平安 PDF 不在该路径 | 单文档模块 skip，其余照跑 |
| docling 首次下载模型 | 给足超时（单测试 ≥ 20 分钟预算） |
| PDF 图片触发 vision 调用 | 正常走 MiniMax vision（主循环派发路径顺带实测） |
| 某轮 chat 失败/降级 | 断言允许 error/no_context 事件，不硬断言每轮必有 token |
| metadata 大文档输出截断（已知缓做项） | 不因此失败：断言只要求 status indexed |

## Files to change

| 变更 | 路径 |
|---|---|
| Create | `tests/fixtures/docs-tables/*.md`（5 份表格密集文档）、`tests/integration/test_live_pdf_ingest.py`、`tests/integration/test_live_table_docs.py`、`tests/integration/test_live_long_conversation.py` |
| Modify | `tests/integration/conftest.py`（如需共享 live_env fixture，则提取为 conftest 级）、`docs/plans/README.md` |

## Reused existing utilities

`test_live_llm.py::live_env`（key 注入 + client 重建模式，提取到 conftest 共享）、`document_indexer.index`（Document 行先行）、`retrieval_engine.retrieve`、`rag_pipeline.execute`、`conversation_memory` 读取接口、ragent_test 隔离库机制。

---

## Tasks

### Task 1: live_env 提取到 conftest + 表格语料创建

- [ ] **Step 1**: 把 `live_env` fixture 从 test_live_llm.py 移入 `tests/integration/conftest.py`（逻辑不变；原文件删除该 fixture 改为引用）。
- [ ] **Step 2**: 创建 5 份表格文档（按上表设计）。
- [ ] **Step 3**: 离线跑一次 `pytest tests/integration -q` 确认 live 用例全部 skip、套件不受影响。

### Task 2: 单文档 PDF 测试

- [ ] **Step 1**: 写 `test_live_pdf_ingest.py`。
- [ ] **Step 2**: `RAGENT_LIVE_LLM=1 pytest tests/integration/test_live_pdf_ingest.py -v`（首跑含 docling 模型下载，给足时间）。
- [ ] **Step 3**: 按实测修正断言阈值，Commit。

### Task 3: 多文档表格测试

- [ ] **Step 1**: 写 `test_live_table_docs.py`。
- [ ] **Step 2**: live 运行，按实测修正（检索 top-k、关系边预期）。
- [ ] **Step 3**: Commit。

### Task 4: 长对话测试

- [ ] **Step 1**: 写 `test_live_long_conversation.py`（16 轮 + 不变式断言）。
- [ ] **Step 2**: live 运行（约 16 次 chat + 摘要调用，分钟级），按实测修正轮次/等待时长。
- [ ] **Step 3**: Commit。

### Task 5: 收尾

- [ ] **Step 1**: 离线全量回归（live 全 skip）确认不破坏既有套件。
- [ ] **Step 2**: 更新 plan 状态与索引，Commit。
- [ ] **Step 3**: 若测试暴露新缺陷：登记为新 plan（不在本 plan 内修）。

## Verification

| 验证项 | 命令 | 期望 |
|---|---|---|
| 离线套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | 110 passed, 2 skipped（live 全 skip 时更多 skipped） |
| PDF 单文档 | `RAGENT_LIVE_LLM=1 ... pytest tests/integration/test_live_pdf_ingest.py -v` | passed（含解析/检索断言） |
| 多文档表格 | `RAGENT_LIVE_LLM=1 ... pytest tests/integration/test_live_table_docs.py -v` | passed |
| 长对话 | `RAGENT_LIVE_LLM=1 ... pytest tests/integration/test_live_long_conversation.py -v` | passed（无丢失不变式成立） |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| 把平安 PDF 提交进仓库 | 二进制版权文件；按路径引用 + skip 兜底 |
| 断言报告具体财务数值 | 内容不可预先断言，只断言检索命中与结构 |
| 修复测试暴露的新缺陷 | 本 plan 只做验证；缺陷登记新 plan |
| metadata 分批调用 | 已知缓做项（大文档 metadata 截断属预期容忍） |
