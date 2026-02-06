from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import appdirs


@dataclass
class SessionInfo:
    session_id: str
    binary_name: str
    created_at: datetime
    last_accessed: datetime


def get_sessions_dir() -> Path:
    sessions_dir = Path(appdirs.user_data_dir("r2agent")) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_session_path(session_id: str) -> Path:
    return get_sessions_dir() / f"{session_id}.db"


def generate_session_id(binary_name: str) -> str:
    safe_name = binary_name.replace("/", "_").replace("\\", "_")
    if not safe_name:
        safe_name = "unknown"

    short_uuid = uuid.uuid4().hex[:8]

    return f"{safe_name}_{short_uuid}"


def parse_session_id(session_id: str) -> tuple[str, str]:
    if "_" not in session_id:
        raise ValueError(f"Invalid session ID format: {session_id}")

    binary_name, short_uuid = session_id.rsplit("_", 1)
    return binary_name, short_uuid


def list_sessions(binary_name: str | None = None) -> list[SessionInfo]:
    sessions_dir = get_sessions_dir()

    if binary_name:
        safe_name = binary_name.replace("/", "_").replace("\\", "_")
        pattern = f"{safe_name}_*.db"
    else:
        pattern = "*.db"

    sessions: list[SessionInfo] = []

    for db_path in sessions_dir.glob(pattern):
        session_id = db_path.stem

        try:
            binary, _ = parse_session_id(session_id)
        except ValueError:
            continue

        stat = db_path.stat()
        sessions.append(
            SessionInfo(
                session_id=session_id,
                binary_name=binary,
                created_at=datetime.fromtimestamp(stat.st_ctime),
                last_accessed=datetime.fromtimestamp(stat.st_mtime),
            )
        )

    sessions.sort(key=lambda s: s.last_accessed, reverse=True)
    return sessions


def session_exists(session_id: str) -> bool:
    return get_session_path(session_id).exists()


def delete_session(session_id: str) -> bool:
    path = get_session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False


def touch_session(session_id: str) -> None:
    path = get_session_path(session_id)
    if path.exists():
        path.touch()
