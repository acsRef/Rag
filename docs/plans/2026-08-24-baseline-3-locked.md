# Baseline-3 — 表格感知摄入后锁定文档

> 日期：2026-08-24 · 状态：**LOCKED**
> 唯一变量 vs Baseline-2：表格双表示（chunks.text 原始 markdown；embedding_text/search_text 走 build_retrieval_text 归一化）+ 元数据列 chunk_type/table_headers/table_meta 落库

## ⚠️ Lock 路径说明（方法论记录）

15-Q gate 两次运行被 ±50pp 单题翻转击穿（run1 C 塌 0%、run2 C 回 50% 但 B 塌 0%，方向相反），证明 2 题类别在该 gate 上无统计力（plan §9 预见："15 题是 regression gate 不是最终统计"）。经用户决策跳过第三次 15-Q，直接以 65 题正式 benchmark 作为 Baseline-3 锁定依据。

Retrieval 诊断（Q17/Q18/Q12 的 diag JSON）显示 top-hit chunk 与 Baseline-2 一致——检索层无回归证据。

## 配置快照

| 项 | Baseline-2 | Baseline-3 |
| --- | --- | --- |
| embedding | Qwen3-Embedding-8B@1024 | 同左 |
| current_embedding_version | 2 | **3** |
| EMBEDDING_INPUT_VERSION | 1 | **2** |
| question channel | OFF | OFF |
| retrieval_text 通道 | 裸 text | build_retrieval_text() 归一化 |
| 元数据列 | 无 | chunk_type/table_headers/table_meta |
| 语料 | 1381 chunks (v2) | 1340 chunks (v3) |

## 完整性（integrity report）

Task 16 当时的原始输出未归档；因语料在 Task 16b 后未再变动，2026-08-24 用同一只读工具对当前库复跑（`tools/index_integrity_report.py --expect-documents 3 --expect-questions zero`），输出如下：

```
[PASS] documents == 3: 3
[PASS] chunks > 0: 1340
[PASS] chunks.embedding NULL == 0: 0
[PASS] chunk 向量维度全部 == 1024（穷举）: 0 行不符
[PASS] embedding_text 空 ratio == 0: 0/1340
[INFO] embedding_text 平均长度: 598
[PASS] search_text 空 ratio == 0: 0/1340
[INFO] table chunks: 64 (4.8%)
[PASS] chunk_questions == 0（硬断言）: 0

INTEGRITY PASSED
```

- table chunks: 64 (4.8%)
- 注：41 chunks 因 metadata-enrichment LLM timeout 被丢（对比 1381）——独立 fragility，非本变更引入，登记后续加固任务

## 65 题正式结果（evaluator v1）

overall: **acc(≥2) 73.8%** · mean **2.23**/3 · 覆盖 **65/65**（0 error、0 未评分，无需重试）
分布：3 分 36 题 · 2 分 12 题 · ≤1 分 17 题

### 分类分面（A-J）

| 类别 | n | acc(≥2) | mean |
| --- | --- | --- | --- |
| A-单文档事实抽取 | 10 | **80.0%** | 2.50 |
| B-表格理解与单位换算 | 6 | **66.7%** | 2.00 |
| C-跨文档对比 | 8 | **50.0%** | 1.88 |
| D-计算与多跳推理 | 7 | 71.4% | 2.29 |
| E-时序与追溯调整 | 6 | 66.7% | 1.67 |
| F-口径与概念辨析 | 6 | 66.7% | 1.83 |
| G-实体消歧 | 6 | 100.0% | 3.00 |
| H-错误前提纠偏 | 5 | 80.0% | 2.20 |
| I-拒答与知识边界 | 5 | 100.0% | 2.80 |
| J-细节与脚注 | 6 | 66.7% | 2.17 |

### 子集分面

| 子集 | n | acc(≥2) | mean |
| --- | --- | --- | --- |
| answerable（非 H/I） | 55 | 70.9% | 2.18 |
| refusal（H+I） | 10 | 90.0% | 2.50 |
| numeric（B+D） | 13 | 69.2% | 2.15 |
| table（B） | 6 | 66.7% | 2.00 |
| cross-doc（C） | 8 | 50.0% | 1.88 |

## 判定准则逐条核对

