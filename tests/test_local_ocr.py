"""Motor local de reconhecimento (Sprint 7.1 e 7.2), de ponta a ponta.

As imagens de `tests/data/local_ocr/` são diagramas **reais** de livro, do split de
teste do dataset que treinou o classificador (ou seja, o modelo nunca os viu no
treino), reduzidos a 320 px — o tamanho típico de um diagrama numa página renderizada
em zoom 2,0. A FEN de cada um está em `boards.json`.

Usar o render do próprio app como fixture não serviria: ele desenha as casas escuras
hachuradas, um estilo que não existe em livro nenhum, e o classificador sai com
confiança 0,005. O teste passaria a medir o renderer, não o reconhecimento.

Tudo aqui pula quando as dependências opcionais não estão instaladas — é exatamente a
situação em que o app cai no motor remoto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chess_pdf_editor import local_ocr

pytestmark = pytest.mark.skipif(
    not local_ocr.available(), reason=local_ocr.unavailable_reason() or "motor local indisponível"
)

DATA_DIR = Path(__file__).parent / "data" / "local_ocr"
PAGE_WIDTH = 420.0
PAGE_HEIGHT = 595.0
#: Onde o diagrama é colado na página de teste, em pontos PDF.
BOARD_RECT = (100.0, 300.0, 260.0, 460.0)
ZOOM = 2.0


def _boards() -> dict[str, dict]:
    return json.loads((DATA_DIR / "boards.json").read_text(encoding="utf-8"))


def _board_png(name: str) -> bytes:
    return (DATA_DIR / name).read_bytes()


@pytest.fixture(scope="module")
def recognizer():
    return local_ocr.get_recognizer()


def _page_png(board_png: bytes, rect=BOARD_RECT) -> bytes:
    """Página de livro sintética: texto em volta e o diagrama real colado no meio."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(fitz.Point(72, 100), "Capitulo 3 - finais de torre", fontsize=13)
    page.insert_text(fitz.Point(72, 520), "As brancas jogam e ganham.", fontsize=11)
    page.insert_image(fitz.Rect(*rect), stream=board_png, keep_proportion=False)
    pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB)
    png = pix.tobytes("png")
    doc.close()
    return png


# ---------------------------------------------------------------------------
# Disponibilidade
# ---------------------------------------------------------------------------


def test_the_bundled_model_ships_with_the_project() -> None:
    """Sem o .pt no lugar o motor local não existe para o usuário final."""
    assert local_ocr.bundled_model_path().is_file()


def test_an_explicit_model_path_wins_over_the_bundled_one(tmp_path: Path) -> None:
    fake = tmp_path / "outro.pt"
    fake.write_bytes(b"nao importa o conteudo para a resolucao de caminho")
    assert local_ocr.default_model_path(str(fake)) == fake


def test_a_missing_preferred_model_falls_back_instead_of_failing(tmp_path: Path) -> None:
    assert local_ocr.default_model_path(str(tmp_path / "nao-existe.pt")) == (
        local_ocr.bundled_model_path()
    )


# ---------------------------------------------------------------------------
# Reconhecimento
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_boards()))
def test_a_real_diagram_is_read_correctly(recognizer, name: str) -> None:
    expected = _boards()[name]["fen"]
    prediction = recognizer.predict(_board_png(name), assume_whole_image=True)

    assert prediction.results, "nenhum tabuleiro reconhecido"
    assert prediction.results[0].fen == expected


def test_the_reported_confidence_is_the_worst_square_not_the_average(recognizer) -> None:
    """~77% das casas são vazias e triviais: a média fica alta mesmo com erro."""
    name = sorted(_boards())[0]
    result = recognizer.predict(_board_png(name), assume_whole_image=True).results[0]
    assert result.confidence is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence > 0.8, "diagrama limpo tem de sair confiante"


def test_no_network_is_used(recognizer, monkeypatch) -> None:
    """A promessa do modo offline: qualquer requisição HTTP aqui é um bug."""
    import requests

    def _boom(*args, **kwargs):
        raise AssertionError("o motor local tentou usar a rede")

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)

    name = sorted(_boards())[0]
    assert recognizer.predict(_board_png(name), assume_whole_image=True).results


def test_an_empty_image_is_refused(recognizer) -> None:
    from chess_pdf_editor.local_ocr.engine import LocalOcrError

    with pytest.raises(LocalOcrError):
        recognizer.predict(b"", assume_whole_image=True)


def test_a_page_without_diagrams_returns_nothing(recognizer) -> None:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(fitz.Point(72, 100), "Somente texto nesta pagina.", fontsize=12)
    png = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB).tobytes("png")
    doc.close()

    prediction = recognizer.predict(png)
    assert prediction.results == []
    assert prediction.status == 204


# ---------------------------------------------------------------------------
# Detecção na página inteira
# ---------------------------------------------------------------------------


def test_a_diagram_is_found_inside_a_page_with_text(recognizer) -> None:
    name = sorted(_boards())[0]
    prediction = recognizer.predict(_page_png(_board_png(name)))

    assert len(prediction.results) == 1
    result = prediction.results[0]
    assert result.fen == _boards()[name]["fen"]

    # A caixa devolvida é normalizada 0–1 sobre a imagem enviada: é o mesmo contrato
    # do serviço remoto, e é o que o worker converte para pontos PDF.
    x0 = (BOARD_RECT[0]) / PAGE_WIDTH
    x1 = (BOARD_RECT[2]) / PAGE_WIDTH
    assert result.xc == pytest.approx((x0 + x1) / 2.0, abs=0.02)
    assert result.width == pytest.approx(x1 - x0, abs=0.02)


