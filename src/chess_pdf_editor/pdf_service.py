from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import quote

import fitz  # type: ignore
from PIL import Image

from .logging_config import get_logger
from .renderer import render_board_pdf, render_board_png
from .types import EraseOperation, OverlayOperation, Rect

logger = get_logger("pdf")

# Cache dos diagramas renderizados (mesma FEN + mesmo tamanho => mesmo asset).
# Vale tanto para a exportacao em lote quanto para a previa ao vivo, onde a
# mesma posicao e re-renderizada a cada ajuste de padding/borda.
_BOARD_PDF_CACHE: "OrderedDict[tuple[str, int, str], Optional[bytes]]" = OrderedDict()
_BOARD_PNG_CACHE: "OrderedDict[tuple[str, int, str], bytes]" = OrderedDict()
_BOARD_CACHE_MAX = 48
# A exportacao roda num worker (workers.ExportWorker) enquanto a previa ao vivo
# continua renderizando na thread da UI: as duas mexem nestes OrderedDicts.
# `move_to_end` + `popitem` nao sao atomicos entre si, entao precisam do lock.
_BOARD_CACHE_LOCK = threading.Lock()


def _board_cache_scope() -> str:
    # A fonte Merida pode ser trocada em tempo de execucao pela GUI; o caminho
    # ativo faz parte da identidade do asset renderizado.
    return os.getenv("CHESS_MERIDA_FONT", "").strip()


def clear_board_render_cache() -> None:
    with _BOARD_CACHE_LOCK:
        _BOARD_PDF_CACHE.clear()
        _BOARD_PNG_CACHE.clear()


def _cached_board_pdf(piece_placement: str, size_px: int) -> Optional[bytes]:
    key = (piece_placement, int(size_px), _board_cache_scope())
    with _BOARD_CACHE_LOCK:
        if key in _BOARD_PDF_CACHE:
            _BOARD_PDF_CACHE.move_to_end(key)
            return _BOARD_PDF_CACHE[key]

    # Render fora do lock: e a parte cara, e duas threads renderizarem a mesma
    # posicao uma vez a mais e melhor do que uma segurar a outra.
    value = render_board_pdf(piece_placement, size_px=size_px)
    with _BOARD_CACHE_LOCK:
        _BOARD_PDF_CACHE[key] = value
        while len(_BOARD_PDF_CACHE) > _BOARD_CACHE_MAX:
            _BOARD_PDF_CACHE.popitem(last=False)
    return value


def _cached_board_png(piece_placement: str, size_px: int) -> bytes:
    key = (piece_placement, int(size_px), _board_cache_scope())
    with _BOARD_CACHE_LOCK:
        if key in _BOARD_PNG_CACHE:
            _BOARD_PNG_CACHE.move_to_end(key)
            return _BOARD_PNG_CACHE[key]

    value = render_board_png(piece_placement, size_px=size_px)
    with _BOARD_CACHE_LOCK:
        _BOARD_PNG_CACHE[key] = value
        while len(_BOARD_PNG_CACHE) > _BOARD_CACHE_MAX:
            _BOARD_PNG_CACHE.popitem(last=False)
    return value


@dataclass
class RenderedPage:
    page_num: int
    width_px: int
    height_px: int
    image_png: bytes
    matrix: tuple[float, float, float, float, float, float]


def _pixmap_to_png(pix: fitz.Pixmap) -> bytes:
    # Render em RGBA e compoe sobre branco para evitar artefatos pretos
    # em PDFs com transparencias / mascaras.
    image_rgba = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    image_rgb = Image.new("RGB", image_rgba.size, "white")
    image_rgb.paste(image_rgba, mask=image_rgba.getchannel("A"))

    buffer = io.BytesIO()
    image_rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _render_page_object(page: fitz.Page, page_num: int, zoom: float) -> RenderedPage:
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=True)
    return RenderedPage(
        page_num=page_num,
        width_px=pix.width,
        height_px=pix.height,
        image_png=_pixmap_to_png(pix),
        matrix=(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f),
    )


# ---------------------------------------------------------------------------
# Os dois espaços de coordenada de uma página (§48)
#
# Tudo que o usuário vê e escolhe está no espaço de `page.rect`: a seleção, a
# galeria, o `rect_pdf` gravado no projeto. Mas **escrever** na página —
# `show_pdf_page`, `insert_image`, `add_redact_annot`, `draw_rect`,
# `insert_text`, `insert_link` — e **ler texto** dela — `get_text`, inclusive o
# `clip=` — são no espaço de escrita, que é o que `page.transformation_matrix`
# produz. Os dois só coincidem quando não há rotação nem CropBox deslocada.
#
# A conversão é uma só, e vale para as quatro rotações combinadas com qualquer
# CropBox e qualquer MediaBox (medido em 80 geometrias, §48.2):
#
#     page.rect = (escrita − origem) * rotation_matrix
#     escrita   = page.rect * derotation_matrix + origem
#
# onde `origem` é o canto superior-esquerdo da CropBox **no espaço de escrita**.
# Sem rotação essa origem é (0, 0) — a `transformation_matrix` já embute o
# deslocamento da CropBox —, e por isso o caso comum passa por aqui inalterado.
# ---------------------------------------------------------------------------


