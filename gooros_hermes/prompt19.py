from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PROMPT19_SQLITE_READONLY_MARKER = "file:...?mode=ro + PRAGMA query_only=1"
STATE_TIMESTAMP_FORMAT = "Unix REAL seconds"
KANBAN_TIMESTAMP_FORMAT = "Unix INTEGER seconds"
GATEWAY_TIMESTAMP_FORMAT = "ISO-8601 string; start_time is monotonic, use updated_at for wall-clock time"


def quote_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("bad SQLite identifier")
    return '"' + value.replace('"', '""') + '"'


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def is_timestamp_column(name: str) -> bool:
    lowered = name.lower()
    if lowered.endswith("_at") or lowered in {"timestamp", "time", "start_time"}:
        return True
    return any(token in lowered for token in ("created", "updated", "started", "finished", "completed", "last_run", "next_run"))


def _sample_column(conn: sqlite3.Connection, table: str, column: str) -> Any:
    qtable = quote_identifier(table)
    qcolumn = quote_identifier(column)
    row = conn.execute(f"SELECT {qcolumn} AS value FROM {qtable} WHERE {qcolumn} IS NOT NULL LIMIT 1").fetchone()
    return row["value"] if row else None


def discover_sqlite(path: Path, expected_timestamp_format: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "read_mode": PROMPT19_SQLITE_READONLY_MARKER,
        "expected_timestamp_format": expected_timestamp_format,
        "tables": [],
        "timestamp_samples": [],
    }
    if not path.exists():
        return report
    conn: sqlite3.Connection | None = None
    try:
        conn = connect_readonly(path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for table_row in tables:
            table = str(table_row["name"])
            qtable = quote_identifier(table)
            columns = [dict(row) for row in conn.execute(f"PRAGMA table_info({qtable})")]
            row_count = conn.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()[0]
            report["tables"].append(
                {
                    "name": table,
                    "row_count": row_count,
                    "columns": [
                        {
                            "name": str(col["name"]),
                            "type": str(col["type"] or ""),
                            "notnull": bool(col["notnull"]),
                            "primary_key": bool(col["pk"]),
                        }
                        for col in columns
                    ],
                }
            )
            for col in columns:
                name = str(col["name"])
                if is_timestamp_column(name):
                    report["timestamp_samples"].append(
                        {
                            "table": table,
                            "column": name,
                            "declared_type": str(col["type"] or ""),
                            "sample": _sample_column(conn, table, name),
                            "expected_format": expected_timestamp_format,
                        }
                    )
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return report


def _summarize_json(value: Any, depth: int = 0) -> dict[str, Any]:
    if isinstance(value, dict):
        summary: dict[str, Any] = {"type": "object", "keys": sorted(str(k) for k in value.keys())}
        if depth < 2:
            summary["children"] = {str(k): _summarize_json(v, depth + 1) for k, v in value.items()}
        return summary
    if isinstance(value, list):
        summary = {"type": "array", "length": len(value)}
        if value and depth < 2:
            summary["item"] = _summarize_json(value[0], depth + 1)
        return summary
    return {"type": type(value).__name__, "sample": value}


def _json_timestamp_samples(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if lowered.endswith("_at") or "time" in lowered:
                note = "monotonic counter, not wall-clock" if lowered == "start_time" else GATEWAY_TIMESTAMP_FORMAT
                samples.append({"path": path, "sample": item, "expected_format": note})
            samples.extend(_json_timestamp_samples(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value[:3]):
            samples.extend(_json_timestamp_samples(item, f"{prefix}[{index}]"))
    return samples


def discover_gateway_state(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "expected_timestamp_format": GATEWAY_TIMESTAMP_FORMAT,
        "structure": {},
        "timestamp_samples": [],
    }
    if not path.exists():
        return report
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        report["structure"] = _summarize_json(data)
        report["timestamp_samples"] = _json_timestamp_samples(data)
        if isinstance(data, dict):
            report["updated_at"] = data.get("updated_at")
            report["start_time"] = data.get("start_time")
    except Exception as exc:
        report["error"] = str(exc)
    return report


def discover_prompt19_sources(hermes_home: Path) -> dict[str, Any]:
    home = hermes_home.expanduser().resolve()
    return {
        "prompt": "Prompt 19 - Hermes data source discovery",
        "hermes_home": str(home),
        "read_only": True,
        "files_created": False,
        "sqlite_read_mode": PROMPT19_SQLITE_READONLY_MARKER,
        "state_db": discover_sqlite(home / "state.db", STATE_TIMESTAMP_FORMAT),
        "kanban_db": discover_sqlite(home / "kanban.db", KANBAN_TIMESTAMP_FORMAT),
        "gateway_state": discover_gateway_state(home / "gateway_state.json"),
    }


def render_prompt19_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Prompt 19 - Hermes data source discovery",
        "",
        f"- HERMES_HOME: `{report['hermes_home']}`",
        f"- Read-only: `{report['read_only']}`",
        f"- Files created by this command: `{report['files_created']}`",
        f"- SQLite mode: `{report['sqlite_read_mode']}`",
        "",
    ]
    for key, title in (("state_db", "state.db"), ("kanban_db", "kanban.db")):
        db = report[key]
        lines.extend([f"## {title}", f"- Path: `{db['path']}`", f"- Exists: `{db['exists']}`"])
        if db.get("error"):
            lines.append(f"- Error: `{db['error']}`")
        for table in db.get("tables", []):
            cols = ", ".join(f"{col['name']}:{col['type'] or 'ANY'}" for col in table.get("columns", []))
            lines.append(f"- Table `{table['name']}`: {table['row_count']} rows; columns: {cols}")
        if db.get("timestamp_samples"):
            lines.append("- Timestamp samples:")
            for sample in db["timestamp_samples"]:
                lines.append(
                    f"  - `{sample['table']}.{sample['column']}` ({sample['declared_type'] or 'ANY'}): "
                    f"`{sample['sample']}` -> {sample['expected_format']}"
                )
        else:
            lines.append("- Timestamp samples: none found")
        lines.append("")
    gateway = report["gateway_state"]
    lines.extend(
        [
            "## gateway_state.json",
            f"- Path: `{gateway['path']}`",
            f"- Exists: `{gateway['exists']}`",
            f"- Expected timestamps: {gateway['expected_timestamp_format']}",
        ]
    )
    if gateway.get("error"):
        lines.append(f"- Error: `{gateway['error']}`")
    structure = gateway.get("structure") or {}
    if structure:
        lines.append(f"- Structure: `{json.dumps(structure, ensure_ascii=False)}`")
    if gateway.get("timestamp_samples"):
        lines.append("- Timestamp samples:")
        for sample in gateway["timestamp_samples"]:
            lines.append(f"  - `{sample['path']}`: `{sample['sample']}` -> {sample['expected_format']}")
    else:
        lines.append("- Timestamp samples: none found")
    return "\n".join(lines).rstrip() + "\n"
