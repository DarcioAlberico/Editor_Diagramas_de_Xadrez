from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .types import EraseOperation, OverlayOperation, StudyPosition

SCHEMA_VERSION = 7
APP_VERSION = "0.1.0"


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file(path: str) -> dict[str, object]:
    p = Path(path)
    stat = p.stat()
    return {
        "path": str(p.resolve()),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": _sha256_file(p),
    }


@dataclass
class ProjectState:
    source_pdf: str
    source_pdf_fingerprint: dict[str, object]
    operations: list[OverlayOperation]
    erase_operations: list[EraseOperation] = field(default_factory=list)
    study_positions: list[StudyPosition] = field(default_factory=list)
    current_page: int = 0
    include_lichess_link: bool = True
    ocr_full_next_page: int = 0
    schema_version: int = SCHEMA_VERSION
    app_version: str = APP_VERSION


def save_project_state(path: str, state: ProjectState) -> None:
    payload = asdict(state)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_project_state(path: str) -> ProjectState:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    ops = [
        OverlayOperation(
            page_num=int(item["page_num"]),
            rect_pdf=tuple(item["rect_pdf"]),
            fen=str(item["fen"]),
            side_to_move=str(item.get("side_to_move", "w")),
            fullmove_number=max(1, int(item.get("fullmove_number", 1))),
            source=str(item.get("source", "manual")),
            confidence=item.get("confidence"),
            whiteout_padding_pt=float(item.get("whiteout_padding_pt", 0.5)),
            whiteout_padding_left_pt=float(item.get("whiteout_padding_left_pt", item.get("whiteout_padding_pt", 0.5))),
            whiteout_padding_top_pt=float(item.get("whiteout_padding_top_pt", item.get("whiteout_padding_pt", 0.5))),
            whiteout_padding_right_pt=float(item.get("whiteout_padding_right_pt", item.get("whiteout_padding_pt", 0.5))),
            whiteout_padding_bottom_pt=float(item.get("whiteout_padding_bottom_pt", item.get("whiteout_padding_pt", 0.5))),
            border_width_pt=float(item.get("border_width_pt", 0.0)),
        )
        for item in payload.get("operations", [])
    ]
    erase_ops = [
        EraseOperation(
            page_num=int(item["page_num"]),
            rect_pdf=tuple(item["rect_pdf"]),
        )
        for item in payload.get("erase_operations", [])
    ]
    study_positions = [
        StudyPosition(
            page_num=int(item["page_num"]),
            rect_pdf=tuple(item["rect_pdf"]),
            fen=str(item["fen"]),
            pgn=str(item.get("pgn", "")),
            comment_before=str(item.get("comment_before", item.get("note", ""))),
            comment_after=str(item.get("comment_after", "")),
            note=str(item.get("note", "")),
        )
        for item in payload.get("study_positions", [])
    ]
    return ProjectState(
        source_pdf=payload["source_pdf"],
        source_pdf_fingerprint=payload.get("source_pdf_fingerprint", {}),
        operations=ops,
        erase_operations=erase_ops,
        study_positions=study_positions,
        current_page=int(payload.get("current_page", 0)),
        include_lichess_link=bool(payload.get("include_lichess_link", True)),
        ocr_full_next_page=max(0, int(payload.get("ocr_full_next_page", 0))),
        schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        app_version=str(payload.get("app_version", APP_VERSION)),
    )
