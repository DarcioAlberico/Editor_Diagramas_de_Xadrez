"""Relatório de alterações em CSV/JSON (Sprint 6.4)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from chess_pdf_editor.report import (
    KIND_CANDIDATE,
    KIND_ERASE,
    KIND_OPERATION,
    build_rows,
    export_report,
    summarize,
)
from chess_pdf_editor.types import EraseOperation, OverlayOperation

FEN = "8/8/8/4k3/8/8/4K3/8"
BAD_FEN = "8/8/8/4k3/8/8/8/8"  # sem rei branco


def _operation(page=0, rect=(10.0, 20.0, 110.0, 120.0), fen=FEN, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=rect, fen=fen, **kwargs)


def test_a_row_carries_geometry_size_and_provenance() -> None:
    rows = build_rows(
        operations=[_operation(source="ocr-page", confidence=0.73, fullmove_number=12)]
    )
    (row,) = rows

    assert row.tipo == KIND_OPERATION
    assert row.pagina == 1, "a página sai 1-based, como o usuário vê"
    assert (row.largura_pt, row.altura_pt) == pytest.approx((100.0, 100.0))
    assert row.origem == "ocr-page"
    assert row.confianca == pytest.approx(0.73)
    assert row.numero_do_lance == 12


def test_validation_warnings_land_in_the_row() -> None:
    """É o que faz uma linha valer revisão."""
    (row,) = build_rows(operations=[_operation(fen=BAD_FEN)])
    assert row.avisos and any("rei" in aviso for aviso in row.avisos)


def test_an_unparseable_fen_becomes_a_warning_not_an_exception() -> None:
    """Um projeto com uma FEN quebrada ainda tem de conseguir exportar relatório."""
    (row,) = build_rows(operations=[_operation(fen="lixo")])
    assert len(row.avisos) == 1
    assert "FEN inválida" in row.avisos[0]


def test_the_three_kinds_appear_and_are_sorted_by_page_then_position() -> None:
    rows = build_rows(
        operations=[_operation(page=2, rect=(0.0, 500.0, 50.0, 550.0))],
        candidates=[_operation(page=0, rect=(0.0, 400.0, 50.0, 450.0))],
        erase_operations=[EraseOperation(page_num=0, rect_pdf=(0.0, 10.0, 20.0, 30.0))],
    )
    assert [row.tipo for row in rows] == [KIND_ERASE, KIND_CANDIDATE, KIND_OPERATION]
    assert [row.pagina for row in rows] == [1, 1, 3]


def test_erase_rows_have_no_fen_or_confidence() -> None:
    (row,) = build_rows(erase_operations=[EraseOperation(page_num=0, rect_pdf=(0.0, 0.0, 5.0, 5.0))])
    assert row.fen == ""
    assert row.confianca is None


def test_summary_counts_what_matters_for_review() -> None:
    resumo = summarize(
        build_rows(
            operations=[
                _operation(confidence=0.95),
                _operation(page=1, fen=BAD_FEN, confidence=0.42),
                _operation(page=1),
            ]
        )
    )
    assert resumo["total"] == 3
    assert resumo["substituicoes"] == 3
    assert resumo["com_avisos"] == 1
    assert resumo["confianca_minima"] == pytest.approx(0.42)
    assert resumo["sem_confianca"] == 1, "confiança ausente continua ausente"
    assert resumo["paginas"] == [1, 2]


def test_csv_has_a_bom_so_excel_reads_the_accents(tmp_path: Path) -> None:
    out = tmp_path / "relatorio.csv"
    export_report(str(out), operations=[_operation()])
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")


def test_csv_round_trips_through_a_reader(tmp_path: Path) -> None:
    out = tmp_path / "relatorio.csv"
    export_report(
        str(out),
        operations=[_operation(source="manual", confidence=0.5)],
        erase_operations=[EraseOperation(page_num=0, rect_pdf=(0.0, 0.0, 5.0, 5.0))],
    )
    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 2
    apagamento = next(row for row in rows if row["tipo"] == KIND_ERASE)
    assert apagamento["confianca"] == "", "célula vazia, não a string 'None'"
    assert apagamento["numero_do_lance"] == ""


def test_json_carries_the_summary_and_the_source(tmp_path: Path) -> None:
    out = tmp_path / "relatorio.json"
    export_report(
        str(out),
        operations=[_operation(confidence=0.9)],
        source_pdf="C:/livros/livro.pdf",
        extra={"motor": "hybrid"},
    )
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["source_pdf"] == "C:/livros/livro.pdf"
    assert payload["motor"] == "hybrid"
    assert payload["resumo"]["total"] == 1
    assert payload["alteracoes"][0]["confianca"] == pytest.approx(0.9)


def test_an_unknown_extension_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_report(str(tmp_path / "relatorio.txt"), operations=[_operation()])


# ---------------------------------------------------------------------------
# As colunas do CSV e os campos da linha (§45)
# ---------------------------------------------------------------------------


def test_the_csv_columns_match_the_row_fields_exactly() -> None:
    """`CSV_COLUMNS` é mantida à mão ao lado de `ReportRow`, e as duas divergem calado.

    Medido com o `csv.DictWriter` que o módulo usa:

    * campo novo sem coluna → `ValueError`, mas só na hora em que alguém exporta;
    * coluna sem campo → grava a coluna **vazia**, sem reclamar nada.

    O segundo é o pior: sobra uma coluna fantasma no relatório de todo mundo. Este
    teste traz as duas falhas para a suíte.

    A **ordem** também entra na comparação de propósito: o cabeçalho do módulo promete
    rótulos estáveis para quem faz diff entre dois relatórios, e reordenar colunas
    quebra esse diff tanto quanto renomear uma.
    """
    import dataclasses

    from chess_pdf_editor.report import CSV_COLUMNS, ReportRow

    assert tuple(field.name for field in dataclasses.fields(ReportRow)) == tuple(CSV_COLUMNS)
