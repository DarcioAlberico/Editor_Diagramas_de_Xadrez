"""Isolamento e fixtures compartilhadas da suite.

O app grava fora do diretorio do projeto: autosave e log vao para
`%LOCALAPPDATA%/ChessPdfEditor` (ou `~/.local/state`). Rodar os testes nao pode
sujar — nem ler — o perfil real do usuario, entao os dois diretorios sao
redirecionados para um temporario, e o `MainWindow` recebe um `QSettings`
apontando para um `.ini` descartavel.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True, scope="session")
def _isolate_app_data(tmp_path_factory):
    base = tmp_path_factory.mktemp("appdata")
    autosave_dir = base / "autosave"
    log_dir = base / "logs"
    autosave_dir.mkdir()
    log_dir.mkdir()

    previous = {
        "CHESS_PDF_EDITOR_AUTOSAVE_DIR": os.environ.get("CHESS_PDF_EDITOR_AUTOSAVE_DIR"),
        "CHESS_PDF_EDITOR_LOG_DIR": os.environ.get("CHESS_PDF_EDITOR_LOG_DIR"),
    }
    os.environ["CHESS_PDF_EDITOR_AUTOSAVE_DIR"] = str(autosave_dir)
    os.environ["CHESS_PDF_EDITOR_LOG_DIR"] = str(log_dir)
    try:
        yield base
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# GUI offscreen
# ---------------------------------------------------------------------------

DIAGRAM_RECT = (100.0, 300.0, 260.0, 460.0)
SECOND_DIAGRAM_RECT = (100.0, 90.0, 260.0, 250.0)
PAGE_WIDTH = 420.0
PAGE_HEIGHT = 595.0


@pytest.fixture(scope="session")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def main_window(qapp, tmp_path):
    """MainWindow com preferencias descartaveis.

    O `QSettings` e injetado (§22.4): antes o app o construia dentro do
    `__init__` e o teste precisava trocar a classe inteira por monkeypatch.
    """
    QtCore = pytest.importorskip("PySide6.QtCore")
    from chess_pdf_editor import app as app_module
    from chess_pdf_editor.pdf_service import clear_board_render_cache

    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.IniFormat)
    # O aviso de privacidade do Sprint 7 usa um QMessageBox de instancia (precisa de
    # checkbox), que o `no_modals` nao alcanca: ele so troca os metodos estaticos. Sem
    # esta chave o `exec()` bloquearia a suite para sempre. Quem testa o proprio aviso
    # limpa a chave e usa a fixture `privacy_prompt`.
    settings.setValue("remote_privacy_ack", True)
    clear_board_render_cache()

    window = app_module.MainWindow(settings=settings)
    try:
        yield window
    finally:
        window.close()
        clear_board_render_cache()


def make_pdf(path: Path, pages: int = 2) -> Path:
    """PDF de teste: um retangulo cinza no lugar do "diagrama" de cada pagina."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text(fitz.Point(72, 100), f"Pagina {index + 1}", fontsize=18)
        page.draw_rect(fitz.Rect(*DIAGRAM_RECT), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    doc.save(str(path))
    doc.close()
    return path


def process_until(qapp, predicate, timeout_sec: float = 20.0) -> bool:
    """Roda o loop de eventos ate `predicate()` virar verdadeiro.

    Os sinais de uma QThread chegam por `QueuedConnection`, ou seja, so sao
    entregues quando o loop de eventos roda — um `sleep` puro nunca veria o
    worker terminar.
    """
    QtCore = pytest.importorskip("PySide6.QtCore")
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        qapp.processEvents(QtCore.QEventLoop.AllEvents, 30)
    return bool(predicate())


@pytest.fixture
def no_modals(monkeypatch):
    """Neutraliza os dialogos modais.

    Um `QMessageBox.information` no fim do lote bloquearia o teste para sempre.
    Devolve a lista de titulos mostrados, para o teste poder conferir.
    """
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    shown: list[tuple[str, str]] = []

    def _record(kind):
        def _inner(parent, title, text, *args, **kwargs):
            shown.append((title, text))
            return QtWidgets.QMessageBox.Yes if kind == "question" else QtWidgets.QMessageBox.Ok

        return _inner

    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_record(name)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(_record("question")))
    return shown


@pytest.fixture
def privacy_prompt(monkeypatch):
    """Intercepta o aviso de envio para servidor externo (Sprint 7.3).

    Ele e um `QMessageBox` de instancia, porque precisa do checkbox "nao perguntar de
    novo" — entao nao da para neutraliza-lo trocando um metodo estatico. Aqui o
    `exec()` e substituido por uma funcao que registra o que foi mostrado e devolve a
    resposta programada.

    Uso: `privacy_prompt.answer = QMessageBox.Cancel` antes de disparar a acao;
    `privacy_prompt.shown` tem os textos exibidos.
    """
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")

    class _Prompt:
        def __init__(self) -> None:
            self.answer = QtWidgets.QMessageBox.Yes
            self.remember = False
            self.shown: list[str] = []

    prompt = _Prompt()

    def _exec(box_self):
        prompt.shown.append(f"{box_self.windowTitle()}|{box_self.text()}|{box_self.informativeText()}")
        checkbox = box_self.checkBox()
        if checkbox is not None:
            checkbox.setChecked(prompt.remember)
        return prompt.answer

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _exec)
    return prompt
