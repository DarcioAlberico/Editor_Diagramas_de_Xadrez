from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

import fitz  # type: ignore
from PIL import Image

from .renderer import render_board_pdf, render_board_png
from .types import EraseOperation, OverlayOperation, Rect


@dataclass
class RenderedPage:
    page_num: int
    width_px: int
    height_px: int
    image_png: bytes
    matrix: tuple[float, float, float, float, float, float]


class PdfService:
    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = str(pdf_path)
        self.doc = fitz.open(self.pdf_path)

    def close(self) -> None:
        self.doc.close()

    @property
    def page_count(self) -> int:
        return len(self.doc)

    def render_page(self, page_num: int, zoom: float = 2.0) -> RenderedPage:
        page = self.doc[page_num]
        matrix = fitz.Matrix(zoom, zoom)
        # Render em RGBA e compoe sobre branco para evitar artefatos pretos
        # em PDFs com transparencias / mascaras.
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=True)
        image_rgba = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        image_rgb = Image.new("RGB", image_rgba.size, "white")
        image_rgb.paste(image_rgba, mask=image_rgba.getchannel("A"))

        buffer = io.BytesIO()
        image_rgb.save(buffer, format="PNG", optimize=True)
        png_bytes = buffer.getvalue()
        return RenderedPage(
            page_num=page_num,
            width_px=pix.width,
            height_px=pix.height,
            image_png=png_bytes,
            matrix=(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f),
        )

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


def _erase_rect(page: fitz.Page, rect: fitz.Rect) -> None:
    rect = rect & page.rect
    if rect.is_empty:
        return
    # Redaction remove o conteudo subjacente de forma mais robusta do que apenas pintar branco.
    try:
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        return
    except Exception:
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
        raise FileNotFoundError(f"PDF de entrada nao encontrado: {input_pdf}")

    doc = fitz.open(str(in_path))
    try:
        if erase_operations:
            for erase_op in erase_operations:
                if erase_op.page_num < 0 or erase_op.page_num >= len(doc):
                    continue
                page = doc[erase_op.page_num]
                erase_rect = fitz.Rect(erase_op.rect_pdf)
                _erase_rect(page, erase_rect)

        for op in operations:
            page = doc[op.page_num]
            rect = fitz.Rect(op.rect_pdf)
            if rect.is_empty:
                continue

            if whiteout:
                pad_default = max(0.0, float(getattr(op, "whiteout_padding_pt", whiteout_margin_pt)))
                pad_left = max(0.0, float(getattr(op, "whiteout_padding_left_pt", pad_default)))
                pad_top = max(0.0, float(getattr(op, "whiteout_padding_top_pt", pad_default)))
                pad_right = max(0.0, float(getattr(op, "whiteout_padding_right_pt", pad_default)))
                pad_bottom = max(0.0, float(getattr(op, "whiteout_padding_bottom_pt", pad_default)))
                wr = fitz.Rect(
                    rect.x0 - pad_left,
                    rect.y0 - pad_top,
                    rect.x1 + pad_right,
                    rect.y1 + pad_bottom,
                )
                _erase_rect(page, wr)

            size_px = max(
                _points_to_pixels(rect.width, dpi=450),
                _points_to_pixels(rect.height, dpi=450),
            )
            pdf_bytes = render_board_pdf(op.fen, size_px=size_px)
            if pdf_bytes:
                src = fitz.open("pdf", pdf_bytes)
                try:
                    page.show_pdf_page(rect, src, 0, overlay=True, keep_proportion=False)
                finally:
                    src.close()
            else:
                png_bytes = render_board_png(op.fen, size_px=size_px)
                page.insert_image(rect, stream=png_bytes, overlay=True, keep_proportion=False)

            border_width = max(0.0, float(getattr(op, "border_width_pt", 0.0)))
            if border_width > 0:
                page.draw_rect(rect, color=(0, 0, 0), width=border_width, overlay=True)

            if include_lichess_link:
                _insert_lichess_link_below_diagram(page, rect, op)

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
        raise ValueError("Selecao invalida para recorte.")
    crop = image.crop((x0i, y0i, x1i, y1i))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
