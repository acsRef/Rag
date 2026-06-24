"""Centralized logging setup: rotating file + console handlers, level from settings.

调用方约定:
  - 各模块用 `logger = logging.getLogger(__name__)`(已约定俗成)
  - 本模块在 FastAPI startup 钩子中**只调一次** `setup_logging()`
  - 文件输出 + stderr 输出**共用同一 formatter**
  - 不接管 uvicorn 的 logger(由 uvicorn 自行管理)

日志格式:`时间 [LEVEL][module]: 消息`
  例如:`2026-06-24 20:30:15 [INFO][app.ingestion.indexer]: ingest.chunked sections=5 chunks=42 elapsed_ms=85.3`

文件切分策略:
  - 文件名带启动日期 `ragent-YYYY-MM-DD.log`
  - 单文件超过 `log_max_bytes` 自动切到 `.1/.2/...`
  - 保留 `log_backup_count` 个 backup
  - **重启后**,新一天的日志会写到新文件,旧文件保留作为历史

注:Python 3.11 的 `TimedRotatingFileHandler` 不支持 `maxBytes`(Python 3.13 才支持),
本实现改用 `RotatingFileHandler` + 启动时绑定日期,行为上等价于"按大小切,文件名带日期"。
"""
import logging
from logging.handlers import RotatingFileHandler
from datetime import date
from pathlib import Path

from app.config import settings

# 行格式:asctime(秒级) + level + 模块名 + 消息
_FMT = "%(asctime)s [%(levelname)s][%(name)s]: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """配置 root logger:一个按大小轮转的文件 handler + 一个 stderr handler。

    行为:
      - 在 `settings.log_dir` 下创建目录(不存在则建)
      - 文件名 `ragent-YYYY-MM-DD.log`,单文件超 `log_max_bytes` 切到 `.1/.2/...`
      - 保留 `log_backup_count` 个 backup 文件(同一天内)
      - **每次启动清空 root 的已有 handlers** — 防止 uvicorn reload 重复挂载,以及覆盖默认 WARNING 配置
      - root level 取自 `settings.log_level`(.env 可覆盖,例如 `LOG_LEVEL=DEBUG`)
    """
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    # 清空 root 已有 handlers(防 uvicorn reload / 多次 setup 重复挂载)
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # 文件名带日期:满足"用时间区分"诉求
    # 同一天内按 `log_max_bytes` 切分:满足"单个日志不要太大"诉求
    log_file = log_dir / f"ragent-{date.today():%Y-%m-%d}.log"
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if settings.log_to_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

