"""Relatório de alterações (Sprint 6.4).

O projeto salvo (`project_state.json`) é o estado de trabalho do app: bom para o app
reabrir, ruim para responder "quais diagramas deste livro o OCR leu com confiança baixa?"
ou "o que mudou entre o processamento de ontem e o de hoje?".

Este módulo despeja a mesma informação em duas formas que ferramentas de fora entendem:
**CSV** para abrir na planilha e ordenar por confiança, **JSON** para diferenciar dois
processamentos com qualquer utilitário.

Uma linha por alteração, com as três coisas que faltavam para auditar:

* a **origem** (`manual`, `ocr-selecao`, `ocr-page`, `local`, `hybrid`…), que diz se um
  humano olhou aquilo;
* a **confiança** que o motor reportou, quando reportou — vazio continua sendo vazio,
  inventar número seria pior;
* os **avisos** de validação da FEN, que é o que faz uma linha valer revisão.

A geometria sai em pontos PDF *e* em tamanho (largura × altura): um diagrama com 2 pt de
diferença entre os lados é o sintoma de bbox torta, e isso não se enxerga lendo x0/x1.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import legality
from .fen import validate_piece_placement
from .logging_config import get_logger
from .types import EraseOperation, OverlayOperation

logger = get_logger("report")

CSV_COLUMNS = (
    "tipo",
    "pagina",
    "x0_pt",
    "y0_pt",
    "x1_pt",
    "y1_pt",
    "largura_pt",
    "altura_pt",
    "fen",
    "lado_a_jogar",
    "numero_do_lance",
    "origem",
    "confianca",
    "avisos",
)

#: Rótulos do campo `tipo`, estáveis para quem faz diff entre relatórios.
KIND_OPERATION = "substituicao"
KIND_CANDIDATE = "candidato"
KIND_ERASE = "apagamento"


@dataclass
class ReportRow:
    tipo: str
    pagina: int
    x0_pt: float
    y0_pt: float
    x1_pt: float
    y1_pt: float
    largura_pt: float
    altura_pt: float
    fen: str = ""
    lado_a_jogar: str = ""
    numero_do_lance: Optional[int] = None
    origem: str = ""
    confianca: Optional[float] = None
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "tipo": self.tipo,
            "pagina": self.pagina,
            "x0_pt": round(self.x0_pt, 3),
            "y0_pt": round(self.y0_pt, 3),
            "x1_pt": round(self.x1_pt, 3),
            "y1_pt": round(self.y1_pt, 3),
            "largura_pt": round(self.largura_pt, 3),
            "altura_pt": round(self.altura_pt, 3),
            "fen": self.fen,
            "lado_a_jogar": self.lado_a_jogar,
            "numero_do_lance": self.numero_do_lance,
            "origem": self.origem,
            "confianca": None if self.confianca is None else round(float(self.confianca), 4),
            "avisos": list(self.avisos),
        }

    def as_csv_row(self) -> dict[str, object]:
        data = self.as_dict()
        # O CSV é lido em planilha: lista vira texto, `None` vira célula vazia (e não a
        # string "None", que ordenaria junto com os números).
        data["avisos"] = " | ".join(self.avisos)
        data["confianca"] = "" if self.confianca is None else data["confianca"]
        data["numero_do_lance"] = "" if self.numero_do_lance is None else self.numero_do_lance
        return data


def _fen_warnings(piece_placement: str, side_to_move: str = "w") -> list[str]:
    """Avisos da escrita da FEN e da legalidade da posição.

    Os dois vão para a mesma coluna `avisos`, e não para uma coluna nova, porque os
    rótulos do CSV são estáveis para quem faz diff entre dois relatórios. Os achados
    de legalidade vêm prefixados (`impossível:` / `suspeita:`), o que dá para
    filtrar na planilha.
    """
    try:
        warnings = list(validate_piece_placement(piece_placement))
    except ValueError as exc:
        return [f"FEN inválida: {exc}"]
    return warnings + legality.labels(
        legality.audit(piece_placement, side_to_move), skip_codes=legality.LEGACY_CODES
    )


def _row_from_operation(op: OverlayOperation, tipo: str) -> ReportRow:
    x0, y0, x1, y1 = (float(v) for v in op.rect_pdf)
    return ReportRow(
        tipo=tipo,
        # 1-based: é o número que o usuário vê na barra de ferramentas e no leitor de PDF.
        pagina=int(op.page_num) + 1,
        x0_pt=x0,
        y0_pt=y0,
        x1_pt=x1,
        y1_pt=y1,
        largura_pt=x1 - x0,
        altura_pt=y1 - y0,
        fen=str(op.fen),
        lado_a_jogar=str(getattr(op, "side_to_move", "w")),
        numero_do_lance=int(getattr(op, "fullmove_number", 1)),
        origem=str(getattr(op, "source", "")),
        confianca=getattr(op, "confidence", None),
        avisos=_fen_warnings(str(op.fen), str(getattr(op, "side_to_move", "w"))),
    )


def _row_from_erase(op: EraseOperation) -> ReportRow:
    x0, y0, x1, y1 = (float(v) for v in op.rect_pdf)
    return ReportRow(
        tipo=KIND_ERASE,
        pagina=int(op.page_num) + 1,
        x0_pt=x0,
        y0_pt=y0,
        x1_pt=x1,
        y1_pt=y1,
        largura_pt=x1 - x0,
        altura_pt=y1 - y0,
    )


def build_rows(
    operations: Sequence[OverlayOperation] = (),
    erase_operations: Sequence[EraseOperation] = (),
    candidates: Sequence[OverlayOperation] = (),
) -> list[ReportRow]:
    """Uma linha por alteração, ordenada por página e depois por posição na página."""
    rows = [_row_from_operation(op, KIND_OPERATION) for op in operations]
    rows += [_row_from_operation(op, KIND_CANDIDATE) for op in candidates]
    rows += [_row_from_erase(op) for op in erase_operations]
    rows.sort(key=lambda row: (row.pagina, row.y0_pt, row.x0_pt, row.tipo))
    return rows


def summarize(rows: Iterable[ReportRow]) -> dict[str, object]:
    rows = list(rows)
    confidences = [row.confianca for row in rows if row.confianca is not None]
    return {
        "total": len(rows),
        "substituicoes": sum(1 for row in rows if row.tipo == KIND_OPERATION),
        "candidatos": sum(1 for row in rows if row.tipo == KIND_CANDIDATE),
        "apagamentos": sum(1 for row in rows if row.tipo == KIND_ERASE),
        "com_avisos": sum(1 for row in rows if row.avisos),
        "paginas": sorted({row.pagina for row in rows}),
        "confianca_minima": min(confidences) if confidences else None,
        "confianca_media": (sum(confidences) / len(confidences)) if confidences else None,
        "sem_confianca": sum(
            1 for row in rows if row.confianca is None and row.tipo != KIND_ERASE
        ),
    }


def write_csv(path: str, rows: Sequence[ReportRow]) -> None:
    # `utf-8-sig`: sem o BOM o Excel em português abre "substituição" como "substituiÃ§Ã£o".
    # `newline=""` é o que o csv exige para não gravar linha em branco entre registros.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())


def write_json(
    path: str,
    rows: Sequence[ReportRow],
    source_pdf: Optional[str] = None,
    extra: Optional[dict[str, object]] = None,
) -> None:
    payload = {
        "source_pdf": source_pdf or "",
        "resumo": summarize(rows),
        "alteracoes": [row.as_dict() for row in rows],
    }
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def export_report(
    path: str,
    operations: Sequence[OverlayOperation] = (),
    erase_operations: Sequence[EraseOperation] = (),
    candidates: Sequence[OverlayOperation] = (),
    source_pdf: Optional[str] = None,
    extra: Optional[dict[str, object]] = None,
) -> list[ReportRow]:
    """Grava o relatório; o formato vem da extensão do arquivo (`.csv` ou `.json`)."""
    rows = build_rows(operations, erase_operations, candidates)
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        write_json(path, rows, source_pdf=source_pdf, extra=extra)
    elif suffix == ".csv":
        write_csv(path, rows)
    else:
        raise ValueError(f"Formato de relatório não suportado: {suffix or '(sem extensão)'}")
    logger.info("Relatório gravado: %s (%d linha(s))", path, len(rows))
    return rows