| # | 准则 | 阈值 | 实测 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | 多题类别（≥4 题）无 0% 塌方 | 全部 >0% | 最低为 C 50.0%（n=8），无一类别为 0% | **PASS** |
| 2 | overall acc(≥2) ≥ 55% | ≥55% | **73.8%** | **PASS** |
| 3 | A 类 ≥ Baseline-2 15-Q 水平（≥66.7%） | ≥66.7% | **80.0%**（n=10，两轮 15-Q 100% 后继续成立） | **PASS** |
| 4 | B 类 ≥ 25% | ≥25% | **66.7%**（n=6） | **PASS** |
| 5 | C 类 ≥ 25% | ≥25% | **50.0%**（n=8） | **PASS** |
| 6 | 未答/error ≤ 3 | ≤3 | **0**（65/65 一次评完） | **PASS** |

六条全过 → **LOCK**。

## 回归观察类逐题明细（B/C）

### B-表格理解与单位换算（6 题，acc 66.7%）

| 题号 | 得分 | judge 理由（摘要） |
| --- | --- | --- |
| Q11 | 1 | 单位错误，应为千元而非元 |
| Q12 | 2 | 单位换算错误，应为约1,733.0亿元而非173.3亿元 |
| Q13 | 2 | 研发投入合计单位错误（应为千元而非亿元），占比正确 |
| Q14 | 1 | 资本化金额单位错误（应为千元而非亿元） |
| Q15 | 3 | 完全正确，关键数字和单位准确 |
| Q16 | 3 | 完全正确，关键数字与年份均准确匹配 |

失败模式单一：全部是"千元/亿元单位换算"类数字滑移，无检索缺失迹象（与 15-Q 诊断一致）。B 在 65-Q 下测得 66.7%，是历次测量中最高（B2 15-Q 50%、B3-run2 0%），证实 run2 的 B=0% 是单题噪声而非塌方。

### C-跨文档对比（8 题，acc 50.0%）

| 题号 | 得分 | judge 理由（摘要） |
| --- | --- | --- |
| Q17 | 3 | 核心信息完整，数字/事实/单位正确 |
| Q18 | 1 | 相关内容但缺 2024/2025 完整数据 |
| Q19 | 1 | 仅含 2023 年数据，未找到 2024/2025 数据 |
| Q20 | 2 | 缺 2025 年数据，部分正确 |
| Q21 | 3 | 完全匹配参考答案 |
| Q22 | 1 | 缺 2024 及 2025 年数据 |
| Q23 | 3 | 核心信息完整 |
| Q24 | 1 | 2024 年数据未被提及 |

失败模式单一：跨三年对比题中 2024/2025 年数据召回不全。这是 C 类的系统性弱点（检索层多文档聚合），方向与 15-Q run2 的 C=50% 一致；run1 的 C=0% 为噪声。留待后续改进，不阻塞本次锁定。

## 与历史参考的关系（不可直比声明）

历史 78.3%（65-Q）系旧 evaluator + 旧语料 + 4096 配置下的数字，仅作定性 sanity band，不构成 delta。本次 73.8% 落在该 band 内，无系统性破坏迹象。

vs Baseline-2 的对照基于：15-Q 两轮配对数据（A 类唯一稳定信号，两轮均 100%）+ retrieval 诊断一致性（top-hit 相同）+ 本轮 65-Q 分面健康度。**不存在 Baseline-2 的 65-Q 对照臂**（旧语料未重摄入，刻意控制成本）。

## 过程记录

1. Task 16 run1: C 0%（Gate FAIL）→ 诊断检索干净
2. Task 16b run2: C 回 50% / B 塌 0%（Branch C 大面积 noise）
3. 用户决策：升级 65-Q
4. 65-Q run: 2026-08-24 17:48–18:40（RAG 阶段约 42 分钟 + judge 阶段约 10 分钟）· 覆盖 65/65 · overall acc(≥2) 73.8%
   - 运维注记：后台 shell 缺 SILICONFLOW_API_KEY 导致首轮 judge 跳过（RAG 答案已全部落盘）；以 resume 模式从根 .env 注入 key 仅补跑 judge 阶段，答案未重生成

## 谱系位置

```
Baseline-2 (66.7% @15Q) ← commit 7385baa
    ↓ Step 2A: 表格感知摄入（commits d0e1628a..53742c5）
Baseline-3 (73.8% @65Q) ← 本档
    ↓ Step 2C: question channel 激活
Baseline-4 (待测)
```

## 遗留项

- metadata-enrichment LLM timeout 加固（41 chunks 丢失根因）
- C 类跨三年数据召回不全（Q18/Q19/Q20/Q22/Q24 同模式）——检索层多文档聚合改进
- B 类单位换算数字滑移（Q11/Q14 满失分）——prompt/后处理候选改进
- Judge calibration P0（memory 已登记）：本轮 B/C 失败判定中 judge 对"基本正确但单位错"给 2 分的口径值得校准复核
- Baseline-4 之后 65-Q 正式收官跑归档到 baselines/final-65q/