def _write_space_cropbox(page: fitz.Page) -> fitz.Rect:
    """A CropBox — a região visível — em coordenadas de escrita.

    É também o recorte correto para qualquer retângulo já convertido: em página
    girada, `page.rect` tem largura e altura trocadas em relação ao espaço de
    escrita, e usá-lo como limite corta o que é válido (§48.3).
    """
    media = page.mediabox
    crop = page.cropbox
    native = fitz.Rect(crop.x0, media.y1 - crop.y1, crop.x1, media.y1 - crop.y0)
    return native * page.transformation_matrix


def _to_write_space(page: fitz.Page, rect: Rect) -> fitz.Rect:
    """Retângulo do espaço que o usuário vê para o espaço de escrita."""
    origin = _write_space_cropbox(page).tl
    return (
        fitz.Rect(rect)
        * page.derotation_matrix
        * fitz.Matrix(1, 0, 0, 1, origin.x, origin.y)
    )


def _render_page_region(page: fitz.Page, zoom: float, rect_pdf: Rect) -> bytes:
    matrix = fitz.Matrix(zoom, zoom)
    clip = fitz.Rect(rect_pdf) & page.rect
    if clip.is_empty:
        raise ValueError("Região vazia para render.")
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=True, clip=clip)
    return _pixmap_to_png(pix)


def operation_signature(op: OverlayOperation) -> tuple:
    """Identidade visual de uma substituicao (usada para cache de previa)."""
    x0, y0, x1, y1 = op.rect_pdf
    return (
        int(op.page_num),
        round(float(x0), 3),
        round(float(y0), 3),
        round(float(x1), 3),
        round(float(y1), 3),
        str(op.fen),
        round(float(getattr(op, "whiteout_padding_left_pt", 0.5)), 3),
        round(float(getattr(op, "whiteout_padding_top_pt", 0.5)), 3),
        round(float(getattr(op, "whiteout_padding_right_pt", 0.5)), 3),
        round(float(getattr(op, "whiteout_padding_bottom_pt", 0.5)), 3),
        round(float(getattr(op, "border_width_pt", 0.0)), 3),
        str(getattr(op, "side_to_move", "w")),
        int(getattr(op, "fullmove_number", 1)),
    )


def erase_signature(op: EraseOperation) -> tuple:
    x0, y0, x1, y1 = op.rect_pdf
    return (
        int(op.page_num),
        round(float(x0), 3),
        round(float(y0), 3),
        round(float(x1), 3),
        round(float(y1), 3),
    )


