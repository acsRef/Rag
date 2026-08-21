# Phase 1 — 清理轮（Cleanup Pass）

> 日期：2026-08-21
> 状态：spec 定稿，待执行
> 来源：外部代码评审收敛（文档漂移 / 死代码 / lint 缺失 / 注释日志化），核查结论见会话记录

## 0. 总原则

**Phase 1 只允许改变"代码/文档表达"，不允许改变"运行时语义"。**

允许：

- 文档更新（CLAUDE.md / README.md / AGENTS.md / TODO.md）
- 删除确认无消费者的死代码
- Ruff 安全修复（仅"机械且语义安全"类）
- `Optional[X]` → `X | None` 机械替换
- 注释重写（设计依据保留、开发过程删除）
- formatter

禁止：

- prompt contract 修改（含 SYSTEM_PROMPT 内容）
- 模型调用修改（模型名、参数、调用路径）
- retrieval strategy 行为修改
- schema 字段修改（含 `year: str` 类型）
- DB schema 修改
- evidence 行为修改
- `<think>` 协议修改
- `complexity` / `sub_dependencies` 删除或修改
- 借类型统一顺手做 `Any→object`、`str→Literal`、默认值变更、返回值重写等任何契约改动

review 时判据：一个 commit 的 diff 如果无法用"表达方式变了"解释，就越界。

## 1. Phase 0 — 不可变基线

以下结果为既有评测/测试基线。**Phase 1 不重跑 retrieval/generation ablation**；这些数字仅用于最终回归声明，不作为中间步骤的重复验收项。

### 测试基线（2026-08-21 全量 pytest）

```
6 failed, 457 passed, 13 skipped
```

预存失败清单（Phase 1 结束时必须保持同一状态——不多不少）：

```
tests/integration/test_cross_doc.py::test_cross_doc_extras_reach_final_results
tests/integration/test_ingestion.py::test_reindex_partial_embed_failure_preserves_old_index
tests/integration/test_ingestion.py::test_failed_doc_can_be_retried
tests/integration/test_ingestion.py::test_questions_align_with_persisted_chunks
tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries
tests/unit/test_retrieval_year_coverage.py::test_supplement_appends_missing_related_years
```

### 评测基线（沿用 docs/plans/2026-08-23-ablation-report.md，不重跑）

| 指标 | 值 |
|---|---|
| retrieval hit@10 / recall@10 | 1.000 |
| MRR | ≈ 0.85–0.87（8 组策略组合无差异） |
| generation eval（gate off） | 73.3% |

## 2. 任务 A：文档一致性

### A1. CLAUDE.md 重写架构快照

改为两层结构：

```
Default Runtime Pipeline（当前默认配置真实路径）
  QueryRewrite → Intent(V3) → Hybrid(向量+BM25+question 三路 RRF)
  → Rerank → MMR → Prompt → SSE

Optional Strategies（代码存在、默认关闭、env 可开）
  cross_doc / section_boost / section_supplement /
  year_supplement / query_decomposition / evidence_gate
```

修正项：

- intent 模型：DeepSeek-R1-0528-Qwen3-8B → **DeepSeek-V3**（R1 已挪 rewrite_model，见 config.py:12-13）
- vision 模型：DeepSeek-OCR → **Qwen/Qwen3-VL-8B-Instruct**
- embedding_text 实验史压缩为一句话 + 指向 plan 文档，只保留有决策价值的结论（v2 负收益回滚），删除过程叙述

### A2. README.md 同步

- 技术栈表：intent / vision 两行改实际模型名
- 检索管线图：Optional Strategies 加"默认关闭"标注
- 对外能力描述保持稳定口径，不写实验细节

### A3. AGENTS.md 修正

- 删除 §9 Git LFS 整节（无 .gitattributes，已过期；CLAUDE.md 已标注）
- §4 LLM 栈："对话 + 视觉走 MiniMax M3" → 实际现状（chat 默认 SiliconFlow DeepSeek-V3，MiniMax 为备选 provider；视觉 Qwen3-VL-8B）
- §3 "无 ruff/black/flake8 配置" → 更新为本轮新增的 ruff 配置说明
- §5 关键文件锚点：纯行号锚点改为「文件 + 函数名」格式（行号必腐坏）；CLAUDE.md 的 markdown 链接行号可点击性保留，只修已知错位

### A4. TODO.md 重写

按优先级分层结构（P0 核心正确性 / P1 工程质量 / P2 优化 / P3 未来），只放真待办：

- 移除已完成项（embedding_text 实验全链、模型切换记录、"最近改动 2026-08-20"整节）
- 实验历史链接到 `docs/plans/` 对应文档，**不新建 `docs/experiments/` 目录**（plans 已承载，避免第二真相源）
- 已知技术债条目保留并指向后续 Phase 计划

### 验收

- CLAUDE.md 架构快照与 config.py 当前默认值逐项一致（策略开关、模型名）
- 四个文件中不再出现：DeepSeek-OCR 作为当前 vision 模型、R1 作为 intent 模型、Git LFS、"小批量验证待决定"等历史状态描述
- grep 验证：`DeepSeek-OCR` 仅出现在历史结论语境；`LFS` 不再出现

## 3. 任务 B：死代码

### B1. metadata.py 清理

- 删除 `_EMBEDDING_TEXT_PROMPT`（metadata.py:101-123）
- 删除尾部弃用注释块（metadata.py:126）
- 顶部 docstring 删除 "Also provides embedding_text enhancement using a small model (Qwen2.5-7B-Instruct)" 段落
- 保留 `METADATA_PROMPT` 与 `ChunkMetadataGenerator` 主功能不动

