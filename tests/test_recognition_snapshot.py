"""Instantâneo em JSON de cada reconhecimento (§55).

O autosave grava um arquivo por livro e o sobrescreve; este grava um por
reconhecimento e não sobrescreve nada. O que estes testes protegem:

* o arquivo cai na pasta do livro, com data e hora no nome;
* dois reconhecimentos no mesmo segundo não viram um só;
* o que foi gravado **carrega de volta** como projeto — senão "não perder a
  detecção" seria só um registro para ler;
* nada disso pode derrubar o reconhecimento se o disco recusar a gravação.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, PAGE_HEIGHT, PAGE_WIDTH, make_pdf, process_until

pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")

from chess_pdf_editor.project_state import (  # noqa: E402
    ProjectState,
    load_project_state,
    save_project_state,
)
from chess_pdf_editor.recognition_snapshot import (  # noqa: E402
    KIND_BOOK,
    KIND_PAGE,
    METADATA_KEY,
    TARGET_CANDIDATES,
    TARGET_OPERATIONS,
    RunInfo,
    snapshot_path,
    write_snapshot,
)
from chess_pdf_editor.types import EraseOperation, OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
OTHER_FEN = "8/8/8/3qk3/8/8/4K3/8"
QUANDO = datetime(2026, 9, 6, 14, 32, 10)


def _op(page: int = 0, fen: str = FEN, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=DIAGRAM_RECT, fen=fen, **kwargs)


def _state(pdf: Path, operations=(), candidates=()) -> ProjectState:
    return ProjectState(
        source_pdf=str(pdf),
        source_pdf_fingerprint={},
        operations=list(operations),
        candidates=list(candidates),
    )


def _run(**kwargs) -> RunInfo:
    base = dict(
        origem=KIND_PAGE,
        destino=TARGET_CANDIDATES,
        encontrados=1,
        paginas="1",
    )
    base.update(kwargs)
    return RunInfo(**base)


# ---------------------------------------------------------------------------
# Onde o arquivo cai
# ---------------------------------------------------------------------------


def test_the_file_lands_next_to_the_pdf_with_the_book_name(tmp_path) -> None:
    pdf = tmp_path / "livros" / "Meu Livro.pdf"
    pdf.parent.mkdir()

    destino = snapshot_path(str(pdf), KIND_PAGE, quando=QUANDO)

    assert destino.parent == pdf.parent, "na pasta do livro, que é onde se procura"
    assert destino.name == "Meu Livro-reconhecimento-pagina-20260906-143210.json"


def test_the_name_says_which_button_produced_it(tmp_path) -> None:
    pdf = tmp_path / "livro.pdf"
    pagina = snapshot_path(str(pdf), KIND_PAGE, quando=QUANDO).name
    livro = snapshot_path(str(pdf), KIND_BOOK, quando=QUANDO).name

    assert "-pagina-" in pagina
    assert "-livro-" in livro


def test_a_second_run_in_the_same_second_does_not_overwrite_the_first(tmp_path) -> None:
    """Sobrescrever é o defeito que este módulo existe para não ter."""
    pdf = tmp_path / "livro.pdf"
    primeiro = snapshot_path(str(pdf), KIND_PAGE, quando=QUANDO)
    primeiro.write_text("{}", encoding="utf-8")

    segundo = snapshot_path(str(pdf), KIND_PAGE, quando=QUANDO)

    assert segundo != primeiro
    assert segundo.name.endswith("-2.json")


def test_the_desambiguation_keeps_counting(tmp_path) -> None:
    pdf = tmp_path / "livro.pdf"
    for _ in range(3):
        caminho = snapshot_path(str(pdf), KIND_PAGE, quando=QUANDO)
        caminho.write_text("{}", encoding="utf-8")

    assert sorted(p.name for p in tmp_path.glob("*.json")) == [
        "livro-reconhecimento-pagina-20260906-143210-2.json",
        "livro-reconhecimento-pagina-20260906-143210-3.json",
        "livro-reconhecimento-pagina-20260906-143210.json",
    ]


# ---------------------------------------------------------------------------
# O que o arquivo contém
# ---------------------------------------------------------------------------


def test_what_was_written_loads_back_as_a_project(tmp_path) -> None:
    """É o que faz "não perder" valer: `Carregar projeto` recupera as detecções."""
    pdf = make_pdf(tmp_path / "livro.pdf")
    state = _state(pdf, operations=[_op(0)], candidates=[_op(1, fen=OTHER_FEN)])

    destino = write_snapshot(str(pdf), state, _run(), quando=QUANDO)

    lido = load_project_state(str(destino))
    assert [op.fen for op in lido.operations] == [FEN]
    assert [op.fen for op in lido.candidates] == [OTHER_FEN]
    assert lido.candidates[0].page_num == 1


def test_the_metadata_block_says_where_the_detections_came_from(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "livro.pdf")
    run = _run(
        origem=KIND_BOOK,
        destino=TARGET_OPERATIONS,
        encontrados=42,
        paginas="1-898",
        ignorados=7,
        grandes_descartadas=3,
        falhas=1,
        cancelado=True,
        motor="hybrid",
    )

    destino = write_snapshot(str(pdf), _state(pdf), run, quando=QUANDO)

    bloco = json.loads(destino.read_text(encoding="utf-8"))[METADATA_KEY]
    assert bloco["origem"] == KIND_BOOK
    assert bloco["destino"] == TARGET_OPERATIONS
    assert bloco["encontrados"] == 42
    assert bloco["paginas"] == "1-898"
    assert bloco["ignorados"] == 7
    assert bloco["grandes_descartadas"] == 3
    assert bloco["falhas"] == 1
    assert bloco["cancelado"] is True
    assert bloco["motor"] == "hybrid"
    assert bloco["quando"].startswith("2026-09-06T14:32:10")


def test_the_metadata_does_not_disturb_the_project_reader(tmp_path) -> None:
    """A chave a mais tem de ser ignorada, não recusada."""
    pdf = make_pdf(tmp_path / "livro.pdf")
    destino = write_snapshot(str(pdf), _state(pdf, operations=[_op(0)]), _run(), quando=QUANDO)

    lido = load_project_state(str(destino))

    assert lido.source_pdf == str(pdf)
    assert len(lido.operations) == 1


def test_extra_keys_cannot_shadow_project_fields(tmp_path) -> None:
    """Deixar passar gravaria um projeto que não é o `state` que se pediu, e o
    erro só apareceria ao recarregar."""
    pdf = tmp_path / "livro.pdf"
    destino = tmp_path / "p.json"

    with pytest.raises(ValueError, match="operations"):
        save_project_state(str(destino), _state(pdf), extra={"operations": []})


def test_the_snapshot_leaves_no_temporary_behind(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "livro.pdf")
    write_snapshot(str(pdf), _state(pdf), _run(), quando=QUANDO)

    assert list(tmp_path.glob("*.tmp")) == []


def test_erasures_and_the_rest_of_the_project_come_along(tmp_path) -> None:
    """O arquivo é o projeto inteiro, não só as detecções: recuperar não pode
    custar o apagamento que o usuário já tinha feito."""
    pdf = make_pdf(tmp_path / "livro.pdf")
    state = _state(pdf, operations=[_op(0)])
    state.erase_operations = [EraseOperation(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0))]

    destino = write_snapshot(str(pdf), state, _run(), quando=QUANDO)

    assert len(load_project_state(str(destino)).erase_operations) == 1


# ---------------------------------------------------------------------------
# Reconhecer página
# ---------------------------------------------------------------------------


def _install_fake_engine(monkeypatch, main_window, fen: str = FEN, results: int = 1):
    """Motor determinístico no lugar do real, para o caminho síncrono."""
    from chess_pdf_editor.types import OcrBoardResult, OcrPrediction

    x0, y0, x1, y1 = DIAGRAM_RECT
    board = OcrBoardResult(
        fen=fen,
        xc=((x0 + x1) / 2.0) / PAGE_WIDTH,
        yc=((y0 + y1) / 2.0) / PAGE_HEIGHT,
        width=(x1 - x0) / PAGE_WIDTH,
        height=(y1 - y0) / PAGE_HEIGHT,
        confidence=0.87,
    )

    class _FakeEngine:
        name = "fake"

        def uses_network(self) -> bool:
            return False

        def predict(self, image_bytes, filename="board.png", assume_whole_image=False):
            return OcrPrediction(
                request_id="fake", status=200, message=None, results=[board] * results
            )

    monkeypatch.setattr(main_window, "_make_engine", lambda: _FakeEngine())


def _snapshots(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("*-reconhecimento-*.json"))


def test_recognizing_a_page_writes_the_json(main_window, tmp_path, monkeypatch, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    _install_fake_engine(monkeypatch, main_window)

    main_window._recognize_current_page()

    arquivos = _snapshots(tmp_path)
    assert len(arquivos) == 1, "um arquivo por reconhecimento"
    assert "-pagina-" in arquivos[0].name
    bloco = json.loads(arquivos[0].read_text(encoding="utf-8"))[METADATA_KEY]
    assert bloco["origem"] == KIND_PAGE
    assert bloco["paginas"] == "1"
    assert bloco["encontrados"] == 1
    assert bloco["destino"] == TARGET_CANDIDATES, "a fila é o padrão do app"
    assert load_project_state(str(arquivos[0])).candidates[0].fen == FEN


def test_the_status_bar_says_where_it_saved(main_window, tmp_path, monkeypatch, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    _install_fake_engine(monkeypatch, main_window)

    main_window._recognize_current_page()

    assert "JSON salvo" in main_window.statusBar().currentMessage()


def test_a_page_without_detections_writes_nothing(main_window, tmp_path, monkeypatch, no_modals) -> None:
    """Um clique que não achou nada não tem o que perder; gravar assim mesmo
    encheria a pasta do livro de arquivos que só dizem "nada aqui"."""
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    _install_fake_engine(monkeypatch, main_window, results=0)

    main_window._recognize_current_page()

    assert _snapshots(tmp_path) == []


def test_the_checkbox_turns_it_off(main_window, tmp_path, monkeypatch, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    main_window.save_recognition_json_check.setChecked(False)
    _install_fake_engine(monkeypatch, main_window)

    main_window._recognize_current_page()

    assert _snapshots(tmp_path) == []
    assert len(main_window.candidates) == 1, "o reconhecimento em si continua valendo"


def test_a_second_recognition_keeps_the_first_file(main_window, tmp_path, monkeypatch, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    _install_fake_engine(monkeypatch, main_window)

    main_window._recognize_current_page()
    main_window.candidates.clear()  # como quem descarta a fila por engano
    main_window._recognize_current_page()

    assert len(_snapshots(tmp_path)) == 2, "o segundo reconhecimento não apaga o primeiro"


def test_a_write_failure_does_not_lose_the_detection(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """Pasta de rede, só de leitura ou cheia não é razão para perder o que já
    está na tela."""
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf")), clear_ops=True)
    _install_fake_engine(monkeypatch, main_window)

    from chess_pdf_editor import ocr_workflow

    def _explode(*args, **kwargs):
        raise OSError("disco cheio")

    monkeypatch.setattr(ocr_workflow, "write_snapshot", _explode)

    main_window._recognize_current_page()

    assert len(main_window.candidates) == 1, "a detecção continua de pé"
    assert "NÃO gravado" in main_window.statusBar().currentMessage()
    assert "disco cheio" in main_window.statusBar().currentMessage()


# ---------------------------------------------------------------------------
# Detectar no PDF
# ---------------------------------------------------------------------------


def _install_fake_batch(monkeypatch, fen: str = FEN):
    from chess_pdf_editor import workers as workers_module
    from chess_pdf_editor.types import OcrBoardResult, OcrPrediction

    x0, y0, x1, y1 = DIAGRAM_RECT
    board = OcrBoardResult(
        fen=fen,
        xc=((x0 + x1) / 2.0) / PAGE_WIDTH,
        yc=((y0 + y1) / 2.0) / PAGE_HEIGHT,
        width=(x1 - x0) / PAGE_WIDTH,
        height=(y1 - y0) / PAGE_HEIGHT,
        confidence=0.9,
    )

    class _FakeEngine:
        name = "fake"

        def uses_network(self) -> bool:
            return False

        def predict(self, image_bytes, filename="board.png", assume_whole_image=False):
            return OcrPrediction(request_id="fake", status=200, message=None, results=[board])

    monkeypatch.setattr(workers_module, "make_engine", lambda *a, **k: _FakeEngine())


def test_detecting_in_the_whole_pdf_writes_the_json(
    main_window, qapp, tmp_path, monkeypatch, no_modals
) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf", pages=3)), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_batch(monkeypatch)

    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None), "o lote não terminou"

    arquivos = _snapshots(tmp_path)
    assert len(arquivos) == 1
    assert "-livro-" in arquivos[0].name
    bloco = json.loads(arquivos[0].read_text(encoding="utf-8"))[METADATA_KEY]
    assert bloco["origem"] == KIND_BOOK
    assert bloco["paginas"] == "1-3"
    assert bloco["encontrados"] == 3
    assert bloco["cancelado"] is False
    assert len(load_project_state(str(arquivos[0])).candidates) == 3


def test_a_batch_that_found_nothing_writes_nothing(
    main_window, qapp, tmp_path, monkeypatch, no_modals
) -> None:
    from chess_pdf_editor import workers as workers_module
    from chess_pdf_editor.types import OcrPrediction

    class _EmptyEngine:
        name = "vazio"

        def uses_network(self) -> bool:
            return False

        def predict(self, image_bytes, filename="board.png", assume_whole_image=False):
            return OcrPrediction(request_id="x", status=204, message=None, results=[])

    monkeypatch.setattr(workers_module, "make_engine", lambda *a, **k: _EmptyEngine())
    main_window._open_pdf(str(make_pdf(tmp_path / "livro.pdf", pages=2)), clear_ops=True)

    main_window._recognize_full_pdf()
    assert process_until(qapp, lambda: main_window._ocr_worker is None)

    assert _snapshots(tmp_path) == []