class PdfService:
    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = str(pdf_path)
        self.doc = fitz.open(self.pdf_path)
        self._preview_doc: Optional[fitz.Document] = None
        self._preview_signature: Optional[tuple] = None

    def close(self) -> None:
        """Fecha o documento. Chamar duas vezes é inofensivo (§59.5).

        Não é zelo: fechar de novo é uma condição **normal** num caminho de erro. A
        janela fecha o serviço ao trocar de livro e o `closeEvent` fecha ao sair, e
        entre os dois cabe uma abertura que falhou. Sem esta guarda o PyMuPDF levanta
        `document closed` de dentro do `closeEvent` — ou seja, o aplicativo não sairia
        nem fechando.
        """
        self._discard_preview_doc()
        if not getattr(self.doc, "is_closed", False):
            self.doc.close()

    @property
    def page_count(self) -> int:
        return len(self.doc)

    def render_page(self, page_num: int, zoom: float = 2.0) -> RenderedPage:
        return _render_page_object(self.doc[page_num], page_num, zoom)

    def render_region(self, page_num: int, zoom: float, rect_pdf: Rect) -> bytes:
        """PNG da regiao pedida na pagina original (lado 'antes')."""
        return _render_page_region(self.doc[page_num], zoom, rect_pdf)

    # ------------------------------------------------------------------
    # Previa do resultado (mesmo caminho de codigo da exportacao)
    # ------------------------------------------------------------------

    def _discard_preview_doc(self) -> None:
        if self._preview_doc is not None:
            try:
                self._preview_doc.close()
            except Exception:
                logger.debug("Documento de previa ja estava fechado", exc_info=True)
        self._preview_doc = None
        self._preview_signature = None

    def _preview_page(
        self,
        page_num: int,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
    ) -> fitz.Page:
        signature = (
            self.pdf_path,
            int(page_num),
            bool(whiteout),
            bool(include_lichess_link),
            bool(erase_coordinates),
            _board_cache_scope(),
            tuple(operation_signature(op) for op in operations),
            tuple(erase_signature(op) for op in erase_operations),
        )
        if self._preview_doc is not None and self._preview_signature == signature:
            return self._preview_doc[0]

        self._discard_preview_doc()
        preview_doc = fitz.open()
        try:
            preview_doc.insert_pdf(
                self.doc,
                from_page=page_num,
                to_page=page_num,
                links=False,
                annots=False,
            )
            page = preview_doc[0]
            apply_page_operations(
                page,
                [replace(op, page_num=0) for op in operations],
                erase_operations=[replace(op, page_num=0) for op in erase_operations],
                whiteout=whiteout,
                include_lichess_link=include_lichess_link,
                erase_coordinates=erase_coordinates,
            )
        except Exception:
            preview_doc.close()
            raise

        self._preview_doc = preview_doc
        self._preview_signature = signature
        return preview_doc[0]

    def render_page_with_operations(
        self,
        page_num: int,
        zoom: float,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
    ) -> RenderedPage:
        """Renderiza a pagina ja com as alteracoes aplicadas (WYSIWYG)."""
        page = self._preview_page(
            page_num,
            operations,
            erase_operations=erase_operations,
            whiteout=whiteout,
            include_lichess_link=include_lichess_link,
            erase_coordinates=erase_coordinates,
        )
        return _render_page_object(page, page_num, zoom)

    def render_region_with_operations(
        self,
        page_num: int,
        zoom: float,
        rect_pdf: Rect,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
    ) -> bytes:
        """PNG de um recorte da pagina ja com as alteracoes (lado 'depois')."""
        page = self._preview_page(
            page_num,
            operations,
            erase_operations=erase_operations,
            whiteout=whiteout,
            include_lichess_link=include_lichess_link,
            erase_coordinates=erase_coordinates,
        )
        return _render_page_region(page, zoom, rect_pdf)

    # ------------------------------------------------------------------

    def image_rect_to_pdf_rect(
        self,
        page_num: int,
        rect_img: Rect,
        matrix_tuple: tuple[float, float, float, float, float, float],
    ) -> Rect:
        page = self.doc[page_num]
        matrix = fitz.Matrix(*matrix_tuple)
        inv = fitz.Matrix(matrix)
        inv.invert()

        x0, y0, x1, y1 = rect_img
        p0 = fitz.Point(x0, y0) * inv
        p1 = fitz.Point(x1, y1) * inv
        rect = fitz.Rect(p0, p1)
        rect = rect & page.rect
        return (rect.x0, rect.y0, rect.x1, rect.y1)

    def pdf_rect_to_image_rect(
        self,
        page_num: int,
        rect_pdf: Rect,
        matrix_tuple: tuple[float, float, float, float, float, float],
    ) -> Rect:
        page = self.doc[page_num]
        rect = fitz.Rect(rect_pdf) & page.rect
        matrix = fitz.Matrix(*matrix_tuple)
        p0 = fitz.Point(rect.x0, rect.y0) * matrix
        p1 = fitz.Point(rect.x1, rect.y1) * matrix
        out = fitz.Rect(p0, p1)
        return (out.x0, out.y0, out.x1, out.y1)

    def extract_text_from_pdf_rect(self, page_num: int, rect_pdf: Rect) -> str:
        page = self.doc[page_num]
        # O `clip=` do `get_text` é em espaço de escrita, não no de `page.rect`
        # (medido: §48.3). Passar a seleção crua devolvia string vazia em toda
        # página girada — e o estudo lê o texto da página por aqui.
        rect = _to_write_space(page, rect_pdf) & _write_space_cropbox(page)
        if rect.is_empty:
            return ""
        return page.get_text("text", clip=rect).strip()


def _points_to_pixels(points: float, dpi: int = 300) -> int:
    return max(64, int(round((points / 72.0) * dpi)))


def _erase_rects(page: fitz.Page, rects: Sequence[fitz.Rect]) -> None:
    """Apaga todas as areas de uma vez.

    Uma unica passada de `apply_redactions` evita reescrever o content stream da
    pagina N vezes (custo relevante na previa ao vivo) e garante que nenhum
    overlay ja desenhado seja apagado por uma redacao posterior.
    """
    targets = [rect for rect in rects if not rect.is_empty]
    if not targets:
        return
    try:
        for rect in targets:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        return
    except Exception:
        # Redaction remove o conteudo subjacente de forma mais robusta, mas se
        # falhar (PDF exotico) pintar branco por cima ainda resolve visualmente.
        logger.warning(
            "apply_redactions falhou em %d area(s); usando retangulo branco",
            len(targets),
            exc_info=True,
        )
        for rect in targets:
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)


