import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from starlette.exceptions import HTTPException

from app.config import LOCAL_DIR


@dataclass
class Entry:
    name: str
    rel: str
    is_dir: bool
    size: int | None
    created: datetime


def root() -> Path:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_DIR.resolve()


def resolve_inside(rel: str) -> Path:
    if rel.startswith("/") or "\x00" in rel:
        raise HTTPException(404)
    base = root()
    candidate = (base / rel).resolve()
    if not candidate.is_relative_to(base):
        raise HTTPException(404)
    return candidate


def _created(path: Path) -> datetime:
    # SFTP preserves this timestamp through the mirror. It is the file's own
    # date, stored as an absolute instant.
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _entry(path: Path, base: Path) -> Entry:
    rel = path.relative_to(base).as_posix()
    is_dir = path.is_dir()
    return Entry(
        name=path.name,
        rel=rel,
        is_dir=is_dir,
        size=None if is_dir else path.stat().st_size,
        created=_created(path),
    )


def list_dir(rel: str) -> tuple[Path, list[Entry]]:
    base = root()
    current = resolve_inside(rel) if rel else base
    if not current.exists() or not current.is_dir():
        raise HTTPException(404)
    entries = [_entry(path, base) for path in current.iterdir()]
    entries.sort(key=lambda item: (not item.is_dir, item.name.casefold()))
    return current, entries


def search_files(query: str, limit: int = 500) -> tuple[list[Entry], bool]:
    base = root()
    needle = query.casefold()
    matches: list[Entry] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        current = Path(dirpath).resolve()
        if not current.is_relative_to(base):
            dirnames.clear()
            continue
        dirnames[:] = sorted(
            (name for name in dirnames if not (current / name).is_symlink()),
            key=str.casefold,
        )
        for name in sorted(filenames, key=str.casefold):
            if needle not in name.casefold():
                continue
            path = current / name
            if path.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(base) or not resolved.is_file():
                    continue
            matches.append(_entry(path, base))
            if len(matches) >= limit:
                return matches, True
    return matches, truncated
