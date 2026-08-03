> 状态: 已完成（commits: 09d0cd8 / a6a4274 / 227658c / 8a4b57e / ceeed84 / f4ebb48 / a5f5e56，分支 fix/residual-fixes；实施期另修复前端既有 TS1016 类型错误）

# 审查遗留修复（audit-residual-fixes）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪；TDD：先失败测试再实现。

**Goal:** 清空 2026-08-02 全栈审查的遗留缺陷与低风险缓做项：搜索函数 finally 掩盖原始异常、add_chunks 微秒溢出、意图路由复活（前端 null + prompt 只有 hex id）、rewritten_query 利用与检索空结果显式信号、PII 中文排除词失效、坏正则静默回退、登录限流代理头与内存上界、默认口令读配置、标题黑名单子串误杀、前端 degraded 事件无人消费。

**Architecture:** 全部为局部修复，不引入新抽象。原则：错误可观测（不再掩盖）、行为可配置（默认口令进 settings）、路由真可用（意图分类拿到 KB 名称、默认全库路由）。

**Tech Stack:** pytest unit + integration（ragent_test）、FastAPI Request、Vue 前端最小改动。

---

## Context

六份 plan 完成后对照审查清单盘点出的遗留项：

**未排进任何 plan 的缺陷**
1. **finally 掩盖异常（中高危）**：`search` / `bm25_search` / `question_vector_search` 的 finally 里 `len(rows)`——execute 抛错时 `rows` 未绑定，`UnboundLocalError` 覆盖真实 DB 错误，生产排障致盲。
2. **add_chunks 微秒溢出（中危）**：`base_ts.replace(microsecond=base_ts.microsecond + i)` 在基准微秒接近 999999 且 chunk 较多时 ValueError，整个写入事务失败。
3. **意图路由双重死代码（功能性失效）**：前端 `knowledge_base_ids` 恒 null → 分类器短路；即使路由，prompt 里只有 hex id 没有 KB 名称，LLM 无法语义路由。多知识库场景路由从未工作。
4. **rewritten_query 未使用 + 检索失败静默**：pipeline 兜底重搜用原始 query（含未消解代词）；全库检索空结果时无任何用户可见信号。

**各 plan 有意缓做、但改动小风险低、顺手清掉的项**
5. **PII 中文排除词失效**：`_has_exclusion` 用 `\b` 包裹排除词，CJK 字符间无词边界 → "示例/测试"类中文排除词永不命中（方向是过度脱敏）。
6. **坏正则静默回退**：load_rules 任一规则 pattern 非法 → 整个加载 try/except 吞掉、静默换默认规则，无告警。
7. **登录限流**：只按 `request.client.host`（反代后全体共享一桶）；`_LOGIN_ATTEMPTS` 只增不删（缓慢内存泄漏）。
8. **默认口令硬编码**：`seed_defaults` 写死 admin/admin123，不读 settings（CLAUDE.md 宣称 `DEFAULT_USERNAME/DEFAULT_PASSWORD` 可配但 Settings 里根本没有这两项）。
9. **标题黑名单子串误杀**：`_is_blacklist_title` 用子串匹配，"目录结构说明"等真实章节被整段丢弃（直接数据丢失）。
10. **前端 degraded 事件无人消费**：后端降级时发 `event: degraded`，前端 chat.ts 无对应分支，静默丢弃。

## Design

1. **rows 初始化**：三个搜索函数体首行 `rows = []`；异常照常上抛，finally 的 debug 日志不再炸。
2. **微秒溢出**：`created_at = base_ts + timedelta(microseconds=i)`（timedelta 自动进位）。
3. **意图路由复活**：
   - pipeline：`all_kb_ids = req.knowledge_base_ids`，为 None/空时经 `asyncio.to_thread(pgvector_store.list_kb_ids)` 取全库 id 列表（默认全库路由，分类器不再短路）。
   - intent.py：`classify` 内把 kb_id 列表解析为 `{id: name}`（`asyncio.to_thread` 包一次小查询），prompt 列 `id（名称）`，输出契约不变（仍返回 kb_id）。
