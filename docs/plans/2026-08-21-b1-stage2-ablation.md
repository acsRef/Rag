# B1 Stage 2 — Evidence Gate Value Ablation (2026-08-21)

> **目的**：回答唯一问题——`Evidence Gate: KEEP / REFACTOR / DELETE`。
> 输入：B1 Stage 1 验证的技术路径（已 PASS）。输出：三轴联合判定 + decision.json。
> 关联：决策 doc [docs/plans/2026-08-22-phase2-b-recon.md](2026-08-22-phase2-b-recon.md) §5.2-5.5；Stage 1 plan [docs/plans/2026-08-21-b1-stage1-smoke-test.md](2026-08-21-b1-stage1-smoke-test.md)。

## 1. Scope

65 题全集 × 4 配置对照实验，唯一变量为 Evidence Gate。其他一切锁定。结果落盘至 `eval/ablation/<timestamp>/`，包含 per-question JSON + aggregate + pairwise + decision。

## 2. Decisions Locked (实验前固化)

### 2.1 4 个配置（不可调整）

| Config | `evidence_gate_enabled` | `evidence_min_coverage` | 说明 |
| --- | --- | --- | --- |
| **off** | false | — | baseline（当前默认） |
| **t05** | true | 0.5 | 宽松 |
| **t07** | true | 0.7 | **当前默认（不是"最优阈值"——只是默认值）** |
| **t09** | true | 0.9 | 严格 |

### 2.2 控制变量（必须锁定）

| 变量 | 锁定值 |
| --- | --- |
| 测试集 | 65 题全集（`eval/eval_sany.py` 数据源） |
| **chat 模型** | **`Qwen/Qwen3-8B`**（**2026-08-21 成本决策：DeepSeek-V3 费用过高，切换为 Qwen3-8B；两模型在中文 RAG 任务上效果相近**） |
| prompt | `SYSTEM_PROMPT` 冻结当前快照（不修改；与 Stage 1 一致） |
| retrieval 策略 | cross_doc / section_boost / section_supplement / year_supplement / query_decomposition 全 False（baseline ablation 已确认无 recall 贡献） |
| question_channel_enabled | True（默认值） |
| temperature | `settings.chat_temperature`（默认 0.7） |
| seed | 如 API 支持则固定；不支持则视为非确定性，单次结果作为近似 |

**唯一变量**：`evidence_gate_enabled` + `evidence_min_coverage`。

**baseline 参照系**：Stage 2 的 "off" 配置用的是 `Qwen/Qwen3-8B`，**所有 accuracy / false_refusal / latency 改善都相对 Qwen3-8B off 计算**，不与 DeepSeek-V3 历史 baseline（73.3%）直接对照。

> 注：本次不修改 `app/config.py` 默认 `chat_model`。Stage 2 用 env override（`CHAT_MODEL=Qwen/Qwen3-8B`）跑。是否同步改默认值属于项目级配置变更，独立决策。

### 2.3 三轴接受门槛（实验前固化，不事后调整）

| 轴 | 通过条件 | 来源 |
| --- | --- | --- |
| **accuracy improvement** | 相对 off 至少 **+2pp 绝对值**（baseline 73.3% → 至少 75.3%） | [docs/plans/2026-08-22-phase2-b-recon.md](2026-08-22-phase2-b-recon.md) §5.5 |
| **false refusal rate** | 应回答的题中被拒比例 **≤ 3%** | 同上 |
| **latency overhead** | p95 相对 off **≤ +20%** | 同上 |

**判定**：三轴**全过**才进入 ENABLE 候选；任一不通过 → 落入 §5 outcome 矩阵相应分支。

### 2.4 题级结果 schema（每个 (question × config) 落盘一条 JSON）

```json
{
  "question_id": "Q50",
  "category": "C",
  "config": {"gate_enabled": true, "threshold": 0.7},
  "coverage": 0.85,
  "temporal_consistent": true,
  "threshold": 0.7,
  "gate_refused": false,
  "refusal_reason": null,
  "generation_answer": "...",
  "gold_answer": "...",
  "is_correct": true,
  "should_answer": true,
  "latency_ms": 5200,
  "sse_events_count": 12,
  "sse_has_evidence_refused": false,
  "sse_has_degraded": false,
  "timestamp": "ISO8601"
}
```

