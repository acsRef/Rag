# Issue #1-A — Complexity split-state + 死参数清理 (2026-08-22)

> **Stage 1 of Issue #1**：[Issue #1 plan](2026-08-22-issue1-answer-policy.md) §4.6（Reasoning protocol GAP-06）+ §4.8（complexity 死参数 GAP-10）
>
> 关联决策：
> - Phase 2 A 类契约决策（[docs/plans/2026-08-22-phase2-contract-decisions.md](2026-08-22-phase2-contract-decisions.md)，commit a96245e）—— 已决策 DELETE `RewriteResult.complexity` 字段（DECISION 已定，**本次 plan 执行该决策**）
> - Phase 2 B 侦察决策（[docs/plans/2026-08-22-phase2-b-recon.md](2026-08-22-phase2-b-recon.md) §4.4）—— `<think>` 跨层协议重构留 B/C 类
>
> 本 plan 只清 `complexity` 的 split-state + 死参数。**`<think>` 协议本身不动**（B/C 类范围）。
>
> `sub_dependencies` 字段不在本 plan 范围（独立 plan 处理）。

## 1. Scope

彻底清除 `complexity` 在仓库中的所有引用：

| 文件 | 修改内容 |
| --- | --- |
| `app/models/schemas.py` | 删除 `RewriteResult.complexity` 字段（line 132） |
| `app/core/rewrite.py` | 删除 R1 prompt 中 `complexity` 字段示例 + 解释文本 + self-check + parser 逻辑 |
| `app/core/pipeline.py` | 删除 `query_complexity` 变量（4 处）+ 透传给 build_messages 的 kwarg |
| `app/core/prompt.py` | (a) 删除 `SYSTEM_PROMPT` 中 complexity 引用（"## 复杂度判断" 整节 + "复杂问题必须用 ``" 条件分支 + checklist 中"如果是复杂问题"项）；(b) `<think>` 改无条件规则；(c) 删除 `build_messages` 死参数 `complexity` |
| `tests/unit/test_rewrite_complexity.py` | 更新 mock JSON 输出（移除 `"complexity": "..."` 键） |

## 2. Decisions Locked

| 决策 | 内容 |
| --- | --- |
| D1 | 移除 `complexity` 字段（Phase 2 A 类决策执行） |
| D2 | `<think>` 改为**无条件规则**（"所有回答采用统一 reasoning/output 协议"）—— 不是 complexity 字段触发的条件分支 |
| D3 | 删 `RAGPromptBuilder.build_messages(complexity=...)` 参数 |
| D4 | `RewriteResult.complexity` 删后，R1 prompt 输出 JSON 示例不再含此字段；parser 不再解析；pipeline 不再读 |
| D5 | 不动 `RewriteResult.sub_dependencies`（独立 plan） |

## 3. Implementation（按文件）

### 3.1 `app/models/schemas.py`（1 处）

删除 `RewriteResult.complexity` 字段（line 126-132 区域）：

```python
# 删除前
    sub_dependencies: list[list[int]] = Field(default_factory=list)
    # 难度分类（控制 CoT 触发）：
    # - "simple": 直接事实查询，无需复杂推理
    # - "complex": 需要跨文档/多步骤推理/前提验证
    # LLM 在 rewrite 时输出，失败时默认 "complex"（保守触发 CoT 防止误判）
    complexity: str = "complex"

# 删除后
    sub_dependencies: list[list[int]] = Field(default_factory=list)
```

### 3.2 `app/core/rewrite.py`（5 处）

**Prompt examples（line 141, 166）**：从 JSON 示例中删除 `"complexity": "complex"`

```python
# line 141（删除前）
{{"rewritten_query": "改写后的主查询", "sub_questions": ["子问题1", "子问题2", ...], "sub_dependencies": [[], [0], [0,1]], "complexity": "complex"}}

# line 141（删除后）
{{"rewritten_query": "改写后的主查询", "sub_questions": ["子问题1", "子问题2", ...], "sub_dependencies": [[], [0], [0,1]]}}
```

