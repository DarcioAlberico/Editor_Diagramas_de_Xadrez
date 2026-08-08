"""Migração do project_state.json entre versões de schema (Sprint 8.2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chess_pdf_editor.migrations import (
    CURRENT_SCHEMA_VERSION,
    OLDEST_DOCUMENTED_SCHEMA,
    ProjectSchemaError,
    migrate_payload,
)
from chess_pdf_editor.project_state import (
    ProjectState,
    load_project_state,
    load_project_state_with_report,
    save_project_state,
)
from chess_pdf_editor.types import OverlayOperation


def _payload(version: int, **extra) -> dict:
    payload = {
        "source_pdf": "livro.pdf",
        "source_pdf_fingerprint": {},
        "operations": [{"page_num": 0, "rect_pdf": [10, 20, 110, 120], "fen": "8/8/8/8/8/8/8/8"}],
        "current_page": 0,
        "schema_version": version,
        "app_version": "0.1.0",
    }
    payload.update(extra)
    return payload


def _write(tmp_path: Path, payload: dict, name: str = "projeto.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Para frente: o buraco que este sprint fecha
# ---------------------------------------------------------------------------


def test_a_project_from_a_newer_app_is_refused() -> None:
    """Carregar descartaria campos e o autosave gravaria a perda por cima."""
    with pytest.raises(ProjectSchemaError) as excinfo:
        migrate_payload(_payload(CURRENT_SCHEMA_VERSION + 1))

    message = str(excinfo.value)
    assert str(CURRENT_SCHEMA_VERSION + 1) in message
    assert "Atualize o aplicativo" in message


def test_the_refusal_happens_before_anything_is_read(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload(99))
    with pytest.raises(ProjectSchemaError):
        load_project_state(path)


def test_a_refused_project_is_left_untouched_on_disk(tmp_path: Path) -> None:
    """O arquivo bom não pode ser tocado por uma tentativa de abrir."""
    payload = _payload(99, candidates=[{"campo": "que este app nao conhece"}])
    path = _write(tmp_path, payload)
    before = Path(path).read_bytes()

    with pytest.raises(ProjectSchemaError):
        load_project_state(path)

    assert Path(path).read_bytes() == before


# ---------------------------------------------------------------------------
# Para trás
# ---------------------------------------------------------------------------


def test_schema_7_gains_an_empty_candidate_queue() -> None:
    """Antes do 8 não havia como enfileirar nada: ausente é vazio, não desconhecido."""
    migrated, report = migrate_payload(_payload(7))

    assert migrated["candidates"] == []
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert report.from_version == 7
    assert report.migrated
    assert "7→8" in report.describe()


def test_an_existing_candidate_queue_is_preserved() -> None:
    candidate = {"page_num": 1, "rect_pdf": [0, 0, 10, 10], "fen": "8/8/8/8/8/8/8/8"}
    migrated, _ = migrate_payload(_payload(7, candidates=[candidate]))
    assert migrated["candidates"] == [candidate]


def test_the_current_version_is_not_reported_as_migrated() -> None:
    _, report = migrate_payload(_payload(CURRENT_SCHEMA_VERSION))
    assert not report.migrated
    assert report.describe() == ""


@pytest.mark.parametrize("version", [1, 2, 6])
def test_pre_7_projects_load_in_compatibility_mode(version: int) -> None:
    """As fronteiras de 1..6 nunca foram registradas; o leitor tolerante cobre."""
    migrated, report = migrate_payload(_payload(version))

    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert any("compatibilidade" in step or "tolerância" in step for step in report.steps)
    assert report.from_version == version


def test_a_missing_version_is_treated_as_the_oldest_documented() -> None:
    payload = _payload(7)
    del payload["schema_version"]
    _, report = migrate_payload(payload)
    assert report.from_version == OLDEST_DOCUMENTED_SCHEMA


def test_a_garbage_version_does_not_crash() -> None:
    _, report = migrate_payload(_payload("sete"))  # type: ignore[arg-type]
    assert report.from_version == OLDEST_DOCUMENTED_SCHEMA


def test_a_non_object_payload_is_refused() -> None:
    with pytest.raises(ProjectSchemaError):
        migrate_payload([])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Ida e volta pelo disco
# ---------------------------------------------------------------------------


def test_loading_an_old_project_reports_the_migration(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload(7))
    state, report = load_project_state_with_report(path)

    assert state.schema_version == CURRENT_SCHEMA_VERSION
    assert state.candidates == []
    assert report.migrated


def test_saving_a_migrated_project_writes_the_current_schema(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload(7))
    state, _ = load_project_state_with_report(path)

    out = tmp_path / "salvo.json"
    save_project_state(str(out), state)
    reloaded = json.loads(out.read_text(encoding="utf-8"))

    assert reloaded["schema_version"] == CURRENT_SCHEMA_VERSION
    assert reloaded["candidates"] == []


def test_a_round_trip_through_the_current_schema_is_stable(tmp_path: Path) -> None:
    state = ProjectState(
        source_pdf="livro.pdf",
        source_pdf_fingerprint={},
        operations=[OverlayOperation(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0), fen="8/8/8/8/8/8/8/8")],
        candidates=[OverlayOperation(page_num=1, rect_pdf=(5.0, 6.0, 7.0, 8.0), fen="8/8/8/8/8/8/8/8")],
    )
    path = tmp_path / "atual.json"
    save_project_state(str(path), state)

    reloaded, report = load_project_state_with_report(str(path))

    assert not report.migrated
    assert len(reloaded.operations) == 1
    assert len(reloaded.candidates) == 1
