# 下一步计划 (2026-08-22)

## 本次完成（Phase 1 清理轮，2026-08-21）

封版提交链（master）：

```
895a271 style: ruff format (Phase 1, no semantic changes)
47d6c6c chore(comments): replace dev-diary comments with design rationale
89f4f2a refactor(types): Optional[X] -> X | None
e4f3126 chore(lint): add ruff config + fix unused/mispositioned imports + latent gate bugs
374acbe chore(ingestion): remove dead _EMBEDDING_TEXT_PROMPT
edaafe1 docs: sync architecture docs with actual defaults
c3f868a docs(plan): Phase 1 execution plan
dfc1d5f docs(plan): Phase 1 cleanup spec
```

**当前 checkpoint**（Phase 0 → Phase 1 之间）：

- 测试：`459 passed, 6 failed, 13 skipped`（6 失败均为预存，无新增）
- 静态质量：`ruff check` 全绿，`ruff format` 已统一格式
- 文档/注释：CLAUDE/README/AGENTS/TODO 已对齐 config.py 真实默认；`Day X` / `plan §` 类开发日记注释清零（`设计审查 Pn-X` 是项目自身决策引用 ID，按 spec §6 保留）
- 死代码：`_EMBEDDING_TEXT_PROMPT` 整块删
- 类型风格：`Optional[X] → X | None` 5 处（仅 `memory.py:366` 的 `Optional[threading.Lock]` 因非 type 对象保留并 noqa）

**静态扫描暴露并修复的 3 个潜伏运行时缺陷**（这是本轮最大价值，超出"清理"定义）：

1. `pipeline.py` evidence gate refuse 分支调裸 `logger.warning`，模块此前未定义 logger → `evidence_gate_enabled=True` 即 NameError。修：模块级 `logger = logging.getLogger(__name__)`。测试锁：[tests/unit/test_evidence_gate_regression.py](tests/unit/test_evidence_gate_regression.py)。
2. `retrieval.py` 字符串注解 `"RetrievalFilter | None"`（3 处）从未导入。修：补 `from app.core.retrieval_filter import RetrievalFilter`；同时删 `pipeline.py:5` 未使用的导入。
3. `embedding.py` openai.RateLimitError 被 base.RateLimitError 遮蔽。修：openai 版别名 `OpenAIRateLimitError`，except 改为 `(RateLimitError, OpenAIRateLimitError)` —— 保住"429 不计熔断"业务不变量。

## Phase 1 不可变基线（重新跑前必看）

| 项 | 值 |
|---|---|
| 全量 pytest | `459 passed, 6 failed, 13 skipped` |
| unit pytest | `400 passed, 2 failed` |
| 6 条预存失败 | tests/integration/test_cross_doc.py::test_cross_doc_extras_reach_final_results<br>tests/integration/test_ingestion.py::test_reindex_partial_embed_failure_preserves_old_index<br>tests/integration/test_ingestion.py::test_failed_doc_can_be_retried<br>tests/integration/test_ingestion.py::test_questions_align_with_persisted_chunks<br>tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries<br>tests/unit/test_retrieval_year_coverage.py::test_supplement_appends_missing_related_years |
| 评测（不重跑） | retrieval hit@10/recall@10 = 1.000；MRR ≈ 0.85–0.87；generation 73.3%（gate off） |
| Ruff | `ruff check` 退出 0，`ruff format --check` 退出 0 |

任何 Phase 2+ 修改不得新增测试失败；失败清单必须维持这 6 条不变（修复其中某些 xfail 自然转绿是允许的，但需从清单中删除）。

## 下一步：Phase 2 重新分桶 + 优先级判定

**关键判断**（来自用户收尾讨论）：Phase 1 之前的 6 候选任务不能按机械顺序执行，需要先做"留 / 改 / 删"的决策判定。

### A. 数据/接口契约
- `complexity`（pipeline / rewrite / schemas / prompt 仍残留字段，无消费方）
- `sub_dependencies`（R1 rewrite prompt 要求 LLM 输出，eval 期间不能贸然改）
- `year: str` → `int | None`（牵动 evidence.py 字符串比较 + RetrievedChunk 契约）

### B. 功能路径
- evidence gate 接通（依赖 A 的契约 + text[:300] 截断处理）

### C. 协议/架构
- `` 协议解耦（需独立设计任务；涉及 SSE/前端/TagStreamParser/DB/thinking_content 多方）

### D. 基础设施
- Alembic 迁移收敛 `init_db()` 的 20+ ALTER TABLE（与 CLAUDE.md "do not modify db.py models without migrations" 约束的关系需理清）

### 优先级决策框架（来自用户收尾判断）

> 哪些东西是"当前系统实际上没有价值却还存在"，哪些东西是"未来确实需要，只是目前实现得不好"？

这个判断直接决定每个 Phase 2 任务是 **delete / refactor / keep**。

## 立即可做的下一步（推荐先做这条）

**A 类已收口**（[docs/plans/2026-08-22-phase2-contract-decisions.md](2026-08-22-phase2-contract-decisions.md) 固化）。**当前 checkpoint**：

```
Phase 0 baseline
   ↓
Phase 1 清理 + 静态质量
   ↓
Phase 2 A 契约决策（不执行）
   ↓
★ 当前
   ↓
Phase 2 B 设计侦察（不执行）
   ↓
B 决策固化
   ↓
再决定是否真正接通 Gate
```

**B 类先做设计侦察、不直接接通 Gate**——避免"为不确定价值的 Gate 先大改 query_type ontology"的二次返工。

侦察 4 步：

1. 全仓梳理 Evidence Gate 当前输入/输出契约（pure read）
2. 梳理 `query_type` 三套 ontology 各自表达什么、谁消费、是否真在生产路径生效
3. 明确 Gate 实际依赖哪些字段——**这步会决定 B1（Gate 价值验证）与 B2（query_type 统一）是否可独立进行**
4. 基于上述发现设计 Gate 实验矩阵

完成 4 步后产出一份 B 类侦察决策文档，类似 A 类的结构，再决定是否进入执行期。

**核心原则**：

> "B 类被列入计划" ≠ "B 类必须执行"。B 类所有候选同样接受 delete / refactor / keep 审查。Evidence Gate 最终完全可能得 **DELETE**——这并不是浪费时间，而是 Phase 2 决策机制有效运行的证明。

## 旧"立即可做"段落（已被替换，保留以追溯思路）

> 重新整理 Phase 2 问题清单——而不是直接执行任何一项：
>
> 1. 给 A-D 每类写一段简短的价值评估...
   - Alembic：项目目前单机单库，引入迁移工具的临界点在哪？
2. 根据评估给出 **delete / refactor / keep** 标签
3. 重新排序成新 Phase 2-4 路线图

不做这一步就直接进 Phase 2 会回到"按 TODO 机械执行"的老路。

## 目标

Phase 1 checkpoint 已稳定。下一步应该是"思考期"，不是"执行期"——用 1-2 个 session 重新评估 Phase 2 候选价值，再开始任何代码层动作。

---

**为什么这一步特别重要**：Phase 1 之前的代码、注释、文档混乱掩盖了"哪些 dead weight / 哪些是未来价值"的判断。现在清理完后，问题边界清晰得多，但也暴露了"按 TODO 顺序机械执行"的危险（可能删了有价值的功能或重构了不该动的东西）。
