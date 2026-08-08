from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional, Sequence
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
        self._discard_preview_doc()
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
    ) -> fitz.Page:
        signature = (
            self.pdf_path,
            int(page_num),
            bool(whiteout),
            bool(include_lichess_link),
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
    ) -> RenderedPage:
        """Renderiza a pagina ja com as alteracoes aplicadas (WYSIWYG)."""
        page = self._preview_page(
            page_num,
            operations,
            erase_operations=erase_operations,
            whiteout=whiteout,
            include_lichess_link=include_lichess_link,
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
    ) -> bytes:
        """PNG de um recorte da pagina ja com as alteracoes (lado 'depois')."""
        page = self._preview_page(
            page_num,
            operations,
            erase_operations=erase_operations,
            whiteout=whiteout,
            include_lichess_link=include_lichess_link,
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
        rect = fitz.Rect(rect_pdf) & page.rect
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


def _operation_full_fen(op: OverlayOperation) -> str:
    side = str(getattr(op, "side_to_move", "w"))
    if side not in {"w", "b"}:
        side = "w"
    try:
        fullmove = max(1, int(getattr(op, "fullmove_number", 1)))
    except Exception:
        fullmove = 1
    return f"{op.fen} {side} - - 0 {fullmove}"


def _operation_lichess_url(op: OverlayOperation) -> str:
    full_fen = " ".join(_operation_full_fen(op).split())
    parts = full_fen.split(" ")
    if not parts:
        return "https://lichess.org/analysis"
    piece_placement = parts[0]
    if len(parts) == 1:
        return f"https://lichess.org/analysis/{piece_placement}"
    fen_tail = " ".join(parts[1:])
    return f"https://lichess.org/analysis/{piece_placement}{quote(' ' + fen_tail, safe='')}"


def _insert_lichess_link_below_diagram(page: fitz.Page, rect: fitz.Rect, op: OverlayOperation) -> None:
    link_text = "Lichess"
    gap_pt = 2.0
    font_size = min(12.0, max(7.0, rect.height * 0.09))

    text_width = fitz.get_text_length(link_text, fontname="helv", fontsize=font_size)
    center_x = (rect.x0 + rect.x1) / 2.0
    x0 = max(page.rect.x0 + 1.0, center_x - (text_width / 2.0) - 2.0)
    x1 = min(page.rect.x1 - 1.0, center_x + (text_width / 2.0) + 2.0)
    if x1 <= x0:
        return

    baseline_y = rect.y1 + gap_pt + font_size
    # Se nao houver espaco abaixo, posiciona acima para manter o link visivel.
    if baseline_y + 2.0 > page.rect.y1:
        baseline_y = rect.y0 - gap_pt
    if baseline_y - font_size < page.rect.y0:
        return

    page.insert_text(
        fitz.Point(x0 + 2.0, baseline_y),
        link_text,
        fontsize=font_size,
        fontname="helv",
        color=(0.0, 0.2, 1.0),
        overlay=True,
    )
    link_rect = fitz.Rect(x0, baseline_y - font_size, x1, baseline_y + 2.0) & page.rect
    if link_rect.is_empty:
        return
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": link_rect,
            "uri": _operation_lichess_url(op),
        }
    )


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
    ) & page.rect


def apply_page_operations(
    page: fitz.Page,
    operations: Iterable[OverlayOperation],
    erase_operations: Iterable[EraseOperation] = (),
    whiteout: bool = True,
    whiteout_margin_pt: float = 0.5,
    include_lichess_link: bool = True,
) -> None:
    """Aplica apagamentos e substituicoes em UMA pagina ja aberta.

    Ponto unico de verdade compartilhado pela exportacao e pela previa: o que a
    previa mostra e exatamente o que o PDF exportado contem.
    """
    ops = [op for op in operations if not fitz.Rect(op.rect_pdf).is_empty]
    erases = list(erase_operations)

    redact_rects: list[fitz.Rect] = []
    for erase_op in erases:
        redact_rects.append(fitz.Rect(erase_op.rect_pdf) & page.rect)
    if whiteout:
        for op in ops:
            redact_rects.append(_whiteout_rect(page, op, whiteout_margin_pt))
    _erase_rects(page, redact_rects)

    for op in ops:
        rect = fitz.Rect(op.rect_pdf)
        size_px = max(
            _points_to_pixels(rect.width, dpi=450),
            _points_to_pixels(rect.height, dpi=450),
        )
        pdf_bytes = _cached_board_pdf(op.fen, size_px)
        if pdf_bytes:
            src = fitz.open("pdf", pdf_bytes)
            try:
                page.show_pdf_page(rect, src, 0, overlay=True, keep_proportion=False)
            finally:
                src.close()
        else:
            png_bytes = _cached_board_png(op.fen, size_px)
            page.insert_image(rect, stream=png_bytes, overlay=True, keep_proportion=False)

        border_width = max(0.0, float(getattr(op, "border_width_pt", 0.0)))
        if border_width > 0:
            page.draw_rect(rect, color=(0, 0, 0), width=border_width, overlay=True)

        if include_lichess_link:
            _insert_lichess_link_below_diagram(page, rect, op)


def apply_operations_to_pdf(
    input_pdf: str,
    output_pdf: str,
    operations: Iterable[OverlayOperation],
    erase_operations: Optional[Iterable[EraseOperation]] = None,
    whiteout: bool = True,
    whiteout_margin_pt: float = 0.5,
    include_lichess_link: bool = True,
) -> None:
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

        for page_num in sorted(set(page_ops) | set(page_erases)):
            apply_page_operations(
                doc[page_num],
                page_ops.get(page_num, []),
                erase_operations=page_erases.get(page_num, []),
                whiteout=whiteout,
                whiteout_margin_pt=whiteout_margin_pt,
                include_lichess_link=include_lichess_link,
            )

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