**`should_answer` 判定**（基于题目类别）：
- `should_answer = true`：A / B / C / D / E / G / J 类（事实/表格/对比/计算/时序/实体消歧/细节）
- `should_answer = false`：I 类（拒答边界）—— 这些题应当被拒答
- H 类（错误前提）：`should_answer = true`（系统应先纠偏再答）

### 2.5 Pairwise 题级分析（off → t 状态转移分类）

每个阈值生成 9 状态转移分类（off_state × t_state ∈ {correct, wrong, refused}）：

| off → t | label | 解读 |
| --- | --- | --- |
| correct → correct | stable_correct | 无变化 |
| correct → wrong | regression | 让对的变错 |
| correct → refused | **false_refusal** ❌ | 误拒（最严重代价） |
| wrong → correct | **recovery** ✅ | 救回错误（最大收益） |
| wrong → wrong | stable_wrong | 无变化 |
| wrong → refused | **replaced_with_refusal** ⚠️ | 把错答换拒答（语义漂移） |
| refused → correct | **unnecessary_refusal_recovery** ✅ | 之前拒答的现在答对 |
| refused → wrong | regression_from_refusal | 之前拒答的现在答错 |
| refused → refused | stable_refused | 无变化 |

**关键判定问题**：

> "Gate 是在阻止错误，还是只是把错误改成拒答？"

用 recovery / replaced_with_refusal ratio 量化：

```
recovery_ratio = recovery_count / (recovery_count + replaced_with_refusal_count)
```

- ratio 接近 1 → Gate 真的在阻止错误（值得保留）
- ratio 接近 0 → Gate 只是在把错答换拒答（语义漂移，无实质价值）

## 3. Implementation

### 3.1 新脚本

`eval/ablation_evidence_gate.py`：核心 runner
- 遍历 4 configs × 65 题 = 260 个 (q, c) 单元
- 每个单元调用 `RAGPipeline().execute()`，捕获 SSE events + 计时
- 解析 generation answer；与 gold answer 对比判 `is_correct`
- 根据 category 标 `should_answer`
- 写 per-question JSON
- 跑完后聚合 aggregate + pairwise + 三轴判定 → decision.json

### 3.2 输出目录结构

```
eval/ablation/<timestamp>/
├── per_question/
│   ├── off_Q01.json ... off_Q65.json
│   ├── t05_Q01.json ... t05_Q65.json
│   ├── t07_*.json
│   └── t09_*.json
├── aggregate/
│   ├── off_summary.json
│   ├── t05_summary.json
│   ├── t07_summary.json
│   └── t09_summary.json
├── pairwise/
│   ├── off_vs_t05.json
│   ├── off_vs_t07.json
│   └── off_vs_t09.json
└── decision.json
```

### 3.3 aggregate summary schema（每个 config 一份）

```json
{
  "config": {"gate_enabled": false, "threshold": null},
  "n_questions": 65,
  "n_should_answer": 55,
  "n_should_refuse": 10,
  "metrics": {
    "accuracy": 0.733,
    "refusal_rate": 0.0,
    "false_refusal_rate": 0.0,
    "refusal_precision": null,
    "refusal_recall": null,
    "latency_p50_ms": 4200,
    "latency_p95_ms": 7800
  },
  "three_axis": {
    "vs_off_accuracy_improvement_pp": 0.0,
    "vs_off_false_refusal_rate": 0.0,
    "vs_off_p95_latency_overhead_pct": 0.0,
    "passes_accuracy": false,
    "passes_false_refusal": true,
    "passes_latency": true,
    "overall_pass": null
  }
}
```

## 4. Execution Protocol

