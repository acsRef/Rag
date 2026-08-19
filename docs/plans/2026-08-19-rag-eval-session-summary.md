# RAG 评测优化会话总结（2026-08-17 ~ 2026-08-18）

## 会话目标
对三一重工年报 RAG 系统进行评测与优化，重点提升 C 类（跨文档对比）和 H 类（错误前提纠偏）得分。

---

## 评测结果演进

| 轮次 | 改动 | 总分 | 状态 |
|------|------|------|------|
| Run 1 | PyMuPDF 纯文本 | 59.2% | 基线 |
| Run 2 | + pymupdf4llm 解析 | 73.6% | ✅ +14.4pp |
| Run 3 | + prompt 工程 | **78.3%** | ⭐ 最佳 |
| Run 8-10 | Tier 2 改动（top_k、源权威性 prompt） | 70-72% | ❌ 负效果 |

---

## ✅ 有效的改动（保留）

### 1. PDF 解析升级
- **文件**：`app/ingestion/parser.py`
- **改动**：PyMuPDF → pymupdf4llm
- **效果**：表格结构保留，+14.4pp

### 2. 噪音清理
- **文件**：`app/ingestion/parser.py`
- **改动**：`_clean_pdf_noise()` 去除页码/重复标题
- **效果**：去除 9.7% 无用内容

### 3. 基础 Prompt 工程
- **文件**：`app/core/prompt.py`
- **改动**：
  - 前提验证规则（H 类改善）
  - 年份/来源标注规则（E 类改善）
  - 跨文档对比结构化要求
  - 检查清单扩展
- **效果**：78.3% 峰值

### 4. Rewrite 增强
- **文件**：`app/core/rewrite.py`
- **改动**：
  - 年份范围自动拆分子问题（"2023-2025年" → 3 个子问题）
  - 前提验证子问题生成
  - sub_dependencies 依赖标注（已实现，未使用）
  - complexity 复杂度分类（已实现，未使用）

### 5. 显式 year 字段
- **文件**：`app/models/schemas.py`, `app/core/retrieval.py`
- **改动**：
  - RetrievedChunk 新增 `year` 字段
  - retrieval 层从 Document.filename 提取注入
  - prompt 中显示 `[YYYY年]`
- **效果**：LLM 能看到 chunk 的年份归属

### 6. 文档多样性截断
- **文件**：`app/core/pipeline.py`
- **改动**：
  - `_truncate_with_doc_diversity()` 保证每个文档至少 1 个 chunk
  - `complex_rerank_top_k = 10`（多文档场景）

### 7. 数据清理
- 删除旧 KB（FAQ/数据字典/测试知识库/默认知识库）
- 清空历史对话和消息

---

## ❌ 失败的改动

| 改动 | 问题 |
|------|------|
| B 类表格专项 prompt | 强制 markdown 表格，简单题变啰嗦 |
| 多步推理 prompt | 触发范围太广，误伤简单题 |
| 合并后二次 rerank | 检索质量本身没解决，rerank 无效 |
| 年份猜测（isdigit） | 脆弱，已改为显式 year 字段 |
| 源权威性 prompt 规则 | LLM 仍只用单一文档 |
| top_k 放大到 10 | 拿到错位 section，效果负 |

---

## 🔴 当前核心问题（未解决）

### 问题 1：检索精度不足
**现象**：检索拿到 3 个文档的 chunks，但每个文档的 chunk 是**错位的 section**

**例**：Q17 问 2023-2025 营收
- 2023 chunks：拿的是第十节（财务报告），不是第二节（主要会计数据）
- 2024 chunks：正确拿到第三节（管理层讨论）
- 2025 chunks：拿的是第三节（管理层讨论），不是第二节（主要会计数据）

**根因**：向量相似度匹配不理解"财务数据应该在第二节"

**验证**：
```sql
SELECT section_path, title FROM chunks WHERE text LIKE '%89,231,023%'
-- 结果：第三节管理层讨论与分析（对），但 LLM 检索时选中了别的 section
```

### 问题 2：LLM 不主动综合多文档
**现象**：即使 prompt 里显示多个 Source（来自不同年份），LLM 仍只用其中一个文档

**例**：Q17 拿到 3 个 source，LLM 回答只引用 2024 年报

