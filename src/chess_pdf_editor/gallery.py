"""Galeria de diagramas do livro (§22.5).

### O problema

Depois do Sprint 7, reconhecer um livro de 898 páginas leva ~8 minutos e produz
centenas de substituições. Conferir o resultado exigia abrir página por página no
visor — e a maior parte das páginas de um livro não tem diagrama nenhum.

A galeria mostra **só os diagramas**, lado a lado, antes e depois. Revisar um livro
inteiro vira rolar uma grade.

### O contrato de thread, de novo

Renderizar 300 pares de miniaturas na thread da UI congelaria a janela por
dezenas de segundos — exatamente o que o Sprint 5 tirou do OCR em lote. Vale aqui
a mesma regra:

> O worker abre o **seu próprio** `fitz.Document` a partir do caminho do arquivo.
> Nenhum `fitz.Page`, `fitz.Document` ou `PdfService` cruza a fronteira de thread.

O que atravessa por sinal são `bytes` de PNG e inteiros.

### Por que ordenar por página antes de renderizar

`PdfService` guarda **um** documento de prévia, com cache por assinatura: mesma
página e mesmo conjunto de alterações reaproveita. Processar os diagramas na ordem
das páginas transforma isso num acerto por diagrama depois do primeiro de cada
página; processar fora de ordem reconstruiria o documento a cada item.

### O "depois" mostra a página inteira aplicada

Uma miniatura de "depois" que mostrasse só a própria substituição mentiria quando
a página tem duas: o PDF exportado terá as duas, e é isso que a conferência
precisa ver. Por isso o render usa todas as operações da página — o mesmo que a
prévia ao vivo faz (§21.3).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .logging_config import get_logger
from .pdf_service import PdfService
from .types import EraseOperation, OverlayOperation, Rect

logger = get_logger("gallery")

KIND_OPERATION = "operation"
KIND_CANDIDATE = "candidate"

#: Lado de cada miniatura, em pixels.
THUMB_SIZE = 150

#: Zoom do recorte. 2.0 dá ~320 px num diagrama de 160 pt — folga suficiente para
#: reduzir para `THUMB_SIZE` sem serrilhado.
THUMB_ZOOM = 2.0

#: Margem em volta do diagrama, para padding, borda e link Lichess aparecerem.
THUMB_MARGIN_RATIO = 0.10


@dataclass(frozen=True)
class GalleryItem:
    """Uma entrada da galeria, antes de ter miniatura."""

    kind: str
    index: int
    page_num: int
    rect_pdf: Rect
    fen: str
    confidence: Optional[float] = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.kind, self.index)


def _expanded_rect(rect_pdf: Rect, ratio: float = THUMB_MARGIN_RATIO) -> Rect:
    x0, y0, x1, y1 = rect_pdf
    margin = max(6.0, max(x1 - x0, y1 - y0) * ratio)
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def build_items(
    operations: Sequence[OverlayOperation],
    candidates: Sequence[OverlayOperation] = (),
) -> list[GalleryItem]:
    """Entradas da galeria, na ordem das páginas."""
    items = [
        GalleryItem(
            kind=KIND_OPERATION,
            index=index,
            page_num=op.page_num,
            rect_pdf=tuple(op.rect_pdf),
            fen=op.fen,
            confidence=getattr(op, "confidence", None),
        )
        for index, op in enumerate(operations)
    ]
    items += [
        GalleryItem(
            kind=KIND_CANDIDATE,
            index=index,
            page_num=op.page_num,
            rect_pdf=tuple(op.rect_pdf),
            fen=op.fen,
            confidence=getattr(op, "confidence", None),
        )
        for index, op in enumerate(candidates)
    ]
    # Ordem de página, e dentro dela de cima para baixo: é a ordem de leitura do
    # livro, e é o que faz o cache de prévia acertar (ver o cabeçalho).
    items.sort(key=lambda item: (item.page_num, item.rect_pdf[1], item.rect_pdf[0]))
    return items


class GalleryWorker(QtCore.QThread):
    """Renderiza os pares antes/depois fora da thread da UI."""

    #: item pronto: chave (tipo, índice), PNG do antes, PNG do depois
    thumbnail_ready = QtCore.Signal(object, bytes, bytes)
    #: quantos prontos, total
    progress = QtCore.Signal(int, int)
    #: item que falhou, mensagem
    item_failed = QtCore.Signal(object, str)
    completed = QtCore.Signal(bool)

    def __init__(
        self,
        pdf_path: str,
        items: Sequence[GalleryItem],
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
        before_operations: Optional[Sequence[OverlayOperation]] = None,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        """`before_operations` troca o que é o lado "antes".

        Sem ele o "antes" é a página crua do PDF, que é a pergunta da galeria
        ("o que este livro tinha aqui?"). Com ele o "antes" é a página já
        substituída com *aquele* conjunto de operações, o que permite comparar
        duas versões do resultado em vez de original contra resultado — é o que o
        estilo em lote precisa (§36).
        """
        super().__init__(parent)
        self._pdf_path = str(pdf_path)
        self._items = list(items)
        # Cópias defensivas: o usuário continua editando enquanto a galeria carrega.
        self._operations = [replace(op) for op in operations]
        self._candidates = [replace(op) for op in candidates]
        self._erase_operations = [replace(op) for op in erase_operations]
        self._before_operations = (
            None if before_operations is None else [replace(op) for op in before_operations]
        )
        self._whiteout = bool(whiteout)
        self._include_lichess_link = bool(include_lichess_link)
        self._erase_coordinates = bool(erase_coordinates)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:  # pragma: no cover - exercitado via teste de integracao
        service: Optional[PdfService] = None
        canceled = False
        try:
            service = PdfService(self._pdf_path)
            by_page: dict[int, list[OverlayOperation]] = defaultdict(list)
            for op in self._operations:
                by_page[op.page_num].append(op)
            before_by_page: Optional[dict[int, list[OverlayOperation]]] = None
            if self._before_operations is not None:
                before_by_page = defaultdict(list)
                for op in self._before_operations:
                    before_by_page[op.page_num].append(op)
            erases_by_page: dict[int, list[EraseOperation]] = defaultdict(list)
            for erase in self._erase_operations:
                erases_by_page[erase.page_num].append(erase)

            total = len(self._items)
            for done, item in enumerate(self._items, start=1):
                if self._cancel_requested:
                    canceled = True
                    break
                try:
                    before, after = self._render_pair(
                        service, item, by_page, erases_by_page, before_by_page
                    )
                except Exception as exc:
                    logger.warning(
                        "Falha ao renderizar miniatura da página %d", item.page_num + 1, exc_info=True
                    )
                    self.item_failed.emit(item.key, str(exc))
                else:
                    self.thumbnail_ready.emit(item.key, before, after)
                self.progress.emit(done, total)
        except Exception as exc:
            logger.exception("Galeria abortada")
            self.item_failed.emit(None, str(exc))
        finally:
            if service is not None:
                service.close()
            self.completed.emit(canceled or self._cancel_requested)

    def _render_pair(
        self,
        service: PdfService,
        item: GalleryItem,
        by_page: dict[int, list[OverlayOperation]],
        erases_by_page: dict[int, list[EraseOperation]],
        before_by_page: Optional[dict[int, list[OverlayOperation]]] = None,
    ) -> tuple[bytes, bytes]:
        rect = _expanded_rect(item.rect_pdf)
        if before_by_page is None:
            before = service.render_region(item.page_num, THUMB_ZOOM, rect)
        else:
            before = service.render_region_with_operations(
                item.page_num,
                THUMB_ZOOM,
                rect,
                list(before_by_page.get(item.page_num, ())),
                erase_operations=erases_by_page.get(item.page_num, []),
                whiteout=self._whiteout,
                include_lichess_link=self._include_lichess_link,
                erase_coordinates=self._erase_coordinates,
            )

        page_ops = list(by_page.get(item.page_num, ()))
        if item.kind == KIND_CANDIDATE and 0 <= item.index < len(self._candidates):
            # O candidato ainda não está em `operations`: para a miniatura de
            # "depois" mostrar como ficaria, ele entra junto das já aplicadas.
            page_ops.append(self._candidates[item.index])

        after = service.render_region_with_operations(
            item.page_num,
            THUMB_ZOOM,
            rect,
            page_ops,
            erase_operations=erases_by_page.get(item.page_num, []),
            whiteout=self._whiteout,
            include_lichess_link=self._include_lichess_link,
            erase_coordinates=self._erase_coordinates,
        )
        return before, after


def compose_pair(before_png: bytes, after_png: bytes, size: int = THUMB_SIZE) -> QtGui.QPixmap:
    """Junta antes e depois num pixmap só, com um traço separando.

    Um ícone por diagrama (em vez de dois widgets por célula) é o que deixa a
    grade ser um `QListWidget` comum, com rolagem e seleção de graça.
    """
    canvas = QtGui.QPixmap(size * 2 + 9, size)
    canvas.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(canvas)
    try:
        for offset, data in ((0, before_png), (size + 9, after_png)):
            pixmap = QtGui.QPixmap()
            if not data or not pixmap.loadFromData(data, "PNG"):
                continue
            scaled = pixmap.scaled(
                size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            painter.drawPixmap(
                offset + (size - scaled.width()) // 2,
                (size - scaled.height()) // 2,
                scaled,
            )
        painter.setPen(QtGui.QPen(QtGui.QColor(140, 140, 140, 160), 1))
        painter.drawLine(size + 4, 6, size + 4, size - 6)
    finally:
        painter.end()
    return canvas


class GalleryDialog(QtWidgets.QDialog):
    """Grade com todos os diagramas do livro, antes e depois."""

    #: (tipo, índice) do diagrama escolhido
    entry_activated = QtCore.Signal(str, int)

    def __init__(
        self,
        pdf_path: str,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation] = (),
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Galeria de diagramas")
        self.resize(1000, 720)

        self._items = build_items(operations, candidates)
        self._rows: dict[tuple[str, int], int] = {}
        self._worker: Optional[GalleryWorker] = None

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setViewMode(QtWidgets.QListView.IconMode)
        self.list_widget.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_widget.setMovement(QtWidgets.QListView.Static)
        self.list_widget.setIconSize(QtCore.QSize(THUMB_SIZE * 2 + 9, THUMB_SIZE))
        self.list_widget.setGridSize(QtCore.QSize(THUMB_SIZE * 2 + 28, THUMB_SIZE + 52))
        self.list_widget.setWordWrap(True)
        self.list_widget.setSpacing(4)
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.itemClicked.connect(self._on_item_activated)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, max(1, len(self._items)))
        self.progress_bar.setVisible(bool(self._items))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(buttons)

        self._populate_placeholders()
        if self._items:
            self._start_worker(
                pdf_path,
                operations,
                candidates,
                erase_operations,
                whiteout,
                include_lichess_link,
                erase_coordinates,
            )

    # -- montagem ------------------------------------------------------

    def _populate_placeholders(self) -> None:
        """Cria as células antes das miniaturas existirem.

        A grade aparece cheia na hora e as imagens vão chegando. O contrário —
        esperar tudo para mostrar algo — deixaria a janela vazia por segundos num
        livro grande.
        """
        if not self._items:
            self.status_label.setText(
                "Nenhum diagrama para mostrar. Adicione substituições ou reconheça o PDF."
            )
            return
        for row, item in enumerate(self._items):
            widget_item = QtWidgets.QListWidgetItem(self._caption(item))
            widget_item.setData(QtCore.Qt.UserRole, item.key)
            widget_item.setTextAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
            self.list_widget.addItem(widget_item)
            self._rows[item.key] = row
        self.status_label.setText(f"{len(self._items)} diagrama(s). Clique para ir até um deles.")

    @staticmethod
    def _caption(item: GalleryItem) -> str:
        confidence = "" if item.confidence is None else f" · conf {float(item.confidence):.2f}"
        marker = "" if item.kind == KIND_OPERATION else " · candidato"
        fen = item.fen[:22] + ("..." if len(item.fen) > 22 else "")
        return f"pág {item.page_num + 1}{marker}{confidence}\n{fen}"

    def _start_worker(
        self,
        pdf_path: str,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation],
        whiteout: bool,
        include_lichess_link: bool,
        erase_coordinates: bool,
    ) -> None:
        worker = GalleryWorker(
            pdf_path,
            self._items,
            operations,
            candidates,
            erase_operations=erase_operations,
            whiteout=whiteout,
            include_lichess_link=include_lichess_link,
            erase_coordinates=erase_coordinates,
            parent=self,
        )
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.progress.connect(self._on_progress)
        worker.item_failed.connect(self._on_item_failed)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        worker.start()

    # -- sinais do worker ----------------------------------------------

    def _on_thumbnail_ready(self, key: object, before_png: bytes, after_png: bytes) -> None:
        row = self._rows.get(tuple(key) if isinstance(key, (list, tuple)) else key)
        if row is None:
            return
        item = self.list_widget.item(row)
        if item is None:
            return
        item.setIcon(QtGui.QIcon(compose_pair(before_png, after_png)))

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)

    def _on_item_failed(self, key: object, message: str) -> None:
        del key
        logger.warning("Miniatura da galeria falhou: %s", message)

    def _on_completed(self, canceled: bool) -> None:
        self.progress_bar.setVisible(False)
        if not canceled:
            self.status_label.setText(
                f"{len(self._items)} diagrama(s). Clique para ir até um deles."
            )
        self._worker = None

    # -- interação -----------------------------------------------------

    def _on_item_activated(self, item: QtWidgets.QListWidgetItem) -> None:
        key = item.data(QtCore.Qt.UserRole)
        if not key:
            return
        kind, index = key
        self.entry_activated.emit(str(kind), int(index))

    def stop_worker(self) -> None:
        """Para o render e espera a thread sair.

        Sem isto, fechar a galeria no meio de um livro grande deixaria uma
        `QThread` viva mexendo num diálogo já destruído — a mesma lição do
        `closeEvent` do Sprint 5.1.
        """
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        if not worker.wait(5000):  # pragma: no cover - só num render patológico
            logger.warning("Worker da galeria não terminou em 5s; encerrando à força")
            worker.terminate()
            worker.wait(1000)
        self._worker = None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.stop_worker()
        super().closeEvent(event)

    def reject(self) -> None:
        self.stop_worker()
        super().reject()
