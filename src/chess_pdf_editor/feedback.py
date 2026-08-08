"""Exportação das correções do usuário para o dataset de treino (§6.5).

Toda vez que alguém conserta uma casa que o reconhecimento errou, produz o dado mais
caro que existe neste domínio: um diagrama real, do estilo de impressão de um livro
real, com a posição correta ao lado. Até aqui esse dado morria no
`project_state.json` — servia para exportar o PDF daquele livro e mais nada.

Aqui ele sai no formato que o **ChessVisionOFF_Puro** (o projeto que treinou o
classificador embutido, ver `local_ocr/_vendor/__init__.py`) consome direto:

    <destino>/samples/board_<carimbo>.png     tabuleiro recortado, 800×800
    <destino>/labels.csv                      uma linha por tabuleiro

Retreinar continua sendo trabalho de lá — é lá que estão os splits, as métricas e o
histórico de experimentos. O que este módulo faz é fechar o circuito: o editor deixa
de ser só consumidor do modelo e passa a alimentá-lo.

**O recorte sai do PDF, não da tela.** Renderizar a região na resolução do dataset
(e não reaproveitar o preview em zoom 2,0) evita treinar o modelo em imagem já
degradada por uma ampliação.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

from PIL import Image

from .logging_config import get_logger
from .types import OverlayOperation

logger = get_logger("feedback")

#: Cabeçalho do `labels.csv` do projeto de origem. A ordem importa: lá o arquivo é
#: lido com `csv.DictReader`, mas é editado à mão com frequência.
LABELS_COLUMNS = (
    "filename",
    "fen",
    "side_to_move",
    "source_pdf",
    "source_page",
    "source_diagram",
    "detection_source",
    "created_at",
    "corrected_by",
)

SAMPLES_DIRNAME = "samples"
LABELS_FILENAME = "labels.csv"

#: Lado do recorte gravado, igual ao `BOARD_SIZE` do dataset de origem.
SAMPLE_SIZE = 800

#: DPI do recorte antes do redimensionamento. 300 dá ~660 px num diagrama de 160 pt,
#: perto o bastante de 800 para o resize não inventar detalhe que não existe.
CROP_DPI = 300


@dataclass(frozen=True)
class ExportedSample:
    filename: str
    fen: str
    page_num: int
    source: str


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _safe_stem(text: str) -> str:
    """Nome de arquivo previsível a partir do nome do livro."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return cleaned[:48] or "livro"


def _crop_board_png(pdf_service, operation: OverlayOperation) -> Optional[bytes]:
    """PNG quadrado do diagrama, na resolução do dataset."""
    zoom = CROP_DPI / 72.0
    try:
        region_png = pdf_service.render_region(operation.page_num, zoom, operation.rect_pdf)
    except Exception:
        logger.warning(
            "Falha ao recortar o diagrama da página %d para o dataset",
            operation.page_num + 1,
            exc_info=True,
        )
        return None

    image = Image.open(io.BytesIO(region_png)).convert("RGB")
    if image.width < 64 or image.height < 64:
        return None
    resized = image.resize((SAMPLE_SIZE, SAMPLE_SIZE), Image.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _append_labels(labels_path: Path, rows: Sequence[dict[str, object]]) -> None:
    """Acrescenta ao `labels.csv`, criando o cabeçalho só na primeira vez.

    Acrescentar em vez de reescrever é deliberado: o arquivo do projeto de origem tem
    milhares de linhas rotuladas à mão, e uma exportação daqui não pode substituí-lo.
    """
    is_new = not labels_path.exists() or labels_path.stat().st_size == 0
    with open(labels_path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(LABELS_COLUMNS))
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_training_samples(
    destination: str,
    pdf_service,
    operations: Iterable[OverlayOperation],
    source_pdf: Optional[str] = None,
    corrected_by: str = "chess-pdf-editor",
) -> list[ExportedSample]:
    """Grava um recorte por substituição e a linha correspondente no `labels.csv`.

    Devolve o que foi efetivamente exportado — uma substituição cuja região não
    renderiza (página fora do intervalo, retângulo vazio) é pulada e registrada no log,
    não derruba a exportação inteira.
    """
    root = Path(destination)
    samples_dir = root / SAMPLES_DIRNAME
    samples_dir.mkdir(parents=True, exist_ok=True)

    book = _safe_stem(Path(source_pdf).stem) if source_pdf else "livro"
    exported: list[ExportedSample] = []
    rows: list[dict[str, object]] = []
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for index, operation in enumerate(operations, start=1):
        png = _crop_board_png(pdf_service, operation)
        if png is None:
            continue
        filename = f"board_{book}_{_timestamp()}_{index:03d}.png"
        (samples_dir / filename).write_bytes(png)
        rows.append(
            {
                "filename": filename,
                "fen": operation.fen,
                "side_to_move": getattr(operation, "side_to_move", "w"),
                "source_pdf": Path(source_pdf).name if source_pdf else "",
                "source_page": operation.page_num + 1,
                "source_diagram": index,
                "detection_source": getattr(operation, "source", ""),
                "created_at": created_at,
                "corrected_by": corrected_by,
            }
        )
        exported.append(
            ExportedSample(
                filename=filename,
                fen=operation.fen,
                page_num=operation.page_num,
                source=str(getattr(operation, "source", "")),
            )
        )

    if rows:
        _append_labels(root / LABELS_FILENAME, rows)
    logger.info("Exportadas %d amostra(s) de treino para %s", len(exported), root)
    return exported
