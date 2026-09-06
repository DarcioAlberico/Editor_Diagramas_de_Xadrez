from __future__ import annotations

import json

from chess_pdf_editor.project_state import ProjectState, load_project_state, save_project_state
from chess_pdf_editor.types import EraseOperation, OverlayOperation, StudyPosition


def test_project_state_roundtrip_with_candidates(tmp_path):
    path = tmp_path / "state.json"
    state = ProjectState(
        source_pdf="orig.pdf",
        source_pdf_fingerprint={"sha256": "abc"},
        operations=[],
        candidates=[
            OverlayOperation(
                page_num=4,
                rect_pdf=(10.0, 20.0, 30.0, 40.0),
                fen="8/8/8/4k3/8/8/4K3/8",
                side_to_move="b",
                fullmove_number=9,
                source="ocr-page-candidato",
                whiteout_padding_left_pt=1.5,
                border_width_pt=0.75,
            )
        ],
    )
    save_project_state(str(path), state)
    loaded = load_project_state(str(path))

    assert loaded.operations == []
    assert len(loaded.candidates) == 1
    candidate = loaded.candidates[0]
    assert candidate.page_num == 4
    assert candidate.fen == "8/8/8/4k3/8/8/4K3/8"
    assert candidate.side_to_move == "b"
    assert candidate.fullmove_number == 9
    assert candidate.source == "ocr-page-candidato"
    assert candidate.whiteout_padding_left_pt == 1.5
    assert candidate.border_width_pt == 0.75


def test_project_without_candidates_field_still_loads(tmp_path):
    """Projetos salvos antes do schema 8 continuam abrindo."""
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "source_pdf": "orig.pdf",
                "source_pdf_fingerprint": {"sha256": "abc"},
                "operations": [
                    {"page_num": 0, "rect_pdf": [1.0, 2.0, 3.0, 4.0], "fen": "8/8/8/8/8/8/8/8"}
                ],
                "schema_version": 7,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_project_state(str(path))
    assert loaded.candidates == []
    assert len(loaded.operations) == 1


def test_project_state_roundtrip_with_erasers(tmp_path):
    path = tmp_path / "state.json"
    state = ProjectState(
        source_pdf="orig.pdf",
        source_pdf_fingerprint={"sha256": "abc"},
        operations=[
            OverlayOperation(
                page_num=0,
                rect_pdf=(10.0, 20.0, 30.0, 40.0),
                fen="8/8/8/8/8/8/8/8",
                side_to_move="b",
                fullmove_number=12,
                whiteout_padding_pt=3.0,
                border_width_pt=1.25,
            )
        ],
        erase_operations=[EraseOperation(page_num=1, rect_pdf=(1.0, 2.0, 3.0, 4.0))],
        study_positions=[
            StudyPosition(
                page_num=3,
                rect_pdf=(11.0, 12.0, 13.0, 14.0),
                fen="8/8/8/8/8/8/8/8",
                side_to_move="b",
                fullmove_number=8,
                pgn='[Event "Study"]\n\n*',
                comment_before="final de reis",
                comment_after="oposicao",
                move_comments={"2": {"before": "antes", "after": "depois"}},
            )
        ],
        current_page=2,
        include_lichess_link=False,
        ocr_full_next_page=7,
    )
    save_project_state(str(path), state)
    loaded = load_project_state(str(path))

    assert len(loaded.operations) == 1
    assert loaded.operations[0].side_to_move == "b"
    assert loaded.operations[0].fullmove_number == 12
    assert loaded.operations[0].whiteout_padding_pt == 3.0
    assert loaded.operations[0].whiteout_padding_left_pt == 0.5
    assert loaded.operations[0].whiteout_padding_top_pt == 0.5
    assert loaded.operations[0].whiteout_padding_right_pt == 0.5
    assert loaded.operations[0].whiteout_padding_bottom_pt == 0.5
    assert loaded.operations[0].border_width_pt == 1.25
    assert len(loaded.erase_operations) == 1
    assert loaded.erase_operations[0].page_num == 1
    assert len(loaded.study_positions) == 1
    assert loaded.study_positions[0].page_num == 3
    assert loaded.study_positions[0].side_to_move == "b"
    assert loaded.study_positions[0].fullmove_number == 8
    assert loaded.study_positions[0].comment_before == "final de reis"
    assert loaded.study_positions[0].comment_after == "oposicao"
    assert loaded.study_positions[0].move_comments == {"2": {"before": "antes", "after": "depois"}}
    assert loaded.current_page == 2
    assert loaded.include_lichess_link is False
    assert loaded.ocr_full_next_page == 7


