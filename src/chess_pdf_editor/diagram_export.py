"""Exportação dos diagramas isolados (§22.5 item 6).

O PDF de saída serve para ler o livro corrigido. Ele não serve para **reaproveitar**
um diagrama: para pôr a posição num slide, numa lista de exercícios ou num post, o
usuário precisava recortar a página na mão.

Aqui cada substituição vira um arquivo próprio, num dos três formatos:

| Formato | Caminho de render | Depende de |
|---|---|---|
| `png` | o mesmo do PDF exportado (Merida → CairoSVG → Pillow) | nada opcional (Pillow é base) |
| `pdf` | o mesmo do PDF exportado (Merida vetorial) | nada opcional |
| `svg` | `chess.svg`, desenho do `python-chess` | nada opcional |

O `svg` é o único cujo desenho **não** é o que vai para o PDF do livro, e é de
propósito — ver `renderer.render_board_svg`.

### O contrato de thread é trivial aqui

Ao contrário da exportação do PDF e da galeria, isto não abre documento nenhum: o
render sai da **FEN**, então nada de `fitz` existe para cruzar fronteira de thread. O
worker (`DiagramExportWorker`) só precisa da lista de operações, que ele copia.

### Cancelamento parcial, ao contrário do PDF

`apply_operations_to_pdf` cancela com **nenhum arquivo**, porque meio PDF no lugar de
um bom é pior que nada (§33). Aqui é o oposto: são N arquivos independentes, e os que
já foram gravados são úteis por si. Cancelar para de gravar novos e **mantém** os
prontos — o resultado é dito em voz alta, com quantos ficaram de fora.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .logging_config import get_logger
from .renderer import render_board_pdf, render_board_png, render_board_svg
from .types import OverlayOperation

logger = get_logger("diagram_export")

#: Formatos aceitos, na ordem em que aparecem na interface.
FORMATS = ("png", "svg", "pdf")

DEFAULT_FORMAT = "png"
DEFAULT_SIZE_PX = 512

#: Índice que acompanha os arquivos. Uma pasta com 300 PNGs sem isto obriga o
#: usuário a abrir um por um para achar a posição que quer.
INDEX_NAME = "indice.csv"
INDEX_COLUMNS = ("arquivo", "pagina", "fen", "lado_a_jogar", "numero_do_lance", "origem")


@dataclass
class DiagramExportResult:
    written: list[Path] = field(default_factory=list)
    #: (nome do arquivo, mensagem) do que falhou — falha de um não aborta os outros.
    failed: list[tuple[str, str]] = field(default_factory=list)
    canceled: bool = False
    index_path: Optional[Path] = None
    #: Quantas operações nem chegaram a ser tentadas, por cancelamento.
    skipped: int = 0

    @property
    def total_written(self) -> int:
        return len(self.written)


def normalize_format(fmt: Optional[str]) -> str:
    value = (fmt or "").strip().lower().lstrip(".")
    return value if value in FORMATS else DEFAULT_FORMAT


def diagram_filename(op: OverlayOperation, position_on_page: int, fmt: str) -> str:
    """Nome estável e ordenável: página com zeros à esquerda, ordem na página.

    Zeros à esquerda porque o usuário vai olhar a pasta ordenada por nome, e
    `pag10` antes de `pag2` é exatamente o que não se quer.
    """
    return f"diagrama-pag{int(op.page_num) + 1:04d}-{int(position_on_page):02d}.{normalize_format(fmt)}"


def _render(piece_placement: str, fmt: str, size_px: int) -> Optional[bytes]:
    if fmt == "svg":
        return render_board_svg(piece_placement, size_px=size_px).encode("utf-8")
    if fmt == "pdf":
        return render_board_pdf(piece_placement, size_px=size_px)
    return render_board_png(piece_placement, size_px=size_px)


def _ordered(operations: Sequence[OverlayOperation]) -> list[tuple[int, OverlayOperation]]:
    """Ordem de leitura do livro, com a posição de cada uma na sua página.

    A mesma ordem da galeria (§31): página, depois de cima para baixo. É o que faz o
    número no nome do arquivo corresponder ao que o usuário vê na tela.
    """
    ordered = sorted(
        operations,
        key=lambda op: (int(op.page_num), float(op.rect_pdf[1]), float(op.rect_pdf[0])),
    )
    numbered: list[tuple[int, OverlayOperation]] = []
    seen_per_page: dict[int, int] = {}
    for op in ordered:
        page = int(op.page_num)
        seen_per_page[page] = seen_per_page.get(page, 0) + 1
        numbered.append((seen_per_page[page], op))
    return numbered


def export_diagrams(
    operations: Sequence[OverlayOperation],
    out_dir: str | Path,
    fmt: str = DEFAULT_FORMAT,
    size_px: int = DEFAULT_SIZE_PX,
    write_index: bool = True,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> DiagramExportResult:
    """Grava um arquivo por substituição em `out_dir`.

    Falha de um diagrama não aborta os outros: ela entra em `failed` e a exportação
    segue. Um livro inteiro perdido por causa de uma FEN estragada seria pior que uma
    pasta com 299 arquivos e um aviso.
    """
    fmt = normalize_format(fmt)
    size = max(64, int(size_px))
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    numbered = _ordered(operations)
    total = len(numbered)
    result = DiagramExportResult()
    rows: list[dict[str, object]] = []

    for done, (position, op) in enumerate(numbered, start=1):
        if should_cancel is not None and should_cancel():
            # Os arquivos já gravados ficam: são úteis por si.
            result.canceled = True
            result.skipped = total - (done - 1)
            break

        name = diagram_filename(op, position, fmt)
        target = directory / name
        try:
            data = _render(str(op.fen), fmt, size)
            if not data:
                raise RuntimeError(f"o renderizador não produziu {fmt.upper()}")
            target.write_bytes(data)
        except Exception as exc:
            logger.warning("Falha ao exportar %s", name, exc_info=True)
            result.failed.append((name, str(exc)))
        else:
            result.written.append(target)
            rows.append(
                {
                    "arquivo": name,
                    "pagina": int(op.page_num) + 1,
                    "fen": str(op.fen),
                    "lado_a_jogar": str(getattr(op, "side_to_move", "w")),
                    "numero_do_lance": int(getattr(op, "fullmove_number", 1) or 1),
                    "origem": str(getattr(op, "source", "")),
                }
            )
        if on_progress is not None:
            on_progress(done, total)

    if write_index and rows:
        result.index_path = _write_index(directory / INDEX_NAME, rows)

    logger.info(
        "Diagramas exportados: %d de %d em %s (formato %s, %d falha(s), cancelado=%s)",
        result.total_written,
        total,
        directory,
        fmt,
        len(result.failed),
        result.canceled,
    )
    return result


def _write_index(path: Path, rows: Sequence[dict[str, object]]) -> Path:
    # `utf-8-sig` e o delimitador padrão são os mesmos do relatório (§26.4): sem o
    # BOM o Excel em português abre "substituição" como "substituiÃ§Ã£o", e ter dois
    # CSVs do mesmo app com separadores diferentes seria pegadinha.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(INDEX_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
