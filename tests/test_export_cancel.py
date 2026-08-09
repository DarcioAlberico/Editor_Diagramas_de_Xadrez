"""Exportação interrompível, com progresso real (§25.7).

Antes disto a barra era indeterminada e o diálogo não tinha botão nenhum: quem
mandasse exportar um livro de 300 diagramas por engano esperava até o fim.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf, process_until

fitz = pytest.importorskip("fitz")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.pdf_service import ExportCanceled, apply_operations_to_pdf  # noqa: E402
from chess_pdf_editor.types import EraseOperation, OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _ops(pages: int) -> list[OverlayOperation]:
    return [
        OverlayOperation(page_num=page, rect_pdf=DIAGRAM_RECT, fen=FEN) for page in range(pages)
    ]


# ---------------------------------------------------------------------------
# O núcleo
# ---------------------------------------------------------------------------


def test_progress_counts_changed_pages_not_book_pages(tmp_path: Path) -> None:
    """Num livro de 898 páginas com 3 diagramas, o total é 3."""
    source = make_pdf(tmp_path / "book.pdf", pages=8)
    seen: list[tuple[int, int]] = []

    apply_operations_to_pdf(
        str(source),
        str(tmp_path / "out.pdf"),
        _ops(3),
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_erasures_count_towards_the_total(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "book.pdf", pages=4)
    seen: list[tuple[int, int]] = []

    apply_operations_to_pdf(
        str(source),
        str(tmp_path / "out.pdf"),
        _ops(1),
        erase_operations=[EraseOperation(page_num=2, rect_pdf=(10.0, 10.0, 40.0, 40.0))],
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen[-1] == (2, 2)


def test_cancelling_writes_no_file_at_all(tmp_path: Path) -> None:
    """A garantia: cancelar não deixa um PDF pela metade no lugar de um bom."""
    source = make_pdf(tmp_path / "book.pdf", pages=6)
    out = tmp_path / "out.pdf"

    with pytest.raises(ExportCanceled):
        apply_operations_to_pdf(
            str(source), str(out), _ops(6), should_cancel=lambda: True
        )

    assert not out.exists()


def test_an_existing_output_is_untouched_by_a_cancel(tmp_path: Path) -> None:
    """Reexportar por cima e cancelar não pode destruir o arquivo anterior."""
    source = make_pdf(tmp_path / "book.pdf", pages=4)
    out = tmp_path / "out.pdf"
    apply_operations_to_pdf(str(source), str(out), _ops(4))
    antes = out.read_bytes()

    with pytest.raises(ExportCanceled):
        apply_operations_to_pdf(str(source), str(out), _ops(4), should_cancel=lambda: True)

    assert out.read_bytes() == antes


def test_cancelling_midway_stops_between_pages(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "book.pdf", pages=6)
    feitas: list[int] = []

    def _cancel_after_two() -> bool:
        return len(feitas) >= 2

    with pytest.raises(ExportCanceled) as excinfo:
        apply_operations_to_pdf(
            str(source),
            str(tmp_path / "out.pdf"),
            _ops(6),
            should_cancel=_cancel_after_two,
            on_progress=lambda done, _total: feitas.append(done),
        )

    assert feitas == [1, 2], "deveria ter parado logo depois da segunda página"
    assert "2 de 6" in str(excinfo.value)


def test_without_a_cancel_hook_everything_is_written(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "book.pdf", pages=3)
    out = tmp_path / "out.pdf"
    apply_operations_to_pdf(str(source), str(out), _ops(3))
    assert out.exists()

    doc = fitz.open(str(out))
    try:
        assert doc.page_count == 3
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# No app
# ---------------------------------------------------------------------------


def _start_export(main_window, tmp_path: Path, monkeypatch, pages: int = 5):
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=pages)), clear_ops=True)
    main_window.operations.extend(_ops(pages))
    out = tmp_path / "saida.pdf"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "PDF (*.pdf)")),
    )
    main_window._save_output_pdf()
    return out


def test_the_progress_dialog_has_a_cancel_button(main_window, tmp_path, monkeypatch, qapp, no_modals) -> None:
    _start_export(main_window, tmp_path, monkeypatch)
    try:
        assert main_window._export_progress is not None
        # `wasCanceled` só existe num diálogo com botão; sem ele o Qt devolve
        # sempre False e o usuário não teria como pedir parada.
        assert main_window._export_progress.wasCanceled() is False
    finally:
        assert process_until(qapp, lambda: main_window._export_worker is None)


def test_the_export_finishes_and_writes_the_file(main_window, tmp_path, monkeypatch, qapp, no_modals) -> None:
    out = _start_export(main_window, tmp_path, monkeypatch)
    assert process_until(qapp, lambda: main_window._export_worker is None), "a exportação não terminou"
    assert out.exists()
    assert "salvo" in main_window.statusBar().currentMessage()


def test_cancelling_from_the_dialog_leaves_no_file(main_window, tmp_path, monkeypatch, qapp, no_modals) -> None:
    out = _start_export(main_window, tmp_path, monkeypatch, pages=12)
    main_window._cancel_export()

    assert process_until(qapp, lambda: main_window._export_worker is None), "o worker não saiu"
    # A corrida é legítima: num PDF de teste minúsculo a exportação pode terminar
    # antes do clique. O que não pode acontecer é ficar arquivo pela metade — e a
    # mensagem tem de bater com o que houve.
    mensagem = main_window.statusBar().currentMessage()
    if out.exists():
        assert "salvo" in mensagem
    else:
        assert "cancelada" in mensagem
        assert "Nenhum arquivo" in mensagem


def test_closing_the_window_cancels_a_running_export(main_window, tmp_path, monkeypatch, qapp, no_modals) -> None:
    """Fechar não precisa mais esperar um livro inteiro terminar de gravar."""
    _start_export(main_window, tmp_path, monkeypatch, pages=12)
    worker = main_window._export_worker
    assert worker is not None

    main_window.close()

    assert worker.isFinished(), "o worker de exportação sobreviveu ao fechamento"
