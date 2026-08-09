"""Auditoria de legalidade da posição (§37).

A validação anterior confere a *escrita* da FEN. Estes testes cobrem o degrau
seguinte: a FEN bem escrita cuja posição **não pode ter existido** — e, do outro
lado, a que apenas parece errada porque o lado a jogar veio preenchido por padrão.

O teste que explica a existência do módulo é
`test_python_chess_alone_would_let_the_three_queens_pass`: ele fixa exatamente o
que o motor não faz, para que se um dia passar a fazer, o nosso código possa sair.
"""
from __future__ import annotations

import pytest

import chess

from chess_pdf_editor.fen import to_full_fen
from chess_pdf_editor.legality import (
    LEGACY_CODES,
    SEVERITY_IMPOSSIBLE,
    SEVERITY_SUSPECT,
    audit,
    has_findings,
    is_impossible,
    labels,
)

NORMAL = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
EMPTY = "8/8/8/8/8/8/8/8"
#: Torre branca em e1, rei preto em e8: as pretas estão em xeque.
BLACK_IN_CHECK = "4k3/8/8/8/8/8/8/4R1K1"
ADJACENT_KINGS = "8/8/8/3kK3/8/8/8/8"
THREE_QUEENS_ALL_PAWNS = "4k3/8/8/8/8/QQQ5/PPPPPPPP/4K3"
THREE_QUEENS_FEW_PAWNS = "4k3/8/8/8/8/QQQ5/PPPPP3/4K3"
NINE_PAWNS = "4k3/8/8/8/8/P7/PPPPPPPP/4K3"
NO_KINGS = "8/8/8/3rr3/8/8/8/8"


def _codes(piece_placement: str, side: str = "w") -> list[str]:
    return [finding.code for finding in audit(piece_placement, side)]


# ---------------------------------------------------------------------------
# O que não deve fazer barulho
# ---------------------------------------------------------------------------


def test_a_normal_position_has_nothing_to_say() -> None:
    assert audit(NORMAL, "w") == []
    assert audit(NORMAL, "b") == []
    assert is_impossible(NORMAL) is False
    assert has_findings(NORMAL) is False


def test_the_empty_board_is_not_an_error() -> None:
    """É o estado em que o app abre; acusá-lo seria ruído sobre quem não montou nada."""
    assert audit(EMPTY, "w") == []
    assert is_impossible(EMPTY) is False


# ---------------------------------------------------------------------------
# A armadilha do lado a jogar
# ---------------------------------------------------------------------------


def test_check_on_the_wrong_side_is_a_swapped_side_not_an_impossible_position() -> None:
    """O app preenche `brancas` por padrão, e um diagrama de livro não diz de quem
    é a vez: acusar de impossível aqui seria falso alarme."""
    findings = audit(BLACK_IN_CHECK, "w")

    assert [f.code for f in findings] == ["lado_a_jogar_trocado"]
    assert findings[0].severity == SEVERITY_SUSPECT
    assert is_impossible(BLACK_IN_CHECK, "w") is False
    # A mensagem diz o que fazer, não só que algo está errado.
    assert "pretas a jogar" in findings[0].message


def test_the_same_position_with_the_right_side_is_clean() -> None:
    assert audit(BLACK_IN_CHECK, "b") == []


def test_adjacent_kings_are_impossible_with_either_side() -> None:
    """Ilegal dos dois lados: aí o problema é a posição, não o preenchimento."""
    for side in ("w", "b"):
        findings = audit(ADJACENT_KINGS, side)
        assert [f.code for f in findings] == ["xeque_do_lado_errado"]
        assert findings[0].severity == SEVERITY_IMPOSSIBLE
        assert is_impossible(ADJACENT_KINGS, side) is True


# ---------------------------------------------------------------------------
# Contabilidade de promoções
# ---------------------------------------------------------------------------


