from __future__ import annotations

from chess_pdf_editor.history import ChangeHistory, ChangeSnapshot
from chess_pdf_editor.types import EraseOperation, OverlayOperation

FEN_A = "8/8/8/4k3/8/8/4K3/8"
FEN_B = "8/8/8/3qk3/8/8/4K3/8"


def _op(page: int = 0, fen: str = FEN_A) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=(10.0, 10.0, 90.0, 90.0), fen=fen)


def _erase(page: int = 0) -> EraseOperation:
    return EraseOperation(page_num=page, rect_pdf=(0.0, 0.0, 20.0, 20.0))


def test_starts_with_nothing_to_undo() -> None:
    history = ChangeHistory()
    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo() is None


def test_undo_and_redo_round_trip() -> None:
    history = ChangeHistory()
    ops = [_op()]
    history.commit("adicionar substituição", ops, [], [])

    assert history.can_undo is True
    assert history.undo_label == "adicionar substituição"

    undone = history.undo()
    assert undone is not None
    assert undone.restore_operations() == []
    assert history.can_redo is True

    redone = history.redo()
    assert redone is not None
    assert len(redone.restore_operations()) == 1
    assert redone.restore_operations()[0].fen == FEN_A


def test_snapshot_is_immune_to_later_mutation() -> None:
    """O historico guarda copia: editar a lista da UI depois nao reescreve o passado."""
    history = ChangeHistory()
    ops = [_op()]
    history.commit("adicionar", ops, [], [])

    ops[0].fen = FEN_B
    ops.append(_op(page=1))

    restored = history.current.restore_operations()
    assert len(restored) == 1
    assert restored[0].fen == FEN_A


def test_commit_without_real_change_is_ignored() -> None:
    history = ChangeHistory()
    ops = [_op()]
    assert history.commit("adicionar", ops, [], []) is True
    assert history.commit("nada mudou", ops, [], []) is False
    assert len(history) == 2


def test_new_commit_discards_the_redo_branch() -> None:
    history = ChangeHistory()
    history.commit("primeira", [_op()], [], [])
    history.commit("segunda", [_op(), _op(page=1)], [], [])
    history.undo()
    assert history.can_redo is True

    history.commit("terceira", [_op(), _op(page=2)], [], [])
    assert history.can_redo is False
    assert history.current.restore_operations()[1].page_num == 2


def test_tracks_erasers_and_candidates_too() -> None:
    history = ChangeHistory()
    history.commit("apagamento", [], [_erase()], [])
    history.commit("candidato", [], [_erase()], [_op(fen=FEN_B)])

    snapshot = history.undo()
    assert snapshot is not None
    assert snapshot.restore_candidates() == []
    assert len(snapshot.restore_erase_operations()) == 1


def test_limit_drops_the_oldest_states_only() -> None:
    history = ChangeHistory(limit=4)
    for index in range(10):
        history.commit(f"passo {index}", [_op(page=index)], [], [])

    assert len(history) == 4
    # O presente continua sendo o ultimo commit.
    assert history.current.restore_operations()[0].page_num == 9
    assert history.can_undo is True


def test_reset_clears_the_past() -> None:
    history = ChangeHistory()
    history.commit("adicionar", [_op()], [], [])
    history.reset([_op(), _op(page=1)], [], [], label="carregar projeto")

    assert history.can_undo is False
    assert history.can_redo is False
    assert len(history.current.restore_operations()) == 2


def test_capture_produces_comparable_content() -> None:
    a = ChangeSnapshot.capture("x", [_op()], [], [])
    b = ChangeSnapshot.capture("y", [_op()], [], [])
    assert a.same_content(b) is True
    assert a.same_content(ChangeSnapshot.capture("z", [_op(fen=FEN_B)], [], [])) is False