（line 166 同样删除 `"complexity": "complex"`）

**Explanatory text（line 170）**：

```python
# 删除前
`complexity` 控制下游是否触发 Chain-of-Thought：
# 删除后
# （整行删除）
```

**Self-check（line 188）**：从 checklist 删除 complexity 项

```python
# 删除前
□ complexity 是否正确标注？
# 删除后
# （整行删除）
```

**Parser（line 262-272）**：删除 complexity 解析逻辑

```python
# 删除前
        # 解析 complexity（控制 CoT 触发）
        complexity = data.get("complexity", "complex")
        if complexity not in ("simple", "complex"):
            complexity = "complex"
        return RewriteResult(
            rewritten_query=rewritten_query,
            sub_questions=sub_questions,
            sub_dependencies=deps,
            complexity=complexity,
        )

# 删除后
        return RewriteResult(
            rewritten_query=rewritten_query,
            sub_questions=sub_questions,
            sub_dependencies=deps,
        )
```

### 3.3 `app/core/pipeline.py`（4 处）

**Fast path（line 319）**：

```python
# 删除前
            query_complexity = "complex"  # fast path 默认复杂
# 删除后
# （整行删除）
```

**sub_questions record（line 331）**：删除 `complexity=...` kwarg

```python
# 删除前
                ctx.record("rewrite",
                    original=req.query,
                    rewritten=rewrite_result.rewritten_query,
                    sub_questions=rewrite_result.sub_questions,
                    sub_dependencies=rewrite_result.sub_dependencies,
                    complexity=rewrite_result.complexity,
                )
# 删除后
                ctx.record("rewrite",
                    original=req.query,
                    rewritten=rewrite_result.rewritten_query,
                    sub_questions=rewrite_result.sub_questions,
                    sub_dependencies=rewrite_result.sub_dependencies,
                )
```

**Variable reassignment（line 336）**：

```python
# 删除前
            query_complexity = rewrite_result.complexity
# 删除后
# （整行删除）
```

**build_messages kwarg（line 573）**：

```python
# 删除前
            complexity=query_complexity,
# 删除后
# （整行删除；query_complexity 变量在前两处已删，不会再被引用）
```

### 3.4 `app/core/prompt.py`（SYSTEM_PROMPT + builder）

#### 3.4.1 `SYSTEM_PROMPT` 文本修改

**Section "## 复杂度判断"（line 52-57）**：整节删除

```python
# 删除前
"## 复杂度判断\n"
"拿不准时默认走复杂路径。涉及以下特征的都算复杂问题：\n"
"- 跨文档对比/区别/差异\n"
"- 为什么/如何/原理类\n"
"- 多步操作流程\n"
"- 用户用代词引用上文（它、这、那个、上面说的）\n"
"\n"

# 删除后
# （整段删除）
```

**Section "## 输出格式"（line 59-70）**：从条件分支改为无条件

```python
# 删除前
"## 输出格式\n"
"复杂问题必须用标签分离思考与回答:\n"
"```\n"
"<think>\n"
"分步推理过程（用户看不到）\n"
"</think>\n"
"<answer>\n"
"最终回答（markdown 格式，用户可见）\n"
"</answer>\n"
"```\n"
"\n"
"简单问题可省略 <think>，直接用 <answer> 或纯文本。\n"