def operation_full_fen(op: OverlayOperation) -> str:
    """FEN completa da operação: peças mais as etiquetas (lado a jogar, lance).

    Pública porque quem edita as etiquetas precisa **ver** o que elas produzem —
    é a única saída delas no PDF, já que nenhuma muda um pixel do tabuleiro
    desenhado. O navegador (§54) mostra esta string; uma segunda cópia da regra
    lá dentro seria o par mantido à mão da §45, e ele divergiria no dia em que o
    campo `halfmove` deixasse de ser fixo.
    """
    side = str(getattr(op, "side_to_move", "w"))
    if side not in {"w", "b"}:
        side = "w"
    try:
        fullmove = max(1, int(getattr(op, "fullmove_number", 1)))
    except Exception:
        fullmove = 1
    return f"{op.fen} {side} - - 0 {fullmove}"


def wants_lichess_link(op: OverlayOperation, global_default: bool) -> bool:
    """Este diagrama leva link Lichess?

    Um lugar só decide, e todos os caminhos que desenham diagrama passam por aqui —
    exportação, prévia e galeria. A regra (`None` segue a global) escrita em três
    lugares seria o par mantido à mão que a §45 documenta: a prévia divergiria da
    exportação, e a §21 garante por teste que as duas são iguais byte a byte.
    """
    escolha = getattr(op, "include_lichess_link", None)
    if escolha is None:
        return bool(global_default)
    return bool(escolha)


def operation_lichess_url(op: OverlayOperation) -> str:
    """URL de análise que o link do PDF aponta. Pública pelo mesmo motivo acima."""
    full_fen = " ".join(operation_full_fen(op).split())
    parts = full_fen.split(" ")
    if not parts:
        return "https://lichess.org/analysis"
    piece_placement = parts[0]
    if len(parts) == 1:
        return f"https://lichess.org/analysis/{piece_placement}"
    fen_tail = " ".join(parts[1:])
    return f"https://lichess.org/analysis/{piece_placement}{quote(' ' + fen_tail, safe='')}"


LINK_TEXT = "Lichess"
LINK_GAP_PT = 2.0