4. **rewritten 利用**：pipeline 兜底重搜用 `rewritten_query`（fast path 时即原 query）；全链路检索空结果时 `yield event: no_context`（前端未识别事件安全忽略）并 `ctx.record("retrieval_empty")`。
5. **CJK 排除词**：`_has_exclusion` 对含 CJK 的排除词用子串匹配，纯 ASCII 词保留 `\b` 边界。
6. **逐规则编译**：load_rules 逐条 compile，非法 pattern 记 warning 并跳过该条（不再整体静默回退）；DB 不可用时仍回退默认但记 warning。
7. **限流硬化**：取 IP 优先 `X-Forwarded-For` 首段，回落 `client.host`；桶总量超 `_RATE_LIMIT_MAX_KEYS`（10000）时整体清空重建（简单有界）。
8. **默认口令**：Settings 增加 `default_username: str = "admin"`、`default_password: str = "admin123"`；seed_defaults 改读 settings。
9. **黑名单精确匹配**：`title.strip() in _TITLE_BLACKLIST`（宁可漏拦样板，不可误删正文）。
10. **前端**：chat.ts 增加 `degraded` 分支，转成 `onStatus("degraded", "部分服务降级：" + providers)`，复用现有状态条展示。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| DB 故障时调用搜索 | 原始异常上抛（不再 UnboundLocalError） |
| 基准微秒 999999 + 多 chunk | timedelta 进位，写入成功 |
| 前端不传 kb_ids | 默认全库 id 列表，意图分类正常工作 |
| KB 名称解析失败 | classify 捕获异常，退回纯 id 列表（不阻断） |
| 检索全空 | 发 no_context 事件 + ctx 记录；LLM 按"如实告知"模板回答 |
| 中文"示例"关键词在 PII 附近 | 跳过脱敏（恢复设计意图） |
| 单条规则正则非法 | 该条跳过 + warning，其余规则生效 |
| 反代部署 | 按真实客户端 IP 限流 |
| 限流表超 10000 key | 整体清空重建，内存有界 |
| 标题"目录结构说明" | 保留（精确匹配不再误杀） |

## Files to change

| 变更 | 路径 |
|---|---|
| Modify | `app/store/pgvector_store.py`（rows 初始化 ×3、微秒溢出）、`app/core/pipeline.py`（默认 kb_ids、rewritten 兜底、no_context）、`app/core/intent.py`（KB 名称）、`app/core/pii_scanner.py`（CJK 排除、逐规则编译）、`app/api/auth.py`（代理头、上界）、`app/store/auth_store.py` + `app/config.py`（默认口令）、`app/ingestion/structurer.py`（精确匹配）、`frontend/src/api/chat.ts`（degraded 分支） |
| Modify | 测试：`tests/integration/test_search_errors.py`（新，2 例）、`tests/integration/test_intent_routing.py`（新，1 例）、`tests/unit/test_pii_exclusion.py`（新，2 例）、`tests/integration/test_auth_hardening.py`（新，1 例）、`tests/unit/test_structurer_blacklist.py`（新，2 例）、`docs/plans/README.md` |

## Reused existing utilities

`pgvector_store.list_kb_ids`（默认路由直接复用）、`_check_rate_limit`（仅扩展 key 来源）、`KnowledgeBase` 模型（名称解析）、conftest 的 `integration_db` / fake LLM 层。

---

## Tasks

### Task 1: 搜索异常可见性 + 微秒溢出

- [ ] **Step 1: 写失败测试 `tests/integration/test_search_errors.py`**