# 删除后
"## 输出格式\n"
"所有回答采用统一的 reasoning/output 协议：\n"
"```\n"
"<think>\n"
"分步推理过程（用户看不到）\n"
"</think>\n"
"<answer>\n"
"最终回答（markdown 格式，用户可见）\n"
"</answer>\n"
"```\n"
```

**Examples（line 72-93）**：简化（去掉"复杂问题 vs 简单问题"二分示例）

保留 1 个无条件示例（合并示例 1 + 示例 2 为单一 `<think>` + `<answer>` 示例）。具体：

```python
# 删除前
"## 示例\n"
"\n"
"【示例1 — 复杂问题】\n"
'用户："JWT 和 Session 有什么区别？"\n'
"<think>\n"
"1. 用户问的是 JWT 和 Session 的对比，涉及跨文档交叉分析。\n"
"2. Source 1 描述了 JWT 的无状态特性。\n"
...
"</answer>\n"
"\n"
"【示例2 — 简单问题】\n"
'用户："JWT 是什么？"\n'
"<answer>\n"
"JWT (JSON Web Token) 是一种无状态的认证机制, 将用户信息加密存储在 token 中...[1]\n"
"</answer>\n"

# 删除后
"## 示例\n"
"\n"
'用户："JWT 和 Session 有什么区别？"\n'
"<think>\n"
"1. 用户问的是 JWT 和 Session 的对比，涉及跨文档交叉分析。\n"
"2. Source 1 描述了 JWT 的无状态特性。\n"
"3. Source 2 描述了 Session 的服务端存储。\n"
"4. 对比结果: JWT 无状态适合分布式, Session 有状态适合单机。\n"
"</think>\n"
"<answer>\n"
"根据文档，JWT 和 Session 的主要区别如下:\n"
"- **JWT**[1]: 无状态, 适合分布式系统, 不需要服务端存储。\n"
"- **Session**[2]: 有状态, 服务端存储, 适合传统单体应用。\n"
"</answer>\n"
```

**Checklist（line 103-114）**：删除 complexity 引用项

```python
# 删除前
"□ 如果是复杂问题，是否用了 <think> 标签？\n"
...
"□ 复杂问题时，<think> 内部是否包含了推理步骤？\n"

# 删除后
"□ 是否使用了 <think> 标签分离推理与回答？\n"
"□ <think> 内部是否包含了推理步骤？\n"
```

#### 3.4.2 `RAGPromptBuilder.build_messages` 参数删除

```python
# 删除前
    def build_messages(
        self,
        query: str,
        history: list[dict],
        summary: str,
        retrieved_chunks: list[RetrievedChunk],
        complexity: str = "complex",  # simple/complex, 控制 CoT 触发（保留参数兼容性）
    ) -> list[dict]:

