# RAG 评测优化计划 (Tier 2)

**目标**: 总分 78.3% → 85%+，重点提升 C/H/A 类

**当前状态**: Phase 1-3 完成（pymupdf4llm + prompt 工程 + rewrite 增强）= 78.3%

## Tier 2 优化（聚焦）

### 1. 年份标签重索引（重头戏，影响 C+E 类）
**代码已写** (`app/ingestion/indexer.py:_extract_year_from_filename`)，缺重跑

**流程**:
1. 删除现有 3 份 PDF 文档
2. 重新上传（~20 分钟解析）
3. 每个 chunk 的 section_path 第一项变为 "2023年" / "2024年" / "2025年"

**预期效果**:
- C 类（跨文档对比）1.60 → 2.5+：LLM 能精确知道每个 chunk 是哪年的
- E 类（时序追溯）保持 3.00
- H 类（前提纠偏）1.00 → 1.5+：LLM 能看到完整三年数据进行纠偏

### 2. B 类表格专项 prompt（影响 B 类）
**文件**: `app/core/prompt.py`

**改动**: 检测到表格类问题时，加专门指令：
- "如涉及表格数据，优先用 markdown 表格展示"
- "数字必须原文保留，不要四舍五入"
- "单位换算必须在表格中标注"

**预期效果**: B 类 1.80 → 2.5+

### 3. 多步推理子问题依赖标注 + 链式检索（影响 Q31 等多步推理题）
**文件**: `app/core/rewrite.py`, `app/core/pipeline.py`

**改动**:
- rewrite 输出加 `dependencies` 字段（如 `dependencies: [1, 2]` 表示子问题3依赖子问题1和2的结果）
- pipeline 按依赖顺序检索，前面子问题的结果作为后面检索的上下文

**预期效果**: D 类（多跳推理）保持/提升，多步推理题正确率提高

## 实施顺序

**Step 1（~5 分钟）**：
- 改 prompt.py：B 类表格专项 + 多步推理 CoT

**Step 2（~15 分钟）**：
- 改 rewrite.py：子问题依赖标注
- 改 pipeline.py：链式检索

**Step 3（~20 分钟）**：
- 重索引：删除旧文档 + 重传 3 份 PDF

**Step 4（~30 分钟）**：
- 跑评测

**合计**: ~70 分钟

## 关键文件
- `app/core/prompt.py` — B 类表格 + 多步推理
- `app/core/rewrite.py` — 子问题依赖
- `app/core/pipeline.py` — 链式检索
- `app/ingestion/indexer.py` — 年份标签（已写）

## 验证
- 跑 `D:/miniConda/envs/rag/python.exe eval_sany.py --no-resume`
- 重点对比 C/H/B/D 类分数变化
- 检查无回归（E/F/J 不降）
