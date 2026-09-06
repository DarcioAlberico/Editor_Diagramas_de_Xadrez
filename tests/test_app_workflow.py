"""Sprint 5 no app real: workers, undo/redo e autosave (Qt offscreen)."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, PAGE_HEIGHT, PAGE_WIDTH, make_pdf, process_until

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

FEN = "8/8/8/4k3/8/8/4K3/8"
FEN_EDITED = "8/8/8/3qk3/8/8/4K3/8"


def _open(window, tmp_path: Path, pages: int = 2, name: str = "book.pdf"):
    window._open_pdf(str(make_pdf(tmp_path / name, pages=pages)), clear_ops=True)
    return window


def _select_diagram(window, fen: str = FEN, rect_pdf=DIAGRAM_RECT) -> None:
    rect_img = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page, rect_pdf, window.current_render.matrix
    )
    window.page_widget.set_selection_rect(rect_img)
    window.board_editor.set_piece_placement(fen)


def _install_fake_batch_ocr(
    monkeypatch,
    fen: str = FEN,
    confidence=0.91,
    rect_pdf=DIAGRAM_RECT,
    delay_sec: float = 0.0,
):
    """Troca o motor de reconhecimento *dentro do worker* por um dublê determinístico.

    O ponto de troca é `workers.make_engine`, e não a classe do cliente HTTP: desde o
    Sprint 7 o worker escolhe entre local, remoto e híbrido, e o teste não quer nenhum
    dos três — quer um motor que responde sempre a mesma coisa, sem rede e sem modelo.

    `delay_sec` simula a latência do serviço real. Sem ela um lote de N páginas
    termina antes do teste conseguir cancelar, e o teste passaria sem nunca ter
    exercitado o cancelamento.

    Devolve a lista de threads em que `predict` rodou — é assim que se prova que
    o trabalho saiu mesmo da thread da UI.
    """
    import time as _time

    from chess_pdf_editor import workers as workers_module
    from chess_pdf_editor.types import OcrBoardResult, OcrPrediction

    x0, y0, x1, y1 = rect_pdf
    result = OcrBoardResult(
        fen=fen,
        xc=((x0 + x1) / 2.0) / PAGE_WIDTH,
        yc=((y0 + y1) / 2.0) / PAGE_HEIGHT,
        width=(x1 - x0) / PAGE_WIDTH,
        height=(y1 - y0) / PAGE_HEIGHT,
        confidence=confidence,
    )
    threads_used: list[object] = []

    class _FakeEngine:
        name = "fake"

        def uses_network(self) -> bool:
            return False

        def predict(self, image_bytes, filename="board.png", assume_whole_image=False):
            threads_used.append(QtCore.QThread.currentThread())
            if delay_sec:
                _time.sleep(delay_sec)
            return OcrPrediction(request_id="fake", status=200, message=None, results=[result])

    monkeypatch.setattr(workers_module, "make_engine", lambda *a, **k: _FakeEngine())
    return threads_used


# ---------------------------------------------------------------------------
# Workers: OCR em lote
# ---------------------------------------------------------------------------


def test_batch_ocr_runs_off_the_ui_thread(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    """O ponto do Sprint 5.1: as requisições saem da thread da UI."""
    _open(main_window, tmp_path, pages=3)
    main_window.auto_apply_check.setChecked(False)
    threads_used = _install_fake_batch_ocr(monkeypatch)

    ui_thread = QtCore.QThread.currentThread()
    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None), "o lote não terminou"

    assert len(threads_used) == 3, "uma requisição por página"
    assert all(thread is not ui_thread for thread in threads_used), "o OCR rodou na thread da UI"
    assert len(main_window.candidates) == 3
    assert main_window.operations == [], "nada pode ser aplicado sem conferência"


def test_the_ui_stays_responsive_during_the_batch(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    """A janela precisa continuar processando eventos enquanto o lote roda."""
    _open(main_window, tmp_path, pages=6)
    _install_fake_batch_ocr(monkeypatch, delay_sec=0.05)
    main_window._recognize_full_pdf()

    ticks = 0
    timer = QtCore.QTimer()
    timer.setInterval(10)

    def _tick():
        nonlocal ticks
        ticks += 1

    timer.timeout.connect(_tick)
    timer.start()
    try:
        process_until(qapp, lambda: main_window._ocr_worker is None)
    finally:
        timer.stop()

    assert ticks > 0, "o loop de eventos ficou parado durante o lote"


def test_batch_ocr_carries_the_confidence_through(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    _open(main_window, tmp_path, pages=1)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_batch_ocr(monkeypatch, confidence=0.73)

    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None)

    assert main_window.candidates[0].confidence == pytest.approx(0.73)


def test_batch_ocr_places_the_diagram_where_it_was_detected(
    main_window, qapp, tmp_path, monkeypatch, no_modals
) -> None:
    """A conversão de coordenadas acontece no worker: ela tem de bater com a bbox real."""
    _open(main_window, tmp_path, pages=1)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_batch_ocr(monkeypatch)

    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None)

    assert main_window.candidates[0].rect_pdf == pytest.approx(DIAGRAM_RECT, abs=0.5)


def test_batch_ocr_can_be_canceled_and_resumed(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    _open(main_window, tmp_path, pages=6)
    main_window.auto_apply_check.setChecked(False)
    # Latência simulada: sem ela o lote termina antes do cancelamento e o teste
    # passaria sem exercitar nada.
    _install_fake_batch_ocr(monkeypatch, delay_sec=0.15)

    main_window._recognize_full_pdf()
    # Cancela assim que a primeira página tiver sido processada.
    assert process_until(qapp, lambda: len(main_window.candidates) >= 1, timeout_sec=10.0)
    main_window._cancel_batch_ocr()

    assert process_until(qapp, lambda: main_window._ocr_worker is None)
    assert main_window.ocr_full_next_page > 0, "sem ponto de retomada"
    assert len(main_window.candidates) < 6, "cancelar não interrompeu nada"


def test_a_second_batch_is_refused_while_one_runs(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    _open(main_window, tmp_path, pages=4)
    _install_fake_batch_ocr(monkeypatch, delay_sec=0.15)

    main_window._recognize_full_pdf()
    first_worker = main_window._ocr_worker
    assert first_worker is not None and first_worker.isRunning()
    main_window._recognize_full_pdf()

    assert main_window._ocr_worker is first_worker, "o segundo lote sobrescreveu o primeiro"
    main_window._cancel_batch_ocr()
    assert process_until(qapp, lambda: main_window._ocr_worker is None)


def test_no_worker_survives_the_window_closing(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    """QThread destruída em execução derruba o processo — o closeEvent tem de esperar."""
    _open(main_window, tmp_path, pages=8)
    _install_fake_batch_ocr(monkeypatch, delay_sec=0.15)
    main_window._recognize_full_pdf()
    worker = main_window._ocr_worker
    assert worker is not None and worker.isRunning(), "o lote já tinha acabado"

    main_window.close()

    assert main_window._ocr_worker is None
    assert worker.isRunning() is False


# ---------------------------------------------------------------------------
# Workers: exportacao
# ---------------------------------------------------------------------------


def test_export_writes_the_pdf_from_a_worker(main_window, qapp, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()

    out_path = tmp_path / "saida.pdf"
    main_window._save_output_pdf(auto_save_path=str(out_path))

    assert main_window._export_worker is not None, "exportou na thread da UI"
    assert process_until(qapp, lambda: out_path.exists() and main_window._export_worker is None)

    doc = fitz.open(str(out_path))
    try:
        assert doc.page_count == 2
    finally:
        doc.close()


def test_export_failure_reaches_the_user(main_window, qapp, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()

    # Diretorio inexistente e sem permissao de criacao: a gravacao falha.
    bad_path = tmp_path / "nao-existe" / "sub" / "saida.pdf"
    main_window._save_output_pdf(auto_save_path=str(bad_path))
    assert process_until(qapp, lambda: main_window._export_worker is None)

    assert not bad_path.exists()
    assert any("Erro ao exportar" in title for title, _ in no_modals), "falha silenciosa"


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def test_undo_restores_a_removed_replacement(main_window, tmp_path, no_modals) -> None:
    """O caso que motivou o sprint: remover era definitivo."""
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()
    assert len(main_window.operations) == 1

    main_window._remove_operation_at_index(0)
    assert main_window.operations == []

    main_window._undo_change()
    assert len(main_window.operations) == 1
    assert main_window.operations[0].fen == FEN


def test_redo_reapplies_what_was_undone(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()

    main_window._undo_change()
    assert main_window.operations == []

    main_window._redo_change()
    assert len(main_window.operations) == 1


def test_undo_brings_back_every_discarded_candidate(main_window, qapp, tmp_path, monkeypatch, no_modals) -> None:
    """`Descartar todos` some com dezenas de detecções de uma vez."""
    _open(main_window, tmp_path, pages=3)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_batch_ocr(monkeypatch)
    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None)
    assert len(main_window.candidates) == 3

    main_window._discard_all_candidates()
    assert main_window.candidates == []

    main_window._undo_change()
    assert len(main_window.candidates) == 3


def test_undo_covers_erasers_too(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_eraser_from_selection()
    assert len(main_window.erase_operations) == 1

    main_window._undo_change()
    assert main_window.erase_operations == []


def test_undo_actions_reflect_what_is_available(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    assert main_window.act_undo.isEnabled() is False
    assert main_window.act_redo.isEnabled() is False

    _select_diagram(main_window)
    main_window._add_operation()
    assert main_window.act_undo.isEnabled() is True
    assert "adicionar substituição" in main_window.act_undo.text()

    main_window._undo_change()
    assert main_window.act_redo.isEnabled() is True


def test_opening_another_pdf_clears_the_history(main_window, tmp_path, no_modals) -> None:
    """Desfazer não pode ressuscitar alterações do livro anterior."""
    _open(main_window, tmp_path, name="primeiro.pdf")
    _select_diagram(main_window)
    main_window._add_operation()
    assert main_window.act_undo.isEnabled() is True

    _open(main_window, tmp_path, name="segundo.pdf")
    assert main_window.act_undo.isEnabled() is False

    main_window._undo_change()
    assert main_window.operations == []


def test_a_no_op_commit_does_not_create_a_history_step(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()
    steps = len(main_window.history)

    main_window._commit_history("sem mudança")
    assert len(main_window.history) == steps


# ---------------------------------------------------------------------------
# Autosave
# ---------------------------------------------------------------------------


def test_autosave_writes_without_the_user_choosing_a_file(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()

    assert main_window._autosave_now(quiet=True) is True

    target = Path(main_window._autosave_target())
    assert target.exists()
    from chess_pdf_editor.project_state import load_project_state

    saved = load_project_state(str(target))
    assert len(saved.operations) == 1
    assert saved.operations[0].fen == FEN


def test_autosave_prefers_the_project_file_the_user_chose(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    chosen = tmp_path / "meu_projeto.json"
    main_window.project_path = str(chosen)
    _select_diagram(main_window)
    main_window._add_operation()

    main_window._autosave_now(quiet=True)
    assert chosen.exists()


def test_closing_the_window_saves_pending_work(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()
    target = Path(main_window._autosave_target())
    if target.exists():
        target.unlink()

    main_window.close()
    assert target.exists(), "fechar a janela perdeu o trabalho da sessão"


def test_autosave_can_be_turned_off(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    main_window.act_autosave.setChecked(False)
    _select_diagram(main_window)
    main_window._add_operation()

    assert main_window._autosave_now(quiet=True) is False
    assert main_window._autosave_timer.isActive() is False


def test_a_failing_autosave_does_not_break_the_app(main_window, tmp_path, monkeypatch, no_modals) -> None:
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()

    from chess_pdf_editor import app as app_module

    def _boom(path, state):
        raise OSError("disco cheio")

    monkeypatch.setattr(app_module, "write_project_atomically", _boom)
    assert main_window._autosave_now(quiet=True) is False
    # Continua utilizável: o trabalho segue em memória e ainda marcado como pendente.
    assert len(main_window.operations) == 1
    assert main_window._autosave_dirty is True


def test_study_work_marks_the_project_as_dirty(main_window, tmp_path, no_modals) -> None:
    """O autosave so grava com a bandeira de pe, e o estudo nunca a levantava (§59.4).

    Sem isto, uma sessao inteira de estudo — criar a posicao, escrever o comentario,
    remover o que sobrou — fechava sem gravar nada: o `closeEvent` tambem so salva
    quando `_autosave_dirty` esta verdadeiro. Nada de erro, nada de aviso, e a
    promessa do Sprint 5.3 valendo para metade do produto.
    """
    _open(main_window, tmp_path)
    _select_diagram(main_window)

    acoes = {
        "estudar seleção": lambda: main_window._study_selection(),
        "partida inicial": lambda: main_window._study_starting_position(),
        "comentário do lance": lambda: (
            main_window.study_comment_before_edit.setPlainText("as brancas jogam e ganham")
        ),
        "remover posição": lambda: main_window._remove_selected_study_position(),
    }
    for rotulo, acao in acoes.items():
        main_window._autosave_dirty = False
        acao()
        assert main_window._autosave_dirty is True, (
            f"'{rotulo}' nao marcou o projeto como pendente: o autosave vai pular"
        )


def test_closing_the_window_saves_study_work_too(main_window, tmp_path, no_modals) -> None:
    """A prova de ponta a ponta do §59.4: o comentario tem de chegar ao disco."""
    from chess_pdf_editor.project_state import load_project_state

    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._study_selection()
    main_window.study_comment_before_edit.setPlainText("as brancas jogam e ganham")

    target = Path(main_window._autosave_target())
    if target.exists():
        target.unlink()
    main_window.close()

    assert target.exists(), "fechar a janela perdeu a sessao de estudo"
    salvo = load_project_state(str(target))
    assert len(salvo.study_positions) == 1
    assert "brancas jogam" in salvo.study_positions[0].comment_before


def test_removing_a_study_position_clears_its_frame(main_window, tmp_path, no_modals) -> None:
    """A moldura verde ficava na pagina ate a proxima troca de pagina (§59.8).

    Sumir sozinha depois e pior que nao sumir: ensina o usuario a nao confiar no que
    esta vendo.
    """
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._study_selection()
    assert len(main_window.page_widget._study_rects) == 1

    main_window.study_positions_list.setCurrentRow(0)
    main_window._remove_selected_study_position()

    assert main_window.page_widget._study_rects == [], "a moldura da posicao removida ficou"


def test_the_side_to_move_chosen_in_the_study_panel_sticks(main_window, tmp_path, no_modals) -> None:
    """Um controle que funciona e depois se desfaz sozinho e pior que um inerte.

    O painel muda a FEN inicial (§59.9); sem levar isso de volta para a entrada da
    lista, sair da posicao e voltar recarregaria o lado antigo.
    """
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._study_selection()

    main_window.study_panel.side_combo.setCurrentIndex(1)  # pretas
    assert main_window.study_positions[0].side_to_move == "b"

    # Sair e voltar: e aqui que a troca se desfazia.
    main_window._focus_study_position(0)
    assert main_window.study_panel.study_board.start_turn() == "b"


def test_the_export_action_does_not_pass_its_checked_flag_as_a_path(
    main_window, tmp_path, no_modals
) -> None:
    """`triggered` carrega um bool, e o PySide o entrega posicionalmente (§59.10).

    Funcionava por acaso — `False` e falsy e caia no ramo do dialogo. Deixaria de
    funcionar no dia em que a acao virasse checavel.
    """
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()  # sem alteracoes a acao nasce desabilitada
    assert main_window.act_save_pdf.isEnabled()

    recebido: list = []
    main_window._save_output_pdf = lambda auto_save_path=None: recebido.append(auto_save_path)

    main_window.act_save_pdf.trigger()

    assert recebido == [None], f"a acao passou {recebido!r} como caminho de saida"


def test_restored_autosave_reopens_the_work(main_window, qapp, tmp_path, no_modals) -> None:
    """O ponto do autosave: a próxima sessão continua de onde parou."""
    _open(main_window, tmp_path)
    _select_diagram(main_window)
    main_window._add_operation()
    main_window._autosave_now(quiet=True)
    autosave_file = main_window._autosave_target()
    main_window.close()

    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "settings2.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("last_project_path", autosave_file)
    restored = app_module.MainWindow(settings=settings)
    try:
        assert len(restored.operations) == 1
        assert restored.operations[0].fen == FEN
        # E o projeto restaurado é a linha de base: nada para desfazer "para trás".
        assert restored.act_undo.isEnabled() is False
    finally:
        restored.close()


# ---------------------------------------------------------------------------
# Configuracao persistida
# ---------------------------------------------------------------------------


def test_the_ocr_endpoint_survives_the_session(main_window, tmp_path) -> None:
    from chess_pdf_editor import app as app_module
    from chess_pdf_editor.ocr_api import default_endpoint
    from chess_pdf_editor.recognition import ENGINE_REMOTE, make_engine

    assert main_window.endpoint_edit.text() == default_endpoint()

    main_window.endpoint_edit.setText("https://interno/predict")
    main_window._on_endpoint_edited()
    settings = main_window.settings
    main_window.close()

    reopened = app_module.MainWindow(settings=settings)
    try:
        assert reopened.endpoint_edit.text() == "https://interno/predict"
        engine = make_engine(ENGINE_REMOTE, endpoint=reopened._ocr_endpoint())
        assert engine._client.endpoints == ["https://interno/predict"]
    finally:
        reopened.close()


def test_an_empty_endpoint_falls_back_to_the_default_chain(main_window) -> None:
    from chess_pdf_editor.ocr_api import DEFAULT_ENDPOINTS
    from chess_pdf_editor.recognition import ENGINE_REMOTE, make_engine

    main_window.endpoint_edit.setText("   ")
    assert main_window._ocr_endpoint() is None
    engine = make_engine(ENGINE_REMOTE, endpoint=main_window._ocr_endpoint())
    assert engine._client.endpoints == list(DEFAULT_ENDPOINTS)
