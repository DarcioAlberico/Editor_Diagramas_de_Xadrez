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
        raise ValueError("FEN inválida: deve conter 8 fileiras.")

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
                raise ValueError(f"Caractere inválido no FEN: {ch!r}")
        if file_count != 8:
            raise ValueError("FEN inválida: cada fileira deve totalizar 8 casas.")

    if white_king != 1 or black_king != 1:
        warnings.append("Posição não possui exatamente 1 rei branco e 1 rei preto.")

    matrix = board_to_matrix(piece_placement)
    for file_idx in range(8):
        top = matrix[0][file_idx]
        bottom = matrix[7][file_idx]
        if top in ("P", "p") or bottom in ("P", "p"):
            warnings.append("Há peão na 1ª ou 8ª fileira. Verifique se está correto.")
            break

    return warnings


def normalize_piece_placement(piece_placement: str) -> str:
    return matrix_to_piece_placement(board_to_matrix(piece_placement))


def to_full_fen(piece_placement: str, side_to_move: str = "w", fullmove_number: int = 1) -> str:
    """FEN completa a partir das peças mais as etiquetas.

    Ponto único desta string (§59.11). Ela era montada à mão em quatro lugares —
    aqui, em `pdf_service.operation_full_fen` e duas vezes em `app.py` —, e as quatro
    cópias só concordavam por terem sido escritas parecidas. É a forma de defeito que
    a §45 documenta: o dia em que o campo `halfmove` deixar de ser fixo, as que
    ficarem para trás não dão erro nenhum, só passam a mentir.

    Os campos de roque e *en passant* saem como `-` de propósito: um diagrama de
    livro não os informa, e inventá-los seria afirmar algo que ninguém leu.
    """
    side = "w" if side_to_move not in ("w", "b") else side_to_move
    try:
        fullmove = max(1, int(fullmove_number))
    except (TypeError, ValueError):
        fullmove = 1
    return f"{piece_placement} {side} - - 0 {fullmove}"


def board_to_matrix(piece_placement: str) -> list[list[str]]:
    rows = piece_placement.split("/")
    if len(rows) != 8:
        raise ValueError("FEN inválida: deve conter 8 fileiras.")

    matrix: list[list[str]] = []
    for row in rows:
        expanded: list[str] = []
        for ch in row:
            if ch.isdigit():
                expanded.extend(["."] * int(ch))
            else:
                if ch not in PIECES:
                    raise ValueError(f"Caractere inválido no FEN: {ch!r}")
                expanded.append(ch)
        if len(expanded) != 8:
            raise ValueError("FEN inválida: cada fileira deve totalizar 8 casas.")
        matrix.append(expanded)
    return matrix


def matrix_to_piece_placement(matrix: Iterable[Iterable[str]]) -> str:
    rows: list[str] = []
    matrix_rows = list(matrix)
    if len(matrix_rows) != 8:
        raise ValueError("Matriz inválida: deve conter 8 linhas.")

    for row in matrix_rows:
        row_values = list(row)
        if len(row_values) != 8:
            raise ValueError("Matriz inválida: cada linha deve conter 8 colunas.")

        run = 0
        out: list[str] = []
        for ch in row_values:
            if ch in (".", "", None):
                run += 1
                continue
            if ch not in PIECES:
                raise ValueError(f"Peça inválida na matriz: {ch!r}")
            if run:
                out.append(str(run))
                run = 0
            out.append(ch)
        if run:
            out.append(str(run))
        rows.append("".join(out) or "8")
    return "/".join(rows)

