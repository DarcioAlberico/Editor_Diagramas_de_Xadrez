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
