# Phase 2 Contract Decisions (2026-08-22)

> **Scope**：Phase 2 A 类数据契约决策——对 `RewriteResult.complexity` / `sub_dependencies` / `query_type` 等字段的去留与伴随 contract 变化做决议。
>
> **本文件不执行修改，只固化决策依据**。执行时按 §6 Validation strategy 走。

## 1. Decision Summary

| 项目 | Decision | Confidence |
| --- | --- | --- |
| `RewriteResult.complexity` (field) | **DELETE** | 高 |
| `RewriteResult.sub_dependencies` (field) | **DELETE** | 高 |
| `complexity` (concept：复杂问题分类) | **KEEP** | 高 |
| `_is_complex_query()` (模型路由启发式) | **KEEP** | 高 |
| `RAGPromptBuilder.build_messages(complexity=...)` (参数) | **DELETE** | 高 |
| SYSTEM_PROMPT "复杂问题 → ``" 条件分支 | **NORMALIZE（改无条件规则）** | 高 |
| `query_type` 多套 ontology 并存 | **REFACTOR，deferred to B 类** | 高 |

**关键术语约定**：

- "complexity **field**" 指的是 `RewriteResult.complexity` 这个 schema 字段
- "complexity **concept**" 指的是"复杂问题需要特殊处理"这个产品/系统层面的概念（由 `_is_complex_query` 决策、由 SYSTEM_PROMPT 文本体现）
- 删 field ≠ 删 concept。本次 DELETE 只针对 field。

## 2. `complexity` field — DELETE

### 2.1 Current lifecycle

