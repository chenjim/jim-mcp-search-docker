"""结构化请求记录：每次 search / scrape 追加一行 JSONL。

按天分文件（``search-YYYYMMDD.jsonl`` / ``scrape-YYYYMMDD.jsonl``），
落在 ``JIM_SEARCH_LOG_DIR``（容器内 ``/app/logs``，宿主 ``./logs``）；
该变量为空时整体关闭，测试环境不会落盘。

写盘失败只记 warning，绝不影响请求返回。保留天数由
``JIM_SEARCH_LOG_RETENTION_DAYS`` 控制，进程每天首次写入时清理过期文件。
"""
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Optional

from src import config

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PRUNED_DAY: Optional[str] = None
_FILE_RE = re.compile(r"^(?:search|scrape)-(\d{8})\.jsonl$")


def _ensure_dir() -> Optional[str]:
    directory = config.LOG_DIR
    if not directory:
        return None
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create log dir %s: %s", directory, exc)
        return None
    return directory


def _prune(directory: str, today: date) -> None:
    cutoff = today - timedelta(days=max(1, config.LOG_RETENTION_DAYS))
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        match = _FILE_RE.match(name)
        if not match:
            continue
        try:
            day = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass


def record(kind: str, payload: dict) -> None:
    """Append one JSON line; silently no-ops when logging is disabled."""
    global _PRUNED_DAY
    directory = _ensure_dir()
    if not directory:
        return

    now = datetime.now()
    day = now.date()
    path = os.path.join(directory, f"{kind}-{day:%Y%m%d}.jsonl")
    line = json.dumps({"ts": now.isoformat(timespec="milliseconds"), **payload}, ensure_ascii=False)

    with _LOCK:
        if _PRUNED_DAY != day.isoformat():
            _prune(directory, day)
            _PRUNED_DAY = day.isoformat()
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("Cannot append to %s: %s", path, exc)