def test_two_diagrams_on_one_page_are_both_found(recognizer) -> None:
    names = sorted(_boards())
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(fitz.Rect(60, 80, 200, 220), stream=_board_png(names[0]), keep_proportion=False)
    page.insert_image(fitz.Rect(60, 340, 200, 480), stream=_board_png(names[1]), keep_proportion=False)
    png = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB).tobytes("png")
    doc.close()

    prediction = recognizer.predict(png)
    found = {result.fen for result in prediction.results}
    assert found == {_boards()[name]["fen"] for name in names}


# ---------------------------------------------------------------------------
# Snap da seleção (§6.2)
# ---------------------------------------------------------------------------


def _expected_rect_img() -> tuple[float, float, float, float]:
    return tuple(value * ZOOM for value in BOARD_RECT)


def _sloppy(target, dx0, dy0, dx1, dy1):
    """Seleção à mão: encolhida e deslocada em relação ao diagrama."""
    width = target[2] - target[0]
    height = target[3] - target[1]
    return (
        target[0] + width * dx0,
        target[1] + height * dy0,
        target[2] - width * dx1,
        target[3] - height * dy1,
    )


def test_a_sloppy_selection_gets_much_closer_to_the_board() -> None:
    from chess_pdf_editor.local_ocr.engine import refine_rect

    page_png = _page_png(_board_png(sorted(_boards())[0]))
    target = _expected_rect_img()
    sloppy = _sloppy(target, 0.09, 0.05, 0.04, 0.10)

    refined = refine_rect(page_png, sloppy)
    assert refined is not None, "o ajuste não encontrou o tabuleiro"

    before = max(abs(sloppy[i] - target[i]) for i in range(4))
    after = max(abs(refined[i] - target[i]) for i in range(4))
    # O alvo é onde a imagem foi colada; a borda que o detector encontra é a da grade,
    # alguns pixels para dentro porque o recorte do dataset carrega uma margem. Por
    # isso a asserção é de aproximação, não de igualdade com o retângulo de colagem.
    assert after < before / 3.0, f"o ajuste mal melhorou: {before:.1f} px -> {after:.1f} px"
    assert after <= 10.0


def test_snapping_converges_to_the_same_edge_from_different_selections() -> None:
    """A promessa do ajuste: o resultado é a borda do tabuleiro, não a sua entrada."""
    from chess_pdf_editor.local_ocr.engine import refine_rect

    page_png = _page_png(_board_png(sorted(_boards())[0]))
    target = _expected_rect_img()

    from_inside = refine_rect(page_png, _sloppy(target, 0.10, 0.08, 0.06, 0.11))
    from_outside = refine_rect(page_png, _sloppy(target, -0.05, -0.07, -0.04, -0.06))

    assert from_inside is not None and from_outside is not None
    assert max(abs(from_inside[i] - from_outside[i]) for i in range(4)) <= 2.0


def test_snapping_an_already_exact_selection_is_stable() -> None:
    """Clicar duas vezes em Ajustar não pode ficar empurrando a seleção."""
    from chess_pdf_editor.local_ocr.engine import refine_rect

    page_png = _page_png(_board_png(sorted(_boards())[0]))
    once = refine_rect(page_png, _sloppy(_expected_rect_img(), 0.08, 0.08, 0.08, 0.08))
    assert once is not None
    twice = refine_rect(page_png, once)
    assert twice is not None
    assert max(abs(twice[i] - once[i]) for i in range(4)) <= 2.0


def test_snapping_far_from_any_board_changes_nothing() -> None:
    """Mexer na seleção sem ter achado borda seria pior que não fazer nada."""
    from chess_pdf_editor.local_ocr.engine import refine_rect

    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(fitz.Point(72, 100), "Somente texto.", fontsize=12)
    png = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB).tobytes("png")
    doc.close()

    assert refine_rect(png, (200.0, 700.0, 400.0, 900.0)) is None


def test_a_selection_too_small_is_refused() -> None:
    from chess_pdf_editor.local_ocr.engine import refine_rect

    page_png = _page_png(_board_png(sorted(_boards())[0]))
    assert refine_rect(page_png, (200.0, 600.0, 205.0, 605.0)) is None


def test_snapping_does_not_need_the_classifier(monkeypatch) -> None:
    """`Ajustar à borda` só usa o detector: tem de funcionar sem modelo carregado."""
    from chess_pdf_editor.local_ocr import engine as engine_module
    from chess_pdf_editor.local_ocr.engine import refine_rect

    def _boom(*args, **kwargs):
        raise AssertionError("o ajuste carregou o classificador sem precisar")

    monkeypatch.setattr(engine_module, "_load_shared_model", _boom)

    page_png = _page_png(_board_png(sorted(_boards())[0]))
    target = _expected_rect_img()
    assert refine_rect(page_png, (target[0] + 8, target[1] + 8, target[2] - 8, target[3] - 8))
