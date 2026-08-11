from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .migrations import (
    CURRENT_SCHEMA_VERSION,
    MigrationReport,
    ProjectSchemaError,
    migrate_payload,
)
from .types import EraseOperation, OverlayOperation, StudyPosition

# O número da versão e as regras de migração vivem em `migrations.py`; aqui ele é
# só reexportado para não quebrar quem já importava daqui.
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
APP_VERSION = "0.1.0"

__all__ = [
    "APP_VERSION",
    "SCHEMA_VERSION",
    "MigrationReport",
    "ProjectSchemaError",
    "ProjectState",
    "fingerprint_file",
    "load_project_state",
    "load_project_state_with_report",
    "save_project_state",
]


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
    # Deteccoes do OCR aguardando conferencia; viram `operations` ao serem aplicadas.
    candidates: list[OverlayOperation] = field(default_factory=list)
    current_page: int = 0
    include_lichess_link: bool = True
    # Apagar as coordenadas (a-h/1-8) que o diagrama original deixou em volta.
    erase_coordinates: bool = False
    ocr_full_next_page: int = 0
    schema_version: int = SCHEMA_VERSION
    app_version: str = APP_VERSION


def save_project_state(path: str, state: ProjectState) -> None:
    payload = asdict(state)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _load_study_move_comments(item: dict[str, object]) -> dict[str, dict[str, str]]:
    raw_comments = item.get("move_comments", {})
    if not isinstance(raw_comments, dict):
        raw_comments = {}
    comments = {
        str(ply): {
            "before": str(values.get("before", "")),
            "after": str(values.get("after", "")),
        }
        for ply, values in raw_comments.items()
        if isinstance(values, dict)
    }
    if not comments:
        before = str(item.get("comment_before", item.get("note", "")))
        after = str(item.get("comment_after", ""))
        if before.strip() or after.strip():
            comments["0"] = {"before": before, "after": after}
    return comments


def _optional_bool(value: object) -> Optional[bool]:
    """`None` continua `None`; o resto vira bool.

    `bool(None)` seria `False`, e `False` aqui não é "sem opinião" — é "não quero o
    link **neste** diagrama". Confundir os dois transformaria todo projeto antigo
    numa recusa explícita, imune à opção global.
    """
    if value is None:
        return None
    return bool(value)


def _load_operation(item: dict[str, object]) -> OverlayOperation:
    default_padding = float(item.get("whiteout_padding_pt", 0.5))
    return OverlayOperation(
        page_num=int(item["page_num"]),
        rect_pdf=tuple(item["rect_pdf"]),
        fen=str(item["fen"]),
        side_to_move=str(item.get("side_to_move", "w")),
        fullmove_number=max(1, int(item.get("fullmove_number", 1))),
        source=str(item.get("source", "manual")),
        confidence=item.get("confidence"),
        whiteout_padding_pt=default_padding,
        whiteout_padding_left_pt=float(item.get("whiteout_padding_left_pt", default_padding)),
        whiteout_padding_top_pt=float(item.get("whiteout_padding_top_pt", default_padding)),
        whiteout_padding_right_pt=float(item.get("whiteout_padding_right_pt", default_padding)),
        whiteout_padding_bottom_pt=float(item.get("whiteout_padding_bottom_pt", default_padding)),
        border_width_pt=float(item.get("border_width_pt", 0.0)),
        # Ausente = `None` = segue a global, que é exatamente como o projeto se
        # comportava antes de o campo existir (§52.1).
        include_lichess_link=_optional_bool(item.get("include_lichess_link")),
    )


def load_project_state(path: str) -> ProjectState:
    """Carrega o projeto, migrando o formato se necessário.

    Levanta `ProjectSchemaError` se o arquivo vier de uma versão mais nova do app.
    """
    return load_project_state_with_report(path)[0]


def load_project_state_with_report(path: str) -> tuple[ProjectState, MigrationReport]:
    """Como `load_project_state`, mas diz também o que a migração fez.

    A GUI usa o relatório para avisar o usuário de que o arquivo dele foi
    atualizado — o próximo salvamento grava no formato novo, e isso não pode ser
    uma surpresa.
    """
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    payload, report = migrate_payload(payload)

    ops = [_load_operation(item) for item in payload.get("operations", [])]
    candidates = [_load_operation(item) for item in payload.get("candidates", [])]
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
            side_to_move=str(item.get("side_to_move", "w")),
            fullmove_number=max(1, int(item.get("fullmove_number", 1))),
            pgn=str(item.get("pgn", "")),
            comment_before=str(item.get("comment_before", item.get("note", ""))),
            comment_after=str(item.get("comment_after", "")),
            move_comments=_load_study_move_comments(item),
            note=str(item.get("note", "")),
        )
        for item in payload.get("study_positions", [])
    ]
    state = ProjectState(
        source_pdf=payload["source_pdf"],
        source_pdf_fingerprint=payload.get("source_pdf_fingerprint", {}),
        operations=ops,
        erase_operations=erase_ops,
        study_positions=study_positions,
        candidates=candidates,
        current_page=int(payload.get("current_page", 0)),
        include_lichess_link=bool(payload.get("include_lichess_link", True)),
        erase_coordinates=bool(payload.get("erase_coordinates", False)),
        ocr_full_next_page=max(0, int(payload.get("ocr_full_next_page", 0))),
        # Sempre a versão corrente: `migrate_payload` já levou o conteúdo até ela,
        # e gravar o número antigo faria a próxima abertura migrar de novo.
        schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        app_version=str(payload.get("app_version", APP_VERSION)),
    )
    return state, report
