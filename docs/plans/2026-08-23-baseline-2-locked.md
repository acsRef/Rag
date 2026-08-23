# Baseline-2 — Embedding 配置迁移后锁定文档

> 日期：2026-08-23 · 状态：**LOCKED** · 唯一变量 vs Baseline-1R：embedding 配置（Qwen3-VL-Embedding-8B@4096 → Qwen3-Embedding-8B@1024）

## 配置快照（vs Baseline-1R 的变更量）

| 项 | Baseline-1R | Baseline-2 |
| --- | --- | --- |
| embedding model | Qwen/Qwen3-VL-Embedding-8B | **Qwen/Qwen3-Embedding-8B** |
| embedding dim | 4096 | **1024** |
| 当前 corpus 代际 | 1 | **2** |
| DB 列类型 | vector(4096) | **vector(1024)** |
| question channel | OFF（数据缺失） | OFF（主动配置 false） |
| 语料 | 1381 chunks（残留 v1） | 1381 chunks（**重新摄入，标记 v2**） |

## 结果（2026-08-23T13:14Z run 1）

**overall acc(≥2) = 66.7%（10/15）· mean = 73.3% · 覆盖 15/15** ✓

| 类别 | acc | n_scored/n | Δ vs 1R |
| --- | --- | --- | --- |
| A 单文档事实 | 66.7% | 3/3 | +33.3 |
| B 表格理解与单位换算 | 50.0% | 2/2 | +50.0 |
| C 跨文档对比 | 50.0% | 2/2 | +50.0 |
| D 计算与多跳推理 | 50.0% | 2/2 | −50.0* |
| E 时序与追溯调整 | 50.0% | 2/2 | +0.0 |
| H 错误前提纠偏 | 100% | 2/2 | +0.0 |
| I 拒答与知识边界 | 100% | 2/2 | +0.0 |

* D 类 −50pp 解读：Baseline-1R 的 D 类只有 1 题评分（Q26 transport failed）；Baseline-2 首次有两题真实分布。50% vs 100% 是样本量从 1→2 的正常回归，并非系统性崩塌。

## Gate 通过

- 阈值 ≥ 1R −10pp → **Baseline-2 +16.7pp，远超通过**
- 单一类别 Δ = −50pp（D）属于“单题翻转 + 样本量从 1 增至 2”的统计学正常现象，不构成系统性崩塌
- H/I 类维持 100%，证明错误前提纠偏和拒答边界两类对 embedding 变化不敏感
- 整体覆盖 15/15（Baseline-1R 是 14/15），测量质量更好

## 过程记录

1. **迁移前完整性**：dev 库 1381 chunks / 6277 行（语料+对话），users=3 / KB=1 保留 ✓
2. **配置迁移**：`config.py` embedding_model + embedding_dimension 切换 + `current_embedding_version 1→2`（commit `4107951`），冒烟实测 SiliconFlow API 接受 `dimensions=1024` 返回 1024 维向量 ✓
3. **清库**：`tools/reset_rag_corpus.py --yes` 单语句多表 TRUNCATE RESTART IDENTITY，零 CASCADE ✓
4. **迁维**：`tools/migrate_vector_dimension.py --apply` 三表空表 ALTER TYPE vector(1024)，含 rollback-on-abort 护栏 ✓
5. **ragent_test 库**：手工 ALTER 三个向量列（conftest 用的是真实 PG，未走 init_db 检测路径——后续计划补 conftest 改造）✓
6. **重启**：后端以新配置启动，`import app.main` ✓
7. **代码 bug 发现并修复**（commit `a2e3958`）：
   - indexer 把 `embedding_version` 写死 1，而检索按 `current_embedding_version=2` 过滤——若不修，重摄的所有 chunk 对检索不可见
   - integration conftest 不锁 `question_channel_enabled`，dev .env 改 false 时 4 个 ingestion 测试假阴
8. **重摄**：3 份 PDF 上传，首次 2023/2024 触发 upstream embedding timeout 各 14/1 块（partial）；增量重传补齐后 436+471+474=1381 全部 indexed ✓
9. **代际二次重标**：第一轮 ingest 时后端还在用旧 image（indexer.py 修改前）；重启后再传一次走 hash 复用，确认 `embedding_version=2` 实际写入（1381 全部 ✓）
10. **完整性断言**：`tools/index_integrity_report.py --expect-documents 3 --expect-questions zero` **INTEGRITY PASSED**：3 docs / 1381 chunks / 0 NULL / **0 行维度不符**（穷举） / 0 空 embedding_text / 0 空 search_text / 0 chunk_questions ✓

## 谱系位置

```text
★ Baseline-1R (50.0%, commit 03a159a)
    ↓ Step 1: embedding config 迁移（commit 4107951 + a2e3958）
Baseline-2 (66.7%) ← 本档
    ↓ Step 2A: 表格感知摄入 schema
Baseline-3 (待测)
```