"""Auditoria de legalidade da posição (§22.5 item 4).

### O que a validação anterior não pegava

`fen.validate_piece_placement` confere a *escrita* da FEN — 8 fileiras, 8 casas por
fileira, caracteres válidos — e mais dois avisos: reis fora de um por cor, e peão na
1ª/8ª fila. Isso deixa passar a leitura de OCR que é bem formada e **impossível**:

* o rei de quem *não* está a jogar em xeque (a posição não teria como ter surgido);
* três damas com os oito peões em casa — cada dama extra exige uma promoção, e
  promover exige um peão que não está mais lá.

O segundo caso o `python-chess` também deixa passar: `Board.status()` conta peças e
peões, mas não faz a **contabilidade de promoções**. Conferido na versão 1.11.2 —
`4k3/8/8/8/8/QQQ5/PPPPPPPP/4K3` é reportado como válido.

### A armadilha do lado a jogar

Um diagrama de livro quase nunca diz de quem é a vez, e o app preenche `brancas` por
padrão. Um `OPPOSITE_CHECK` calculado sobre esse preenchimento acusaria de impossível
uma posição que só está com o lado trocado — é o mesmo cuidado que fez a
`orientation.plausibility` não usar nenhuma regra dependente do lado.

Então a auditoria é feita com **os dois lados**:

| O problema aparece | Conclusão |
|---|---|
| com os dois lados | a posição é impossível |
| só com o lado indicado | o *lado a jogar* provavelmente está trocado |

A segunda é uma suspeita útil e concreta, e não um falso alarme de impossibilidade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import chess

from .fen import board_to_matrix, to_full_fen
from .logging_config import get_logger

logger = get_logger("legality")

#: A posição não pode ter surgido de um jogo. É erro, não gosto.
SEVERITY_IMPOSSIBLE = "impossivel"
#: Possível, mas do tipo que costuma ser erro de leitura.
SEVERITY_SUSPECT = "suspeita"

#: Peças que começam com um par, e a dama, que começa sozinha. Base da
#: contabilidade de promoções.
_PAIRED = (("cavalo", chess.KNIGHT, 2), ("bispo", chess.BISHOP, 2), ("torre", chess.ROOK, 2))
_QUEEN = ("dama", chess.QUEEN, 1)

_FLAG_MESSAGES: dict[chess.Status, tuple[str, str]] = {
    chess.STATUS_NO_WHITE_KING: ("sem_rei_branco", "não há rei branco"),
    chess.STATUS_NO_BLACK_KING: ("sem_rei_preto", "não há rei preto"),
    chess.STATUS_TOO_MANY_KINGS: ("reis_demais", "há mais de um rei de alguma cor"),
    chess.STATUS_TOO_MANY_WHITE_PAWNS: ("peoes_brancos_demais", "mais de 8 peões brancos"),
    chess.STATUS_TOO_MANY_BLACK_PAWNS: ("peoes_pretos_demais", "mais de 8 peões pretos"),
    chess.STATUS_PAWNS_ON_BACKRANK: ("peao_na_ultima_fila", "há peão na 1ª ou na 8ª fila"),
    chess.STATUS_TOO_MANY_WHITE_PIECES: ("pecas_brancas_demais", "mais de 16 peças brancas"),
    chess.STATUS_TOO_MANY_BLACK_PIECES: ("pecas_pretas_demais", "mais de 16 peças pretas"),
    chess.STATUS_OPPOSITE_CHECK: (
        "xeque_do_lado_errado",
        "o rei de quem não está a jogar está em xeque",
    ),
    chess.STATUS_TOO_MANY_CHECKERS: (
        "xeques_demais",
        "há mais peças dando xeque do que seria possível",
    ),
    chess.STATUS_IMPOSSIBLE_CHECK: (
        "xeque_impossivel",
        "este xeque não poderia ter acontecido",
    ),
}

#: Códigos que `fen.validate_piece_placement` já reporta com as suas próprias
#: palavras. Quem mostra as duas listas juntas filtra estes para não dizer a mesma
#: coisa duas vezes.
LEGACY_CODES = frozenset({"sem_rei_branco", "sem_rei_preto", "reis_demais", "peao_na_ultima_fila"})


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str

    @property
    def impossible(self) -> bool:
        return self.severity == SEVERITY_IMPOSSIBLE

    def label(self) -> str:
        """Texto para a interface e para a coluna `avisos` do relatório."""
        prefix = "impossível" if self.impossible else "suspeita"
        return f"{prefix}: {self.message}"


def _status_flags(status: chess.Status) -> list[chess.Status]:
    return [flag for flag in _FLAG_MESSAGES if status & flag]


def _finding_for(flag: chess.Status, severity: str) -> Finding:
    code, message = _FLAG_MESSAGES[flag]
    return Finding(code=code, message=message, severity=severity)


def _promotion_findings(piece_placement: str) -> list[Finding]:
    """Contabilidade de promoções, que o `python-chess` não faz.

    Cada peça além do conjunto inicial só pode ter vindo de uma promoção, e cada
    promoção gasta um peão. Se as promoções exigidas passam dos peões que faltam, a
    posição não existe — é condição *necessária*, e basta para acusar.
    """
    board = chess.Board(to_full_fen(piece_placement))
    findings: list[Finding] = []
    for color, label in ((chess.WHITE, "brancas"), (chess.BLACK, "pretas")):
        pawns = len(board.pieces(chess.PAWN, color))
        missing_pawns = max(0, 8 - pawns)
        needed = 0
        detail: list[str] = []
        for name, piece_type, initial in (*_PAIRED, _QUEEN):
            count = len(board.pieces(piece_type, color))
            extra = max(0, count - initial)
            if extra:
                needed += extra
                detail.append(f"{count} {name}(s)")
        if not needed:
            continue
        joined = ", ".join(detail)
        if needed > missing_pawns:
            findings.append(
                Finding(
                    code="promocoes_impossiveis",
                    message=(
                        f"material impossível nas {label}: {joined} exige "
                        f"{needed} promoção(ões), mas só faltam {missing_pawns} peão(ões)"
                    ),
                    severity=SEVERITY_IMPOSSIBLE,
                )
            )
        else:
            findings.append(
                Finding(
                    code="material_incomum",
                    message=(
                        f"material incomum nas {label}: {joined} — "
                        f"exige {needed} promoção(ões)"
                    ),
                    severity=SEVERITY_SUSPECT,
                )
            )
    return findings


def audit(piece_placement: str, side_to_move: str = "w") -> list[Finding]:
    """Achados de legalidade da posição, os impossíveis primeiro.

    Tabuleiro vazio devolve lista vazia: é o estado de partida do app, e acusá-lo
    seria ruído em cima de quem ainda não montou nada.
    """
    try:
        board_to_matrix(piece_placement)
    except ValueError as exc:
        # Quem chama costuma ter validado antes; se não validou, não é hora de
        # engolir o problema em silêncio.
        return [Finding(code="fen_invalida", message=str(exc), severity=SEVERITY_IMPOSSIBLE)]

    side = "b" if str(side_to_move) == "b" else "w"
    other = "w" if side == "b" else "b"
    try:
        given = chess.Board(to_full_fen(piece_placement, side)).status()
        flipped = chess.Board(to_full_fen(piece_placement, other)).status()
    except ValueError as exc:  # pragma: no cover - board_to_matrix já barrou
        logger.warning("FEN recusada pelo python-chess: %s", exc)
        return [Finding(code="fen_invalida", message=str(exc), severity=SEVERITY_IMPOSSIBLE)]

    if given & chess.STATUS_EMPTY:
        return []

    findings: list[Finding] = []
    # Presente com os dois lados = o problema é da posição, não do preenchimento.
    for flag in _status_flags(given & flipped):
        findings.append(_finding_for(flag, SEVERITY_IMPOSSIBLE))

    side_only = _status_flags(given & ~flipped)
    if side_only:
        reasons = "; ".join(_FLAG_MESSAGES[flag][1] for flag in side_only)
        lado = "brancas" if side == "w" else "pretas"
        outro = "pretas" if side == "w" else "brancas"
        findings.append(
            Finding(
                code="lado_a_jogar_trocado",
                message=(
                    f"com as {lado} a jogar, {reasons} — a posição fica legal com as "
                    f"{outro} a jogar, então o lado a jogar provavelmente está trocado"
                ),
                severity=SEVERITY_SUSPECT,
            )
        )

    findings.extend(_promotion_findings(piece_placement))
    # Impossíveis primeiro: é o que precisa de decisão, não de opinião.
    findings.sort(key=lambda item: 0 if item.impossible else 1)
    return findings


def is_impossible(piece_placement: str, side_to_move: str = "w") -> bool:
    """A posição não pode ter surgido de um jogo."""
    return any(finding.impossible for finding in audit(piece_placement, side_to_move))


def has_findings(piece_placement: str, side_to_move: str = "w") -> bool:
    return bool(audit(piece_placement, side_to_move))


def labels(findings: Iterable[Finding], skip_codes: Iterable[str] = ()) -> list[str]:
    """Textos prontos, opcionalmente sem os códigos que outra lista já cobre."""
    skip = set(skip_codes)
    return [finding.label() for finding in findings if finding.code not in skip]
