"""Exportação das correções para o dataset de treino (§6.5)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf

fitz = pytest.importorskip("fitz")

from chess_pdf_editor.feedback import (  # noqa: E402
    LABELS_COLUMNS,
    LABELS_FILENAME,
    SAMPLE_SIZE,
    SAMPLES_DIRNAME,
    export_training_samples,
)
from chess_pdf_editor.pdf_service import PdfService  # noqa: E402
from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


@pytest.fixture
def service(tmp_path: Path):
    pdf = make_pdf(tmp_path / "livro.pdf", pages=2)
    service = PdfService(str(pdf))
    yield service
    service.close()


def _operation(page=0, fen=FEN, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=DIAGRAM_RECT, fen=fen, **kwargs)


def _read_labels(root: Path) -> list[dict[str, str]]:
    with open(root / LABELS_FILENAME, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_a_sample_and_a_label_are_written(service, tmp_path: Path) -> None:
    out = tmp_path / "dataset"
    exported = export_training_samples(
        str(out), service, [_operation(source="ocr-page")], source_pdf=service.pdf_path
    )

    assert len(exported) == 1
    (row,) = _read_labels(out)
    assert row["fen"] == FEN
    assert row["source_page"] == "1", "a página sai 1-based, como no dataset de origem"
    assert row["detection_source"] == "ocr-page"
    assert row["source_pdf"] == "livro.pdf"
    assert (out / SAMPLES_DIRNAME / row["filename"]).is_file()


def test_the_crop_has_the_dataset_size(service, tmp_path: Path) -> None:
    """O treino espera 800×800; um tamanho diferente entraria torto no dataset."""
    from PIL import Image

    out = tmp_path / "dataset"
    (sample,) = export_training_samples(str(out), service, [_operation()])
    with Image.open(out / SAMPLES_DIRNAME / sample.filename) as image:
        assert image.size == (SAMPLE_SIZE, SAMPLE_SIZE)


def test_the_header_matches_the_upstream_dataset(service, tmp_path: Path) -> None:
    out = tmp_path / "dataset"
    export_training_samples(str(out), service, [_operation()])
    with open(out / LABELS_FILENAME, encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert tuple(header) == LABELS_COLUMNS


def test_a_second_export_appends_instead_of_replacing(service, tmp_path: Path) -> None:
    """O labels.csv de destino tem milhares de linhas rotuladas à mão."""
    out = tmp_path / "dataset"
    export_training_samples(str(out), service, [_operation()])
    export_training_samples(str(out), service, [_operation(page=1)])

    rows = _read_labels(out)
    assert len(rows) == 2
    assert [row["source_page"] for row in rows] == ["1", "2"]


def test_filenames_do_not_collide(service, tmp_path: Path) -> None:
    out = tmp_path / "dataset"
    exported = export_training_samples(
        str(out), service, [_operation(), _operation(page=1), _operation()]
    )
    assert len({sample.filename for sample in exported}) == 3


def test_an_operation_outside_the_pdf_is_skipped_not_fatal(service, tmp_path: Path) -> None:
    """Um projeto reaberto contra um PDF menor não pode derrubar a exportação."""
    out = tmp_path / "dataset"
    exported = export_training_samples(
        str(out), service, [_operation(), _operation(page=99)]
    )

    assert len(exported) == 1
    assert len(_read_labels(out)) == 1


def test_nothing_to_export_writes_no_labels_file(service, tmp_path: Path) -> None:
    out = tmp_path / "dataset"
    assert export_training_samples(str(out), service, []) == []
    assert not (out / LABELS_FILENAME).exists()


def test_a_book_name_with_odd_characters_still_produces_a_valid_filename(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "Livro: Finais / Vol 2.pdf".replace("/", "-").replace(":", ""), pages=1)
    service = PdfService(str(pdf))
    try:
        out = tmp_path / "dataset"
        (sample,) = export_training_samples(
            str(out), service, [_operation()], source_pdf="C:/livros/Aagaard: Vol #2.pdf"
        )
    finally:
        service.close()

    assert (out / SAMPLES_DIRNAME / sample.filename).is_file()
    assert not set(sample.filename) & set(':/\\*?"<>|')