def _page_words(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    """Palavras da página com a caixa de cada uma.

    `get_text(clip=...)` **não** serve para detectar sobreposição: o `clip` do
    PyMuPDF devolve o texto *contido* no retângulo, então uma legenda larga
    passando por trás de um rótulo estreito não apareceria. Aqui as caixas vêm
    todas e quem decide a interseção é o chamador.
    """
    try:
        return [
            (fitz.Rect(word[0], word[1], word[2], word[3]), str(word[4]))
            for word in page.get_text("words")
        ]
    except Exception:
        logger.warning("Não foi possível ler as palavras da página", exc_info=True)
        return []


def _region_is_free(page: fitz.Page, rect: fitz.Rect) -> bool:
    """Não há texto do livro nesta área?

    A checagem roda **depois** das redações de `apply_page_operations`, então o
    que o whiteout já apagou não conta como ocupado — e o rótulo de uma operação
    anterior na mesma página conta, o que impede dois links se sobreporem.
    """
    if rect.is_empty:
        return False
    for box, text in _page_words(page):
        if text.strip() and not (box & rect).is_empty:
            return False
    return True


def _link_label_slots(page: fitz.Page, rect: fitz.Rect, font_size: float) -> list[float]:
    """Linhas de base candidatas para o rótulo, da preferida para a alternativa."""
    slots = [
        rect.y1 + LINK_GAP_PT + font_size,  # abaixo do diagrama
        rect.y0 - LINK_GAP_PT,              # acima
    ]
    # `rect` já é de escrita, então o limite da página também tem de ser (§48.3).
    visible = _write_space_cropbox(page)
    return [
        baseline
        for baseline in slots
        if baseline + 2.0 <= visible.y1 and baseline - font_size >= visible.y0
    ]


def _insert_lichess_link_below_diagram(page: fitz.Page, rect: fitz.Rect, op: OverlayOperation) -> None:
    """Rótulo `Lichess` clicável, sem escrever por cima do livro (§22.4).

    A versão anterior desenhava o rótulo logo abaixo do diagrama e pronto. Só que
    é justamente ali que o livro costuma pôr a legenda ("Diagrama 12", "as brancas
    jogam"): o texto azul saía sobreposto ao do autor e os dois ficavam ilegíveis —
    num arquivo que o usuário vai ler, não num rascunho.

    Agora o rótulo procura espaço livre. Se não houver nenhum, o **diagrama
    inteiro** vira a área clicável, sem texto visível: o link continua existindo e
    nada do livro é estragado. Perde-se a descoberta visual, que é o preço certo a
    pagar — a alternativa é vandalizar a página.
    """
    font_size = min(12.0, max(7.0, rect.height * 0.09))
    text_width = fitz.get_text_length(LINK_TEXT, fontname="helv", fontsize=font_size)
    center_x = (rect.x0 + rect.x1) / 2.0
    visible = _write_space_cropbox(page)
    x0 = max(visible.x0 + 1.0, center_x - (text_width / 2.0) - 2.0)
    x1 = min(visible.x1 - 1.0, center_x + (text_width / 2.0) + 2.0)

    uri = operation_lichess_url(op)

    if x1 > x0:
        for baseline_y in _link_label_slots(page, rect, font_size):
            label_rect = fitz.Rect(x0, baseline_y - font_size, x1, baseline_y + 2.0) & visible
            if not _region_is_free(page, label_rect):
                continue
            page.insert_text(
                fitz.Point(x0 + 2.0, baseline_y),
                LINK_TEXT,
                fontsize=font_size,
                fontname="helv",
                color=(0.0, 0.2, 1.0),
                overlay=True,
            )
            page.insert_link({"kind": fitz.LINK_URI, "from": label_rect, "uri": uri})
            return

    fallback = fitz.Rect(rect) & visible
    if fallback.is_empty:
        return
    logger.info(
        "Sem espaço livre para o rótulo Lichess na página %d; o diagrama virou o link",
        page.number + 1 if page.number is not None else 0,
    )
    page.insert_link({"kind": fitz.LINK_URI, "from": fallback, "uri": uri})


# --- coordenadas residuais do diagrama original ------------------------------
#
# O diagrama do livro quase sempre traz as coordenadas impressas em volta do
# tabuleiro (a-h embaixo, 1-8 na lateral). O whiteout cobre o tabuleiro e um
# padding pequeno; as coordenadas ficam **fora** dele e sobrevivem à substituição,
# emolduraando o diagrama novo com as letrinhas do antigo.
#
# Até aqui a saída era manual: selecionar cada faixa e clicar em `Adicionar
# apagamento`, diagrama por diagrama. Num livro de 300 diagramas isso é trabalho
# de tarde inteira, e é o tipo de coisa que se erra por cansaço.
#
# A detecção é deliberadamente conservadora — apagar texto do livro por engano é
# muito pior que deixar uma letrinha:
#
#   1. só palavras de **um caractere** em `a-h` ou `1-8`;
#   2. só na faixa em volta do tabuleiro, e **fora** dele;
#   3. letras só acima/abaixo do tabuleiro, dígitos só à esquerda/direita;
#   4. e — a regra que de fato segura o resto — só quando há **uma fileira delas**:
#      pelo menos 4, alinhadas entre si.
#
# A regra 4 não é excesso de zelo. Em português, `a` e `e` são palavras inteiras, e
# uma legenda "Diagrama 12 - brancas jogam **e** ganham" logo abaixo do diagrama cai
# direto nas regras 1 a 3. Sozinho, esse `e` não forma fileira com ninguém; as oito
# coordenadas de verdade formam. Sem essa regra, o apagamento comia a legenda do
# autor — foi o que o teste mostrou na primeira versão.

# Medido em livros reais: a fileira de coordenadas quase nunca sai como oito
# palavras de um caractere. O PDF a guarda num único text run, e a extração
# devolve `abcdefgh` inteiro — ou `abcdef` + `gh`, quando o espaçamento quebra o
# run no meio. A primeira versão deste detector achava 10 diagramas em 147 pelo
# motivo mais bobo possível: exigia palavras de um caractere.
#
# Daí as duas formas aceitas:
#
#   palavra de 1 caractere  → precisa de fileira (>= 4 alinhadas), porque `a` e
#                             `e` são palavras inteiras em português;
#   corrida de 2+           → precisa ser um trecho **contíguo e em ordem** de
#                             `abcdefgh` / `12345678` / `87654321`.
#
# A segunda regra é o que separa `cdef` de `faced` — as duas só têm letras de
# `a`-`h`, mas só a primeira é um pedaço da sequência.
_FILE_LABELS = frozenset("abcdefgh")
_RANK_LABELS = frozenset("12345678")

_FILE_SEQUENCE = "abcdefgh"
_RANK_SEQUENCES = ("12345678", "87654321")

#: Quanto a soma das corridas precisa cobrir do lado do tabuleiro para valer.
MIN_RUN_COVERAGE = 0.45


def _coordinate_run_kind(token: str) -> Optional[str]:
    """`"file"`, `"rank"` ou `None` para uma corrida de 2+ caracteres."""
    if len(token) < 2:
        return None
    lowered = token.lower()
    if lowered in _FILE_SEQUENCE:
        return "file"
    if any(token in sequence for sequence in _RANK_SEQUENCES):
        return "rank"
    return None

#: Largura da faixa examinada em volta do tabuleiro, como fração do lado dele.
COORDINATE_RING_RATIO = 0.10
COORDINATE_RING_MIN_PT = 9.0
COORDINATE_RING_MAX_PT = 30.0

#: Quantas coordenadas alinhadas fazem uma fileira. Um diagrama traz 8; livro que
#: imprime só as pontas traz 2, e esse caso fica de fora de propósito — o risco de
#: falso positivo com 2 é alto demais.
MIN_LABELS_IN_A_ROW = 4

#: Quanto uma coordenada pode fugir da mediana da fileira e ainda pertencer a ela.
_COLLINEAR_TOLERANCE_PT = 3.0

#: Folga ao apagar, para não deixar meio pixel do glifo.
_COORDINATE_PAD_PT = 0.6


def _coordinate_ring_pt(rect: fitz.Rect) -> float:
    side = max(rect.width, rect.height)
    return min(COORDINATE_RING_MAX_PT, max(COORDINATE_RING_MIN_PT, side * COORDINATE_RING_RATIO))


def _aligned_row(entries: list[tuple[fitz.Rect, float]]) -> list[fitz.Rect]:
    """Da lista de um lado, as que formam fileira; vazio se não formarem."""
    if len(entries) < MIN_LABELS_IN_A_ROW:
        return []
    positions = sorted(position for _box, position in entries)
    median = positions[len(positions) // 2]
    row = [box for box, position in entries if abs(position - median) <= _COLLINEAR_TOLERANCE_PT]
    return row if len(row) >= MIN_LABELS_IN_A_ROW else []


def _covering_runs(entries: list[fitz.Rect], span_start: float, span_end: float, horizontal: bool) -> list[fitz.Rect]:
    """Corridas que, somadas, cobrem boa parte do lado do tabuleiro.

    Uma corrida sozinha já é um sinal forte (ser um trecho contíguo de `abcdefgh`
    não acontece por acaso), mas exigir cobertura evita adotar um `gh` perdido
    longe do tabuleiro como se fosse a fileira inteira.
    """
    if not entries:
        return []
    side = abs(span_end - span_start)
    if side <= 0:
        return []
    covered = sum(
        (box.x1 - box.x0) if horizontal else (box.y1 - box.y0) for box in entries
    )
    return entries if covered >= side * MIN_RUN_COVERAGE else []


def find_coordinate_labels(page: fitz.Page, rect_pdf: Rect) -> list[fitz.Rect]:
    """Caixas das coordenadas do diagrama original em volta de `rect_pdf`."""
    board = fitz.Rect(rect_pdf)
    if board.is_empty:
        return []
    ring = _coordinate_ring_pt(board)
    outer = fitz.Rect(board.x0 - ring, board.y0 - ring, board.x1 + ring, board.y1 + ring)
    # Folga no alinhamento: a coordenada da coluna `a` fica centralizada na casa,
    # e não exatamente na borda do tabuleiro.
    slack = ring

    # Cada lado é avaliado separadamente: uma fileira de letras embaixo não
    # legitima um dígito solto na lateral.
    singles: dict[str, list[tuple[fitz.Rect, float]]] = {
        "above": [], "below": [], "left": [], "right": [],
    }
    runs: dict[str, list[fitz.Rect]] = {"above": [], "below": [], "left": [], "right": []}

    for box, text in _page_words(page):
        token = text.strip()
        if not token:
            continue
        if (box & outer).is_empty:
            continue

        run_kind = _coordinate_run_kind(token)
        is_file = run_kind == "file" or (len(token) == 1 and token.lower() in _FILE_LABELS)
        is_rank = run_kind == "rank" or (len(token) == 1 and token in _RANK_LABELS)
        if not (is_file or is_rank):
            continue

        center_x = (box.x0 + box.x1) / 2.0
        center_y = (box.y0 + box.y1) / 2.0
        # "Fora do tabuleiro" é medido pelo **centro**, não por interseção zero: a
        # fileira de coordenadas encosta na borda, e a caixa da palavra invade o
        # retângulo detectado por 1 ou 2 pt. Exigir interseção vazia descartava
        # justamente as fileiras coladas — o caso mais comum.
        if board.x0 < center_x < board.x1 and board.y0 < center_y < board.y1:
            continue
        side: Optional[str] = None
        if is_file and board.x0 - slack <= center_x <= board.x1 + slack:
            side = "below" if center_y > board.y1 else ("above" if center_y < board.y0 else None)
        elif is_rank and board.y0 - slack <= center_y <= board.y1 + slack:
            side = "right" if center_x > board.x1 else ("left" if center_x < board.x0 else None)
        if side is None:
            continue

        if run_kind is not None:
            runs[side].append(box)
        else:
            # A posição guardada é a do eixo em que a fileira se alinha.
            singles[side].append((box, center_y if side in ("above", "below") else center_x))

    found: list[fitz.Rect] = []
    for entries in singles.values():
        found.extend(_aligned_row(entries))
    for side, entries in runs.items():
        horizontal = side in ("above", "below")
        found.extend(
            _covering_runs(
                entries,
                board.x0 if horizontal else board.y0,
                board.x1 if horizontal else board.y1,
                horizontal,
            )
        )

    # As caixas do `get_text` já são de escrita, e o limite delas é a região
    # visível nesse mesmo espaço — não `page.rect`, que numa página girada tem
    # largura e altura trocadas e cortaria coordenada válida (§48.3).
    visible = _write_space_cropbox(page)
    return [
        fitz.Rect(
            box.x0 - _COORDINATE_PAD_PT,
            box.y0 - _COORDINATE_PAD_PT,
            box.x1 + _COORDINATE_PAD_PT,
            box.y1 + _COORDINATE_PAD_PT,
        )
        & visible
        for box in found
    ]


def _whiteout_rect(page: fitz.Page, op: OverlayOperation, fallback_margin_pt: float) -> fitz.Rect:
    rect = fitz.Rect(op.rect_pdf)
    pad_default = max(0.0, float(getattr(op, "whiteout_padding_pt", fallback_margin_pt)))
    pad_left = max(0.0, float(getattr(op, "whiteout_padding_left_pt", pad_default)))
    pad_top = max(0.0, float(getattr(op, "whiteout_padding_top_pt", pad_default)))
    pad_right = max(0.0, float(getattr(op, "whiteout_padding_right_pt", pad_default)))
    pad_bottom = max(0.0, float(getattr(op, "whiteout_padding_bottom_pt", pad_default)))
    return fitz.Rect(
        rect.x0 - pad_left,
        rect.y0 - pad_top,
        rect.x1 + pad_right,
        rect.y1 + pad_bottom,
    ) & _write_space_cropbox(page)


def _operation_in_write_space(page: fitz.Page, op):
    """Cópia da operação com o retângulo em espaço de escrita (§46, §48).

    Cópia, e não mutação: a mesma lista de operações é reusada pela prévia, pela
    galeria e pela exportação, e converter o retângulo no lugar corromperia as
    outras.
    """
    rect = _to_write_space(page, op.rect_pdf)
    return replace(op, rect_pdf=(rect.x0, rect.y0, rect.x1, rect.y1))


def apply_page_operations(
    page: fitz.Page,
    operations: Iterable[OverlayOperation],
    erase_operations: Iterable[EraseOperation] = (),
    whiteout: bool = True,
    whiteout_margin_pt: float = 0.5,
    include_lichess_link: bool = True,
    erase_coordinates: bool = False,
) -> None:
    """Aplica apagamentos e substituicoes em UMA pagina ja aberta.

    Ponto unico de verdade compartilhado pela exportacao e pela previa: o que a
    previa mostra e exatamente o que o PDF exportado contem.

    `erase_coordinates` inclui na mesma passada de redacao as coordenadas
    (a-h/1-8) que o diagrama original deixou em volta — elas ficam fora do
    whiteout e emolduram o diagrama novo com as letrinhas do antigo.
    """
    ops = [op for op in operations if not fitz.Rect(op.rect_pdf).is_empty]
    erases = list(erase_operations)

    # Página com `/Rotate` (livro escaneado de lado) ou com CropBox deslocada
    # (livro preparado para impressão): o retângulo guardado está no espaço que o
    # usuário vê — o mesmo de `page.rect` —, mas escrever no conteúdo da página é
    # no espaço de escrita. Sem converter, o whiteout não cobria o diagrama
    # original e o tabuleiro novo ia para outro lugar, deitado (§46, §48).
    #
    # Daqui para baixo, **todo** retângulo está em espaço de escrita, e quem
    # limita é `visible`, não `page.rect`.
    visible = _write_space_cropbox(page)
    ops = [_operation_in_write_space(page, op) for op in ops]
    erases = [_operation_in_write_space(page, op) for op in erases]

    redact_rects: list[fitz.Rect] = []
    for erase_op in erases:
        redact_rects.append(fitz.Rect(erase_op.rect_pdf) & visible)
    if whiteout:
        for op in ops:
            redact_rects.append(_whiteout_rect(page, op, whiteout_margin_pt))
    if erase_coordinates:
        # Detectado antes da redacao, porque depois dela o texto nao existe mais
        # para ser encontrado.
        for op in ops:
            redact_rects.extend(find_coordinate_labels(page, op.rect_pdf))
    _erase_rects(page, redact_rects)

    for op in ops:
        rect = fitz.Rect(op.rect_pdf)
        size_px = max(
            _points_to_pixels(rect.width, dpi=450),
            _points_to_pixels(rect.height, dpi=450),
        )
        # Numa página girada o conteúdo inserido tem de girar junto, senão o
        # tabuleiro sai deitado para quem lê o PDF. Medido: `rotate = page.rotation`
        # devolve um recorte **idêntico** ao da mesma posição numa página sem
        # rotação (§46.3).
        rotate = int(page.rotation) % 360
        pdf_bytes = _cached_board_pdf(op.fen, size_px)
        if pdf_bytes:
            src = fitz.open("pdf", pdf_bytes)
            try:
                page.show_pdf_page(
                    rect, src, 0, overlay=True, keep_proportion=False, rotate=rotate
                )
            finally:
                src.close()
        else:
            png_bytes = _cached_board_png(op.fen, size_px)
            page.insert_image(
                rect, stream=png_bytes, overlay=True, keep_proportion=False, rotate=rotate
            )

        border_width = max(0.0, float(getattr(op, "border_width_pt", 0.0)))
        if border_width > 0:
            page.draw_rect(rect, color=(0, 0, 0), width=border_width, overlay=True)

        if wants_lichess_link(op, include_lichess_link):
            _insert_lichess_link_below_diagram(page, rect, op)


class ExportCanceled(RuntimeError):
    """A exportação foi interrompida a pedido. Nenhum arquivo foi gravado."""


def apply_operations_to_pdf(
    input_pdf: str,
    output_pdf: str,
    operations: Iterable[OverlayOperation],
    erase_operations: Optional[Iterable[EraseOperation]] = None,
    whiteout: bool = True,
    whiteout_margin_pt: float = 0.5,
    include_lichess_link: bool = True,
    erase_coordinates: bool = False,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Grava o PDF de saída com todas as alterações aplicadas.

    `should_cancel` é consultado **entre páginas**: interromper no meio de uma
    página deixaria metade das substituições dela aplicadas, e a gravação é o
    último passo, então cancelar significa **nenhum arquivo** — nunca um PDF pela
    metade no lugar de um bom. Por isso a exceção, e não um retorno silencioso.

    `on_progress(feitas, total)` conta páginas alteradas, não páginas do livro:
    num livro de 898 páginas com 60 diagramas, o total é 60, e é isso que faz a
    barra andar de forma honesta.
    """
    in_path = Path(input_pdf)
    if not in_path.exists():
        raise FileNotFoundError(f"PDF de entrada não encontrado: {input_pdf}")

    doc = fitz.open(str(in_path))
    try:
        page_ops: dict[int, list[OverlayOperation]] = defaultdict(list)
        page_erases: dict[int, list[EraseOperation]] = defaultdict(list)

        for op in operations:
            if 0 <= op.page_num < len(doc):
                page_ops[op.page_num].append(op)
        for erase_op in erase_operations or []:
            if 0 <= erase_op.page_num < len(doc):
                page_erases[erase_op.page_num].append(erase_op)

        pages = sorted(set(page_ops) | set(page_erases))
        total = len(pages)
        for done, page_num in enumerate(pages, start=1):
            if should_cancel is not None and should_cancel():
                logger.info("Exportação cancelada em %d/%d páginas", done - 1, total)
                raise ExportCanceled(f"Exportação cancelada após {done - 1} de {total} páginas.")
            apply_page_operations(
                doc[page_num],
                page_ops.get(page_num, []),
                erase_operations=page_erases.get(page_num, []),
                whiteout=whiteout,
                whiteout_margin_pt=whiteout_margin_pt,
                include_lichess_link=include_lichess_link,
                erase_coordinates=erase_coordinates,
            )
            if on_progress is not None:
                on_progress(done, total)

        # Última checagem antes de gravar: o `save` de um livro grande também
        # demora, e chegar até aqui não obriga ninguém a esperar por ele.
        if should_cancel is not None and should_cancel():
            raise ExportCanceled("Exportação cancelada antes de gravar o arquivo.")
        doc.save(output_pdf, deflate=True, garbage=3)
    finally:
        doc.close()


def crop_from_rendered_page(image_png: bytes, rect_img: Rect) -> bytes:
    image = Image.open(io.BytesIO(image_png)).convert("RGB")
    x0, y0, x1, y1 = rect_img
    x0i = max(0, int(round(min(x0, x1))))
    y0i = max(0, int(round(min(y0, y1))))
    x1i = min(image.width, int(round(max(x0, x1))))
    y1i = min(image.height, int(round(max(y0, y1))))
    if x1i <= x0i or y1i <= y0i:
        raise ValueError("Seleção inválida para recorte.")
    crop = image.crop((x0i, y0i, x1i, y1i))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