1. **前置**：`grep -rn "EVIDENCE_GATE\|evidence_gate" app/` 确认无遗漏的 env override
2. **baseline first**：先跑 off 配置确认 pipeline 全 65 题可完成（避免 65×3 = 195 题跑一半才发现底层问题）
3. **顺序跑剩余 3 配置**：t05 / t07 / t09（顺序不重要）
4. **实时进度**：`progress.jsonl` 每完成一题追加一行
5. **总耗时估计**：4 × 65 = 260 题；按 baseline eval 经验约 4-8 秒/题 ≈ 17-35 分钟（单配置）；全套约 70-140 分钟
6. **完成后聚合**：aggregate → pairwise → 三轴判定 → decision.json
7. **decision.json 必须包含**：`KEEP` / `REFACTOR + ENABLE` / `DELETE` 单字结论 + 三轴数据 + 推荐阈值

## 5. Outcome → Decision Matrix

| outcome | decision |
| --- | --- |
| 三轴全过，单一阈值（0.5/0.7/0.9 任一）| **REFACTOR + ENABLE**（取该阈值作为新默认） |
| 仅 0.5 三轴过 | **KEEP + 调参文档化**（保持 off + 0.5 备选 env） |
| 三轴不通过但 pairwise 上 `recovery > replaced_with_refusal` | **KEEP + 自适应阈值探索**（列为未来 work） |
| 全部组合三轴不通过 | **DELETE**（移除 evidence.py + gate 开关 + query_type 整套） |

> **DELETE 是合理结局**——不要默认"现成代码值得启用"。与 complexity / sub_dependencies 处理逻辑一致。

## 6. Out of Scope (本 plan 不做)

- 调整 `evidence_min_coverage` 默认值（执行阶段不动）
- 修改 `SYSTEM_PROMPT` / prompt 模板
- 修改 retrieval 或任何策略开关
- B2（query_type DELETE，独立 plan）
- Phase 2 C / D

## 7. Explicit Non-Decisions

执行完成后**唯一输出**：`decision.json` 给出 `KEEP` / `REFACTOR + ENABLE` / `DELETE` 单字结论。
**不预设默认结论**；不"为 ENABLE 而 ENABLE"；不写"实验验证 Gate 价值"类叙事，只写事实与判定。

## 8. Validation Strategy

执行完成后：

1. **基线守住**：`pytest -q` 维持 `459 passed / 6 failed / 13 skipped`（6 failed 集合不变）；`ruff check` 退出 0
2. **输出完整性**：per_question JSON 共 4 × 65 = 260 个文件全部存在
3. **聚合正确性**：手算 5 题验证 aggregate（accuracy / refusal / latency 三个指标各 1-2 题）
4. **三轴计算正确性**：手算 pairwise 转移分类，验证 ratio 计算
5. **decision.json 一致性**：与 §5 outcome 矩阵映射一致；附原始数据指针

## 9. Reproducibility

每个 (q, c) JSON 必含：
- `timestamp`（ISO8601）
- 模型 + temperature + seed（如 API 支持）
- 如 API 不支持 seed：在 `decision.json` 顶部注明 `nondeterministic` + 单次结果视为近似

**不在 plan 内解决**：多次重复跑取置信区间（属于未来工作）。

---

## 附录 A：与 Phase 2 B 侦察决策的对应关系

| 侦察决策 §5.1-5.5 | 本 plan 对应 |
| --- | --- |
| §5.1 Stage 1（场景化 smoke test） | 已完成（9/9 PASS），是本 plan 的前置 |
| §5.2 Stage 2（value ablation） | 本 plan 主体 |
| §5.3 指标矩阵（核心 + 辅助） | §2.4 schema + §2.5 pairwise + §3.3 aggregate |
| §5.4 outcome 矩阵 | §5 outcome → decision matrix |
| §5.5 最小收益门槛 | §2.3 三轴接受门槛（具体数字已锁定） |

## 附录 B：与 B2 的关系

B1 Stage 2 与 B2 完全独立（已证明：见 recon §4）。
本 plan 启动不阻塞 B2；B2 启动不依赖本 plan 结果。
两个 work item 各自独立 commit、独立回滚。