def test_load_project_state_legacy_defaults(tmp_path):
    path = tmp_path / "legacy.json"
    payload = {
        "source_pdf": "orig.pdf",
        "source_pdf_fingerprint": {},
        "operations": [
            {
                "page_num": 0,
                "rect_pdf": [10, 20, 30, 40],
                "fen": "8/8/8/8/8/8/8/8",
            }
        ],
        "current_page": 0,
        "schema_version": 1,
        "app_version": "0.1.0",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_project_state(str(path))

    assert len(loaded.operations) == 1
    assert loaded.operations[0].side_to_move == "w"
    assert loaded.operations[0].fullmove_number == 1
    assert loaded.operations[0].whiteout_padding_pt == 0.5
    assert loaded.operations[0].whiteout_padding_left_pt == 0.5
    assert loaded.operations[0].whiteout_padding_top_pt == 0.5
    assert loaded.operations[0].whiteout_padding_right_pt == 0.5
    assert loaded.operations[0].whiteout_padding_bottom_pt == 0.5
    assert loaded.operations[0].border_width_pt == 0.0
    assert loaded.erase_operations == []
    assert loaded.study_positions == []
    assert loaded.include_lichess_link is True
    assert loaded.ocr_full_next_page == 0


def test_load_project_state_legacy_study_note_to_comment_before(tmp_path):
    path = tmp_path / "legacy_study.json"
    payload = {
        "source_pdf": "orig.pdf",
        "source_pdf_fingerprint": {},
        "operations": [],
        "study_positions": [
            {
                "page_num": 0,
                "rect_pdf": [10, 20, 30, 40],
                "fen": "8/8/8/8/8/8/8/8",
                "note": "texto antigo",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_project_state(str(path))

    assert loaded.study_positions[0].comment_before == "texto antigo"
    assert loaded.study_positions[0].comment_after == ""
    assert loaded.study_positions[0].side_to_move == "w"
    assert loaded.study_positions[0].fullmove_number == 1
    assert loaded.study_positions[0].move_comments == {"0": {"before": "texto antigo", "after": ""}}


def test_load_project_state_legacy_uniform_padding_to_sides(tmp_path):
    path = tmp_path / "legacy_uniform.json"
    payload = {
        "source_pdf": "orig.pdf",
        "source_pdf_fingerprint": {},
        "operations": [
            {
                "page_num": 0,
                "rect_pdf": [10, 20, 30, 40],
                "fen": "8/8/8/8/8/8/8/8",
                "whiteout_padding_pt": 2.0,
            }
        ],
        "erase_operations": [],
        "current_page": 0,
        "schema_version": 2,
        "app_version": "0.1.0",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_project_state(str(path))
    op = loaded.operations[0]

    assert op.whiteout_padding_pt == 2.0
    assert op.whiteout_padding_left_pt == 2.0
    assert op.whiteout_padding_top_pt == 2.0
    assert op.whiteout_padding_right_pt == 2.0
    assert op.whiteout_padding_bottom_pt == 2.0


# ---------------------------------------------------------------------------
# Cobertura de campos do formato salvo (§45)
# ---------------------------------------------------------------------------
#
# Os testes acima conferem campos escolhidos a dedo. O risco que eles não cobrem é
# outro: alguém acrescenta um campo a `OverlayOperation` e esquece de lê-lo em
# `_load_operation`. O `asdict` grava o campo novo no JSON, o carregador o ignora, e o
# valor do usuário volta como default — **sem erro nenhum**. Conferido de propósito
# antes de escrever isto: um campo novo desaparece em silêncio.
#
# Os testes daqui enumeram os campos pelo próprio dataclass, então campo novo entra
# na conferência sozinho. Tipo que o gerador não conheça **falha** o teste, em vez de
# ser pulado — a intenção é forçar uma decisão, não passar batido.

import dataclasses

import pytest


def _nudged(value: object, field_name: str) -> object:
    """Um valor diferente do que está ali, para qualquer perda ficar visível."""
    # `side_to_move` só aceita w/b, e `move_comments` tem forma fixa: o carregador
    # normaliza os dois, então valor genérico não provaria nada.
    if field_name == "side_to_move":
        return "b" if value != "b" else "w"
    if field_name == "move_comments":
        return {"5": {"before": "antes do lance", "after": "depois do lance"}}
    # `include_lichess_link` nasce `None` e é tri-estado: sem este caso ele cairia
    # na regra do `confidence` abaixo e gravaria 0.5, que volta como `True` e faz
    # a rede acusar uma perda que não houve. O valor que interessa provar é o
    # `False`, porque é o único que `bool(None)` imitaria.
    if field_name == "include_lichess_link":
        return False
    if isinstance(value, bool):
        return not value
    if isinstance(value, tuple):
        return tuple(float(item) + 1.5 for item in value)
    if isinstance(value, dict):
        return {"sha256": "outro", "size": 99}
    if isinstance(value, int):
        return int(value) + 7
    if isinstance(value, float):
        return float(value) + 1.25
    if isinstance(value, str):
        return f"{value}-alterado" if value else "preenchido"
    if value is None:
        # `confidence` nasce `None`; o que importa é que um número sobreviva.
        return 0.5
    if isinstance(value, list):
        return value
    pytest.fail(
        f"o gerador não sabe criar valor para `{field_name}` ({type(value).__name__}); "
        "acrescente o caso em `_nudged` e confira que o campo sobrevive ao round-trip"
    )


def _fill_every_field(instance):
    """Cópia do objeto com **todos** os campos diferentes do default."""
    changes = {
        field.name: _nudged(getattr(instance, field.name), field.name)
        for field in dataclasses.fields(instance)
    }
    return dataclasses.replace(instance, **changes)


def _assert_survives(original, reloaded, label: str) -> None:
    lost = []
    for field in dataclasses.fields(original):
        before = getattr(original, field.name)
        after = getattr(reloaded, field.name)
        if isinstance(before, tuple) or isinstance(after, tuple):
            before, after = tuple(before), tuple(after)
        if before != after:
            lost.append(f"{field.name}: gravou {before!r}, voltou {after!r}")
    assert not lost, f"{label} perdeu {len(lost)} campo(s) no round-trip:\n  " + "\n  ".join(lost)


BASE_OPERATION = OverlayOperation(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0), fen="8/8/8/8/8/8/8/8")
BASE_ERASE = EraseOperation(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0))
BASE_STUDY = StudyPosition(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0), fen="8/8/8/8/8/8/8/8")


def test_every_field_of_a_substitution_survives_the_roundtrip(tmp_path) -> None:
    filled = _fill_every_field(BASE_OPERATION)
    path = tmp_path / "state.json"
    save_project_state(
        str(path),
        ProjectState(source_pdf="l.pdf", source_pdf_fingerprint={}, operations=[filled]),
    )

    _assert_survives(filled, load_project_state(str(path)).operations[0], "OverlayOperation")


def test_every_field_of_a_candidate_survives_the_roundtrip(tmp_path) -> None:
    """Candidato usa o mesmo carregador, mas por outra lista: vale conferir os dois."""
    filled = _fill_every_field(BASE_OPERATION)
    path = tmp_path / "state.json"
    save_project_state(
        str(path),
        ProjectState(
            source_pdf="l.pdf", source_pdf_fingerprint={}, operations=[], candidates=[filled]
        ),
    )

    _assert_survives(filled, load_project_state(str(path)).candidates[0], "candidato")


def test_every_field_of_an_erasure_survives_the_roundtrip(tmp_path) -> None:
    filled = _fill_every_field(BASE_ERASE)
    path = tmp_path / "state.json"
    save_project_state(
        str(path),
        ProjectState(
            source_pdf="l.pdf",
            source_pdf_fingerprint={},
            operations=[],
            erase_operations=[filled],
        ),
    )

    _assert_survives(filled, load_project_state(str(path)).erase_operations[0], "EraseOperation")


def test_every_field_of_a_study_position_survives_the_roundtrip(tmp_path) -> None:
    filled = _fill_every_field(BASE_STUDY)
    path = tmp_path / "state.json"
    save_project_state(
        str(path),
        ProjectState(
            source_pdf="l.pdf",
            source_pdf_fingerprint={},
            operations=[],
            study_positions=[filled],
        ),
    )

    _assert_survives(filled, load_project_state(str(path)).study_positions[0], "StudyPosition")


def test_every_scalar_of_the_project_survives_the_roundtrip(tmp_path) -> None:
    """Inclui `current_page`, `ocr_full_next_page` e os dois ajustes do livro."""
    base = ProjectState(source_pdf="l.pdf", source_pdf_fingerprint={}, operations=[])
    scalars = {
        field.name: _nudged(getattr(base, field.name), field.name)
        for field in dataclasses.fields(base)
        if field.name not in {"operations", "erase_operations", "study_positions", "candidates"}
    }
    # A versão do schema é reescrita pelo carregador de propósito (ver
    # `load_project_state`): gravar o número antigo faria a próxima abertura migrar
    # de novo. Então ela não entra na conferência.
    scalars.pop("schema_version", None)
    filled = dataclasses.replace(base, **scalars)

    path = tmp_path / "state.json"
    save_project_state(str(path), filled)
    reloaded = load_project_state(str(path))

    lost = [
        f"{name}: gravou {value!r}, voltou {getattr(reloaded, name)!r}"
        for name, value in scalars.items()
        if getattr(reloaded, name) != value
    ]
    assert not lost, "o projeto perdeu escalares:\n  " + "\n  ".join(lost)
