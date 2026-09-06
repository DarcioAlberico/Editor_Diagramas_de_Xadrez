"""Abrir um livro: o que acontece quando dá certo, e o que **não** pode acontecer
quando dá errado (§59.5, §59.6).

O defeito que originou este arquivo era de ordem, não de tratamento: `_open_pdf`
fechava o documento anterior antes de construir o novo, então um arquivo que não
abre deixava a janela segurando um documento fechado — e o próprio `closeEvent`
passava a estourar com `document closed`. Um clique no arquivo errado deixava o
app num estado de que ele não saía nem fechando.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_pdf

fitz = pytest.importorskip("fitz")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _nao_e_pdf(tmp_path: Path, name: str = "quebrado.pdf") -> Path:
    caminho = tmp_path / name
    caminho.write_bytes(b"isto nao e um PDF")
    return caminho


# ---------------------------------------------------------------------------
# Abertura transacional (§59.5)
# ---------------------------------------------------------------------------


def test_a_broken_file_keeps_the_previous_book_usable(main_window, tmp_path, no_modals) -> None:
    """Falhar ao abrir não pode custar o livro que já estava aberto."""
    bom = make_pdf(tmp_path / "bom.pdf", pages=2)
    main_window._open_pdf(str(bom), clear_ops=True)

    with pytest.raises(Exception):
        main_window._open_pdf(str(_nao_e_pdf(tmp_path)), clear_ops=True)

    assert main_window.current_pdf_path == str(bom), "o caminho mudou para um livro que não abriu"
    assert main_window.pdf_service is not None
    # A prova de que o serviço continua vivo, e não só presente.
    assert main_window.pdf_service.render_page(0) is not None


def test_the_window_still_closes_after_a_failed_open(main_window, tmp_path, no_modals) -> None:
    """`closeEvent` fecha o serviço; com um documento já fechado ali, ele estourava."""
    main_window._open_pdf(str(make_pdf(tmp_path / "bom.pdf")), clear_ops=True)
    with pytest.raises(Exception):
        main_window._open_pdf(str(_nao_e_pdf(tmp_path)), clear_ops=True)

    main_window.close()  # não pode levantar


def test_closing_the_service_twice_is_harmless(tmp_path) -> None:
    """Fechar de novo é condição normal de um caminho de erro, não defeito de quem chama."""
    from chess_pdf_editor.pdf_service import PdfService

    service = PdfService(str(make_pdf(tmp_path / "book.pdf")))
    service.close()
    service.close()


def test_the_dialog_reports_the_failure_instead_of_crashing(
    main_window, tmp_path, monkeypatch, no_modals
) -> None:
    """Pelo diálogo, o erro tem de virar mensagem — não rastro num console."""
    main_window._open_pdf(str(make_pdf(tmp_path / "bom.pdf")), clear_ops=True)
    quebrado = _nao_e_pdf(tmp_path)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(quebrado), "")),
    )

    main_window._open_pdf_dialog()

    assert any("Não foi possível abrir o PDF" == titulo for titulo, _ in no_modals)
    assert main_window.pdf_service.render_page(0) is not None


def test_a_project_pointing_at_an_unreadable_pdf_is_refused(
    main_window, tmp_path, no_modals
) -> None:
    """Seguir carregaria as operações sobre o livro **anterior**, que continua aberto."""
    from chess_pdf_editor.autosave import write_project_atomically
    from chess_pdf_editor.project_state import ProjectState

    main_window._open_pdf(str(make_pdf(tmp_path / "bom.pdf")), clear_ops=True)
    quebrado = _nao_e_pdf(tmp_path, "livro.pdf")
    projeto = tmp_path / "projeto.json"
    write_project_atomically(
        str(projeto),
        ProjectState(
            source_pdf=str(quebrado),
            source_pdf_fingerprint={},
            operations=[OverlayOperation(page_num=0, rect_pdf=(1.0, 1.0, 2.0, 2.0), fen=FEN)],
        ),
    )

    assert main_window._load_project_from_path(str(projeto), show_dialogs=True) is False
    assert main_window.operations == [], "o projeto entrou apesar de o livro não abrir"
    assert any("Erro ao carregar projeto" == titulo for titulo, _ in no_modals)


# ---------------------------------------------------------------------------
# As janelas que vivem do livro (§59.6)
# ---------------------------------------------------------------------------


def test_changing_the_book_closes_the_gallery(main_window, tmp_path, no_modals) -> None:
    """Mesma razão que já fechava o navegador: outro livro, outras páginas.

    A galeria guarda o caminho do PDF **e** referências para as listas da janela
    (§52.3). Depois de `_open_pdf`, as duas coisas são de outro livro: as miniaturas
    seguem renderizando o anterior e o rodapé edita uma lista que foi substituída por
    `[]` — sem erro nenhum, que é o pior jeito de uma edição sumir.
    """
    primeiro = make_pdf(tmp_path / "primeiro.pdf", pages=2)
    segundo = make_pdf(tmp_path / "segundo.pdf", pages=2)
    main_window._open_pdf(str(primeiro), clear_ops=True)
    main_window.operations.append(
        OverlayOperation(page_num=0, rect_pdf=(100.0, 300.0, 260.0, 460.0), fen=FEN)
    )
    main_window._refresh_operations_list()
    main_window._open_gallery()
    assert main_window.gallery_dialog is not None

    main_window._open_pdf(str(segundo), clear_ops=True)

    assert main_window.gallery_dialog is None, "a galeria sobreviveu à troca de livro"
