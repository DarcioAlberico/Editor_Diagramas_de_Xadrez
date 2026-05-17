from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .fen import board_to_matrix, to_full_fen

LIGHT_SQUARE = (240, 217, 181)
DARK_SQUARE = (181, 136, 99)
PIECE_COLOR = {
    "white": (25, 25, 25),
    "black": (10, 10, 10),
}
PIECE_UNICODE = {
    "P": "♙",
    "N": "♘",
    "B": "♗",
    "R": "♖",
    "Q": "♕",
    "K": "♔",
    "p": "♟",
    "n": "♞",
    "b": "♝",
    "r": "♜",
    "q": "♛",
    "k": "♚",
}

MERIDA_LIGHT_GLYPH = {
    "P": "p",
    "N": "n",
    "B": "b",
    "R": "r",
    "Q": "q",
    "K": "k",
    "p": "o",
    "n": "m",
    "b": "v",
    "r": "t",
    "q": "w",
    "k": "l",
    ".": " ",
}
MERIDA_DARK_GLYPH = {
    "P": "P",
    "N": "N",
    "B": "B",
    "R": "R",
    "Q": "Q",
    "K": "K",
    "p": "O",
    "n": "M",
    "b": "V",
    "r": "T",
    "q": "W",
    "k": "L",
    ".": "+",
}


def render_board_pdf(piece_placement: str, size_px: int = 1024) -> Optional[bytes]:
    merida_pdf = _render_with_merida_font_pdf(piece_placement, size_px=size_px)
    if merida_pdf is not None:
        return merida_pdf
    return _render_with_python_chess_pdf(piece_placement, size_px=size_px)


def render_board_png(piece_placement: str, size_px: int = 1024) -> bytes:
    merida_bytes = _render_with_merida_font(piece_placement, size_px=size_px)
    if merida_bytes is not None:
        return merida_bytes

    vector_bytes = _render_with_python_chess(piece_placement, size_px=size_px)
    if vector_bytes is not None:
        return vector_bytes
    return _render_with_pillow(piece_placement, size_px=size_px)


def _render_with_python_chess(piece_placement: str, size_px: int) -> Optional[bytes]:
    try:
        import chess
        import chess.svg
        import cairosvg
    except Exception:
        return None

    try:
        board = chess.Board(to_full_fen(piece_placement))
        svg_text = chess.svg.board(board=board, size=size_px, coordinates=False)
        png_bytes = cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"),
            output_width=size_px,
            output_height=size_px,
        )
        return png_bytes
    except Exception:
        return None


def _render_with_python_chess_pdf(piece_placement: str, size_px: int) -> Optional[bytes]:
    try:
        import chess
        import chess.svg
        import cairosvg
    except Exception:
        return None

    try:
        board = chess.Board(to_full_fen(piece_placement))
        svg_text = chess.svg.board(board=board, size=size_px, coordinates=False)
        pdf_bytes = cairosvg.svg2pdf(
            bytestring=svg_text.encode("utf-8"),
            output_width=size_px,
            output_height=size_px,
        )
        return pdf_bytes
    except Exception:
        return None


def _find_merida_font() -> Optional[Path]:
    env_font = os.getenv("CHESS_MERIDA_FONT", "").strip()
    candidates = []
    if env_font:
        candidates.append(Path(env_font))

    root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            root / "assets" / "fonts" / "Merida.ttf",
            root / "assets" / "fonts" / "MERIDA.TTF",
            root / "assets" / "fonts" / "Merida.otf",
            root / "assets" / "fonts" / "MERIDA.OTF",
            root / "chessmerida.otf",
            Path.cwd() / "assets" / "fonts" / "Merida.ttf",
            Path.cwd() / "assets" / "fonts" / "MERIDA.TTF",
            Path.cwd() / "assets" / "fonts" / "Merida.otf",
            Path.cwd() / "assets" / "fonts" / "MERIDA.OTF",
            Path.cwd() / "chessmerida.otf",
        ]
    )
    for fonts_dir in [root / "assets" / "fonts", Path.cwd() / "assets" / "fonts"]:
        if not fonts_dir.exists() or not fonts_dir.is_dir():
            continue
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            for path in fonts_dir.glob(ext):
                if "merida" in path.name.lower():
                    candidates.append(path)
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def _merida_rows(piece_placement: str) -> list[str]:
    matrix = board_to_matrix(piece_placement)
    rows: list[str] = []
    for rank in range(8):
        chars: list[str] = []
        for file_idx in range(8):
            piece = matrix[rank][file_idx]
            dark_square = (rank + file_idx) % 2 == 1
            mapping = MERIDA_DARK_GLYPH if dark_square else MERIDA_LIGHT_GLYPH
            chars.append(mapping[piece])
        rows.append("".join(chars))
    return rows


