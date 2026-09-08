# 下一步计划 (2026-09-07)

## 本次完成 — Issue #3 封板（RAG 项目最终收尾）

### P0 全部达标
- **pytest 全量 590 passed / 13 skipped / 0 failed**（此前 582/7：7 个既有失败全部修复，超过"6 不得新增"门槛）
  - `tests/__init__.py` + `tests/unit` + `tests/integration` 包化：修复 `tests.integration.conftest` 被 site-packages 同名包遮蔽的 `ModuleNotFoundError`（3 个摄入测试）
  - `test_cross_doc_extras`：fake_collect 补 `document_ids/filters` 形参 + 显式开启 `cross_doc_enabled`（ablation 后默认关）
  - `test_supplement_appends_missing_related_years` / `test_supplement_skips`：显式开启 `year_supplement_enabled`
  - `test_questions_align_with_persisted_chunks`：显式开启 `question_channel_enabled`
  - `test_oversized_section_packs_on_element_boundaries`：**chunker 真实 bug 修复**——`_pack_oversized` 中原子块 + 路径头超出上限时切掉了整块表（header 开销未计入单元素判断）；修复：去掉重复路径头保整体，title/section_path 元数据仍携带上下文
  - `test_render_table_doc`：对齐未提交的 `mcp_server/render.py` P15 prelude 新格式（标题无反引号 + 概述段 + 字段详解 + 表关系断言）
- **ruff check . → 0 错误**（eval 脚本 F401/F541/SIM115 全清）
- **ruff format --check . → 0**（新增 `[format] exclude = ["*.md"]`——ruff ≥0.12 开始格式化 markdown 代码围栏，docs/plans 历史文档保留原始事实只增 churn；代码文件 16 个格式化）
- **eval.gold 唯一入口确认**：13 个 eval 脚本由直接 `open(rag_testset.json)` 改为 `from gold import iter_questions`；migrate_gold/build_human_verified 作为文件作者工具保留直接读（写回 schema 需要 raw dict）；test_ch/test_serial/test_targeted 的死路径（`D:/PyProject/ragent-py/三一重工年报/`）一并修复
- **Baseline lineage 补齐**：新建 `docs/plans/2026-09-07-baseline-3r-rebuild.md`（3R 控制组档案），plans/README.md 收录 3R + B4-invalid 条目
- **stale reference 清扫**：README/AGENTS.md/CLAUDE.md 的 `Qwen3-VL-Embedding-8B 4096d` / `DeepSeek-V3`（chat 默认）→ `Qwen3-Embedding-8B 1024d` / `Qwen3-8B`；`app/core/doc_relation.py` 4096 注释

### P1 全部完成
- README 重写：与当前代码/配置全对齐（1024d、策略开关表、mcp_server、目录树、评测 v1、890→590 测试基线、`python -m app.main` 修正）；新增「检索链路」「Known Limitations」「工具」「Baseline 阵容表」
- `docs/architecture-decisions.md`（ADR 摘要，10 项决策 + trade-off）
- `docs/screenshots/` 4 张实拍截图（login/chat 带引用答问答/documents/kb）→ README 引用
- `.env.example` 模板（此前缺失）
- Issue #1 补 `test_citation_format_rule_present`（引用格式规则层锁定）

### Issue 处置
- #3 封板：P0/P1 checklist 全勾、关闭
- #2（table-aware）：第一期已随 Baseline-3 落地（双表示 + 元数据列），二期超出封板范围 → 带结论关闭
- #1（answer policy）：验收 1/2/3/5 满足（prompt 强约束 + evidence 契约 + 测试）；4 保持 65Q 基线继续有效；剩余缺口（引用对齐自动验证、gate 默认关、"补充通用知识"条款冲突）记入 Known Limitations / TODO → 带总结关闭

## 当前状态
- 测试基线：**590 passed / 13 skipped / 0 failed**
- RAG v2 = Baseline-3 LOCKED（65Q 73.8%）+ Baseline-3R 控制组（15Q 66.7%）+ Baseline-4 候选 INVALID 归档
- 代码已封板；未提交待确认（含用户 WIP `mcp_server/render.py` + 配套测试更新）

## 下一步（已不阻塞封板，见 docs/TODO.md）
1. 提交本批次改动（含用户确认 WIP render.py 是否一并提交）
2. 后续实验按需：Baseline-4 重跑 / 表格检索二期 / 引用对齐测试 / Evidence Gate 校准
3. 项目转向 ReportAgent + 求职准备

## 优先级
- **P0**：提交 + 封板说明（今天）
- **P1**：引用对齐自动化测试（面试前可选）
- **P2**：后续实验（有完整时间预算再启动）
