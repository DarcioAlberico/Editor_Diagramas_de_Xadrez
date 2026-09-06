"""Diff entre dois projetos salvos (§40).

O teste que carrega o sprint é `test_a_refined_bbox_is_the_same_diagram`: um diff por
chave exata reportaria todo diagrama reenquadrado como removido + readicionado, o que
é tecnicamente correto e inútil justamente no caso de uso — reprocessar um livro com
um detector melhor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chess_pdf_editor.project_diff import (
    MATCH_IOU,
    REASON_CONFIDENCE,
    REASON_FEN,
    REASON_META,
    REASON_RECT,
    REASON_STYLE,
    diff_files,
    diff_states,
    format_diff,
    rect_iou,
)
from chess_pdf_editor.project_state import ProjectState, save_project_state
from chess_pdf_editor.types import EraseOperation, OverlayOperation, StudyPosition

FEN = "8/8/8/4k3/8/8/4K3/8"
OTHER_FEN = "8/8/8/3qk3/8/8/4K3/8"
RECT = (100.0, 300.0, 260.0, 460.0)
FAR_RECT = (100.0, 90.0, 260.0, 250.0)


def _op(fen: str = FEN, rect=RECT, page: int = 0, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=rect, fen=fen, **kwargs)


def _state(
    operations=(),
    erase_operations=(),
    study_positions=(),
    candidates=(),
    sha: str = "mesmo-livro",
    source: str = "livro.pdf",
    **kwargs,
) -> ProjectState:
    return ProjectState(
        source_pdf=source,
        source_pdf_fingerprint={"sha256": sha} if sha else {},
        operations=list(operations),
        erase_operations=list(erase_operations),
        study_positions=list(study_positions),
        candidates=list(candidates),
        **kwargs,
    )


def _shift(rect, dx: float = 0.0, dy: float = 0.0, grow: float = 0.0):
    x0, y0, x1, y1 = rect
    return (x0 + dx - grow, y0 + dy - grow, x1 + dx + grow, y1 + dy + grow)


# ---------------------------------------------------------------------------
# IoU e casamento
# ---------------------------------------------------------------------------


def test_iou_of_the_same_rect_is_one() -> None:
    assert rect_iou(RECT, RECT) == pytest.approx(1.0)


def test_iou_of_disjoint_rects_is_zero() -> None:
    assert rect_iou(RECT, FAR_RECT) == 0.0


def test_nothing_changed_when_the_two_projects_are_equal() -> None:
    diff = diff_states(_state([_op()]), _state([_op()]))

    assert diff.has_changes is False
    assert diff.unchanged == 1
    assert (diff.added, diff.removed, diff.changed) == ([], [], [])


def test_a_refined_bbox_is_the_same_diagram() -> None:
    """O caso de uso do sprint: detector melhor devolve a moldura alguns pontos
    diferente. Um diff por chave exata veria dois diagramas onde há um."""
    before = _state([_op()])
    after = _state([_op(rect=_shift(RECT, dx=3.0, dy=-2.0, grow=1.5))])

    diff = diff_states(before, after)

    assert (diff.added, diff.removed) == ([], []), "o mesmo diagrama foi contado duas vezes"
    assert len(diff.changed) == 1
    assert diff.changed[0].reasons == (REASON_RECT,)
    assert diff.changed[0].fen_changed is False


def test_a_diagram_that_moved_far_is_added_and_removed() -> None:
    """Sem sobreposição não há como afirmar que é o mesmo; dizer que é seria pior."""
    diff = diff_states(_state([_op(rect=RECT)]), _state([_op(rect=FAR_RECT)]))

    assert len(diff.added) == 1
    assert len(diff.removed) == 1
    assert diff.changed == []


def test_the_match_threshold_is_the_documented_one() -> None:
    """Prende o limiar: mudá-lo sem querer muda o que o diff reporta."""
    # Retângulo encolhido até ficar logo abaixo e logo acima de MATCH_IOU.
    x0, y0, x1, y1 = RECT
    side = x1 - x0

    def shrunk(ratio: float):
        return (x0, y0, x0 + side * ratio, y0 + side * ratio)

    # IoU de quadrados concêntricos-no-canto = ratio^2.
    below = shrunk((MATCH_IOU - 0.08) ** 0.5)
    above = shrunk((MATCH_IOU + 0.08) ** 0.5)
    assert rect_iou(RECT, below) < MATCH_IOU
    assert rect_iou(RECT, above) > MATCH_IOU

    assert diff_states(_state([_op()]), _state([_op(rect=below)])).changed == []
    assert len(diff_states(_state([_op()]), _state([_op(rect=above)])).changed) == 1


def test_two_diagrams_on_a_page_pair_with_the_right_partners() -> None:
    """Casamento guloso pelo maior IoU: o par mais sobreposto resolve primeiro."""
    top, bottom = FAR_RECT, RECT
    before = _state([_op(fen=FEN, rect=top), _op(fen=OTHER_FEN, rect=bottom)])
    after = _state(
        [
            _op(fen=OTHER_FEN, rect=_shift(bottom, dy=2.0)),
            _op(fen=FEN, rect=_shift(top, dy=-2.0)),
        ]
    )

    diff = diff_states(before, after)

    assert (diff.added, diff.removed) == ([], [])
    # Cada um casou com o seu: nenhuma FEN mudou.
    assert diff.unchanged + len(diff.changed) == 2
    assert diff.fen_changes == []


def test_a_diagram_on_another_page_never_matches() -> None:
    diff = diff_states(_state([_op(page=0)]), _state([_op(page=5)]))

    assert len(diff.added) == 1 and len(diff.removed) == 1


# ---------------------------------------------------------------------------
# Motivos da alteração
# ---------------------------------------------------------------------------


def test_a_different_reading_is_reported_as_a_fen_change() -> None:
    diff = diff_states(_state([_op(fen=FEN)]), _state([_op(fen=OTHER_FEN)]))

    assert len(diff.changed) == 1
    assert diff.changed[0].reasons == (REASON_FEN,)
    assert diff.fen_changes == diff.changed


def test_confidence_alone_is_a_reason() -> None:
    diff = diff_states(
        _state([_op(confidence=0.42)]), _state([_op(confidence=0.97)])
    )
    assert diff.changed[0].reasons == (REASON_CONFIDENCE,)


def test_appearing_confidence_counts_as_a_change() -> None:
    """Motor que passou a reportar confiança é informação, não ruído."""
    diff = diff_states(_state([_op(confidence=None)]), _state([_op(confidence=0.9)]))
    assert diff.changed[0].reasons == (REASON_CONFIDENCE,)


def test_style_alone_is_a_reason() -> None:
    diff = diff_states(
        _state([_op(border_width_pt=0.0)]), _state([_op(border_width_pt=1.5)])
    )
    assert diff.changed[0].reasons == (REASON_STYLE,)


def test_side_to_move_alone_is_a_reason() -> None:
    diff = diff_states(_state([_op(side_to_move="w")]), _state([_op(side_to_move="b")]))
    assert diff.changed[0].reasons == (REASON_META,)


def test_rounding_noise_is_not_a_change() -> None:
    """Meio décimo de ponto é arredondamento do detector, não notícia."""
    diff = diff_states(_state([_op()]), _state([_op(rect=_shift(RECT, dx=0.2))]))

    assert diff.changed == []
    assert diff.unchanged == 1


def test_several_reasons_are_all_reported() -> None:
    before = _state([_op(fen=FEN, confidence=0.5)])
    after = _state([_op(fen=OTHER_FEN, rect=_shift(RECT, dx=4.0), confidence=0.99)])

    reasons = diff_states(before, after).changed[0].reasons

    assert set(reasons) == {REASON_FEN, REASON_RECT, REASON_CONFIDENCE}


# ---------------------------------------------------------------------------
# O resto do projeto
# ---------------------------------------------------------------------------


def test_erasures_are_matched_geometrically_too() -> None:
    before = _state(erase_operations=[EraseOperation(page_num=0, rect_pdf=RECT)])
    after = _state(
        erase_operations=[EraseOperation(page_num=0, rect_pdf=_shift(RECT, dx=2.0))]
    )

    diff = diff_states(before, after)

    assert (diff.erases_added, diff.erases_removed) == ([], [])


def test_a_new_erasure_shows_up() -> None:
    after = _state(
        erase_operations=[
            EraseOperation(page_num=0, rect_pdf=RECT),
            EraseOperation(page_num=1, rect_pdf=FAR_RECT),
        ]
    )
    before = _state(erase_operations=[EraseOperation(page_num=0, rect_pdf=RECT)])

    diff = diff_states(before, after)

    assert len(diff.erases_added) == 1
    assert diff.erases_removed == []
    assert diff.has_changes is True


def test_counts_that_only_moved_are_reported_as_counts() -> None:
    study = StudyPosition(page_num=0, rect_pdf=RECT, fen=FEN)
    diff = diff_states(_state(), _state(study_positions=[study], candidates=[_op()]))

    assert (diff.study_before, diff.study_after) == (0, 1)
    assert (diff.candidates_before, diff.candidates_after) == (0, 1)


def test_book_wide_settings_are_reported() -> None:
    diff = diff_states(
        _state(include_lichess_link=True, erase_coordinates=False),
        _state(include_lichess_link=False, erase_coordinates=True),
    )

    assert dict((name, (a, b)) for name, a, b in diff.settings) == {
        "include_lichess_link": (True, False),
        "erase_coordinates": (False, True),
    }
    assert diff.has_changes is True


# ---------------------------------------------------------------------------
# A checagem que vem antes de tudo
# ---------------------------------------------------------------------------


def test_projects_of_different_books_are_flagged() -> None:
    diff = diff_states(
        _state([_op()], sha="livro-a", source="a.pdf"),
        _state([_op()], sha="livro-b", source="b.pdf"),
    )

    assert diff.same_source is False
    assert "provavelmente não quer dizer nada" in format_diff(diff)


def test_a_missing_fingerprint_is_not_evidence_of_another_book() -> None:
    """Projeto antigo pode não ter sha; acusar por ausência seria falso alarme."""
    diff = diff_states(_state([_op()], sha=""), _state([_op()], sha="livro-b"))

    assert diff.same_source is True


def test_the_same_book_is_not_flagged() -> None:
    diff = diff_states(_state([_op()]), _state([_op()]))

    assert diff.same_source is True
    assert "PDFs diferentes" not in format_diff(diff)


# ---------------------------------------------------------------------------
# Saída legível e arquivos
# ---------------------------------------------------------------------------


def test_the_summary_says_nothing_changed_when_nothing_did() -> None:
    assert "nada mudou" in format_diff(diff_states(_state([_op()]), _state([_op()])))


def test_the_summary_counts_and_lists() -> None:
    before = _state([_op(fen=FEN), _op(fen=FEN, rect=FAR_RECT)])
    after = _state([_op(fen=OTHER_FEN), _op(fen=FEN, rect=FAR_RECT, page=3)])

    text = format_diff(diff_states(before, after))

    assert "1 adicionada(s)" in text
    assert "1 removida(s)" in text
    assert "1 alterada(s)" in text
    assert "mudaram de FEN" in text


def test_long_lists_say_how_many_were_left_out() -> None:
    """Recorte silencioso se lê como "foi só isso"."""
    before = _state()
    after = _state([_op(page=page) for page in range(30)])

    text = format_diff(diff_states(before, after), limit=5)

    assert "e mais 25" in text


def test_diffing_two_saved_files(tmp_path: Path) -> None:
    """O caminho real: dois `project_state.json` em disco."""
    before_path = tmp_path / "antes.json"
    after_path = tmp_path / "depois.json"
    save_project_state(str(before_path), _state([_op(fen=FEN)]))
    save_project_state(str(after_path), _state([_op(fen=OTHER_FEN)]))

    diff = diff_files(str(before_path), str(after_path))

    assert len(diff.changed) == 1
    assert diff.changed[0].reasons == (REASON_FEN,)


# ---------------------------------------------------------------------------
# Na janela
# ---------------------------------------------------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_the_window_shows_the_diff(main_window, tmp_path, monkeypatch) -> None:
    before_path = tmp_path / "antes.json"
    after_path = tmp_path / "depois.json"
    save_project_state(str(before_path), _state([_op(fen=FEN)]))
    save_project_state(str(after_path), _state([_op(fen=OTHER_FEN)]))

    shown: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QDialog,
        "exec",
        lambda self: shown.append(self.findChild(QtWidgets.QPlainTextEdit).toPlainText()),
    )
    main_window._show_project_diff(
        diff_files(str(before_path), str(after_path)), str(before_path), str(after_path)
    )

    assert shown and "1 alterada(s)" in shown[0]
    assert main_window.project_diff_dialog is None, "o diálogo ficou pendurado"


def test_the_command_exists_and_explains_itself(main_window) -> None:
    action = main_window.act_compare_projects
    assert action.toolTip().strip()
    assert action.isEnabled() is True, "comparar arquivos não depende de PDF aberto"


# ---------------------------------------------------------------------------
# O link Lichess por diagrama (§59.12)
# ---------------------------------------------------------------------------


def test_the_diff_sees_the_per_diagram_lichess_choice() -> None:
    """O campo entrou no schema 10 e o diff nao foi junto.

    Trocar o link de 300 diagramas na galeria e comparar os dois projetos respondia
    "nada mudou entre os dois projetos".
    """
    from chess_pdf_editor.project_diff import REASON_LINK

    diff = diff_states(
        _state([_op(include_lichess_link=None)]),
        _state([_op(include_lichess_link=False)]),
    )

    assert diff.has_changes
    assert diff.changed[0].reasons == (REASON_LINK,)


def test_default_and_explicit_no_are_different_states() -> None:
    """`None` segue a global; `False` recusa o link **neste** diagrama (§52.1).

    Comparar os dois como booleanos convertidos apagaria a distincao — e ela e a
    razao de o campo ter tres estados em vez de dois.
    """
    from chess_pdf_editor.project_diff import REASON_LINK

    diff = diff_states(
        _state([_op(include_lichess_link=None)]),
        _state([_op(include_lichess_link=True)]),
    )
    assert diff.changed[0].reasons == (REASON_LINK,)

    igual = diff_states(
        _state([_op(include_lichess_link=False)]),
        _state([_op(include_lichess_link=False)]),
    )
    assert not igual.changed, "a mesma escolha virou mudanca"