def _render_with_merida_font_pdf(piece_placement: str, size_px: int) -> Optional[bytes]:
    font_path = _find_merida_font()
    if font_path is None:
        return None

    try:
        import fitz  # type: ignore

        rows = _merida_rows(piece_placement)
        sq = float(size_px) / 8.0
        font_size = sq * 0.98
        font = fitz.Font(fontfile=str(font_path))
        font_height = (font.ascender - font.descender) * font_size
        baseline_offset = (sq - font_height) / 2.0 + (font.ascender * font_size)

        doc = fitz.open()
        page = doc.new_page(width=float(size_px), height=float(size_px))
        font_name = "MeridaEmbedded"
        page.insert_font(fontname=font_name, fontfile=str(font_path))

        for rank, row in enumerate(rows):
            y = rank * sq + baseline_offset
            for file_idx, glyph in enumerate(row):
                if glyph == " ":
                    continue
                char_w = font.text_length(glyph, fontsize=font_size)
                x = file_idx * sq + (sq - char_w) / 2.0
                page.insert_text(
                    fitz.Point(x, y),
                    glyph,
                    fontsize=font_size,
                    fontname=font_name,
                    color=(0, 0, 0),
                )
        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()
        return out
    except Exception:
        return None


def _render_with_merida_font(piece_placement: str, size_px: int) -> Optional[bytes]:
    font_path = _find_merida_font()
    if font_path is None:
        return None

    matrix = board_to_matrix(piece_placement)
    image = Image.new("RGB", (size_px, size_px), "white")
    draw = ImageDraw.Draw(image)
    sq = size_px / 8.0
    font = ImageFont.truetype(str(font_path), max(22, int(sq * 0.82)))

    for rank in range(8):
        for file_idx in range(8):
            x0 = int(file_idx * sq)
            y0 = int(rank * sq)
            x1 = int((file_idx + 1) * sq)
            y1 = int((rank + 1) * sq)
            color = LIGHT_SQUARE if (rank + file_idx) % 2 == 0 else DARK_SQUARE
            draw.rectangle([x0, y0, x1, y1], fill=color)

            piece = matrix[rank][file_idx]
            if piece != ".":
                glyph = PIECE_UNICODE.get(piece, piece)
                tx0, ty0, tx1, ty1 = draw.textbbox((0, 0), glyph, font=font)
                tw = tx1 - tx0
                th = ty1 - ty0
                px = int(x0 + (sq - tw) / 2)
                py = int(y0 + (sq - th) / 2)
                draw.text((px, py), glyph, fill=(16, 16, 16), font=font)

    draw.rectangle([0, 0, size_px - 1, size_px - 1], outline=(30, 30, 30), width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _render_with_pillow(piece_placement: str, size_px: int) -> bytes:
    matrix = board_to_matrix(piece_placement)
    image = Image.new("RGB", (size_px, size_px), "white")
    draw = ImageDraw.Draw(image)
    sq = size_px / 8.0

    font_size = max(18, int(sq * 0.52))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    for rank in range(8):
        for file_idx in range(8):
            x0 = int(file_idx * sq)
            y0 = int(rank * sq)
            x1 = int((file_idx + 1) * sq)
            y1 = int((rank + 1) * sq)
            color = LIGHT_SQUARE if (rank + file_idx) % 2 == 0 else DARK_SQUARE
            draw.rectangle([x0, y0, x1, y1], fill=color)

            piece = matrix[rank][file_idx]
            if piece != ".":
                txt = PIECE_UNICODE.get(piece, piece)
                tx, ty, tx2, ty2 = draw.textbbox((0, 0), txt, font=font)
                tw = tx2 - tx
                th = ty2 - ty
                px = int(x0 + (sq - tw) / 2)
                py = int(y0 + (sq - th) / 2)
                pc = PIECE_COLOR["white"] if piece.isupper() else PIECE_COLOR["black"]
                draw.text((px, py), txt, fill=pc, font=font)

    draw.rectangle([0, 0, size_px - 1, size_px - 1], outline=(30, 30, 30), width=3)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