```python
"""搜索层错误可见性：原始异常上抛、微秒边界写入。"""
from datetime import timedelta


def test_search_propagates_original_error(monkeypatch):
    """DB 出错时上抛原始异常，而不是 finally 里的 UnboundLocalError。"""
    from app.store import pgvector_store

    class BrokenSession:
        def execute(self, *a, **kw):
            raise RuntimeError("db down")

        def close(self):
            pass

    monkeypatch.setattr(pgvector_store, "get_session", lambda: BrokenSession())
    try:
        pgvector_store.search(["test-kb"], [0.1] * 4096, can_read_all=True)
        raise AssertionError("应当抛异常")
    except RuntimeError as e:
        assert "db down" in str(e)


def test_add_chunks_microsecond_overflow(integration_db):
    """基准微秒接近上限时，多 chunk 写入不得 ValueError。"""
    from app.store import pgvector_store
    from app.store.db import utc_now

    base = utc_now().replace(microsecond=999998)
    monkey = None
    original = pgvector_store.utc_now
    pgvector_store.utc_now = lambda: base
    try:
        pgvector_store.add_chunks([
            {"chunk_id": f"ovf_{i}", "document_id": "ovf-doc", "kb_id": "test-kb",
             "text": f"溢出测试 {i}", "embedding": [0.1] * 4096}
            for i in range(4)
        ])
    finally:
        pgvector_store.utc_now = original
    got = pgvector_store.get_chunks_by_document("ovf-doc")
    assert len(got) == 4
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`search` / `bm25_search` / `question_vector_search` 三个函数体首行（`session = get_session()` 前）加 `rows = []`。

`add_chunks`：

```python
    session = get_session()
    try:
        base_ts = utc_now()
        for i, c in enumerate(chunks_data):
            session.add(Chunk(
                ...
                created_at=base_ts + timedelta(microseconds=i),   # timedelta 自动进位，不再溢出
```

（顶部 `from datetime import timedelta` 导入。）

- [ ] **Step 4: 运行 + Commit**

```bash
git add app/store/pgvector_store.py tests/integration/test_search_errors.py
git commit -m "fix(store): propagate original search errors, timedelta-based chunk timestamps + plan: audit-residual-fixes"
```

---

### Task 2: 意图路由复活（默认全库 + KB 名称）

- [ ] **Step 1: 写失败测试 `tests/integration/test_intent_routing.py`**

```python
"""意图路由：prompt 必须含 KB 名称，LLM 才可能语义路由。"""


def test_intent_prompt_contains_kb_names(integration_db, monkeypatch):
    from app.core.intent import intent_classifier
    from app.llm.chat import minimax_client

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return '{"intent_type": "KB", "matches": []}'

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    import asyncio
    asyncio.run(intent_classifier.classify("什么是 Transformer", ["test-kb"]))
    assert captured
    assert "测试知识库" in captured[0], "意图 prompt 里没有 KB 名称，LLM 无法语义路由"
```

- [ ] **Step 2: 运行确认失败**（当前 prompt 只有 hex id）

- [ ] **Step 3: 实现 `app/core/intent.py` 名称解析**

`classify` 里构建 kb_list_str 前：

```python
        # KB 名称与 id 一并给 LLM：只有 hex id 时无法语义路由（旧实现形同虚设）
        try:
            import asyncio as _asyncio
            kb_names = await _asyncio.to_thread(_resolve_kb_names, kb_ids)
            kb_list_str = "\n".join(
                f"- {kid}（{kb_names.get(kid, '未命名')}）" for kid in kb_ids)
        except Exception:
            kb_list_str = "\n".join(f"- {kid}" for kid in kb_ids)
```

模块底部辅助：

```python
def _resolve_kb_names(kb_ids: list[str]) -> dict[str, str]:
    from app.store.db import get_db_ctx, KnowledgeBase
    with get_db_ctx() as session:
        rows = session.query(KnowledgeBase.id, KnowledgeBase.name).filter(
            KnowledgeBase.id.in_(kb_ids)).all()
        return {r.id: r.name for r in rows}
```

（删除原 `kb_list_str = "\n".join(f"- {kid}" for kid in kb_ids)` 行。）

- [ ] **Step 4: 实现 `app/core/pipeline.py` 默认全库路由**

`all_kb_ids = req.knowledge_base_ids` 改为：

```python
        all_kb_ids = req.knowledge_base_ids
        if not all_kb_ids:
            # 默认全库路由：不再让意图分类器因 kb_ids 为空而短路
            all_kb_ids = await asyncio.to_thread(pgvector_store.list_kb_ids)
```

（文件顶部已有 `from app.store import pgvector_store`——若无则加导入；注意 pipeline 内其他 `from app.store.pgvector_store import ...` 局部导入不冲突。）

- [ ] **Step 5: 运行 + Commit**

```bash
git add app/core/intent.py app/core/pipeline.py tests/integration/test_intent_routing.py
git commit -m "fix(intent): route all KBs by default, give LLM KB names instead of bare hex ids"
```

---

### Task 3: rewritten_query 兜底 + 检索空结果信号

- [ ] **Step 1: 实现 `app/core/pipeline.py`**

needs_decomp 分支记录改写结果：

```python
        needs_decomp = _needs_decomposition(req.query)
        if not needs_decomp:
            sub_queries = [req.query]
            rewritten_query = req.query
        else:
            rewrite_result = ...（不变）
            sub_queries = rewrite_result.sub_questions
            rewritten_query = rewrite_result.rewritten_query or req.query
```

兜底重搜改用改写后的查询：

```python
        if not all_chunks:
            try:
                chunks = await retrieval_engine.retrieve(
                    rewritten_query, None,     # 旧实现用 req.query，代词未消解
                    ...
```

sources yield 之后、prompt 构建前，空结果信号：

```python
        if not unique_chunks:
            if ctx:
                ctx.record("retrieval_empty", query=req.query)
            yield "event: no_context\ndata: {}\n\n"
```

- [ ] **Step 2: 验证 import 链 + 全量套件**（pipeline 无专属自动化测试，靠回归 + 手工冒烟）

- [ ] **Step 3: Commit**

```bash
git add app/core/pipeline.py
git commit -m "fix(pipeline): fallback re-search uses rewritten query, emit no_context when retrieval empty"
```

---

### Task 4: PII 中文排除词 + 逐规则编译

- [ ] **Step 1: 写失败测试 `tests/unit/test_pii_exclusion.py`**

```python
"""PII：中文排除词生效、坏正则不拖垮其他规则。"""


def test_cjk_exclusion_word_skips_masking():
    from app.core.pii_scanner import scan
    # "示例"是排除词：旧实现 \b 对 CJK 无效 → 照样命中脱敏
    findings = scan("下面是示例号码 13800138000 仅用于演示")
    assert not any(f.rule_name == "cn_phone" for f in findings)


def test_ascii_exclusion_still_works():
    from app.core.pii_scanner import scan
    findings = scan("sample number 13800138000 for demo")
    assert not any(f.rule_name == "cn_phone" for f in findings)
```

- [ ] **Step 2: 运行确认第一条失败**

- [ ] **Step 3: 实现 `app/core/pii_scanner.py`**

`_has_exclusion`：

```python
def _has_exclusion(text: str, match_start: int, match_end: int, exclusion_words: set[str]) -> bool:
    """Check if exclusion words appear within a window before/after the match.

    含 CJK 的排除词用子串匹配——`\b` 在汉字之间不存在词边界，
    旧实现对中文排除词（示例/测试等）永不命中。
    """
    if not exclusion_words:
        return False
    window = text[max(0, match_start - 20): min(len(text), match_end + 20)]
    for word in exclusion_words:
        if re.search(r'[\u4e00-\u9fff]', word):
            if word in window:
                return True
        elif re.search(r'\b' + re.escape(word) + r'\b', window, re.IGNORECASE):
            return True
    return False
```

`load_rules` DB 分支改逐条编译（替换列表推导）：

```python
    try:
        rows = session.query(SensitiveRule).filter(SensitiveRule.is_active == True).all()
    except Exception:
        logger.warning("PII rules: DB unavailable, falling back to default rules")
        _rule_cache = _fallback_rules()
        _cache_ts = now
        return _rule_cache
    try:
        compiled = []
        for r in rows:
            if not r.pattern:
                continue
            try:
                pat = re.compile(r.pattern)
            except re.error:
                logger.warning("PII rules: invalid pattern for rule %r skipped: %s",
                               r.rule_name, r.pattern[:80])
                continue
            compiled.append({
                "rule_name": r.rule_name,
                "pattern": pat,
                "validation_fn": r.validation_fn,
                "strategy": r.strategy,
                "mask_mode": r.mask_mode,
                "exclusion_words": set(
                    w.strip() for w in (r.exclusion_words or "").split(";") if w.strip()
                ),
            })
        _rule_cache = compiled
        _cache_ts = now
        return _rule_cache
    finally:
        session.close()
```

- [ ] **Step 4: 运行 + Commit**

```bash
git add app/core/pii_scanner.py tests/unit/test_pii_exclusion.py
git commit -m "fix(pii): CJK exclusion words work, invalid rule patterns skipped with warning"
```

---

### Task 5: 登录限流硬化 + 默认口令读配置

- [ ] **Step 1: 写测试 `tests/integration/test_auth_hardening.py`**

```python
"""限流按真实客户端 IP（X-Forwarded-For）计桶。"""


def test_rate_limit_key_uses_forwarded_for():
    from app.api.auth import _client_ip

    class FakeClient:
        host = "10.0.0.1"

    class FakeRequest:
        client = FakeClient()
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}

    assert _client_ip(FakeRequest()) == "203.0.113.7"

    class NoHeader:
        client = FakeClient()
        headers = {}

    assert _client_ip(NoHeader()) == "10.0.0.1"
```

- [ ] **Step 2: 运行确认失败（_client_ip 不存在）**

- [ ] **Step 3: 实现 `app/api/auth.py`**

```python
_LOGIN_WINDOW = 300  # seconds
_LOGIN_MAX_ATTEMPTS = 10
_RATE_LIMIT_MAX_KEYS = 10000  # 桶数量上界，超出整体清空（简单有界，防缓慢泄漏）


def _client_ip(request) -> str:
    """反代部署时取 X-Forwarded-For 首段作为真实客户端 IP。"""
    fwd = request.headers.get("x-forwarded-for", "")
    first = fwd.split(",")[0].strip() if fwd else ""
    return first or (request.client.host if request.client else "unknown")
```

`login` 里 `_check_rate_limit(request.client.host)` → `_check_rate_limit(_client_ip(request))`；
`register` 的限流 key 同样用 `_client_ip(request)`。
`_check_rate_limit` 开头加上界：

```python
    if len(_LOGIN_ATTEMPTS) > _RATE_LIMIT_MAX_KEYS:
        _LOGIN_ATTEMPTS.clear()
```

- [ ] **Step 4: `app/config.py` 加默认凭据、`app/store/auth_store.py` seed 读配置**

Settings 里 JWT 段后：

```python
    default_username: str = "admin"
    default_password: str = "admin123"
```

`seed_defaults`：`username="admin"` → `settings.default_username`；`hash_password("admin123")` → `hash_password(settings.default_password)`（补 `from app.config import settings` 导入）。

- [ ] **Step 5: 运行 + Commit**

```bash
git add app/api/auth.py app/config.py app/store/auth_store.py tests/integration/test_auth_hardening.py
git commit -m "fix(auth): rate-limit by X-Forwarded-For with bounded buckets, seed credentials from settings"
```

---

### Task 6: 标题黑名单精确匹配

- [ ] **Step 1: 写失败测试 `tests/unit/test_structurer_blacklist.py`**

```python
"""标题黑名单：精确匹配，不再子串误杀真实章节。"""
from app.ingestion.structurer import document_structurer

NL = chr(10)


def test_boilerplate_exact_title_dropped():
    md = NL.join(["# 文档", "## 目录", "这里只是页脚样板。", "## 正文", "真正的内容。"])
    sections = document_structurer.structure(md)
    titles = [s.title for s in sections]
    assert "正文" in titles
    assert all("这里只是页脚样板" not in (e.text or "") for s in sections for e in s.elements)


def test_legitimate_section_not_dropped():
    md = NL.join(["# 手册", "## 目录结构说明", "本节描述目录如何组织。", "## 其他", "内容。"])
    sections = document_structurer.structure(md)
    texts = " ".join(e.text or "" for s in sections for e in s.elements)
    assert "本节描述目录如何组织" in texts, "真实章节被黑名单子串误杀"
```

- [ ] **Step 2: 运行确认第二条失败**

- [ ] **Step 3: 实现 `app/ingestion/structurer.py`**

```python
def _is_blacklist_title(title: str) -> bool:
    # 精确匹配：子串匹配曾把"目录结构说明"这类真实章节整段丢弃（数据丢失）。
    # 宁可漏拦变体样板，不可误删正文。
    return title.strip() in _TITLE_BLACKLIST
```

- [ ] **Step 4: 运行 + Commit**

```bash
git add app/ingestion/structurer.py tests/unit/test_structurer_blacklist.py
git commit -m "fix(structurer): blacklist uses exact title match, no more substring content loss"
```

---

### Task 7: 前端 degraded 事件 + 构建验证

- [ ] **Step 1: `frontend/src/api/chat.ts` 在 `cross_doc` 分支后加**

```typescript
            } else if (lastEventType === 'degraded') {
              lastEventType = ''
              try {
                const parsed = JSON.parse(data)
                if (onStatus) onStatus('degraded', '部分服务暂时降级：' + (parsed.providers || []).join(', '))
              } catch { /* ignore */ }
            } else if (data.startsWith('{')) {
```

- [ ] **Step 2: 构建验证**

Run:
```bash
cd frontend && npx vue-tsc -b && npx vite build
```
Expected: 构建成功。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/chat.ts
git commit -m "fix(frontend): surface degraded SSE event via status line instead of dropping it"
```

---

### Task 8: 全量回归 + 收尾

- [ ] **Step 1: 全量运行**

Run: `D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: `110 passed, 0 xfailed, 2 skipped`（102 基线 + 8 新例；以实测为准）。

- [ ] **Step 2: 更新 plan 状态与索引，Commit**

```bash
git add docs/plans/
git commit -m "docs(plans): mark audit-residual-fixes complete + plan: audit-residual-fixes"
```

## Verification

| 验证项 | 期望 |
|---|---|
| 全量套件 | `110 passed, 0 xfailed, 2 skipped` |
| 原始异常上抛 | `test_search_propagates_original_error` passed |
| 微秒进位 | `test_add_chunks_microsecond_overflow` passed |
| KB 名称进 prompt | `test_intent_prompt_contains_kb_names` passed |
| 中文排除词 | `test_cjk_exclusion_word_skips_masking` passed |
| 代理头限流 | `test_rate_limit_key_uses_forwarded_for` passed |
| 黑名单精确 | `test_structurer_blacklist.py` 2 passed |
| 前端构建 | vue-tsc + vite build 成功 |
| no_context / rewritten 兜底 | 手工冒烟：空库查询见 no_context 事件；带代词多轮对话兜底重搜命中改写句 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| RBAC 细粒度强制（权限矩阵替换 is_admin 二元判断） | 结构性改造，涉及全部 admin 端点与前端菜单权限，单独成 plan |
| 统一 LLM 网关抽象 / response_format 结构化输出 | 同上，属 llm 层重构议题 |
| JWT iss/aud/nbf 校验 | 部署形态决策（单租户/多租户）未定 |
| 注册口用户名枚举统一文案 | 产品体验决策（需产品确认是否接受"假成功"） |
| rerank 读 Retry-After、vision 旧 client 关闭 | 低危微优化，收益不抵改动面 |
| metadata 大文档分批、atomic 块保真、emit 解耦、消息归档 | 结构性，各自需要独立设计 |
| pipeline SSE 端到端自动化测试 | 需要 fake chat_stream 的 ASGI 级基建，独立议题 |