### B2. 明确排除（不做）

- `sub_dependencies` / `complexity` → Phase 2 契约变更
- `app/ingestion/embedding_text.py` 模块 + indexer.py:22 的 `noqa: F401` 导入 → tools/reembed_v2.py ablation 依赖（ablation 报告明确决策"保留"）
- Ruff 报出的 unused import 逐个人工判断后才删，防误伤 re-export / 副作用导入

### 验收

- 全仓 grep `_EMBEDDING_TEXT_PROMPT` 为 0 命中
- `import app.main` 通过
- tests/unit 全绿（与基线一致）

## 4. 任务 C：Ruff

### C1. 配置与安全修复

requirements-dev.txt 增加 `ruff`。新建 `ruff.toml`：

```toml
line-length = 100
target-version = "py311"

[lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
```

**修复三分类硬约束**（防"为了绿灯机械改坏代码"）：

1. **机械且语义安全** → 允许 `--fix`：unused import、import 排序（I）、f-string 前缀（UP 部分）、明显拼写类
2. **可能改变行为** → 逐个人工判断：UP 的部分规则（如 typing 改写）、SIM 分支简化、B008/B904 等。改不改看 diff 可读性与风险，拿不准就 ignore
3. **需要设计判断** → 一律进白名单并写理由，不在本轮修

ignore 白名单初始预期（以实际扫描结果为准，每条必须带理由写入 ruff.toml）：

```toml
[lint.ignore]
# 示例格式（执行时按实际命中填充）：
# B904 = "raise-from 改写涉及异常链语义，历史代码量大，留待行为轮"
# SIM108 = "三元表达式改写降低分支可读性"
```

范围：`app/` `tools/` `eval/` `tests/`

### C2. format 单独收尾 commit

`ruff format` 在所有其他任务完成后的**最后一个独立 commit** 执行，与任何语义修改隔离，保住 git blame。

### 验收

- `ruff check .` 退出码 0（含白名单）
- 白名单每条均有理由注释
- C1 与 C2 是两个独立 commit
- format commit 的 diff 用 `--stat` 抽查无语义行变化（仅空白/引号/换行）

## 5. 任务 D：类型风格（严格划界）

**唯一允许的替换**：`Optional[X]` → `X | None`（schemas.py 及全仓同模式处）。

禁止顺手：

- 默认值变更（如 `= ""` → `= None`）
- 返回值类型重写
- Pydantic schema 结构调整
- `year: str = ""` 改动（牵动 evidence.py 字符串比较与 RetrievedChunk 契约，属 Phase 2）

注意 AGENTS.md 已有的例外：`threading.Lock` 等非 type 对象不能用 `|`，保持 `Optional[X]`。

### 验收

- 全仓 `typing.Optional` 剩余使用仅为非 type 对象场景
- tests/unit 与基线一致

## 6. 任务 E：注释去日志化

### 保留原则（一句话定义）

> 注释只保留"当前设计是什么 + 为什么如此"；删除"什么时候做的 + 谁决定的 + 当时做过哪些尝试"。

清扫对象：

- `Day X 上午/下午`、`plan §x`、`2026-08-XX` 开发日记式注释
- "回滚""历史""临时""POC"等过程性标记（结论有价值时改写保留，过程删除）

改写示例：

```python
# 前：Strategy flags (Day 1 下午；plan §四.1) ... Day 2 上午 ...
# 后：
# Retrieval strategies default off: 8-group ablation showed no recall gain
# and slightly negative MRR on the Sany corpus. Env vars keep them available.
```

config.py 每个字段注释按「用途 / trade-off」重写。个别确有决策价值的实验出处保留一条指向 ablation 报告的引用，仅此而已。

### 验收

- grep `Day \d|plan §|回滚到|已弃用` 在 app/ 下 0 命中（历史结论语境除外，逐条判断）
- 抽查任意被改注释均回答"为什么"，而非"何时"

## 7. Commit 边界

| # | 内容 | commit |
|---|---|---|
| 1 | 任务 A 四个文档 | `docs: sync architecture docs with actual defaults (Phase 1)` |
| 2 | 任务 B 死代码 | `chore(ingestion): remove dead _EMBEDDING_TEXT_PROMPT (Phase 1)` |
| 3 | 任务 C1 ruff 配置 + safe fixes | `chore(lint): add ruff config + apply safe fixes (Phase 1)` |
| 4 | 任务 D 类型风格 | `refactor(types): Optional[X] -> X \| None (Phase 1)` |
| 5 | 任务 E 注释 | `chore(comments): replace dev-diary comments with design rationale (Phase 1)` |
| 6 | 任务 C2 format | `style: ruff format (Phase 1, no semantic changes)` |

每个 commit 后跑 `pytest tests/unit -q` 快检；任务 B/C1/D 任一失败立即回退该 commit 排查。

## 8. 最终验收

1. `D:/miniConda/envs/rag/python.exe -m pytest -q` → 与 Phase 0 基线完全一致：457 passed，且失败清单不多不少同为那 6 条
2. `D:/miniConda/envs/rag/python.exe -c "import app.main"` → 通过
3. `ruff check .` → 退出码 0
4. 文档 grep 检查通过（A 验收标准）
5. 注释 grep 检查通过（E 验收标准）
6. 不重跑评测 ablation——评测基线声明直接引用第 1 节数字
