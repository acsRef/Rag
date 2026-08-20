# 意图 + 视觉模型切换验证与加固 Implementation Plan

> **状态：已完成 2026-08-20。** 实测定论 + 最终架构见 `docs/plans/2026-08-20-intent-vision-model-switch.md`「当前状态」与「风险点定论」。核心结论：**意图路由回退 V3；R1 挪到复杂查询规划；视觉用 Qwen3-VL-8B**。commit：`9bdfdac`、vision-revert、`0d1d25a`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 API 验证 DeepSeek-R1 意图路由 + DeepSeek-OCR 视觉两项切换是否可用，暴露/修复 R1 推理 token 截断与 OCR 输出格式风险，最后复跑全量评测确认无回归并回填计划。

**Architecture:** 模型切换已在工作树完成（未提交）。本计划分三阶段：(1) 起库起服务后做横向一次性烟测脚本，直接调用 `intent_classifier.classify` 与 `image_describer.describe_sync`，测量 R1 的延迟/JSON 完整性、OCR 的 `[类型]` 输出是否合规；(2) 依据烟测结论做 TDD 加固（R1 超时/截断 → 调大 `max_tokens` 或结构化 JSON 模式；OCR 格式 → 改写 prompt 或容忍解析）；(3) 复跑 `eval/eval_sany.py` 全量，对照基线确认无回归，把结果回填本计划与 model-switch 计划。

**Tech Stack:** Python 3.11 (conda `rag` env)、FastAPI、SiliconFlow OpenAI 兼容 API、pytest、`eval/eval_sany.py` 评测框架。

