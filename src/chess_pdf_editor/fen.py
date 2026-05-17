from __future__ import annotations

from typing import Iterable

PIECES = set("PNBRQKpnbrqk")
EMPTY_FEN = "8/8/8/8/8/8/8/8"


def extract_piece_placement(fen_like: str) -> str:
    fen_like = (fen_like or "").strip()
    if not fen_like:
        return EMPTY_FEN
    return fen_like.split()[0]


def validate_piece_placement(piece_placement: str) -> list[str]:
    warnings: list[str] = []
    rows = piece_placement.split("/")
    if len(rows) != 8:
        raise ValueError("FEN invalido: deve conter 8 fileiras.")

    white_king = 0
    black_king = 0
    for row in rows:
        file_count = 0
        for ch in row:
            if ch.isdigit():
                file_count += int(ch)
            elif ch in PIECES:
                file_count += 1
                if ch == "K":
                    white_king += 1
                elif ch == "k":
                    black_king += 1
            else:
                raise ValueError(f"Caractere invalido no FEN: {ch!r}")
        if file_count != 8:
            raise ValueError("FEN invalido: cada fileira deve totalizar 8 casas.")

    if white_king != 1 or black_king != 1:
        warnings.append("Posicao nao possui exatamente 1 rei branco e 1 rei preto.")

    matrix = board_to_matrix(piece_placement)
    for file_idx in range(8):
        top = matrix[0][file_idx]
        bottom = matrix[7][file_idx]
        if top in ("P", "p") or bottom in ("P", "p"):
            warnings.append("Ha peao na 1a ou 8a fileira. Verifique se esta correto.")
            break

    return warnings


def normalize_piece_placement(piece_placement: str) -> str:
    return matrix_to_piece_placement(board_to_matrix(piece_placement))


def to_full_fen(piece_placement: str, side_to_move: str = "w") -> str:
    side = "w" if side_to_move not in ("w", "b") else side_to_move
    return f"{piece_placement} {side} - - 0 1"


def board_to_matrix(piece_placement: str) -> list[list[str]]:
    rows = piece_placement.split("/")
    if len(rows) != 8:
        raise ValueError("FEN invalido: deve conter 8 fileiras.")

    matrix: list[list[str]] = []
    for row in rows:
        expanded: list[str] = []
        for ch in row:
            if ch.isdigit():
                expanded.extend(["."] * int(ch))
            else:
                if ch not in PIECES:
                    raise ValueError(f"Caractere invalido no FEN: {ch!r}")
                expanded.append(ch)
        if len(expanded) != 8:
            raise ValueError("FEN invalido: cada fileira deve totalizar 8 casas.")
        matrix.append(expanded)
    return matrix


def matrix_to_piece_placement(matrix: Iterable[Iterable[str]]) -> str:
    rows: list[str] = []
    matrix_rows = list(matrix)
    if len(matrix_rows) != 8:
        raise ValueError("Matriz invalida: deve conter 8 linhas.")

    for row in matrix_rows:
        row_values = list(row)
        if len(row_values) != 8:
            raise ValueError("Matriz invalida: cada linha deve conter 8 colunas.")

        run = 0
        out: list[str] = []
        for ch in row_values:
            if ch in (".", "", None):
                run += 1
                continue
            if ch not in PIECES:
                raise ValueError(f"Peca invalida na matriz: {ch!r}")
            if run:
                out.append(str(run))
                run = 0
            out.append(ch)
        if run:
            out.append(str(run))
        rows.append("".join(out) or "8")
    return "/".join(rows)

