"""ragent-py HTTP 客户端：JWT 登录（401 重登一次）、KB 按名 ensure、
确定性文件名上传、摄入状态轮询、/retrieve 调用。

凭据只从构造函数/env 读——MCP 工具入参永不携带凭据。
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx


class RagentClientError(RuntimeError):
    """面向 MCP 工具调用方的可读错误——直接作为工具返回文本。"""


class RagentClient:
    def __init__(self, base_url: str = "", username: str = "", password: str = "", timeout: float = 30.0):
        self.base_url = (base_url or os.getenv("RAGENT_URL", "")).rstrip("/")
        if not self.base_url:
            # 默认 base_url 设为空字符串而非 localhost 兜底——__init__ 失败即响亮，
            # 调用方在启动时就会发现环境变量没配，而不是等到第一次 HTTP 请求才报错。
            raise RagentClientError("RAGENT_URL 未配置：请设置环境变量 RAGENT_URL 或显式传入 base_url")
        self.username = username or os.getenv("RAGENT_USER", "")
        self.password = password or os.getenv("RAGENT_PASSWORD", "")
        # MCP 工具调用是串行的；并发场景（asyncio.gather / 多 worker / HTTP transport）
        # 需外加 asyncio.Lock 包裹 _login + _request，避免重登竞态覆盖 token。
        self._token: str | None = None
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> None:
        try:
            resp = await self._http.post(
                "/api/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
        except httpx.HTTPError as exc:
            raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        if resp.status_code == 401:
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")
        if resp.status_code != 200:
            raise RagentClientError(f"登录异常：HTTP {resp.status_code}")
        try:
            self._token = resp.json()["access_token"]
        except (KeyError, json.JSONDecodeError) as exc:
            raise RagentClientError(f"登录响应格式异常: {exc}") from exc

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._token:
            await self._login()
        headers = kwargs.pop("headers", {})
        # kwargs 中不再含 headers；重试时显式传 headers= 参数
        headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        if resp.status_code == 401:  # token 过期 → 重登一次
            original_detail = resp.text[:200]
            self._token = None
            try:
                await self._login()
            except RagentClientError as exc:
                # 重登本身失败（账号被锁、token 黑名单等）→ 把第一次 401 的诊断体附上抛出
                raise RagentClientError(
                    f"401 重登失败：{exc}；原始响应：{original_detail}"
                ) from exc
            headers["Authorization"] = f"Bearer {self._token}"
            try:
                resp = await self._http.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise RagentClientError(f"ragent-py 服务不可达（{self.base_url}）: {exc}") from exc
        return resp

    async def ensure_kb(self, name: str, visibility: str = "internal") -> str:
        resp = await self._request("GET", "/api/v1/kb")
        if resp.status_code != 200:
            raise RagentClientError(f"知识库列表获取失败：HTTP {resp.status_code}")
        for kb in resp.json():
            if kb.get("name") == name:
                return kb["id"]
        resp = await self._request("POST", "/api/v1/kb", json={"name": name, "visibility": visibility})
        if resp.status_code == 403:
            raise RagentClientError("服务账号缺少 kb.create 权限")
        if resp.status_code != 200:
            raise RagentClientError(f"知识库创建失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()["id"]

    async def upload_document(self, kb_id: str, filename: str, content: str) -> dict:
        files = {"file": (filename, content.encode("utf-8"), "text/markdown")}
        resp = await self._request("POST", "/api/v1/documents/upload",
                                   files=files, data={"kb_id": kb_id})
        if resp.status_code != 200:
            raise RagentClientError(f"上传失败 {filename}：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def wait_indexed(self, document_id: str, timeout_s: float = 180.0, interval_s: float = 2.0) -> dict:
        """轮询直至 status ∈ {indexed, failed}；超时未收敛则抛错而非伪成功。

        - 失败状态正常返回 dict（让上层看到失败原因）。
        - 超时未收敛：抛 RagentClientError，避免下游误把 status="processing" 当作
        仍在等待而忽略超时。
        """
        remaining = timeout_s
        while remaining > 0:
            resp = await self._request("GET", f"/api/v1/documents/{document_id}")
            if resp.status_code == 200:
                doc = resp.json()
                if doc.get("status") in ("indexed", "failed"):
                    return doc
            await asyncio.sleep(interval_s)
            remaining -= interval_s
        raise RagentClientError(
            f"摄入轮询超时 ({timeout_s:.0f}s): document_id={document_id}"
        )

    async def retrieve(self, query: str, kb_ids: list[str], top_k: int = 5) -> dict:
        resp = await self._request("POST", "/api/v1/retrieve",
                                   json={"query": query, "kb_ids": kb_ids, "top_k": top_k})
        if resp.status_code == 401:
            raise RagentClientError("登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD")
        if resp.status_code == 403:
            raise RagentClientError(f"无权读取字典知识库：{resp.text[:200]}")
        if resp.status_code != 200:
            raise RagentClientError(f"检索失败：HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def list_documents(self, kb_id: str, limit: int = 200) -> list[dict]:
        """仅返回当前服务账号可见的字典文档（除非服务账号有 doc.read_all 权限）。

        服务端按 owner_id 过滤非 admin / 非 read_all 用户——本方法不会看到他人
        上传的字典文档。A7 的 reconciliation 流程必须依赖这一点（避免静默漏看）。

        GET /documents 无 kb_id 参数——取一页后客户端侧过滤。
        """
        resp = await self._request("GET", "/api/v1/documents", params={"limit": limit})
        if resp.status_code != 200:
            raise RagentClientError(f"文档列表获取失败：HTTP {resp.status_code}")
        return [d for d in resp.json() if d.get("kb_id") == kb_id]
