"""No app real: motor de reconhecimento, aviso de privacidade e as ações do Sprint 6.

Qt offscreen. O que estes testes protegem é o contrato com o usuário: o modo local não
pode vazar nada para a rede, e o modo remoto não pode enviar o livro sem perguntar.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf, process_until

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor import local_ocr  # noqa: E402
from chess_pdf_editor.recognition import (  # noqa: E402
    ENGINE_HYBRID,
    ENGINE_LOCAL,
    ENGINE_REMOTE,
)
from chess_pdf_editor.types import OcrPrediction, OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _open(window, tmp_path: Path, pages: int = 2):
    window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=pages)), clear_ops=True)
    return window


def _set_mode(window, mode: str) -> None:
    window.engine_combo.setCurrentIndex(window.engine_combo.findData(mode))


def _forbid_recognition(monkeypatch):
    """Motor que explode se alguém chegar a reconhecer. Prova que foi cancelado antes."""
    from chess_pdf_editor import app as app_module

    def _boom(*args, **kwargs):
        raise AssertionError("o reconhecimento rodou apesar do cancelamento")

    monkeypatch.setattr(app_module, "make_engine", _boom)


def _stub_engine(monkeypatch, module, calls: list):
    class _Engine:
        name = "stub"

        def uses_network(self) -> bool:
            return False

        def predict(self, image_png, filename="board.png", assume_whole_image=False):
            calls.append(filename)
            return OcrPrediction(request_id="stub", status=204, message=None, results=[])

    monkeypatch.setattr(module, "make_engine", lambda *a, **k: _Engine())


# ---------------------------------------------------------------------------
# Aviso de privacidade (§7.3)
# ---------------------------------------------------------------------------


def test_the_remote_mode_asks_before_sending_the_book(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    _open(main_window, tmp_path, pages=5)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_REMOTE)
    privacy_prompt.answer = QtWidgets.QMessageBox.Cancel
    _forbid_recognition(monkeypatch)

    main_window._recognize_full_pdf()

    assert privacy_prompt.shown, "nenhum aviso foi exibido"
    aviso = privacy_prompt.shown[0]
    assert "5 página(s)" in aviso, "o aviso tem de dizer quantas páginas"
    assert "helpman" in aviso or "predict" in aviso, "o aviso tem de nomear o destino"
    assert main_window._ocr_worker is None, "o lote começou mesmo cancelado"


def test_cancelling_the_warning_recognizes_nothing(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    _open(main_window, tmp_path)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_REMOTE)
    privacy_prompt.answer = QtWidgets.QMessageBox.Cancel
    _forbid_recognition(monkeypatch)

    main_window._recognize_current_page()

    assert main_window.candidates == []
    assert main_window.operations == []


def test_accepting_once_with_the_checkbox_never_asks_again(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    """Repetir o aviso a cada página o transformaria em ruído que ninguém lê."""
    from chess_pdf_editor import app as app_module

    _open(main_window, tmp_path)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_REMOTE)
    privacy_prompt.answer = QtWidgets.QMessageBox.Yes
    privacy_prompt.remember = True
    calls: list[str] = []
    _stub_engine(monkeypatch, app_module, calls)

    main_window._recognize_current_page()
    main_window._recognize_current_page()

    assert len(privacy_prompt.shown) == 1, "o aviso voltou depois do 'não perguntar'"
    assert len(calls) == 2, "as duas execuções tinham de acontecer"
    assert bool(main_window.settings.value("remote_privacy_ack", False, bool))


def test_accepting_without_the_checkbox_asks_again(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    from chess_pdf_editor import app as app_module

    _open(main_window, tmp_path)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_REMOTE)
    privacy_prompt.answer = QtWidgets.QMessageBox.Yes
    privacy_prompt.remember = False
    _stub_engine(monkeypatch, app_module, [])

    main_window._recognize_current_page()
    main_window._recognize_current_page()

    assert len(privacy_prompt.shown) == 2


def test_the_local_mode_never_asks(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    """Nada sai da máquina, então não há o que perguntar."""
    from chess_pdf_editor import app as app_module

    _open(main_window, tmp_path)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_LOCAL)
    _stub_engine(monkeypatch, app_module, [])

    main_window._recognize_current_page()

    assert privacy_prompt.shown == []


def test_the_hybrid_mode_asks_because_it_may_send(
    main_window, tmp_path, monkeypatch, privacy_prompt, no_modals
) -> None:
    """O aviso descreve o que *pode* acontecer, não a média."""
    _open(main_window, tmp_path, pages=3)
    main_window.settings.setValue("remote_privacy_ack", False)
    _set_mode(main_window, ENGINE_HYBRID)
    privacy_prompt.answer = QtWidgets.QMessageBox.Cancel
    _forbid_recognition(monkeypatch)

    main_window._recognize_full_pdf()

    assert privacy_prompt.shown
    assert "podem ser enviadas" in privacy_prompt.shown[0]


# ---------------------------------------------------------------------------
# Escolha do motor
# ---------------------------------------------------------------------------


def test_the_engine_choice_survives_the_session(main_window) -> None:
    from chess_pdf_editor import app as app_module

    _set_mode(main_window, ENGINE_LOCAL)
    settings = main_window.settings
    main_window.close()

    reopened = app_module.MainWindow(settings=settings)
    try:
        assert reopened._engine_mode() == ENGINE_LOCAL
    finally:
        reopened.close()


def test_the_default_engine_is_hybrid(main_window) -> None:
    assert main_window._engine_mode() == ENGINE_HYBRID


def test_the_status_label_says_what_leaves_the_machine(main_window) -> None:
    """O rótulo tem duas coisas a dizer, e a ordem entre elas é regra.

    Sem o motor local instalado, dizer "nenhuma página sai" seria verdade e inútil: o
    que o usuário precisa saber é que o motor que ele acabou de escolher não existe na
    máquina. Então a indisponibilidade vem primeiro, e é isso que a CI sem as
    dependências opcionais exercita — pular o teste ali esconderia justamente o caso.
    """
    _set_mode(main_window, ENGINE_LOCAL)
    text = main_window.engine_status_label.text()
    if local_ocr.available():
        assert "Nenhuma página sai" in text
    else:
        assert "indisponível" in text, f"nem a promessa nem o motivo: {text!r}"

    # O modo remoto não depende do motor local, então a promessa vale nos dois casos.
    _set_mode(main_window, ENGINE_REMOTE)
    assert "enviadas" in main_window.engine_status_label.text()


def test_the_batch_worker_gets_the_chosen_engine(
    main_window, qapp, tmp_path, monkeypatch, no_modals
) -> None:
    """Trocar o motor na UI não vale nada se o lote continuar usando o antigo."""
    from chess_pdf_editor import workers as workers_module

    _open(main_window, tmp_path, pages=1)
    _set_mode(main_window, ENGINE_LOCAL)
    seen: dict = {}

    def _capture(mode, endpoint=None, model_path=None, **kwargs):
        seen["mode"] = mode

        class _Engine:
            def uses_network(self):
                return False

            def predict(self, image_png, filename="board.png", assume_whole_image=False):
                return OcrPrediction(request_id="x", status=204, message=None, results=[])

        return _Engine()

    monkeypatch.setattr(workers_module, "make_engine", _capture)

    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None), "o lote não terminou"
    assert seen.get("mode") == ENGINE_LOCAL


def test_a_batch_whose_engine_refuses_to_build_still_finishes(
    main_window, qapp, tmp_path, monkeypatch, no_modals
) -> None:
    """O motor que nao constroi tem de virar erro, e nao um dialogo eterno (§59.17.1).

    `_build_engine` levanta em dois casos reais — motor local escolhido sem torch ou
    sem o `.pt`, e `warm_up()` num checkpoint corrompido. Enquanto ele ficou fora do
    `try` do `run()`, a excecao saia da thread sem emitir sinal nenhum: quem fecha o
    `QProgressDialog` esta ligado a `completed`, entao a janela ficava travada atras de
    um modal com a barra em zero e um `Cancelar` que nao cancelava nada.
    """
    from chess_pdf_editor import workers as workers_module

    _open(main_window, tmp_path, pages=1)

    def _refuse(mode, endpoint=None, model_path=None, **kwargs):
        raise RuntimeError("motor local indisponivel")

    monkeypatch.setattr(workers_module, "make_engine", _refuse)

    main_window._recognize_full_pdf()

    assert process_until(qapp, lambda: main_window._ocr_worker is None), (
        "o lote nao terminou: `completed` nunca chegou"
    )
    assert main_window._ocr_progress is None, "o dialogo de progresso ficou na tela"


# ---------------------------------------------------------------------------
# Procedencia da leitura (§59.17.2)
# ---------------------------------------------------------------------------


def _fake_selection_ocr(monkeypatch, fen: str, confidence: float) -> None:
    """Motor que devolve um tabuleiro ocupando a selecao inteira."""
    from chess_pdf_editor import app as app_module
    from chess_pdf_editor.types import OcrBoardResult

    class _Engine:
        def uses_network(self):
            return False

        def predict(self, image_png, filename="board.png", assume_whole_image=False):
            return OcrPrediction(
                request_id="x",
                status=200,
                message=None,
                results=[
                    OcrBoardResult(
                        fen=fen, xc=0.5, yc=0.5, width=1.0, height=1.0, confidence=confidence
                    )
                ],
            )

    monkeypatch.setattr(app_module, "make_engine", lambda *a, **k: _Engine())


def _select(window, rect_pdf) -> None:
    rect_img = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page, rect_pdf, window.current_render.matrix
    )
    window.page_widget.set_selection_rect(rect_img)


def test_a_recognized_diagram_keeps_its_ocr_provenance(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """A rede da §59.17.2 nao pode custar o caso que ela existe para preservar."""
    _open(main_window, tmp_path, pages=2)
    _fake_selection_ocr(monkeypatch, FEN, 0.91)
    _select(main_window, DIAGRAM_RECT)

    main_window._recognize_selection()
    main_window._add_operation()

    op = main_window.operations[-1]
    assert op.source == "ocr"
    assert op.confidence == pytest.approx(0.91)


def test_a_hand_made_diagram_is_not_credited_to_the_ocr(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """Depois do primeiro reconhecimento, tudo virava 'ocr' com a confianca alheia.

    `_last_ocr_result` nunca era zerado. O estrago cai nas duas coisas que dependem
    da procedencia: a coluna `origem` do relatorio (§26), que existe para dizer se um
    humano olhou aquilo, e a fila de revisao (§29), que passava a julgar um diagrama
    pela confianca de outro — de outra pagina, inclusive.
    """
    from conftest import SECOND_DIAGRAM_RECT

    _open(main_window, tmp_path, pages=2)
    _fake_selection_ocr(monkeypatch, FEN, 0.42)
    _select(main_window, DIAGRAM_RECT)
    main_window._recognize_selection()

    # Outra pagina, outra area, posicao digitada a mao.
    main_window.current_page = 1
    main_window._render_current_page()
    _select(main_window, SECOND_DIAGRAM_RECT)
    main_window.fen_edit.setText("8/8/8/8/4k3/8/4K3/8")
    main_window._on_fen_edited()
    main_window._add_operation()

    op = main_window.operations[-1]
    assert op.source == "manual", "a substituicao feita a mao foi creditada ao OCR"
    assert op.confidence is None, "herdou a confianca de um diagrama de outra pagina"


def test_editing_the_board_drops_the_ocr_credit(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """A outra metade da rede: corrigir a posicao sem sair da area."""
    _open(main_window, tmp_path, pages=1)
    _fake_selection_ocr(monkeypatch, FEN, 0.55)
    _select(main_window, DIAGRAM_RECT)
    main_window._recognize_selection()

    main_window.board_editor.set_piece_placement("8/8/8/8/4k3/8/4K3/8")
    main_window._add_operation()

    op = main_window.operations[-1]
    assert op.source == "manual"
    assert op.confidence is None


# ---------------------------------------------------------------------------
# Auto-orientar (§6.3)
# ---------------------------------------------------------------------------


UPRIGHT = "8/5p2/4k3/8/8/3K4/5P2/8"


def test_auto_orient_turns_an_upside_down_position(main_window) -> None:
    from chess_pdf_editor.fen import board_to_matrix, matrix_to_piece_placement

    matrix = board_to_matrix(UPRIGHT)
    for _ in range(2):
        matrix = [list(row) for row in zip(*matrix[::-1])]
    upside_down = matrix_to_piece_placement(matrix)
    main_window.board_editor.set_piece_placement(upside_down)

    main_window._auto_orient_position()

    assert main_window.board_editor.piece_placement() == UPRIGHT
    assert "girada" in main_window.statusBar().currentMessage()


def test_auto_orient_leaves_a_correct_position_alone(main_window) -> None:
    main_window.board_editor.set_piece_placement(UPRIGHT)

    main_window._auto_orient_position()

    assert main_window.board_editor.piece_placement() == UPRIGHT
    assert "já é a mais plausível" in main_window.statusBar().currentMessage()


def test_auto_orient_syncs_the_fen_field(main_window) -> None:
    """A FEN da caixa de texto é o que a prévia e a substituição leem."""
    main_window.board_editor.set_piece_placement("8/5P2/4K3/8/8/3k4/5p2/8")
    main_window._auto_orient_position()

    assert main_window.fen_edit.text() == main_window.board_editor.piece_placement()


# ---------------------------------------------------------------------------
# Relatório (§6.4)
# ---------------------------------------------------------------------------


def _add_operation(window, page=0, fen=FEN, **kwargs) -> None:
    window.operations.append(
        OverlayOperation(page_num=page, rect_pdf=DIAGRAM_RECT, fen=fen, **kwargs)
    )


def test_the_report_refuses_when_there_is_nothing_to_report(main_window, no_modals) -> None:
    main_window._export_report_dialog()
    assert any("Relatório" == title for title, _ in no_modals)


def test_the_report_writes_every_change(main_window, tmp_path, monkeypatch, no_modals) -> None:
    _open(main_window, tmp_path)
    _add_operation(main_window, source="ocr-page", confidence=0.62)
    main_window.candidates.append(
        OverlayOperation(page_num=1, rect_pdf=DIAGRAM_RECT, fen=FEN, source="local-candidato")
    )
    out = tmp_path / "relatorio.csv"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)")),
    )

    main_window._export_report_dialog()

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert {row["tipo"] for row in rows} == {"substituicao", "candidato"}
    assert any(row["origem"] == "ocr-page" and row["confianca"] == "0.62" for row in rows)


def test_the_report_records_which_engine_produced_it(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """Comparar dois processamentos exige saber qual motor gerou cada um."""
    _open(main_window, tmp_path)
    _add_operation(main_window)
    _set_mode(main_window, ENGINE_LOCAL)
    out = tmp_path / "relatorio.json"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "JSON (*.json)")),
    )

    main_window._export_report_dialog()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["motor"] == ENGINE_LOCAL
    assert payload["source_pdf"] == main_window.current_pdf_path


def test_a_missing_extension_is_filled_from_the_chosen_filter(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """O diálogo do Qt nem sempre acrescenta a extensão, e o formato vem dela."""
    _open(main_window, tmp_path)
    _add_operation(main_window)
    out = tmp_path / "sem_extensao"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "JSON (*.json)")),
    )

    main_window._export_report_dialog()

    assert out.with_suffix(".json").exists()


# ---------------------------------------------------------------------------
# Ajustar à borda (§6.2)
# ---------------------------------------------------------------------------


def test_snapping_without_a_selection_warns(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    main_window._snap_selection_to_board()
    assert any("Sem seleção" == title for title, _ in no_modals)


def test_snapping_without_a_pdf_warns(main_window, no_modals) -> None:
    main_window._snap_selection_to_board()
    assert any("Sem PDF" == title for title, _ in no_modals)


@pytest.mark.skipif(
    not local_ocr.dependencies_available(), reason="requer opencv/numpy (extra `local`)"
)
def test_snapping_pulls_a_sloppy_selection_onto_the_figure(
    main_window, tmp_path, no_modals
) -> None:
    """O PDF de teste tem um retângulo cinza no lugar do diagrama; é nele que encosta."""
    _open(main_window, tmp_path)
    target = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    width = target[2] - target[0]
    height = target[3] - target[1]
    sloppy = (
        target[0] + width * 0.10,
        target[1] + height * 0.08,
        target[2] - width * 0.05,
        target[3] - height * 0.12,
    )
    main_window.page_widget.set_selection_rect(sloppy)

    main_window._snap_selection_to_board()

    refined = main_window.page_widget.selection_rect()
    before = max(abs(sloppy[i] - target[i]) for i in range(4))
    after = max(abs(refined[i] - target[i]) for i in range(4))
    assert after < before / 3.0, f"o ajuste mal melhorou: {before:.1f} px -> {after:.1f} px"
    assert "ajustada à borda" in main_window.statusBar().currentMessage()


@pytest.mark.skipif(
    not local_ocr.dependencies_available(), reason="requer opencv/numpy (extra `local`)"
)
def test_snapping_over_plain_text_changes_nothing(main_window, tmp_path, no_modals) -> None:
    """Sem borda encontrada, mexer na seleção seria pior que não fazer nada."""
    _open(main_window, tmp_path)
    # Região só de texto, longe do retângulo cinza.
    text_area = main_window.pdf_service.pdf_rect_to_image_rect(
        0, (60.0, 60.0, 200.0, 200.0), main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(text_area)

    main_window._snap_selection_to_board()

    assert main_window.page_widget.selection_rect() == pytest.approx(text_area)
    assert "Nenhuma borda" in main_window.statusBar().currentMessage()