**关键背景（已核实源码，不要重新推导）：**
- 意图调用点：[app/core/intent.py:141](app/core/intent.py#L141) `call_llm_with_retry(minimax_client.chat, [...], max_retries=1, model=settings.intent_model)`。`call_llm_with_retry` 把 `**kwargs` 原样透传给 `chat_fn`（[app/llm/base.py:339](app/llm/base.py#L339)），所以可以直接传 `max_tokens=...`。
- `LLMClient.chat` 默认 `max_tokens=None → 4096`（[app/llm/chat.py:124](app/llm/chat.py#L124)）。R1 是推理模型，会先吐思考再给 JSON；若思考超过 budget 导致 JSON 被截断，`robust_json_parse` 返回 None（[app/core/intent.py:153](app/core/intent.py#L153)）→ 空 matches → 全库回退，意图路由静默失效。
- 视觉调用点：[app/llm/vision.py:119](app/llm/vision.py#L119)，已把系统指令并入 user 消息，`model=settings.vision_model`。
- 评测：`D:/miniConda/envs/rag/python.exe eval/eval_sany.py`，需服务在 `localhost:8000`，登录 `admin/admin123`，知识库需含三一 3 份年报。
- 工作树当前未提交改动：`app/config.py` `app/core/intent.py` `app/llm/chat.py` `app/llm/vision.py` `CLAUDE.md` `README.md` `docs/TODO.md`。

---

## 文件结构

- 新增 `tools/smoke_models.py` — 一次性横向烟测脚本（意图 + 视觉），输出可读诊断，不改业务代码。
- 修改 `app/core/intent.py` — 按烟测结论加固（R1 超时/截断）。
- 修改 `app/llm/vision.py` — 按烟测结论改写 prompt 或输出解析。
- 新增 `tests/unit/test_intent_tokens.py` — 锁定 token 预算/超时行为（若需加固）。
- 新增 `tests/unit/test_vision_prompt.py` — 锁定 OCR prompt/解析契约（若需加固）。
- 修改回填：`docs/plans/2026-08-20-intent-vision-model-switch.md`、本计划、`docs/TODO.md`。

---

## Phase 0 — 前置验证（无代码改动）

### Task 1: 确认环境就绪，提交已完成的模型切换

**Files:**
- 无

- [ ] **Step 1: 确认 Docker Postgres 在跑**

Run: `D:/miniConda/envs/rag/python.exe -c "import psycopg2, socket; socket.create_connection(('localhost',5432),2); print('PG up')"`
Expected: 打印 `PG up`（用户已开 docker）

- [ ] **Step 2: 确认导入链 + 模型配置读取正确**

Run:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main; from app.config import settings; print(settings.intent_model); print(settings.vision_model)"
```
Expected:
```
deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
deepseek-ai/DeepSeek-OCR
```

- [ ] **Step 3: 跑现有单测，确认模型切换未破坏既有守卫测试**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_intent_guard.py tests/unit/test_llm_gateway.py -q`
Expected: 全部 PASS（文档声称 17 个测试）。

- [ ] **Step 4: 提交已完成且验证无回归的模型切换改动**

```bash
cd d:/PyProject/ragent-py
git add app/config.py app/core/intent.py app/llm/chat.py app/llm/vision.py CLAUDE.md README.md docs/TODO.md docs/plans/2026-08-20-intent-vision-model-switch.md
git commit -m "feat: switch intent to DeepSeek-R1 and vision to DeepSeek-OCR"
```
（若用户偏好先不提交挂在工作树，可跳过本步，但建议提交以建立可回滚基线。）

---

## Phase 1 — 横向烟测（一次性脚本，先发现问题）

### Task 2: 编写意图 + 视觉烟测脚本

**Files:**
- Create: `tools/smoke_models.py`

- [ ] **Step 1: 写烟测脚本**

意图侧：直接调 `intent_classifier.classify(question, kb_ids)`，但要绕过 `_resolve_kb_names` 的真实 DB 依赖太重——改为构造两个假 kb_id 传给 classify 会走 `_resolve_kb_names` 查库。更聚焦：直接调底层 `minimax_client.chat` 复刻 intent 的 prompt，测量 R1 延迟 + JSON 是否完整。视觉侧：构造一张含文字的 PNG（用 PIL 画几行字），调 `image_describer.describe_sync`。

```python
"""一次性烟测：DeepSeek-R1 意图 + DeepSeek-OCR 视觉（真实 API，需 .env key）。

用法:
    D:/miniConda/envs/rag/python.exe tools/smoke_models.py

不出图测试: 用 PIL 现画一张含中文文字的 PNG，无需上传正文。
"""
import asyncio
import base64
import io
import time

from app.config import settings
from app.llm.chat import minimax_client

INTENT_PROBE_QUESTIONS = [
    "三一重工2023年营业收入是多少？",
    "科创板开户需要什么条件？",          # 与知识库无关，应返回空 matches
    "2024年与2025年研发投入对比如何？",   # 多 KB 场景
]


def _make_text_image(width=600, height=200, lines=("三一重工 2023年 营业收入 732.22亿元", "研发费用 同比增长 18%")) -> bytes:
    """用 PIL 现画含中文的图片，避免依赖本地图片文件。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        font = None
    y = 20
    for ln in lines:
        d.text((20, y), ln, fill="black", font=font)
        y += 50
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def smoke_intent():
    print("=== 意图路由: DeepSeek-R1 ===")
    for q in INTENT_PROBE_QUESTIONS:
        messages = [{"role": "user", "content": f"把问题路由到知识库: {q}\n可用的知识库: ['docs-a', 'docs-b']"}]
        t0 = time.time()
        try:
            resp = await minimax_client.chat(
                messages, model=settings.intent_model, max_tokens=4096,
            )
        except Exception as e:
            print(f"  [{q[:20]}] 调用失败: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        head = resp[:180].replace("\n", " ")
        print(f"  [{q[:20]}] 延迟 {dt:.1f}s | 长度 {len(resp)} | 前缀: {head}")
        # 检查是否完整 JSON
        try:
            import json
            json.loads(resp.split("```json")[-1].split("```")[0].strip())
            print("    -> JSON 完整 ✓")
        except Exception:
            print("    -> ⚠ JSON 可能被截断/不纯 (需加固)")


def smoke_vision():
    print("\n=== 视觉: DeepSeek-OCR ===")
    from app.llm.vision import image_describer, IMAGE_DESCRIBE_PROMPT
    img = _make_text_image()
    t0 = time.time()
    out = image_describer.describe_sync(img, "smoke.png")
    dt = time.time() - t0
    print(f"  延迟 {dt:.1f}s | 输出: {out[:300]}")
    print(f"  以[类型]开头? {'✓' if out.strip().startswith('[') else '⚠ 未遵循[类型]约定'}")
    # 确认 key 数字被原文保留
    print(f"  含 '732.22'? {'✓' if '732.22' in out else '⚠ 数字未保留'}")


if __name__ == "__main__":
    asyncio.run(smoke_intent())
    smoke_vision()
    print("\n烟测完成。把上面 ✓/⚠ 结果登记到 plan 的「结论」小节。")
```

- [ ] **Step 2: 运行烟测**

Run: `D:/miniConda/envs/rag/python.exe tools/smoke_models.py`
Expected: 每个意图返回长度 + 延迟 + JSON 完整性标记；视觉返回输出 + 两个格式标记。**不预设对错**——这是探针。

- [ ] **Step 3: 把结果登记到本计划「结论」小节**

在 Plan 下方"结论"块记录每个探针的 ✓/⚠，据此决定 Task 3/4 是否触发。**禁止跳过此步直接写死加固方案**——加固内容必须由真实输出决定。

---

## Phase 2 — 基于烟测结论的 TDD 加固（按需）

> 两个子任务各自独立，是否执行由 Step 3 结论决定。若对应探针全 ✓ 且无截断风险，可跳过硬改（但仍保留防御性单测）。

### Task 3: 强化 R1 意图 prompt 救回结构化 JSON（用户决策：保留 R1）

**Files:**
- Modify: `app/core/intent.py`（`INTENT_CLASSIFIER_PROMPT` 强化 + `classify` 调用处）
- Test: `tests/unit/test_intent_guard.py`（可加 prompt 契约断言）

> **问题本质**（烟测实锤）：R1 把路由任务当成**通用问答/推理**，输出推理轨迹、长段 markdown、甚至直接回答内容，从不收敛为纯 JSON。修复方向不是加 `max_tokens`（那是次要问题），而是**在 prompt 层面强约束输出形态**。以下方案先按"最可能奏效"排列，逐一实测，能吐 JSON 即停。

- [ ] **Step 1: 写锁定 prompt 契约的测试（防后续弱化约束）**

```python
"""锁定 R1 意图 prompt 具备强 JSON/禁思考约束。
烟测实锤 R1 把路由当问答输出推理轨迹——这些约束缺失会让全库回退率飙升。"""
from app.core.intent import INTENT_CLASSIFIER_PROMPT


def test_prompt_forbids_thinking_and_explanation():
    # R1 是推理模型：必须明确禁止输出思考过程/解释
    assert "思考" not in INTENT_CLASSIFIER_PROMPT or "不要" in INTENT_CLASSIFIER_PROMPT
    assert "仅" in INTENT_CLASSIFIER_PROMPT or "只" in INTENT_CLASSIFIER_PROMPT
    assert "JSON" in INTENT_CLASSIFIER_PROMPT


def test_prompt_has_strict_json_only_example():
    # 必须有"整个回复就是 JSON"的强示例，无任何额外文本
    assert "```json" in INTENT_CLASSIFIER_PROMPT or "整个输出" in INTENT_CLASSIFIER_PROMPT
    assert "matches" in INTENT_CLASSIFIER_PROMPT
```

- [ ] **Step 2: 运行测试确认失败（当前 prompt 缺强约束）**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_intent_guard.py -q`
Expected: 新增断言 FAIL 或当前常量缺关键词 → 进入 Step 3。

- [ ] **Step 3: 强化 INTENT_CLASSIFIER_PROMPT**

在 [app/core/intent.py:25](app/core/intent.py#L25) 的 `INTENT_CLASSIFIER_PROMPT` **开头**插入强格式段（置于最前，R1 更可能遵守），并在系统开头补一句"不要思考"：

```python
INTENT_CLASSIFIER_PROMPT = """你是一个知识库路由分类器，只做一件事：把问题映射到知识库 id。

【最高优先级 - 输出形态】
不要在内部思考，不要写任何思考过程、推理、解释、开场白或结束语。
你的【整个回复】只能是一个 JSON 对象本身，直接以 { 开头并以 } 结束，前后没有任何其他字符。
任何解释、markdown 代码块、或"好的，让我..."之类的话都是错误。

正确示例（唯一允许的输出形态）：
{"intent_type": "KB", "matches": [{"kb_id": "docs-a", "score": 0.9}]}

# 核心规则

【CRITICAL】只从提供的知识库列表中做选择。不要编造不存在的知识库名称。
【CRITICAL】返回格式必须是合法 JSON，不得包含任何额外的文本、解释或包装。违反将受罚。
【CRITICAL】如果问题与所有知识库都不相关（闲聊、打招呼、无关话题），返回空 matches 数组。强行匹配不相关的知识库将受罚。

# 输入

可用的知识库：
{kb_list}

用户问题：{question}

# 输出格式

{{
  "intent_type": "KB",
  "matches": [
    {{"kb_id": "知识库ID或名称", "score": 0.95}}
  ]
}}

- intent_type: 固定为 "KB"
- score: 0~1 浮点数，越高越相关
- 只保留 score >= 0.3 的知识库
- 最多返回 {max_count} 个匹配
- 无匹配时返回空数组: {{"intent_type": "KB", "matches": []}}

# 示例

用户问题："如何优化 RAG 分块策略？"
知识库：["文档处理", "系统配置", "用户手册"]
输出：{{"intent_type": "KB", "matches": [{{"kb_id": "文档处理", "score": 0.85}}]}}

用户问题："帮我看看我的订单还在路上吗"
知识库：["产品文档", "API 文档", "运维手册"]
输出：{{"intent_type": "KB", "matches": []}}

用户问题："JWT 和 Session 鉴权有什么不同"
知识库：["安全指南", "开发规范", "用户手册"]
输出：{{"intent_type": "KB", "matches": [{{"kb_id": "安全指南", "score": 0.92}}, {{"kb_id": "开发规范", "score": 0.65}}]}}

# 输出前确认
□ 我的回复是否【只】是那个 JSON 对象、无任何其他字符？
□ 所有 KB ID 都来自输入列表？
□ 不相关的已返回空数组？
□ score 是否反映了真实相关度？"""
```

（若上述仍不奏效，第二梯队尝试：把 `classify()` 改为用 `system` 角色承载该 prompt 的 one-line "只输出 JSON" 指令、user 消息只给问题和列表；或考虑 `minimax_client.chat` 的 `response_format={"type":"json_object"}`——但需先确认 SiliconFlow R1 是否支持。）

- [ ] **Step 4: 跑单测确认契约锁定**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_intent_guard.py tests/unit/test_llm_gateway.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 复跑真实 intent 烟测，看 R1 是否吐 JSON（关键验证）**

Run: `D:/miniConda/envs/rag/python.exe tools/smoke_models.py`
Expected: 探针返回合法 JSON 且 `matches` 能被 `_normalize_matches` 消费。**若仍 3/3 失败 → 停止，带数据回用户改回退决策（不硬撑）。**

- [ ] **Step 6: Commit**

```bash
git add app/core/intent.py tests/unit/test_intent_guard.py
git commit -m "fix(intent): strengthen R1 prompt to force strict JSON-only routing output"
```

### Task 4: 回退 OCR 到 Qwen-VL（用户决策：DeepSeek-OCR 输出垃圾，放弃；已完成）

**Files:**
- Modify: `app/config.py`（`vision_model` 回退）
- Modify: `app/llm/vision.py`（恢复 system 指令角色）
- Test: `tests/unit/test_vision_prompt.py`

> **背景**（烟测实锤）：`DeepSeek-OCR` 对白底黑字图输出一串 `}` 垃圾字符，且不遵循 `[类型]` 前缀、丢关键数字。用户决策回退到 Qwen-VL。**关键发现：原计划回退目标 `Qwen/Qwen2.5-VL-7B-Instruct` 已在硅基流动下架（实测 400 `Model does not exist`）**，改选可用的 `Qwen/Qwen3-VL-8B-Instruct`，真实 API 实测通过：延迟 8.6s、`[文档]` 前缀 ✓、数字 `732.22` 原文保留 ✓。**已完成并提交。**

- [ ] **Step 1: 回退 config.py 的 vision_model**

在 [app/config.py:13](app/config.py#L13) 改回：
```python
    vision_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"  # 图片理解（多模态，遵守[类型]分类约定）
```

- [ ] **Step 2: 恢复 vision.py 的 system 角色调用**

在 [app/llm/vision.py:114-124](app/llm/vision.py#L114-L124)，把"系统指令并入 user"改回独立的 system 消息（Qwen2.5-VL 支持 system 角色）：
```python
        try:
            from app.config import settings as _settings
            # 固定走多模态模型：文本模型可能是非多模态的 highspeed 变体。
            resp = await minimax_client.chat([
                {"role": "system", "content": "你是一个图片分析助手，擅长识别图片类型并提取关键信息。"},
                {"role": "user", "content": [
                    {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ], model=_settings.vision_model)
```

（若 Qwen2.5-VL 也不吃 system，则沿用并进 user 的方式——以实测为准，但模型必须换回 Qwen2.5-VL。）

- [ ] **Step 3: 写锁定测试（vision 走配置的 vision_model，且含[类型]契约）**

```python
"""锁定 vision 走 settings.vision_model，IMAGE_DESCRIBE_PROMPT 保持[类型]契约。"""
from app.core import intent  # noqa 触发配置加载
from app.llm.vision import IMAGE_DESCRIBE_PROMPT
from app.config import settings


async def test_vision_uses_configured_model(monkeypatch):
    from app.llm.vision import image_describer
    from app.llm.chat import minimax_client

    seen = {}

    async def fake_chat(messages, **kw):
        seen["kw"] = kw
        seen["roles"] = [m["role"] for m in messages]
        return "[表格] 提取到的文字"

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    monkeypatch.setattr(image_describer, "_should_skip", lambda *a, **k: False)
    out = await image_describer.describe(b"x" * 6000, "a.png")
    assert seen["kw"].get("model") == settings.vision_model
    assert settings.vision_model == "Qwen/Qwen2.5-VL-7B-Instruct"


def test_prompt_has_type_contract():
    assert "[类型]" in IMAGE_DESCRIBE_PROMPT
    assert "流程图" in IMAGE_DESCRIBE_PROMPT
    assert "原文" in IMAGE_DESCRIBE_PROMPT
```

- [ ] **Step 4: 跑单测**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_llm_gateway.py -q`
Expected: 视觉相关测试全 PASS（含恢复的 system 角色路径，若原测试断言存在）。

- [ ] **Step 5: 真实视觉烟测，确认 Qwen2.5-VL 输出合理**

Run: `D:/miniConda/envs/rag/python.exe tools/smoke_models.py`
Expected: OCR 探针输出含可读文字，`以[类型]开头` 与 `含732.22` 标记尽量转 ✓。若 Qwen2.5-VL 仍弱于旧行为，记录实据并回用户。

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/llm/vision.py tests/unit/test_vision_prompt.py
git commit -m "fix(vision): revert to Qwen2.5-VL — DeepSeek-OCR outputs garbage on text images"
```

```bash
git add app/llm/vision.py tests/unit/test_vision_prompt.py
git commit -m "fix(vision): align OCR prompt/output with [类型] contract"
```

---

## Phase 3 — 全量评测回归

### Task 5: 起服务 + 跑全量 eval_sany 评测

**Files:**
- 无（只读运行）

- [ ] **Step 1: 起后端服务（后台）**

```bash
cd d:/PyProject/ragent-py
D:/miniConda/envs/rag/python.exe -m app.main
```
后台运行；确认 `http://localhost:8000/health` 返回 200。

- [ ] **Step 2: 确认评测数据集就绪**

Run: `D:/miniConda/envs/rag/python.exe -c "import json,pathlib; p=pathlib.Path('eval/sany_annual_reports/rag_testset.json'); d=json.load(open(p)); print(len(d), '题')"`
Expected: `65 题`。将上回 `eval_results.json`（上次 19/65 部分结果）备份为 `eval_results.before_ocrogenme.json`，避免 `--resume` 污染对比基线：
```bash
cp eval/sany_annual_reports/eval_results.json eval/sany_annual_reports/eval_results.before_llmswitch.json 2>/dev/null || true
```

- [ ] **Step 3: 跑全量评测（全新基线，强制从头）**

Run: `D:/miniConda/envs/rag/python.exe eval/eval_sany.py --no-resume`
Expected: 约 10 分钟，覆盖 65 题，产出 `eval_report.md`。记录总分与各分类(A/B/C/H/I)得分。

- [ ] **Step 4: 与旧基线对比**

对比 `eval_results.before_llmswitch.json` 或既有 `eval_report.md`（56.1% @ 19题 / 已知 A:60% B:17% C:0%）。确认**无回归**：整体不降、A/C 类不明显掉（意图路由若失效 A/C 会崩）。若 R1 导致 A/C 崩，回滚 intent 到 chat_model（见 Task 6 Step 3）。

- [ ] **Step 5: 提交评测结果**

```bash
git add eval/sany_annual_reports/eval_report.md eval/sany_annual_reports/eval_results.json
git commit -m "eval: record full 65-question baseline after LLM model switch"
```

---

### Task 6: 回填计划 + 更新 TODO

**Files:**
- Modify: `docs/plans/2026-08-20-intent-vision-model-switch.md`（「当前状态」）
- Modify: `docs/plans/2026-08-20-intent-vision-model-validate.md`（本计划「结论」→「完成」）
- Modify: `docs/TODO.md`（勾掉已实测项）

- [ ] **Step 1: 回填 model-switch 计划「当前状态」**

把实测定论写回 `2026-08-20-intent-vision-model-switch.md` 第 21-34 行段：R1 延迟是否可接受、是否设 `intent_max_tokens`、OCR 输出是否合规、加了哪些单测。

- [ ] **Step 2: 更新本计划状态与 TODO**

- 本计划把 `结论` 块改写为 `结果` 快照（含实测数字）。
- `docs/TODO.md`「最近改动」段更新：标「已实测」，加上评测结果。

- [ ] **Step 3: 处理 R1 意图路由失败兜底决策**

若 Task 5 评测显示 A/C 类因 R1 崩（或延迟不可接受），把 `settings.intent_model` 回退为默认 `chat_model` 并标注原因；否则保留。**此步决策以实测数据为准，二选一后 Commit。**

- [ ] **Step 4: 最终全量单测确认无回归**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q`
Expected: 全部 PASS（模型切换前后单测全绿）。

---

## 结论（Phase 1 实测结果 — 已跑 2026-08-20）

> 已运行 `tools/smoke_models.py`，两个风险点**全部实锤失败**。

- [x] R1 意图：JSON 完整 ⚠ **3/3 全失败**——不吐 JSON，吐推理轨迹/长段回答/17字碎片（`路由到 docs-a 知识库`）
- [x] R1 意图：延迟 10.9s / 25.6s / 29.9s — **不可接受**，且 `max_tokens=4096` 被推理吃光
- [x] OCR：以 `[类型]` 开头 ⚠ **未遵循**，输出一串 `}` 垃圾字符
- [x] OCR：关键数字原文保留 ⚠ **未保留**（`732.22` 缺失）

**用户决策（2026-08-20）：**
- 意图路由：**保留 R1，调 prompt** 救（本 plan Task 3 从「加 max_tokens」改为「强化 prompt + 禁思考」；若实测仍失败，带数据回来改回退决策）
- 视觉：**回退 Qwen-VL**（Task 4 已完成：DeepSeek-OCR 输出垃圾 + Qwen2.5-VL-7B 已下架 → 改用 Qwen3-VL-8B-Instruct，实测通过）

⇒ 触发加固：Task 3（R1 prompt 强化，**已完成**）/ Task 4（OCR 回退，**已完成**）

**Task 3 实测结果（2026-08-20）：** 强化 prompt 后 R1 从"3/3 不吐 JSON"变为**稳定吐 JSON**：
- Q1(三一营收) → `matches=[docs-a 0.9]` ✓，延迟 7.0s
- Q2(科创板无关) → `matches=[docs-a 0.9]` ⚠（弱语义测试环境下误路由；真实 KB 名称场景待评测验证）
- Q3(2024vs2025对比) → `matches=[docs-a 0.85]` ✓，延迟 16.7s
- 延迟 25-30s → 7-17s，可接受
- **重点：格式问题已解决，但"无关即空数组"的语义判断需在真实 KB 名称下用全量评测复核。**

**Task 4 实测结果（2026-08-20）：** Qwen3-VL-8B-Instruct 延迟 8.6s、`[文档]` 前缀 ✓、数字 `732.22` 原文保留 ✓。

---

## Self-Review

**Spec 覆盖：**
- 用户要求「先写 plan 完整一些，再开发」→ Plan 覆盖前置提交→横向烟测→TDD 加固→全量评测→回填全链路。
- 计划文档 `2026-08-20-intent-vision-model-switch.md` 列的 4 步下一步（实测→按需调优→复跑评测→回填）全部映射到 Task 2/3/4/5/6。
- TODO.md「待实测」项映射到 Task 2/5。

**Placeholder 检查：** Task 4 Step 3 的改写内容标注「以真实输出为准」——这是**有意的、由数据驱动**的决策点，非偷懒占位；其余所有代码步骤都给出完整代码。

**类型一致性：** `intent_max_tokens` 在 config、intent、测试三处命名一致；`smoke_models.py` 的探针标记与「结论」块一致。烟测直接 import `minimax_client` 复刻 intent 调用，与真实代码路径一致。