| 层级 | 文件:行 | 行为 | 角色 |
| --- | --- | --- | --- |
| Schema | [app/models/schemas.py:132](app/models/schemas.py#L132) | `complexity: str = "complex"` | writer |
| Prompt（生成） | [app/core/rewrite.py:141,158,162,166](app/core/rewrite.py) | R1 输出 JSON 示例含字段 | writer |
| Prompt（说明） | [app/core/rewrite.py:170](app/core/rewrite.py#L170) | 解释字段语义 | writer |
| Prompt（自检） | [app/core/rewrite.py:188](app/core/rewrite.py#L188) | R1 输出检查项 | writer |
| Runtime（解析） | [app/core/rewrite.py:262-272](app/core/rewrite.py) | 解析 R1 JSON，非 simple/complex 强制回 "complex" | writer |
| Runtime（写入） | [app/core/pipeline.py:319](app/core/pipeline.py#L319) | fast path 硬编码 `query_complexity = "complex"` | writer |
| Runtime（透传） | [app/core/pipeline.py:331,336,573](app/core/pipeline.py) | 透传给下游调用 | writer |
| Prompt 模板（接收） | [app/core/prompt.py:163](app/core/prompt.py#L163) | `build_messages(complexity=...)` 参数；**注释自承"保留参数兼容性"** | **形式 reader，无消费** |
| Reader (frontend) | frontend/ | 0 命中 | 无 |
| Reader (eval) | eval/ | 0 命中 | 无 |
| Reader (tools) | tools/ | 0 命中 | 无 |
| Reader (diag API) | app/api/diagnostics.py | 0 命中 | 无 |
| Reader (diag UI) | tools/diagnostics.html `renderRewrite` | 不渲染 complexity | 无 |
| Reader (test) | tests/unit/test_rewrite_complexity.py | 测试 `_is_complex_query`，**不依赖字段** | 名字相关，不消费 |
| Reader (concept) | SYSTEM_PROMPT [app/core/prompt.py:50-71](app/core/prompt.py#L50-L71) | "复杂问题必须用 ``" 文本分支 | **与字段脱钩——LLM 自判** |

### 2.2 Evidence (current code state, primary)

1. `build_messages` 收 `complexity` 参数但**完全不使用**——`prompt.py:163` 注释自承
2. SYSTEM_PROMPT 文本层有 complexity 分支，但**依赖 LLM 自判**，与字段无关
3. 所有可能的 reader 层（frontend / eval / tools / diag API / diag UI）全部 0 命中
4. **No in-repository reader found**

### 2.3 Historical context (informational, not evidence)

`docs/plans/2026-08-19-cross-doc-rag-improvement.md:100,162` 曾计划将 `complexity` 通过 `query_type` 灌入 `evidence_organize`——该 wiring 从未实现。但此事实仅证明"当时有人认为它可能有用"，**不证明现在没用**。决策依据仍以 §2.2 的当前代码状态为准。

### 2.4 Decision

**DELETE field. KEEP concept.**

- 删字段不删概念。`_is_complex_query` 与 SYSTEM_PROMPT 中"复杂问题"语义保留
- 删除后 SYSTEM_PROMPT 中的"复杂问题用 ``"条件分支必须 normal 化为无条件规则（见 §4）

### 2.5 Required changes (execution checklist)

- [ ] `app/models/schemas.py:132` 删除 `complexity` 字段
- [ ] `app/core/rewrite.py` R1 prompt examples (line 121-170) 删除 `complexity` 字段
- [ ] `app/core/rewrite.py:170` 解释文字删除
- [ ] `app/core/rewrite.py:188` 自检项删除
- [ ] `app/core/rewrite.py:262-272` 解析逻辑删除
- [ ] `app/core/pipeline.py:319,331,336,573` 透传删除
- [ ] `app/core/prompt.py:163` `build_messages` 参数删除
- [ ] SYSTEM_PROMPT 同步 normal 化（见 §4）

### 2.6 Explicit non-changes

- **`_is_complex_query()` 保留**——模型路由决策与本次 DELETE 无关
- **`tests/unit/test_rewrite_complexity.py` 不删**——该测试测的是 `_is_complex_query`，不依赖字段消费
- **SYSTEM_PROMPT 中"复杂问题"概念保留**——只是把条件分支改成无条件（§4）
- **`query_type` 字段保留**——与 complexity 是独立 contract，不在本次范围

### 2.7 Test file rename (implementation phase, not now)

`tests/unit/test_rewrite_complexity.py` 文件名易与本 DELETE 决策混淆。实施阶段建议改名为 `test_rewrite_routing.py` 或类似——避免后续维护者误以为该测试依赖字段、进而把它一并删除。

## 3. `sub_dependencies` field — DELETE

### 3.1 Current lifecycle

| 层级 | 文件:行 | 行为 | 角色 |
| --- | --- | --- | --- |
| Schema | [app/models/schemas.py:127](app/models/schemas.py#L127) | `sub_dependencies: list[list[int]] = []` | writer |
| Prompt（生成） | [app/core/rewrite.py:141,154,158,162,166](app/core/rewrite.py) | R1 输出 JSON 示例 | writer |
| Prompt（说明） | [app/core/rewrite.py:143,148](app/core/rewrite.py) | 标注规则 | writer |
| Prompt（自检） | [app/core/rewrite.py:188](app/core/rewrite.py#L188) | R1 输出自检项 | writer |
| Runtime（解析） | [app/core/rewrite.py:247-249](app/core/rewrite.py) | 从 R1 JSON 解析 | writer |
| Runtime（透传） | [app/core/pipeline.py:330](app/core/pipeline.py#L330) | 透传给 `ctx.record` | writer |
| Runtime（**执行顺序**） | [app/core/pipeline.py:358](app/core/pipeline.py#L358) | `asyncio.gather(*[_retrieve_one(q) for q in sub_queries])` | **按列表顺序并发，不读依赖** |
| Reader (frontend) | frontend/ | 0 命中 | 无 |
| Reader (eval) | eval/ | 0 命中 | 无 |
| Reader (tools) | tools/ | 0 命中 | 无 |
| Reader (diag API) | app/api/diagnostics.py | 0 命中 | 无 |
| Reader (diag UI) | tools/diagnostics.html `renderRewrite` | 不渲染 sub_dependencies | 无 |
| Reader (test) | tests/unit/test_rewrite_complexity.py | 名字含字段，**断言不验证消费** | 形式 reader |

### 3.2 Evidence (current code state, primary)

1. **运行时执行路径完全脱钩**——`asyncio.gather` 按列表顺序并发，与依赖关系无关
2. 所有可能的 reader 层 0 命中
3. **No in-repository reader found**
4. 字段被 R1 prompt 强约束生成、被写入 ctx.record、被设计要求自检——但**没有一处**将其作为决策输入

### 3.3 Historical context (informational)

`docs/plans/2026-08-19-cross-doc-rag-improvement.md:527` 提到"sub_dependencies 在 P0 阶段开始使用（控制证据表的呈现顺序）"——该 wiring 从未实现。同样仅作 context，不作决策证据。

### 3.4 Decision

**DELETE field.**

### 3.5 Required changes

- [ ] `app/models/schemas.py:127` 删除 `sub_dependencies` 字段
- [ ] `app/core/rewrite.py` R1 prompt examples 删除该字段
- [ ] `app/core/rewrite.py:143,148` 说明文字删除
- [ ] `app/core/rewrite.py:188` 自检项删除
- [ ] `app/core/rewrite.py:247-249` 解析逻辑删除
- [ ] `app/core/pipeline.py:330` `ctx.record` 删除该字段

### 3.6 Explicit non-changes

- 子问题并发执行逻辑不变（`asyncio.gather` 按 sub_questions 列表顺序）
- `query_type` 字段保留（独立 contract）
- 若未来 Evidence Gate 接通且需要"按依赖顺序呈现"再考虑引入——属新设计，不属本次恢复

### 3.7 External contract caveat

"No in-repository reader found" — 本结论限定为当前仓库内。**未覆盖**：

- 仓库外脚本读取 diag.json
- 监控/告警系统解析诊断数据
- 人工手动检查 diag.json 做调试

如有此类外部消费者存在，需另行评估。删除前建议确认运维/团队习惯。

## 4. SYSTEM_PROMPT normalization — 伴随 contract 改动

### 4.1 原状

[app/core/prompt.py:50-71](app/core/prompt.py#L50-L71)：

- "## 复杂度判断" 节：列出复杂问题特征（跨文档对比 / 为什么类 / 多步流程 / 代词引用）
- "## 输出格式" 节：复杂问题必须用 `` 标签；简单问题可省略 ``
- "## 输出前检查清单"：含"如果是复杂问题，是否用了 `` 标签？"项

### 4.2 Decision

**NORMALIZE**: 把"复杂问题 → 必须 ``"改为无条件规则。

这不是"删 complexity 时顺手清理"——这是**必须的 contract normalization**。否则 DELETE complexity 后会形成二阶 split-state：prompt 文本仍引用 complexity 概念，但字段已删，LLM 无法再获得 `complexity` 字段信息。

### 4.3 Implementation sketch

- [ ] 删除 "## 复杂度判断" 整节（LLM 自判能力足够，无需启发式列表）
- [ ] "## 输出格式" 简化为无条件 `` 协议要求
- [ ] "## 输出前检查清单" 中 "如果是复杂问题，是否用了 ``" 改为无条件 "是否用了 `` 标签？"

### 4.4 Cross-impact

这是 protocol 变更，与 Phase 2 C 类 `` 协议解耦方向一致。建议：

- **在 Phase 2 C 类正式开工前先做这一步**（作为 complexity DELETE 的伴随改动）
- 避免两个协议层改动相互干扰
- 此次 normal 化**不涉及**解耦 SSE / DB / frontend——只是把 prompt 文本统一

### 4.5 Explicit non-changes

- 不删除 `` 标签本身
- 不动 TagStreamParser
- 不动前端 SSE 事件格式
- 不动 DB `thinking_content` 列

## 5. `query_type` — REFACTOR, deferred to Phase 2 B 类

### 5.1 Finding (本次最重要的新发现)

同一个字段名 `query_type` 在仓库内承载**至少 3 套不兼容 ontology**：

| 来源 | 词汇 | 维度 |
| --- | --- | --- |
| [docs/plans/2026-08-19-cross-doc-rag-improvement.md:93](docs/plans/2026-08-19-cross-doc-rag-improvement.md#L93) | `"comparison" \| "summary" \| "single"` | task type |
| [app/core/evidence.py:150, 384](app/core/evidence.py#L150) | `"simple" \| "complex"` | complexity |
| [app/core/pipeline.py:440](app/core/pipeline.py#L440) | `"simple" \| "complex"` | complexity |
| [app/core/evidence.py:476, 490](app/core/evidence.py#L476) | `"comparison" \| "summary"` | task type |
| [tests/unit/test_evidence.py:28, 48](tests/unit/test_evidence.py#L28) | `"comparison" \| "summary"` | task type |

→ **同一个字段名在不同模块承载不同 ontology**：complexity vs intent/task type。

### 5.2 Decision

**REFACTOR, deferred.** **不与 complexity / sub_dependencies 合并执行。**

### 5.3 Rationale for deferral

- 与 Evidence Gate 接通（Phase 2 B 类）是同一数据契约问题，应一并处理
- 当前 `evidence_gate_enabled=False`，影响范围仅在 EvidenceTable 构造期，不会因 query_type 多语义产生线上 bug
- [evidence.py:476, 490](app/core/evidence.py#L476) 与 `n_slots > 1` 逻辑重叠，说明原作者自己也意识到语义模糊——是观察时点的诚实记录，不是 bug

### 5.4 Open question for Phase 2 B 类

接通 Evidence Gate 时是否统一 query_type 为单一 ontology？

- 选项 A：沿用 plan 文档的 `"comparison" \| "summary" \| "single"`（task type 维度）
- 选项 B：沿用 evidence.py 的 `"simple" \| "complex"`（complexity 维度）
- 选项 C：拆为两个独立字段

此问题留至 Phase 2 B 类决定。

## 6. Validation strategy (执行 DELETE 时)

### 6.1 Before deletion (baseline)

```bash
# 记录命中清单，作为删除后对照
grep -rn "complexity" app/ tests/ tools/ eval/ frontend/ docs/
grep -rn "sub_dependencies" app/ tests/ tools/ eval/ frontend/ docs/
```

记录：
- 所有命中点的 file:line
- 哪些属于"保留的 concept"（_is_complex_query / SYSTEM_PROMPT 中正常用法）
- 哪些属于"应删除的 field reference"

### 6.2 After deletion (每步后立即跑)

```bash
D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q
D:/miniConda/envs/rag/python.exe -c "import app.main"
```

**必须 0 命中（除 §2.6 / §3.6 显式保留项）**：

```bash
grep -rn "RewriteResult.complexity\|query_complexity\|build_messages(complexity" app/
grep -rn "RewriteResult.sub_dependencies" app/
```

### 6.3 Final acceptance (after full DELETE + §4 normalization)

- `pytest -q` → **维持基线**：`459 passed, 6 failed, 13 skipped`；6 failed 不变
- `ruff check` → 退出码 0
- `ruff format --check` → 已 formatted
- R1 prompt 输出 JSON 示例中**无** `complexity` / `sub_dependencies` 字段
- `RewriteResult` 字段数 -2
- SYSTEM_PROMPT 文本简化（"## 复杂度判断" 节删除，"复杂问题必须 ``" 改为无条件）
- `tests/unit/test_rewrite_complexity.py` 改名 `test_rewrite_routing.py`（实施阶段）

### 6.4 External contract caveat (post-delete)

删除后**新写入的 diag.json** 不含 complexity / sub_dependencies 字段。**已存在的历史 diag.json** 仍含这些字段。如有外部脚本读取历史 diag.json 做分析，需列入旧字段兼容名单——超出本决策范围。

## 7. Method note (设计审查原则 — 本次形成的可复用方法)

本次决策的形成方法，建议作为项目可复用的设计审查原则：

1. **不要把"字段存在 / Prompt 提到 / runtime 写入 / runtime 消费 / 诊断输出"混为一谈**——分别建立生命周期，再决定
2. **每个字段至少分四层核查**：Schema / Prompt / Runtime / Reader；每一层都给出 file:line 证据
3. **"没有 reader" 是必要条件，不是充分条件**——同时要确认 "concept 本身是否有其他表达路径"
4. **field 与 concept 严格区分**：删字段 ≠ 删概念。误删概念会造成"系统某些决策维度消失"的连锁问题
5. **伴随改动必须 explicit 列出**：防止"删除 X 时顺手动了 Y"造成隐式行为变更
6. **External contract caveat 必写**：grep 只能证明 in-repository 无读者；仓库外契约需另行评估
7. **历史计划是 context，不是 evidence**：曾有人计划用，不证明现在有价值

---

**Phase 2 A 类决策正式收口**。下一步是 Phase 2 B 类（Evidence Gate 接通 + query_type 统一）——那是另一个独立决策周期，不应与本次 DELETE 混做。