**根因**：
- Prompt 规则不够强，LLM 惯性思维难改
- 示例不够丰富，LLM 没学会"2023 查 2023 年报、2025 查 2025 年报"

### 问题 3：裁判 API 不稳定
**现象**：MiniMax API 频繁超时/限流，65 题通常只能评 43-49 题

**验证**：
```
评分状态分布:
  3分（满分）: 30
  1分（部分）: 8
  2分（基本）: 1
  0分（确实错）: 7  ← 真正答错
  -1（API 挂掉）: 19  ← 没评上
```

**结论**：实际真正答错只有 7 题，19 题是裁判 API 超时挂掉

---

## 建议的下一步

### 方案 A：修复裁判 API（优先级最高，30 分钟）
**目的**：确保评测分数准确，消除"样本偏差"

**改动**：
1. `eval_sany.py`：judge 函数改用 SiliconFlow API
2. `rejudge.py`：同上
3. `.env`：确保 SILICONFLOW_API_KEY 配置正确

**预期**：65 题全评上分，分数更准确

### 方案 B：解决检索精度（优先级高，1-2 天）
**目的**：让 C/H 类真正提升

**改动**：
1. `app/ingestion/structurer.py`：解析 section 时提取类型（财报/管理层讨论/公司治理/释义）
2. `app/ingestion/indexer.py`：chunk 存 section_type 字段
3. `app/store/pgvector_store.py`：搜索时支持 section_type 过滤/boost
4. `app/core/retrieval.py`：根据 query 类型选择 section 偏好
   - 财务数据 → 优先"主要会计数据"section
   - 业务定位 → 优先"管理层讨论"section
   - 公司基本信息 → 优先"公司治理"section

**预期**：
- C 类 1.50 → 2.5+
- E 类 1.83 → 2.5+
- H 类 0.00 → 2.0+

### 方案 C：增强 LLM 多文档综合（优先级中，半天）
**目的**：让 LLM 学会综合多文档

**改动**：
1. `app/core/prompt.py`：加"多文档强制综合"规则
2. 加 3-5 个具体示例，演示"2023 查 2023 年报、2024 查 2024 年报"
3. 在 KB_ANSWER_TEMPLATE 里加强制提示

**预期**：
- C 类 1.50 → 2.0+
- 但不解决检索错位问题

---

## 关键文件清单

### 评测相关
- `eval/eval_sany.py`：全量评测脚本
- `eval/test_targeted.py`：针对性测试（10 题 bad case）
- `eval/test_serial.py`：串行测试（不并发）
- `eval/rejudge.py`：重评未评分的题
- `eval/sany_annual_reports/eval_results.json`：评测结果
- `eval/sany_annual_reports/eval_report.md`：评测报告

### RAG 核心
- `app/core/pipeline.py`：主流程
- `app/core/prompt.py`：prompt 工程
- `app/core/rewrite.py`：查询改写
- `app/core/retrieval.py`：检索引擎
- `app/core/intent.py`：意图识别

### 文档处理
- `app/ingestion/parser.py`：PDF 解析（pymupdf4llm）
- `app/ingestion/structurer.py`：Markdown 结构化
- `app/ingestion/chunker.py`：分块
- `app/ingestion/indexer.py`：索引（年份标签注入）

### 数据存储
- `app/models/schemas.py`：Pydantic 模型（RetrievedChunk 含 year 字段）
- `app/store/pgvector_store.py`：向量检索
- `app/store/db.py`：SQLAlchemy 模型（不要改）

---

## 快速复现指南

### 启动服务
```bash
docker compose up -d
D:/miniConda/envs/rag/python.exe -m app.main
```

### 跑评测
```bash
# 全量评测（65 题，~10 分钟）
D:/miniConda/envs/rag/python.exe eval_sany.py --no-resume

# 针对性测试（10 题 bad case）
D:/miniConda/envs/rag/python.exe test_targeted.py

# 重评未评分的题
D:/miniConda/envs/rag/python.exe rejudge.py
```

---

## 总结

**最佳分数**：78.3%（Run 3）

**主要问题**：
1. 检索精度（chunk 拿到错 section）
2. 裁判 API 不稳定（MiniMax 超时）

**建议优先级**：
1. 先修复裁判 API（30 分钟）
2. 再解决检索精度（1-2 天）
3. 最后增强 LLM 多文档综合（半天）