def test_python_chess_alone_would_let_the_three_queens_pass() -> None:
    """A razão de este módulo existir, fixada em teste.

    Se um dia o `python-chess` passar a contabilizar promoções, este teste falha e
    avisa que o nosso código pode sair.
    """
    board = chess.Board(to_full_fen(THREE_QUEENS_ALL_PAWNS))
    assert board.status() == chess.STATUS_VALID, (
        "o python-chess passou a pegar isto; reavalie `_promotion_findings`"
    )
    assert is_impossible(THREE_QUEENS_ALL_PAWNS) is True


def test_three_queens_with_every_pawn_home_is_impossible() -> None:
    findings = audit(THREE_QUEENS_ALL_PAWNS, "w")

    assert [f.code for f in findings] == ["promocoes_impossiveis"]
    assert findings[0].severity == SEVERITY_IMPOSSIBLE
    assert "2 promoção(ões)" in findings[0].message
    assert "0 peão(ões)" in findings[0].message


def test_three_queens_with_pawns_missing_is_only_unusual() -> None:
    """Possível: os peões que faltam podem ter sido os promovidos."""
    findings = audit(THREE_QUEENS_FEW_PAWNS, "w")

    assert [f.code for f in findings] == ["material_incomum"]
    assert findings[0].severity == SEVERITY_SUSPECT
    assert is_impossible(THREE_QUEENS_FEW_PAWNS) is False


def test_extra_officers_are_counted_per_side() -> None:
    """Três cavalos brancos exigem uma promoção tanto quanto três damas."""
    three_knights = "4k3/8/8/8/8/NNN5/PPPPPPPP/4K3"
    assert _codes(three_knights) == ["promocoes_impossiveis"]

    black_side = "4k3/pppppppp/nnn5/8/8/8/8/4K3"
    findings = audit(black_side, "w")
    assert [f.code for f in findings] == ["promocoes_impossiveis"]
    assert "pretas" in findings[0].message


# ---------------------------------------------------------------------------
# O que o python-chess já pega
# ---------------------------------------------------------------------------


def test_nine_pawns_is_impossible() -> None:
    assert "peoes_brancos_demais" in _codes(NINE_PAWNS)
    assert is_impossible(NINE_PAWNS) is True


def test_a_position_without_kings_is_impossible() -> None:
    codes = _codes(NO_KINGS)
    assert "sem_rei_branco" in codes
    assert "sem_rei_preto" in codes


def test_a_pawn_on_the_last_rank_is_impossible() -> None:
    assert "peao_na_ultima_fila" in _codes("4k2P/8/8/8/8/8/8/4K3")


# ---------------------------------------------------------------------------
# Forma da saída
# ---------------------------------------------------------------------------


def test_the_impossible_comes_before_the_merely_suspect() -> None:
    """Impossível precisa de decisão; suspeita é opinião. A ordem reflete isso."""
    # Torre em e7 dá xeque ao rei preto (suspeita de lado trocado, porque com as
    # pretas a jogar a posição é legal) e três damas com os oito peões em casa
    # (impossível).
    mixed = "4k3/4R3/8/8/8/QQQ5/PPPPPPPP/7K"
    findings = audit(mixed, "w")

    severities = [f.severity for f in findings]
    assert SEVERITY_IMPOSSIBLE in severities and SEVERITY_SUSPECT in severities
    assert severities == sorted(severities, key=lambda s: 0 if s == SEVERITY_IMPOSSIBLE else 1)
    assert findings[0].code == "promocoes_impossiveis"


def test_the_label_says_which_kind_it_is() -> None:
    impossible = audit(ADJACENT_KINGS, "w")[0]
    suspect = audit(BLACK_IN_CHECK, "w")[0]

    assert impossible.label().startswith("impossível: ")
    assert suspect.label().startswith("suspeita: ")


def test_labels_can_skip_what_another_list_already_says() -> None:
    """`validate_piece_placement` já reporta reis e peão na última fila."""
    findings = audit(NO_KINGS, "w")

    assert labels(findings)
    assert labels(findings, skip_codes=LEGACY_CODES) == []


