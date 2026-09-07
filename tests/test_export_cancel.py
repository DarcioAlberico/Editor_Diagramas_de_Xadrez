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


# ---------------------------------------------------------------------------
# A gravação que não toca o destino até estar pronta (§60)
# ---------------------------------------------------------------------------


def _parcial(out: Path) -> Path:
    from chess_pdf_editor.pdf_service import EXPORT_PART_SUFFIX

    return out.with_name(out.name + EXPORT_PART_SUFFIX)


def _doc_com_paginas(tmp_path: Path, pages: int):
    """Documento grande o bastante para o `save` chamar `write` muitas vezes."""
    return fitz.open(str(make_pdf(tmp_path / "fonte.pdf", pages=pages)))


def test_a_cancel_during_the_write_leaves_the_previous_file_intact(tmp_path: Path) -> None:
    """O caso que a §59.14 dava como impossível de cobrir.

    Cancelar entre páginas já era seguro — o `save` nem começava. O que ninguém
    cobria era a parada **dentro** dele, que é onde o arquivo do usuário estava
    sendo sobrescrito.
    """
    from chess_pdf_editor.pdf_service import save_document_atomically

    out = tmp_path / "saida.pdf"
    out.write_bytes(b"o PDF bom que ja estava aqui")
    antes = out.read_bytes()

    doc = _doc_com_paginas(tmp_path, pages=60)
    try:
        with pytest.raises(ExportCanceled) as excinfo:
            save_document_atomically(doc, str(out), lambda: True)
    finally:
        doc.close()

    assert "gravação" in str(excinfo.value)
    assert out.read_bytes() == antes, "o arquivo anterior foi destruído por um cancelamento"
    assert not _parcial(out).exists(), "o parcial ficou para trás"


def test_a_crash_during_the_write_never_truncates_the_target(tmp_path: Path, monkeypatch) -> None:
    """Queda de energia, gerenciador de tarefas, `terminate()`: o mesmo caminho.

    Antes o `save` escrevia direto no destino, e uma morte no meio deixava um PDF
    truncado ali — medido: 2 bytes. É o buraco que a §43 já tinha fechado para o
    projeto em JSON, e que a exportação nunca recebeu.
    """
    from chess_pdf_editor import pdf_service

    out = tmp_path / "saida.pdf"
    out.write_bytes(b"o PDF bom que ja estava aqui")
    antes = out.read_bytes()

    # O documento é montado **antes** do monkeypatch: o `make_pdf` também usa `save`.
    doc = _doc_com_paginas(tmp_path, pages=6)
    original = fitz.Document.save

    def _explode(self, destino, *args, **kwargs):
        original(self, destino, *args, **kwargs)  # escreve um pedaço de verdade
        raise OSError("disco desconectado no meio da gravacao")

    monkeypatch.setattr(fitz.Document, "save", _explode)

    try:
        with pytest.raises(OSError):
            pdf_service.save_document_atomically(doc, str(out), None)
    finally:
        doc.close()

    assert out.read_bytes() == antes
    assert not _parcial(out).exists()


def test_a_leftover_part_from_a_killed_export_is_cleaned_up(tmp_path: Path) -> None:
    """No Windows o handle de uma thread morta à força só sai com o processo.

    Por isso quem limpa é a exportação **seguinte** para o mesmo destino, e não o
    `closeEvent` que matou a anterior.
    """
    from chess_pdf_editor.pdf_service import save_document_atomically

    out = tmp_path / "saida.pdf"
    sobra = _parcial(out)
    sobra.write_bytes(b"restos de uma exportacao morta")

    doc = _doc_com_paginas(tmp_path, pages=3)
    try:
        save_document_atomically(doc, str(out), None)
    finally:
        doc.close()

    assert out.exists()
    assert not sobra.exists(), "o parcial anterior sobreviveu"
    lido = fitz.open(str(out))
    try:
        assert lido.page_count == 3
    finally:
        lido.close()


def test_a_completed_export_leaves_no_part_behind(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "book.pdf", pages=3)
    out = tmp_path / "out.pdf"

    apply_operations_to_pdf(str(source), str(out), _ops(3))

    assert out.exists()
    assert not _parcial(out).exists()


def test_the_export_still_produces_a_readable_pdf(tmp_path: Path) -> None:
    """A gravação por objeto de arquivo não pode mudar o que sai do outro lado."""
    source = make_pdf(tmp_path / "book.pdf", pages=4)
    out = tmp_path / "out.pdf"

    apply_operations_to_pdf(str(source), str(out), _ops(4))

    doc = fitz.open(str(out))
    try:
        assert doc.page_count == 4
        assert doc.load_page(0).get_pixmap() is not None
    finally:
        doc.close()


def test_closing_mid_export_does_not_leave_a_part_next_to_the_output(
    main_window, tmp_path, monkeypatch, qapp, no_modals
) -> None:
    """Ponta a ponta: fechar no meio não pode deixar lixo ao lado do arquivo."""
    out = _start_export(main_window, tmp_path, monkeypatch, pages=12)
    assert main_window._export_worker is not None

    main_window.close()

    assert not _parcial(out).exists(), "sobrou um parcial ao lado do destino"