# 删除后
    def build_messages(
        self,
        query: str,
        history: list[dict],
        summary: str,
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[dict]:
```

### 3.5 `tests/unit/test_rewrite_complexity.py`（2 处 mock 输出）

```python
# line 55 删除前
            '"sub_dependencies": [[], [], []], "complexity": "complex"}'
# line 55 删除后
            '"sub_dependencies": [[], [], []]}'

# line 73 删除前
        return '{"rewritten_query": "2023年营收", "sub_questions": ["2023年营收"], "sub_dependencies": [[]], "complexity": "simple"}'
# line 73 删除后
        return '{"rewritten_query": "2023年营收", "sub_questions": ["2023年营收"], "sub_dependencies": [[]]}'
```

> ⚠️ 注意：该测试文件 `tests/unit/test_rewrite_complexity.py` 命名仍含 `complexity`，但实际**测的是 `_is_complex_query` 模型路由决策**，不依赖字段。Issue #1-D 或后续阶段重命名为 `test_rewrite_routing.py`（per Issue #1 plan §2.2 Reasoning protocol 行备注）。**本 plan 不改名**。

## 4. Validation Protocol

### 4.1 删除前 baseline（已记录）

- 全量 pytest：`459 passed / 6 failed / 13 skipped`
- unit pytest：`400 passed / 2 failed`（含 Issue #1-A 即将修改的 prompt.py + pipeline.py 相关测试）
- 6 failed 预存失败集合不变

### 4.2 执行步骤

1. 修改 `app/models/schemas.py`（schema field 删）
2. 修改 `app/core/rewrite.py`（5 处）
3. 修改 `app/core/pipeline.py`（4 处）
4. 修改 `app/core/prompt.py`（SYSTEM_PROMPT 文本 + build_messages 参数）
5. 修改 `tests/unit/test_rewrite_complexity.py`（mock JSON）
6. 运行 `ruff format`（单独 commit）
7. 运行 `pytest tests/unit -q`：**失败集合不变**（6 failed 仍是原 6 条）；新 PASS（如果有新测试或因删除 dead param 释放测试）

### 4.3 必须通过的 grep

```bash
grep -rn "complexity" app/ tests/ --include="*.py" | grep -v __pycache__
```

**Expected**：0 hits（除 `tests/unit/test_rewrite_complexity.py` 文件名外——那是文件名遗留，Issue #1-D 重命名处理）。

```bash
grep -rn "query_complexity" app/ --include="*.py"
grep -rn "RewriteResult.complexity" app/ --include="*.py"
grep -rn "build_messages.*complexity" app/ --include="*.py"
```

**Expected**：0 hits each。

## 5. Out of Scope

- ✗ **不动 `RewriteResult.sub_dependencies`**（独立 plan 处理）
- ✗ **不动 `<think>` 协议本身**——本 plan 只把"由 complexity 触发"改为无条件，协议层（TagStreamParser / SSE / DB）B/C 类处理
- ✗ **不动 Evidence Gate**——audit GAP-04 / GAP-07 / GAP-09 是后续 sub-phase
- ✗ **不动 `tests/unit/test_rewrite_complexity.py` 文件名**——Issue #1-D 或后续阶段
- ✗ **不改 model routing**（`_is_complex_query` 函数不动）

## 6. Non-goals (this plan)

| 项 | 边界 |
| --- | --- |
| Prompt 内容强化 | 仅清理 complexity 引用；Grounding/Refusal/Citation/Output 措辞优化在 Issue #1-D/E |
| `RewriteResult.sub_dependencies` | 独立 plan |
| `<think>` 协议 | B/C 类 |
| Evidence Gate 接通 | 独立 plan |

## 7. Risk

| Risk | 缓解 |
| --- | --- |
| 删除 `complexity` 字段后某处代码仍引用导致 AttributeError | §4.3 grep 强制 0 命中验证；pytest 全量守口 |
| R1 模型仍产生 `complexity` 字段（prompt contract 变化） | parser 已删，不会 crash；字段被 R1 自生成忽略即可 |
| pipeline.py:573 `complexity=query_complexity` kwarg 与 build_messages 死参数删除顺序 | 同步进行（同一 plan），`grep -rn "complexity"` 验证对齐 |
| `tests/unit/test_rewrite_complexity.py` 行为变化 | mock JSON 删除字段不影响断言（断言测的是 `_is_complex_query` 路由） |

## 8. Execution Sequence

```
本 plan
    ↓
Commit 1: chore(schemas): remove RewriteResult.complexity field (Phase 2 A-class decision execution)
    ↓
Commit 2: chore(rewrite): remove complexity from R1 prompt contract + parser
    ↓
Commit 3: chore(pipeline): remove query_complexity variable
    ↓
Commit 4: chore(prompt): make <think> unconditional + remove build_messages dead param
    ↓
Commit 5: style: ruff format (separate commit per project convention)
    ↓
Validation: pytest + ruff + grep
    ↓
★ STOP — Issue #1-B (GAP-07 + GAP-09 modularization + tests)
```

每个 commit 独立可 revert。

## 9. 边界提醒

本次执行**等于**将 Phase 2 A 类契约决策（commit a96245e，2026-08-21）中 "DELETE `RewriteResult.complexity` 字段" 从决策落地为代码改动。

A 类决策表还剩：
- ❌ DELETE `RewriteResult.sub_dependencies` 字段（独立 plan）
- ❌ NORMALIZE SYSTEM_PROMPT "complexity" 概念（**本 plan 包含**）
- ❌ DELETE `RAGPromptBuilder.build_messages(complexity=...)` 参数（**本 plan 包含**）

完成后 A 类决策**部分执行**，剩余 sub_dependencies 字段由后续 plan 处理。
