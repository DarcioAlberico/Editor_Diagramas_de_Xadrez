"""Estilo em lote com prévia (§22.5 item 2).

### O que faltava, exatamente

`Aplicar em todas as substituições` está ligado por padrão, e a cada passo de um
spinbox de padding ou borda ele reescreve o estilo de **todas** as substituições
do livro. O usuário vê o efeito na página que está aberta; nas outras 299, não vê
nada. Era isso que a §22.5 chamava de "às cegas".

A galeria do Sprint 9.3 resolveu metade do problema sem que ninguém percebesse:
como o estilo é aplicado na hora, o `Ctrl+G` já mostra o resultado do estilo atual
em todo o livro. O que continuava faltando é o outro lado — **experimentar** um
estilo, ver o efeito no livro inteiro e só então aceitar ou desistir.

É isso que este módulo é: uma proposta de estilo que não toca em nada até ser
aceita, e uma grade que mostra `estilo atual` contra `estilo proposto`.

### Por que uma amostra e não o livro todo

Um livro real tem centenas de diagramas, e a grade re-renderiza a cada ajuste dos
spinboxes. Renderizar 312 pares por ajuste seria inútil: ninguém compara 312
miniaturas para decidir um padding. A grade mostra uma **amostra espalhada pelo
livro** — e diz na cara quantos de quantos, porque um recorte silencioso se lê
como "conferi tudo".

Espalhada, e não os N primeiros: os primeiros diagramas de um livro costumam ser
todos do mesmo capítulo, com o mesmo enquadramento. A variedade que interessa está
distribuída.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .gallery import GalleryItem, GalleryWorker, build_items, compose_pair
from .logging_config import get_logger
from .types import EraseOperation, OverlayOperation

logger = get_logger("style_batch")

#: Quantos diagramas a grade mostra. 24 enche a janela sem virar espera.
DEFAULT_SAMPLE = 24

#: Maior que as 150 da galeria, e de propósito. Lá a pergunta é "que diagrama é
#: este?"; aqui é "esta borda encostou no texto?", que precisa de mais pixel. O
#: recorte vem com `THUMB_ZOOM` = 2.0, ou seja ~320 px num diagrama de 160 pt —
#: 190 continua sendo redução, sem serrilhado.
STYLE_THUMB_SIZE = 190


@dataclass(frozen=True)
class StyleProposal:
    """Padding por lado e borda, sem dono ainda."""

    padding_left_pt: float = 0.5
    padding_top_pt: float = 0.5
    padding_right_pt: float = 0.5
    padding_bottom_pt: float = 0.5
    border_width_pt: float = 0.0

    @classmethod
    def from_operation(cls, op: OverlayOperation) -> "StyleProposal":
        return cls(
            padding_left_pt=float(op.whiteout_padding_left_pt),
            padding_top_pt=float(op.whiteout_padding_top_pt),
            padding_right_pt=float(op.whiteout_padding_right_pt),
            padding_bottom_pt=float(op.whiteout_padding_bottom_pt),
            border_width_pt=float(op.border_width_pt),
        )

    @property
    def padding_mean_pt(self) -> float:
        """O campo legado `whiteout_padding_pt`, que é a média dos quatro lados."""
        return (
            self.padding_left_pt
            + self.padding_top_pt
            + self.padding_right_pt
            + self.padding_bottom_pt
        ) / 4.0

    def apply_in_place(self, op: OverlayOperation) -> None:
        """Muta a operação. É o caminho do commit: outros painéis guardam a
        mesma referência, então trocar o objeto os deixaria com a versão velha."""
        op.whiteout_padding_left_pt = self.padding_left_pt
        op.whiteout_padding_top_pt = self.padding_top_pt
        op.whiteout_padding_right_pt = self.padding_right_pt
        op.whiteout_padding_bottom_pt = self.padding_bottom_pt
        op.whiteout_padding_pt = self.padding_mean_pt
        op.border_width_pt = self.border_width_pt

    def applied_to(self, op: OverlayOperation) -> OverlayOperation:
        """Cópia com este estilo. É o caminho da prévia: nada é mutado."""
        clone = replace(op)
        self.apply_in_place(clone)
        return clone

    def matches(self, op: OverlayOperation) -> bool:
        return self == StyleProposal.from_operation(op)


def restyle(
    operations: Sequence[OverlayOperation], proposal: StyleProposal
) -> list[OverlayOperation]:
    """Cópias das operações com o estilo proposto."""
    return [proposal.applied_to(op) for op in operations]


def count_affected(operations: Sequence[OverlayOperation], proposal: StyleProposal) -> int:
    """Quantas mudariam de fato. Aplicar o estilo que já está lá não é mudança."""
    return sum(0 if proposal.matches(op) else 1 for op in operations)


def sample_items(items: Sequence[GalleryItem], limit: int = DEFAULT_SAMPLE) -> list[GalleryItem]:
    """Até `limit` entradas, espalhadas pela lista (inclui a primeira e a última)."""
    total = len(items)
    if limit <= 0 or total == 0:
        return []
    if total <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    picked: list[GalleryItem] = []
    seen: set[int] = set()
    for step in range(limit):
        index = round(step * (total - 1) / (limit - 1))
        if index not in seen:
            seen.add(index)
            picked.append(items[index])
    return picked


class StyleBatchDialog(QtWidgets.QDialog):
    """Grade `estilo atual` x `estilo proposto`, com o botão de aplicar no fim.

    O contrato de thread é o do Sprint 5.1, herdado da galeria: o render vive num
    `GalleryWorker`, que abre o seu próprio documento a partir do caminho do
    arquivo. Nada de `fitz` cruza a fronteira.
    """

    #: Espera entre o último passo do spinbox e o re-render. Cada passo emite um
    #: sinal; sem isto, arrastar um spinbox dispararia um render por passo.
    REFRESH_DELAY_MS = 350

    def __init__(
        self,
        pdf_path: str,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
        proposal: Optional[StyleProposal] = None,
        sample_limit: int = DEFAULT_SAMPLE,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Experimentar estilo em todas as substituições")
        self.resize(1000, 720)

        self._pdf_path = str(pdf_path)
        self._operations = list(operations)
        self._erase_operations = list(erase_operations)
        self._whiteout = bool(whiteout)
        self._include_lichess_link = bool(include_lichess_link)
        self._erase_coordinates = bool(erase_coordinates)
        self._worker: Optional[GalleryWorker] = None
        self._rows: dict[tuple[str, int], int] = {}

        all_items = build_items(self._operations)
        self._sample = sample_items(all_items, sample_limit)
        self._total_items = len(all_items)

        base = proposal or (
            StyleProposal.from_operation(self._operations[0])
            if self._operations
            else StyleProposal()
        )
        self._spins = self._build_spins(base)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setViewMode(QtWidgets.QListView.IconMode)
        self.list_widget.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_widget.setMovement(QtWidgets.QListView.Static)
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.list_widget.setIconSize(QtCore.QSize(STYLE_THUMB_SIZE * 2 + 9, STYLE_THUMB_SIZE))
        self.list_widget.setGridSize(QtCore.QSize(STYLE_THUMB_SIZE * 2 + 28, STYLE_THUMB_SIZE + 52))
        self.list_widget.setWordWrap(True)
        self.list_widget.setSpacing(4)

        self.sample_label = QtWidgets.QLabel(self._sample_text())
        self.sample_label.setWordWrap(True)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, max(1, len(self._sample)))
        self.progress_bar.setVisible(False)

        self.buttons = QtWidgets.QDialogButtonBox()
        self.apply_button = self.buttons.addButton(
            self._apply_text(base), QtWidgets.QDialogButtonBox.AcceptRole
        )
        self.buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._build_form())
        layout.addWidget(self.sample_label)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.buttons)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self.REFRESH_DELAY_MS)
        self._refresh_timer.timeout.connect(self.refresh_now)

        self._populate_placeholders()
        self.refresh_now()

    # -- montagem ------------------------------------------------------

    def _build_spins(self, base: StyleProposal) -> dict[str, QtWidgets.QDoubleSpinBox]:
        spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        specs = (
            ("padding_left_pt", "Padding esq.", 0.0, 40.0, 0.5, 1, base.padding_left_pt),
            ("padding_top_pt", "Padding topo", 0.0, 40.0, 0.5, 1, base.padding_top_pt),
            ("padding_right_pt", "Padding dir.", 0.0, 40.0, 0.5, 1, base.padding_right_pt),
            ("padding_bottom_pt", "Padding base", 0.0, 40.0, 0.5, 1, base.padding_bottom_pt),
            ("border_width_pt", "Borda", 0.0, 12.0, 0.25, 2, base.border_width_pt),
        )
        for name, _label, low, high, step, decimals, value in specs:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setSuffix(" pt")
            spin.setValue(float(value))
            spin.valueChanged.connect(self._on_style_edited)
            spins[name] = spin
        self._spin_labels = {name: label for name, label, *_rest in specs}
        return spins

    def _build_form(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Estilo proposto")
        grid = QtWidgets.QGridLayout(box)
        for column, (name, spin) in enumerate(self._spins.items()):
            grid.addWidget(QtWidgets.QLabel(self._spin_labels[name]), 0, column)
            grid.addWidget(spin, 1, column)
        grid.setColumnStretch(len(self._spins), 1)
        return box

    def _populate_placeholders(self) -> None:
        """Células antes das miniaturas, como na galeria: a grade aparece cheia."""
        self.list_widget.clear()
        self._rows.clear()
        for row, item in enumerate(self._sample):
            widget_item = QtWidgets.QListWidgetItem(f"pág {item.page_num + 1}\natual | proposto")
            widget_item.setData(QtCore.Qt.UserRole, item.key)
            widget_item.setTextAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
            self.list_widget.addItem(widget_item)
            self._rows[item.key] = row

    def _sample_text(self) -> str:
        if not self._total_items:
            return "Nenhuma substituição para restilizar."
        if len(self._sample) >= self._total_items:
            return (
                f"{self._total_items} substituição(ões) do livro, todas na grade. "
                "À esquerda o estilo atual, à direita o proposto."
            )
        return (
            f"Amostra de {len(self._sample)} de {self._total_items} substituições, "
            "espalhadas pelo livro. À esquerda o estilo atual, à direita o proposto. "
            "Aplicar vale para todas."
        )

    def _apply_text(self, proposal: StyleProposal) -> str:
        affected = count_affected(self._operations, proposal)
        if affected == 0:
            return "Aplicar (nada muda)"
        return f"Aplicar em {affected} de {len(self._operations)}"

    # -- proposta ------------------------------------------------------

    def proposal(self) -> StyleProposal:
        return StyleProposal(
            padding_left_pt=float(self._spins["padding_left_pt"].value()),
            padding_top_pt=float(self._spins["padding_top_pt"].value()),
            padding_right_pt=float(self._spins["padding_right_pt"].value()),
            padding_bottom_pt=float(self._spins["padding_bottom_pt"].value()),
            border_width_pt=float(self._spins["border_width_pt"].value()),
        )

    def _on_style_edited(self, value: float) -> None:
        del value
        self.apply_button.setText(self._apply_text(self.proposal()))
        self._refresh_timer.start()

    # -- render --------------------------------------------------------

    def refresh_now(self) -> None:
        """Reinicia a grade com a proposta atual."""
        self._refresh_timer.stop()
        self.stop_worker()
        if not self._sample:
            self.status_label.setText("")
            return
        proposal = self.proposal()
        worker = GalleryWorker(
            self._pdf_path,
            self._sample,
            restyle(self._operations, proposal),
            (),
            erase_operations=self._erase_operations,
            whiteout=self._whiteout,
            include_lichess_link=self._include_lichess_link,
            erase_coordinates=self._erase_coordinates,
            # O "antes" é o estilo que está salvo hoje, não a página crua: a
            # comparação aqui é entre duas versões do resultado.
            before_operations=self._operations,
            parent=self,
        )
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.progress.connect(self._on_progress)
        worker.item_failed.connect(self._on_item_failed)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        self.progress_bar.setRange(0, max(1, len(self._sample)))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Renderizando a prévia...")
        worker.start()

    def _on_thumbnail_ready(self, key: object, before_png: bytes, after_png: bytes) -> None:
        row = self._rows.get(tuple(key) if isinstance(key, (list, tuple)) else key)
        if row is None:
            return
        item = self.list_widget.item(row)
        if item is None:
            return
        item.setIcon(QtGui.QIcon(compose_pair(before_png, after_png, size=STYLE_THUMB_SIZE)))

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)

    def _on_item_failed(self, key: object, message: str) -> None:
        del key
        logger.warning("Miniatura do estilo em lote falhou: %s", message)

    def _on_completed(self, canceled: bool) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText("" if canceled else "Prévia pronta.")
        self._worker = None

    def stop_worker(self) -> None:
        """Para o render e espera a thread sair — a lição do `closeEvent` do
        Sprint 5.1: uma `QThread` viva mexendo em diálogo destruído derruba o app."""
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        if not worker.wait(5000):  # pragma: no cover - só num render patológico
            logger.warning("Worker do estilo em lote não terminou em 5s; encerrando à força")
            worker.terminate()
            worker.wait(1000)
        self._worker = None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._refresh_timer.stop()
        self.stop_worker()
        super().closeEvent(event)

    def accept(self) -> None:
        self._refresh_timer.stop()
        self.stop_worker()
        super().accept()

    def reject(self) -> None:
        self._refresh_timer.stop()
        self.stop_worker()
        super().reject()
