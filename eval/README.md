# RAG 评测系统

本目录包含 RAG 系统的评测脚本和数据集。

## 目录结构

```
eval/
├── eval_sany.py           # 全量评测脚本（65 题）
├── eval_detail.py         # 详细评测脚本（按类别测试）
├── eval_single.py         # 逐个测试脚本（带重试）
├── rejudge.py             # 重评未评分的题目
└── sany_annual_reports/   # 三一重工年报评测数据集
    ├── rag_testset.json   # 测试题集（65 题）
    ├── *.pdf              # 三份年报（2023-2025）
    └── eval_*.md/json     # 评测结果和报告
```

## 评测脚本

### 1. 全量评测 (`eval_single.py`)

逐个测试所有 65 题，带重试机制，避免 API 限流导致失败。

```bash
D:/miniConda/envs/rag/python.exe eval/eval_single.py
```

**特点**：
- 每题独立评测，失败自动重试
- 每 5 秒间隔，避免 API 限流
- 输出详细的评测报告到 `sany_annual_reports/eval_detail_report.md`

### 2. 按类别评测 (`eval_detail.py`)

只测试特定类别的题目，适合快速验证某类问题的改进效果。

```bash
# 只测 A 类（单文档事实）
D:/miniConda/envs/rag/python.exe eval/eval_detail.py --category A --limit 10

# 只测 C 类（跨文档对比）
D:/miniConda/envs/rag/python.exe eval/eval_detail.py --category C
```

**支持的类别**：
- A：单文档事实抽取
- B：表格理解与单位换算
- C：跨文档对比
- D：计算与多跳推理
- E：时序与追溯调整
- F：口径与概念辨析
- G：实体消歧
- H：错误前提纠偏
- I：拒答与知识边界
- J：细节与脚注

### 3. 重评未评分题目 (`rejudge.py`)

当评测因 API 限流等原因导致部分题目未评分时，用此脚本重评。

```bash
D:/miniConda/envs/rag/python.exe eval/rejudge.py
```

**原理**：
- 读取 `eval_results.json`
- 找出 `judge_score` 为 `-1` 或 `None` 的题目
- 重新调用裁判 API 评分
- 更新结果文件

## 评测数据集

### 三一重工年报 (`sany_annual_reports/`)

包含三份年报（2023-2025）和 65 道测试题，覆盖 10 个类别。

**文件说明**：
- `rag_testset.json`：测试题集（JSON 格式）
- `RAG测试题集_三一重工年报.md`：测试题集（Markdown 格式，便于阅读）
- `RAG测试题集_三一重工年报.xlsx`：测试题集（Excel 格式，便于筛选）
- `三一重工_2023/2024/2025年年度报告.pdf`：三份年报原文

## 评测流程

1. **准备环境**：
   ```bash
   # 确保后端服务运行
   D:/miniConda/envs/rag/python.exe -m app.main
   
   # 确保知识库已上传年报
   # 通过前端上传 3 份 PDF 到"三一重工年报"知识库
   ```

2. **运行评测**：
   ```bash
   D:/miniConda/envs/rag/python.exe eval/eval_single.py
   ```

3. **查看结果**：
   - 打开 `eval/sany_annual_reports/eval_detail_report.md`
   - 查看每题的得分、RAG 回答、裁判理由

4. **分析问题**：
   - 按类别统计得分率
   - 找出失分最多的类别
   - 查看具体错题的诊断信息

## 评测指标

每题 0-3 分：
- **3 分**：完全正确，关键数字/事实正确
- **2 分**：基本正确，有小遗漏或偏差
- **1 分**：部分正确，有明显错误
- **0 分**：完全错误或拒答

**总分计算**：`实际得分 / (题数 × 3) × 100%`

## 裁判模型

使用 SiliconFlow DeepSeek-V3 作为裁判模型，相比 MiniMax 更稳定，不会频繁超时。

**裁判 Prompt**：见各评测脚本中的 `JUDGE_PROMPT` 变量。

## 常见问题

### Q: 评测中途失败怎么办？

A: 使用 `eval_single.py`，它会自动跳过已评测的题目，从中断处继续。

### Q: 部分题目评分为 -1？

A: 表示裁判 API 调用失败。运行 `rejudge.py` 重评这些题目。

### Q: 如何只测试某几道题？

A: 使用 `eval_detail.py --limit N` 只测前 N 题，或修改 `rag_testset.json` 只保留要测试的题目。

### Q: 评测结果保存在哪里？

A: 
- `eval_results.json`：完整的评测结果（JSON 格式）
- `eval_detail_report.md`：详细的评测报告（Markdown 格式）
- `eval_report.md`：简要的评测报告（Markdown 格式）

## 相关文件

- [项目总结](../docs/plans/2026-08-19-rag-optimization-summary.md)
- [下一步计划](../docs/plans/2026-08-19-rag-next-phase-plan.md)
- [TODO 清单](../docs/TODO.md)