def test_a_broken_fen_is_reported_and_not_swallowed() -> None:
    findings = audit("nao/e/uma/fen", "w")

    assert [f.code for f in findings] == ["fen_invalida"]
    assert findings[0].severity == SEVERITY_IMPOSSIBLE


@pytest.mark.parametrize("side", ["w", "b", "", "x", None])
def test_any_side_value_is_accepted(side) -> None:
    """O lado chega de FEN de terceiros e de projeto salvo; não pode explodir."""
    assert audit(NORMAL, side) == []


# ---------------------------------------------------------------------------
# Onde a auditoria aparece
# ---------------------------------------------------------------------------

fitz = pytest.importorskip("fitz")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from conftest import DIAGRAM_RECT, make_pdf  # noqa: E402

from chess_pdf_editor.report import build_rows  # noqa: E402
from chess_pdf_editor.types import OverlayOperation  # noqa: E402


def _candidate(fen: str, confidence, side: str = "w") -> OverlayOperation:
    return OverlayOperation(
        page_num=0,
        rect_pdf=DIAGRAM_RECT,
        fen=fen,
        side_to_move=side,
        source="local-candidato",
        confidence=confidence,
    )


def test_an_impossible_reading_goes_to_the_review_queue_even_when_confident(
    main_window, tmp_path
) -> None:
    """O ganho de escala do sprint: confiança 0,99 não salva posição impossível."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)

    confident_and_legal = _candidate(NORMAL, 0.99)
    confident_and_impossible = _candidate(THREE_QUEENS_ALL_PAWNS, 0.99)

    assert main_window._is_uncertain(confident_and_legal) is False
    assert main_window._is_uncertain(confident_and_impossible) is True


def test_a_swapped_side_alone_does_not_flood_the_queue(main_window, tmp_path) -> None:
    """Suspeita não é impossibilidade: o lado a jogar vem preenchido por padrão em
    todo diagrama de livro, e mandar todos para a fila esvaziaria o filtro."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)

    assert main_window._is_uncertain(_candidate(BLACK_IN_CHECK, 0.99)) is False


def test_the_queue_says_why_an_impossible_candidate_is_there(main_window, tmp_path) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.candidates = [_candidate(THREE_QUEENS_ALL_PAWNS, 0.99), _candidate(NORMAL, 0.99)]
    main_window._refresh_candidates_list()

    rows = [
        main_window.candidates_list.item(row).text()
        for row in range(main_window.candidates_list.count())
    ]
    flagged = [text for text in rows if "impossível" in text]
    assert len(flagged) == 1, f"esperava um marcado e um limpo, veio {rows}"


def test_the_live_warnings_show_the_audit(main_window, tmp_path) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)

    main_window.board_editor.set_piece_placement(THREE_QUEENS_ALL_PAWNS)
    assert "impossível" in main_window.warnings.text()

    main_window.board_editor.set_piece_placement(NORMAL)
    assert main_window.warnings.text() == ""


def test_the_live_warnings_do_not_say_the_same_thing_twice(main_window, tmp_path) -> None:
    """`validate_piece_placement` já avisa sobre reis; a auditoria não repete."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)

    main_window.board_editor.set_piece_placement(NO_KINGS)
    text = main_window.warnings.text()

    assert text.count("rei") <= 2, f"aviso repetido: {text!r}"
    assert "impossível: não há rei" not in text


def test_the_report_carries_the_audit(tmp_path) -> None:
    """O relatório é como se audita um livro inteiro fora do app."""
    rows = build_rows(
        operations=[
            OverlayOperation(page_num=0, rect_pdf=DIAGRAM_RECT, fen=THREE_QUEENS_ALL_PAWNS),
            OverlayOperation(page_num=1, rect_pdf=DIAGRAM_RECT, fen=NORMAL),
        ],
        erase_operations=[],
    )

    assert any("impossível" in aviso for aviso in rows[0].avisos)
    assert rows[1].avisos == []
