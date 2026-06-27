"""Diagnostics context for RAG pipeline telemetry.

Usage:
    ctx = DiagContext(query="用户问题")
    ctx.record("rewrite", original=..., rewritten=..., sub_questions=...)
    ctx.record("intent", kbs=...)
    # ... 各步骤 ...
    ctx.save()

    # SSE 结束后补写流指标
    ctx.update("stream", first_token_ms=..., total_tokens=..., total_ms=...)
    ctx.save()
"""

import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings


DIAG_DIR = Path(settings.diagnostics_dir)


class DiagContext:
    """Collects intermediate data from each RAG pipeline step and persists to JSON."""

    def __init__(self, query: str = "") -> None:
        self.id: str = self._generate_id()
        self.query: str = query
        self.conversation_id: str = ""
        self.steps: dict[str, Any] = {}
        self.errors: list[dict[str, Any]] = []
        self._start_time: float = time.time()
        self._saved: bool = False  # track first-save elapsed_ms

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, step: str, **data: Any) -> None:
        """Record data for a pipeline step (full replace)."""
        self.steps[step] = data

    def update(self, step: str, **data: Any) -> None:
        """Add / merge fields into an existing step without wiping existing data."""
        if step not in self.steps:
            self.steps[step] = {}
        self.steps[step].update(data)

    def track_error(
        self,
        step: str,
        error_type: str,
        message: str,
        *,
        retried: int = 0,
        degraded: bool = False,
    ) -> None:
        """Record a LLM error/retry/degradation event for diagnostics."""
        self.errors.append({
            "step": step,
            "type": error_type,
            "message": message,
            "retried": retried,
            "degraded": degraded,
            "elapsed_ms": round((time.time() - self._start_time) * 1000, 1),
        })

    def append(self, step: str, value: Any) -> None:
        """Append a value to a list-typed step (for multi-round calls).

        Retrieval may be called once per sub-question; each call appends
        a round of diagnostics so nothing is lost.
        """
        if step not in self.steps:
            self.steps[step] = []
        self.steps[step].append(value)

    def save(self) -> None:
        """Persist the diagnostic record as a JSON file and refresh the index."""
        record = self._build_record()
        path = self._ensure_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        self._update_index(record)
        self._saved = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_id() -> str:
        ts = datetime.now().strftime("%H%M%S")
        suffix = secrets.token_hex(3)  # 6 hex chars
        return f"{ts}-{suffix}"

    def _build_record(self) -> dict[str, Any]:
        elapsed = round((time.time() - self._start_time) * 1000, 1) if not self._saved else None
        record: dict[str, Any] = {
            "id": self.id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "query": self.query,
            "conversation_id": self.conversation_id,
            "steps": self.steps,
        }
        if self.errors:
            record["errors"] = self.errors
        if elapsed is not None:
            record["pipeline_elapsed_ms"] = elapsed
        return record

    def _ensure_path(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return DIAG_DIR / today / f"{self.id}.json"

    def _update_index(self, record: dict[str, Any]) -> None:
        index_path = DIAG_DIR / "index.json"

        # Build index entry
        entry: dict[str, Any] = {
            "id": record["id"],
            "timestamp": record["timestamp"],
            "query": record["query"],
            "conversation_id": record["conversation_id"],
            "pipeline_elapsed_ms": record.get("pipeline_elapsed_ms"),
        }

        # Extract KB names from intent step
        if (intent := self.steps.get("intent")) and intent.get("kbs"):
            entry["kbs"] = [kb.get("name", "") for kb in intent["kbs"] if kb.get("name")]

        # TopK count from topk step
        if (topk := self.steps.get("topk")) and topk.get("chunks"):
            entry["topk_count"] = len(topk["chunks"])

        # Steps present (for filtering)
        entry["steps"] = list(self.steps.keys())

        # Load or create index
        index: list[dict[str, Any]] = []
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except (json.JSONDecodeError, OSError):
                index = []

        # Deduplicate by id (stream updates shouldn't create duplicate entries)
        index = [e for e in index if e.get("id") != self.id]
        index.insert(0, entry)

        # Cap entries
        index = index[: settings.diagnostics_max_index]

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
