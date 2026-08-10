from __future__ import annotations

import io
from pathlib import Path

import fitz  # type: ignore
import pytest
from PIL import Image

from chess_pdf_editor.pdf_service import (
    PdfService,
    apply_operations_to_pdf,
    clear_board_render_cache,
)
from chess_pdf_editor.types import EraseOperation, OverlayOperation

FEN_A = "8/8/8/4k3/8/8/4K3/8"
FEN_B = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
DIAGRAM_RECT = (100.0, 300.0, 260.0, 460.0)


def _make_pdf(path: Path, pages: int = 2, rotation: int = 0) -> Path:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=420, height=595)
        page.insert_text(fitz.Point(72, 100), f"Pagina {index + 1}", fontsize=18)
        # "Diagrama" de baixa qualidade que sera substituido.
        page.draw_rect(fitz.Rect(*DIAGRAM_RECT), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
        if rotation:
            page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def _image(png_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def _make_operation(page_num: int = 0, fen: str = FEN_A) -> OverlayOperation:
    return OverlayOperation(
        page_num=page_num,
        rect_pdf=DIAGRAM_RECT,
        fen=fen,
        whiteout_padding_left_pt=1.5,
        whiteout_padding_top_pt=1.5,
        whiteout_padding_right_pt=1.5,
        whiteout_padding_bottom_pt=1.5,
    )


@pytest.fixture(autouse=True)
def _clean_board_cache():
    clear_board_render_cache()
    yield
    clear_board_render_cache()


def test_preview_render_changes_diagram_area(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "book.pdf")
    service = PdfService(str(pdf_path))
    try:
        original = service.render_page(0, zoom=2.0)
        preview = service.render_page_with_operations(
            0,
            2.0,
            [_make_operation()],
            include_lichess_link=False,
        )

        assert (preview.width_px, preview.height_px) == (original.width_px, original.height_px)
        assert preview.matrix == original.matrix
        assert preview.image_png != original.image_png

        center = (
            int((DIAGRAM_RECT[0] + DIAGRAM_RECT[2]) / 2 * 2.0),
            int((DIAGRAM_RECT[1] + DIAGRAM_RECT[3]) / 2 * 2.0),
        )
        assert _image(original.image_png).getpixel(center) != _image(preview.image_png).getpixel(center)
    finally:
        service.close()


def test_preview_matches_exported_pdf(tmp_path: Path) -> None:
    """Garantia WYSIWYG: a previa e o PDF exportado sao o mesmo render."""
    pdf_path = _make_pdf(tmp_path / "book.pdf")
    out_path = tmp_path / "out.pdf"
    operations = [_make_operation(), _make_operation(page_num=1, fen=FEN_B)]
    erasers = [EraseOperation(page_num=0, rect_pdf=(80.0, 90.0, 300.0, 120.0))]

    apply_operations_to_pdf(
        str(pdf_path),
        str(out_path),
        operations,
        erase_operations=erasers,
        include_lichess_link=True,
    )

    service = PdfService(str(pdf_path))
    exported = PdfService(str(out_path))
    try:
        for page_num in (0, 1):
            preview = service.render_page_with_operations(
                page_num,
                2.0,
                [op for op in operations if op.page_num == page_num],
                erase_operations=[op for op in erasers if op.page_num == page_num],
                include_lichess_link=True,
            )
            final = exported.render_page(page_num, zoom=2.0)
            assert preview.image_png == final.image_png, f"pagina {page_num + 1} divergiu"
    finally:
        service.close()
        exported.close()


def test_preview_region_crop_matches_full_page(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "book.pdf")
    service = PdfService(str(pdf_path))
    try:
        operation = _make_operation()
        full = service.render_page_with_operations(0, 2.0, [operation], include_lichess_link=False)
        region = service.render_region_with_operations(
            0,
            2.0,
            DIAGRAM_RECT,
            [operation],
            include_lichess_link=False,
        )
        before = service.render_region(0, 2.0, DIAGRAM_RECT)

        region_img = _image(region)
        full_img = _image(full.image_png)
        expected = full_img.crop(
            (
                int(DIAGRAM_RECT[0] * 2.0),
                int(DIAGRAM_RECT[1] * 2.0),
                int(DIAGRAM_RECT[2] * 2.0),
                int(DIAGRAM_RECT[3] * 2.0),
            )
        )
        assert region_img.size == expected.size
        assert region_img.getpixel((10, 10)) == expected.getpixel((10, 10))
        assert _image(before).size == region_img.size
        assert before != region
    finally:
        service.close()


def test_preview_cache_is_reused_and_invalidated(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "book.pdf")
    service = PdfService(str(pdf_path))
    try:
        operation = _make_operation()
        first = service.render_page_with_operations(0, 2.0, [operation], include_lichess_link=False)
        signature = service._preview_signature
        second = service.render_page_with_operations(0, 2.0, [operation], include_lichess_link=False)
        assert service._preview_signature == signature
        assert first.image_png == second.image_png

        changed = service.render_page_with_operations(
            0,
            2.0,
            [_make_operation(fen=FEN_B)],
            include_lichess_link=False,
        )
        assert service._preview_signature != signature
        assert changed.image_png != first.image_png
    finally:
        service.close()


def test_preview_keeps_page_geometry_on_rotated_pages(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "rotated.pdf", pages=1, rotation=90)
    service = PdfService(str(pdf_path))
    try:
        original = service.render_page(0, zoom=2.0)
        preview = service.render_page_with_operations(
            0,
            2.0,
            [_make_operation()],
            include_lichess_link=False,
        )
        assert (preview.width_px, preview.height_px) == (original.width_px, original.height_px)
        assert preview.image_png != original.image_png
    finally:
        service.close()


def test_export_skips_operations_outside_page_range(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "book.pdf", pages=1)
    out_path = tmp_path / "out.pdf"
    apply_operations_to_pdf(
        str(pdf_path),
        str(out_path),
        [_make_operation(page_num=0), _make_operation(page_num=7)],
        erase_operations=[EraseOperation(page_num=9, rect_pdf=(0.0, 0.0, 10.0, 10.0))],
        include_lichess_link=False,
    )
    assert out_path.exists()
