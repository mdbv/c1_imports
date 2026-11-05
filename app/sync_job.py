import fcntl
import os
import re
import subprocess
from pathlib import Path

from starlette.exceptions import HTTPException

from app.config import LOCK_FILE, LOG_DIR, SYNC_SCRIPT

LOG_NAME = re.compile(r"sftp_sync_\d{8}_\d{6}\.log$")
_OUTCOME = ("[ERROR]", "completed in", "already in progress", "completed successfully")


def sync_state() -> tuple[bool, str]:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.lseek(fd, 0, os.SEEK_SET)
            pid = os.read(fd, 128).decode(errors="replace").strip()
            return True, pid
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False, ""
    finally:
        os.close(fd)


def start_sync() -> None:
    running, _pid = sync_state()
    if running:
        return
    subprocess.Popen(
        [SYNC_SCRIPT],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def list_logs() -> list[Path]:
    if not LOG_DIR.is_dir():
        return []
    files = [path for path in LOG_DIR.iterdir() if path.is_file() and LOG_NAME.match(path.name)]
    files.sort(key=lambda path: path.name, reverse=True)
    return files


def read_log(name: str) -> str:
    if not LOG_NAME.match(name):
        raise HTTPException(404)
    path = (LOG_DIR / name).resolve()
    if not path.is_file() or not path.is_relative_to(LOG_DIR.resolve()):
        raise HTTPException(404)
    data = path.read_bytes()
    if len(data) > 1_000_000:
        text = data[-1_000_000:].decode("utf-8", errors="replace")
        return "… showing the last 1 MB of this log …\n" + text
    return data.decode("utf-8", errors="replace")


def last_sync_summary() -> str:
    logs = list_logs()
    if not logs:
        return "No sync has run yet"
    lines = [line for line in read_log(logs[0].name).splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in _OUTCOME):
            return line
    return lines[-1] if lines else "Empty log"
