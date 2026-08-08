"""Auto-orientação da posição (Sprint 6.3).

Um diagrama pode chegar de cabeça para baixo (livro que imprime do ponto de vista das
pretas) ou de lado (página escaneada torta, recorte girado). O editor sempre teve
`Rotacionar 90°` e `Espelhar`, mas descobrir *qual* aplicar era olho do usuário.

Aqui as quatro rotações são pontuadas e a mais plausível vence. Todos os critérios são
**independentes do lado a jogar** — um diagrama de livro não carrega essa informação, e
o app preenche "brancas" por padrão, então qualquer regra que dependesse disso estaria
pontuando o preenchimento e não a posição:

    reis            exatamente um de cada cor
    peões           nenhum na 1ª nem na 8ª fila (promovido / impossível de chegar)
    sentido         peão anda para frente e não volta: brancos embaixo, pretos em cima
    contagem        no máximo 8 peões e 16 peças por cor

O sentido dos peões é o que de fato separa 0° de 180°, porque as outras três regras
sobrevivem a girar o tabuleiro: uma posição legal girada 180° continua legal. É por isso
que ele pesa e as demais são principalmente **eliminatórias**.

Este módulo é de propósito puro — só `fen.py` e listas. Ele funciona sem torch e sem
OpenCV, então o botão `Auto-orientar` existe mesmo numa instalação sem o motor local. O
motor local tem a sua própria escolha de orientação, que olha os **pixels** e é melhor
quando disponível (ver `local_ocr/_vendor/inference.predict_with_orientation`).
"""
from __future__ import annotations

from dataclasses import dataclass

from .fen import board_to_matrix, matrix_to_piece_placement

#: Diferença de pontuação abaixo da qual a escolha não é confiável.
AMBIGUOUS_MARGIN = 0.75


@dataclass(frozen=True)
class OrientationCandidate:
    piece_placement: str
    #: Graus no sentido horário aplicados à posição original.
    rotation: int
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class OrientationResult:
    best: OrientationCandidate
    runner_up: OrientationCandidate
    candidates: tuple[OrientationCandidate, ...]

    @property
    def rotation(self) -> int:
        return self.best.rotation

    @property
    def piece_placement(self) -> str:
        return self.best.piece_placement

    @property
    def margin(self) -> float:
        return self.best.score - self.runner_up.score

    @property
    def ambiguous(self) -> bool:
        """A escolha foi apertada: vale conferir com o olho."""
        return self.margin < AMBIGUOUS_MARGIN

    @property
    def changed(self) -> bool:
        return self.best.rotation != 0


def _rotate_clockwise(matrix: list[list[str]]) -> list[list[str]]:
    return [list(row) for row in zip(*matrix[::-1])]


def _rank_of_row(row: int) -> int:
    """Linha 0 da matriz é a 8ª fila."""
    return 8 - row


def plausibility(piece_placement: str) -> tuple[float, tuple[str, ...]]:
    """Quão plausível é esta posição como diagrama de pé. Maior é melhor."""
    matrix = board_to_matrix(piece_placement)
    flat = [cell for row in matrix for cell in row]
    reasons: list[str] = []
    score = 0.0

    white_kings = flat.count("K")
    black_kings = flat.count("k")
    if white_kings == 1 and black_kings == 1:
        score += 3.0
    else:
        # Girar não cria nem destrói rei, então isto quase nunca desempata rotações —
        # mas mantém a pontuação honesta quando a leitura do OCR veio quebrada.
        score -= 3.0 * (abs(white_kings - 1) + abs(black_kings - 1))
        reasons.append(f"reis: {white_kings} branco(s), {black_kings} preto(s)")

    backrank_pawns = sum(
        1 for row in (0, 7) for cell in matrix[row] if cell in ("P", "p")
    )
    if backrank_pawns:
        score -= 2.0 * backrank_pawns
        reasons.append(f"{backrank_pawns} peão(ões) na 1ª/8ª fila")

    white_ranks = [_rank_of_row(r) for r in range(8) for c in range(8) if matrix[r][c] == "P"]
    black_ranks = [_rank_of_row(r) for r in range(8) for c in range(8) if matrix[r][c] == "p"]
    if white_ranks and black_ranks:
        gap = sum(black_ranks) / len(black_ranks) - sum(white_ranks) / len(white_ranks)
        score += max(-4.0, min(4.0, gap)) * 0.75
        if gap < 0:
            reasons.append(f"peões apontam o sentido oposto ({gap:+.1f} filas)")
    else:
        # Sem peão de alguma cor o sinal mais forte não tem o que dizer. Fingir um
        # número seria pior que admitir isso — a ambiguidade fica visível na margem.
        reasons.append("sem peões dos dois lados: sentido indeterminado")

    for label, pawn, pieces in (("brancas", "P", "PNBRQK"), ("pretas", "p", "pnbrqk")):
        pawn_count = flat.count(pawn)
        piece_count = sum(flat.count(ch) for ch in pieces)
        if pawn_count > 8:
            score -= 2.0 * (pawn_count - 8)
            reasons.append(f"peões {label} demais ({pawn_count})")
        if piece_count > 16:
            score -= 2.0 * (piece_count - 16)
            reasons.append(f"peças {label} demais ({piece_count})")

    return score, tuple(reasons)


def rank_orientations(piece_placement: str) -> list[OrientationCandidate]:
    """As quatro rotações, da mais plausível para a menos."""
    matrix = board_to_matrix(piece_placement)
    candidates: list[OrientationCandidate] = []
    for rotation in (0, 90, 180, 270):
        placement = matrix_to_piece_placement(matrix)
        score, reasons = plausibility(placement)
        candidates.append(
            OrientationCandidate(
                piece_placement=placement,
                rotation=rotation,
                score=score,
                reasons=reasons,
            )
        )
        matrix = _rotate_clockwise(matrix)
    # `sorted` é estável: empate mantém a ordem 0 → 90 → 180 → 270, ou seja, não girar
    # ganha de girar quando nada distingue as duas. É o comportamento menos surpreendente.
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def auto_orient(piece_placement: str) -> OrientationResult:
    """Escolhe a rotação mais plausível entre as quatro."""
    candidates = rank_orientations(piece_placement)
    return OrientationResult(
        best=candidates[0],
        runner_up=candidates[1],
        candidates=tuple(candidates),
    )
