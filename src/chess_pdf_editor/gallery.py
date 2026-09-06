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
from .theme import DESTRUCTIVE_BUTTON_STYLE
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


def spin_clamp(value: int, menor: int, maior: int) -> int:
    return min(max(int(value), int(menor)), int(maior))


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
        zoom: float = THUMB_ZOOM,
        margin_ratio: float = THUMB_MARGIN_RATIO,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        """`before_operations` troca o que é o lado "antes".

        Sem ele o "antes" é a página crua do PDF, que é a pergunta da galeria
        ("o que este livro tinha aqui?"). Com ele o "antes" é a página já
        substituída com *aquele* conjunto de operações, o que permite comparar
        duas versões do resultado em vez de original contra resultado — é o que o
        estilo em lote precisa (§36).

        `zoom` e `margin_ratio` são o recorte. Os padrões são os da grade, onde a
        pergunta é "que diagrama é este?"; quem mostra **um** diagrama grande pede
        mais pixel e mais margem em volta — o rótulo `Lichess` fica abaixo do
        tabuleiro e some de um recorte apertado (§54).
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
        self._zoom = max(0.1, float(zoom))
        self._margin_ratio = max(0.0, float(margin_ratio))
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
        rect = _expanded_rect(item.rect_pdf, self._margin_ratio)
        if before_by_page is None:
            before = service.render_region(item.page_num, self._zoom, rect)
        else:
            before = service.render_region_with_operations(
                item.page_num,
                self._zoom,
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
            self._zoom,
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

    #: (tipo, índice) do diagrama cujos campos do rodapé mudaram.
    entry_edited = QtCore.Signal(str, int)

    #: Quantos diagramas mudaram de uma vez. Sinal separado do de cima porque do
    #: outro lado a diferença importa: um lote tem de virar **um** passo de
    #: desfazer, e não N.
    batch_edited = QtCore.Signal(int)

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
        self.resize(1000, 780)

        self._items = build_items(operations, candidates)
        self._rows: dict[tuple[str, int], int] = {}
        self._worker: Optional[GalleryWorker] = None

        # Guardados por **referência**: os objetos aqui são os mesmos da janela
        # principal, então o rodapé edita o diagrama de verdade e não uma cópia
        # que alguém teria de lembrar de reconciliar depois.
        self._operations = operations
        self._candidates = candidates
        self._global_lichess = bool(include_lichess_link)
        self._loading_footer = False
        #: Miniaturas a refazer quando o render principal terminar (ver
        #: `_queue_thumbnail_refresh`).
        self._dirty_keys: set[tuple[str, int]] = set()
        self._render_args = (
            pdf_path,
            erase_operations,
            whiteout,
            include_lichess_link,
            erase_coordinates,
        )

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setViewMode(QtWidgets.QListView.IconMode)
        self.list_widget.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_widget.setMovement(QtWidgets.QListView.Static)
        self.list_widget.setIconSize(QtCore.QSize(THUMB_SIZE * 2 + 9, THUMB_SIZE))
        self.list_widget.setGridSize(QtCore.QSize(THUMB_SIZE * 2 + 28, THUMB_SIZE + 52))
        self.list_widget.setWordWrap(True)
        self.list_widget.setSpacing(4)
        # Seleção múltipla para o lote (§52.5). Ctrl+clique junta, Shift+clique
        # pega o intervalo, Ctrl+A pega tudo.
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self._update_batch_row)
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.itemClicked.connect(self._on_item_activated)
        # O rodapé segue a seleção **corrente**, e não o clique: assim as setas do
        # teclado também o atualizam. Navegar a janela principal continua sendo
        # coisa do clique, que é um gesto deliberado.
        self.list_widget.currentItemChanged.connect(self._on_current_changed)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, max(1, len(self._items)))
        self.progress_bar.setVisible(bool(self._items))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._build_filter_bar())
        layout.addWidget(self.status_label)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self._build_footer())
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

    # -- filtro --------------------------------------------------------

    def _build_filter_bar(self) -> QtWidgets.QWidget:
        """Recortar a grade antes de agir sobre ela.

        `Ctrl+A` já pegava tudo; o que faltava era pegar **um pedaço** sem rolar e
        Shift+clicar por 90 páginas. Os três recortes são os que se pedem na prática:
        o capítulo (faixa de páginas), o tipo (o que já está aplicado × o que ainda é
        candidato) e a escolha de link — este último para achar as exceções, que num
        livro de centenas de diagramas é agulha em palheiro.

        **O filtro é de vista, não de trabalho.** As miniaturas de todos continuam
        sendo renderizadas: o filtro muda a qualquer momento, e um render que só
        cobrisse o recorte atual teria de recomeçar a cada mudança.
        """
        paginas = [item.page_num + 1 for item in self._items] or [1]

        self.filter_kind = QtWidgets.QComboBox()
        self.filter_kind.addItem("Todos", None)
        self.filter_kind.addItem("Substituições", KIND_OPERATION)
        self.filter_kind.addItem("Candidatos", KIND_CANDIDATE)

        self.filter_page_from = QtWidgets.QSpinBox()
        self.filter_page_to = QtWidgets.QSpinBox()
        for spin, valor in ((self.filter_page_from, min(paginas)), (self.filter_page_to, max(paginas))):
            spin.setRange(min(paginas), max(paginas))
            spin.setValue(valor)

        self.filter_lichess = QtWidgets.QComboBox()
        self.filter_lichess.addItem("Todos", "all")
        self.filter_lichess.addItem("Segue o padrão", "default")
        self.filter_lichess.addItem("Com link", "on")
        self.filter_lichess.addItem("Sem link", "off")

        self.btn_clear_filter = QtWidgets.QPushButton("Mostrar tudo")
        self.btn_clear_filter.setStyleSheet(DESTRUCTIVE_BUTTON_STYLE)
        self.btn_clear_filter.clicked.connect(self._clear_filter)

        for widget in (self.filter_kind, self.filter_lichess):
            widget.currentIndexChanged.connect(self._apply_filter)
        for spin in (self.filter_page_from, self.filter_page_to):
            spin.valueChanged.connect(self._apply_filter)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel("Mostrar"))
        row.addWidget(self.filter_kind)
        row.addSpacing(10)
        row.addWidget(QtWidgets.QLabel("Páginas"))
        row.addWidget(self.filter_page_from)
        row.addWidget(QtWidgets.QLabel("a"))
        row.addWidget(self.filter_page_to)
        row.addSpacing(10)
        row.addWidget(QtWidgets.QLabel("Link"))
        row.addWidget(self.filter_lichess)
        row.addSpacing(10)
        row.addWidget(self.btn_clear_filter)
        row.addStretch(1)

        bar = QtWidgets.QWidget()
        bar.setLayout(row)
        self.filter_bar = bar
        return bar

    def _clear_filter(self) -> None:
        self.filter_kind.setCurrentIndex(0)
        self.filter_lichess.setCurrentIndex(0)
        self.filter_page_from.setValue(self.filter_page_from.minimum())
        self.filter_page_to.setValue(self.filter_page_to.maximum())
        self._apply_filter()

    def _sync_filter_range(self) -> None:
        """Reajusta a faixa de páginas depois que a lista de itens mudou (§59.7).

        Os limites nascem dos itens, então um `rebind` que removeu o último diagrama
        do livro deixaria o `QSpinBox` aceitando uma página que não existe mais. E o
        valor **escolhido** tem de sobreviver: quem recortou um capítulo e apertou
        `Ctrl+Z` não pediu para voltar a ver o livro inteiro.

        A exceção é o filtro que ninguém tocou — aquele em que o valor é o próprio
        limite. Ali seguir o limite novo é o que mantém a promessa de "mostrar tudo".
        """
        paginas = [item.page_num + 1 for item in self._items] or [1]
        menor, maior = min(paginas), max(paginas)
        segue_menor = self.filter_page_from.value() == self.filter_page_from.minimum()
        segue_maior = self.filter_page_to.value() == self.filter_page_to.maximum()
        for spin, novo_valor in (
            (self.filter_page_from, menor if segue_menor else spin_clamp(self.filter_page_from.value(), menor, maior)),
            (self.filter_page_to, maior if segue_maior else spin_clamp(self.filter_page_to.value(), menor, maior)),
        ):
            spin.blockSignals(True)
            spin.setRange(menor, maior)
            spin.setValue(novo_valor)
            spin.blockSignals(False)

    def _passes_filter(self, item: GalleryItem) -> bool:
        kind = self.filter_kind.currentData()
        if kind is not None and item.kind != kind:
            return False
        pagina = item.page_num + 1
        # A faixa é tolerante à ordem: quem digita "40 a 12" quis 12 a 40, e recusar
        # isso seria transformar um engano de digitação numa grade vazia.
        inicio, fim = sorted((self.filter_page_from.value(), self.filter_page_to.value()))
        if not (inicio <= pagina <= fim):
            return False
        escolha = self.filter_lichess.currentData()
        if escolha == "all":
            return True
        entry = self._entry_at(item.key)
        atual = getattr(entry, "include_lichess_link", None) if entry is not None else None
        return {"default": atual is None, "on": atual is True, "off": atual is False}[escolha]

    def _apply_filter(self) -> None:
        visiveis = 0
        for row, item in enumerate(self._items):
            widget_item = self.list_widget.item(row)
            if widget_item is None:
                continue
            mostra = self._passes_filter(item)
            widget_item.setHidden(not mostra)
            if mostra:
                visiveis += 1
            else:
                # Esconder **não** deseleciona no Qt, e um item escondido e ainda
                # selecionado entraria no lote sem aparecer na tela. É exatamente o
                # acidente que a §23 proibiu: ação em massa não toca no que o filtro
                # escondeu. Aqui a proibição é cumprida na origem.
                widget_item.setSelected(False)
        self._update_status(visiveis)
        self._update_batch_row()
        if self.list_widget.currentItem() is not None and self.list_widget.currentItem().isHidden():
            self.list_widget.setCurrentItem(None)
            self._show_entry_in_footer(None)

    def _update_status(self, visiveis: Optional[int] = None) -> None:
        total = len(self._items)
        if not total:
            return
        if visiveis is None:
            visiveis = sum(
                1
                for row in range(self.list_widget.count())
                if not self.list_widget.item(row).isHidden()
            )
        if visiveis == total:
            self.status_label.setText(
                f"{total} diagrama(s). Clique para ir até um deles e ajustá-lo abaixo."
            )
        else:
            self.status_label.setText(
                f"{visiveis} de {total} diagrama(s) — {total - visiveis} fora do filtro."
            )

    # -- rodapé de edição ----------------------------------------------

    def _build_footer(self) -> QtWidgets.QWidget:
        """Os campos que valem por diagrama, no rodapé da grade.

        A galeria já era o único lugar que mostra o livro inteiro; sem isto ela só
        servia para **achar** um diagrama, e ajustar qualquer coisa nele exigia
        fechar, voltar ao painel e reencontrá-lo lá.

        Os campos são os que têm um valor por substituição e cabem numa linha:
        lado a jogar, número do lance, link Lichess e a borda. Padding ficou de
        fora de propósito — são quatro números, e quatro números viram um segundo
        formulário, não um rodapé.
        """
        self.footer_label = QtWidgets.QLabel("Nenhum diagrama selecionado")
        self.footer_label.setStyleSheet("QLabel { font-weight: 600; }")

        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItem("Brancas", "w")
        self.side_combo.addItem("Pretas", "b")
        self.side_combo.currentIndexChanged.connect(self._apply_footer_to_entry)

        self.move_spin = QtWidgets.QSpinBox()
        self.move_spin.setRange(1, 9999)
        self.move_spin.valueChanged.connect(self._apply_footer_to_entry)

        # Três estados, como o campo do modelo: "Padrão" é o que deixa a opção
        # global valer, e é diferente de um "Não" escolhido para este diagrama.
        self.lichess_combo = QtWidgets.QComboBox()
        self.lichess_combo.addItem("Padrão", None)
        self.lichess_combo.addItem("Com link", True)
        self.lichess_combo.addItem("Sem link", False)
        self.lichess_combo.currentIndexChanged.connect(self._apply_footer_to_entry)

        self.border_spin = QtWidgets.QDoubleSpinBox()
        self.border_spin.setRange(0.0, 10.0)
        self.border_spin.setSingleStep(0.25)
        self.border_spin.setSuffix(" pt")
        self.border_spin.valueChanged.connect(self._apply_footer_to_entry)

        self._footer_fields = (
            self.side_combo,
            self.move_spin,
            self.lichess_combo,
            self.border_spin,
        )

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Vez de jogar"))
        row.addWidget(self.side_combo)
        row.addSpacing(12)
        row.addWidget(QtWidgets.QLabel("Lance"))
        row.addWidget(self.move_spin)
        row.addSpacing(12)
        row.addWidget(QtWidgets.QLabel("Link Lichess"))
        row.addWidget(self.lichess_combo)
        row.addSpacing(12)
        row.addWidget(QtWidgets.QLabel("Borda"))
        row.addWidget(self.border_spin)
        row.addStretch(1)

        box = QtWidgets.QGroupBox("Diagrama selecionado")
        inner = QtWidgets.QVBoxLayout(box)
        inner.addWidget(self.footer_label)
        inner.addLayout(row)
        inner.addWidget(self._build_batch_row())
        self.footer_box = box
        self._show_entry_in_footer(None)
        return box

    def _build_batch_row(self) -> QtWidgets.QWidget:
        """Empurrar os valores acima para todos os selecionados.

        A alternativa — os campos de cima já valerem para a seleção inteira — foi
        recusada: um lote não pode ser efeito colateral de mexer num campo. Quem
        tivesse o livro todo selecionado e encostasse no spin do lance carimbaria
        "lance 5" em 300 diagramas sem ter pedido nada.

        Daí as caixas: elas dizem **quais** campos o lote toca, e são a mesma
        disciplina da §23 — ação em massa declara o alcance antes de agir. Nenhuma
        vem marcada, então o botão nasce desabilitado e o lote precisa de dois
        gestos explícitos.

        O `Lance` está entre elas por simetria, mas é o que menos faz sentido em
        lote: cada diagrama tem o seu. `Link` e `Borda` são o motivo desta linha
        existir — são as escolhas que valem para o livro inteiro ou para um capítulo.
        """
        self.batch_label = QtWidgets.QLabel("")
        self.batch_checks = {
            "side": QtWidgets.QCheckBox("Vez"),
            "move": QtWidgets.QCheckBox("Lance"),
            "lichess": QtWidgets.QCheckBox("Link"),
            "border": QtWidgets.QCheckBox("Borda"),
        }
        self.btn_apply_batch = QtWidgets.QPushButton("Aplicar à seleção")
        self.btn_apply_batch.clicked.connect(self._apply_batch)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.batch_label)
        for check in self.batch_checks.values():
            check.toggled.connect(self._update_batch_row)
            row.addWidget(check)
        row.addWidget(self.btn_apply_batch)
        row.addStretch(1)

        self.batch_row = QtWidgets.QWidget()
        self.batch_row.setLayout(row)
        self.batch_row.setVisible(False)
        return self.batch_row

    def _selected_keys(self) -> list[tuple[str, int]]:
        """Selecionados **e** à vista.

        O `isHidden()` aqui é redundante — `_apply_filter` já deseleciona o que
        esconde — e fica de propósito. É a regra que a §23 estabeleceu (ação em massa
        não toca no que o filtro escondeu) escrita no lugar por onde o lote de fato
        passa, e não só no lugar que hoje a garante. Um caminho novo que esconda um
        item sem deselecioná-lo não vira um lote que mexe no invisível.
        """
        chaves = []
        for item in self.list_widget.selectedItems():
            if item.isHidden():
                continue
            raw = item.data(QtCore.Qt.UserRole)
            if raw:
                chaves.append((str(raw[0]), int(raw[1])))
        return chaves

    def _update_batch_row(self) -> None:
        """A linha do lote só aparece quando há lote. Com um selecionado ela seria
        um segundo caminho para o que os campos de cima já fazem na hora."""
        total = len(self._selected_keys())
        self.batch_row.setVisible(total > 1)
        self._sync_footer_enabled()
        if total <= 1:
            self.footer_box.setTitle("Diagrama selecionado")
            return
        # O título muda junto porque o rodapé mudou de função: com vários
        # selecionados ele não edita mais na hora, e um rótulo que continuasse
        # dizendo "diagrama selecionado" prometeria o contrário.
        self.footer_box.setTitle(f"{total} diagramas selecionados")
        self.footer_label.setText(
            f"Os valores abaixo não são aplicados enquanto você digita — "
            f"marque os campos e use “Aplicar aos {total}”."
        )
        marcados = [nome for nome, check in self.batch_checks.items() if check.isChecked()]
        self.batch_label.setText(f"Aplicar aos {total} selecionados:")
        self.btn_apply_batch.setText(f"Aplicar aos {total}")
        self.btn_apply_batch.setEnabled(bool(marcados))
        self.btn_apply_batch.setToolTip(
            "Marque ao menos um campo acima" if not marcados else
            f"Copia {', '.join(marcados)} do diagrama atual para os outros {total - 1}"
        )

    def _apply_batch(self) -> None:
        chaves = self._selected_keys()
        marcados = {nome for nome, check in self.batch_checks.items() if check.isChecked()}
        if len(chaves) < 2 or not marcados:
            return

        side = str(self.side_combo.currentData() or "w")
        move = max(1, int(self.move_spin.value()))
        lichess = self.lichess_combo.currentData()
        border = float(self.border_spin.value())

        tocados = 0
        for chave in chaves:
            entry = self._entry_at(chave)
            if entry is None:
                continue
            if "side" in marcados:
                entry.side_to_move = side
            if "move" in marcados:
                entry.fullmove_number = move
            if "lichess" in marcados:
                entry.include_lichess_link = lichess
            if "border" in marcados:
                entry.border_width_pt = border
            self._refresh_caption(chave)
            self._dirty_keys.add(chave)
            tocados += 1

        # Uma passada de render para o lote inteiro, e **um** sinal: do outro lado
        # isto tem de virar um passo de desfazer só.
        self._flush_thumbnail_refresh()
        # Dizer o que ficou de fora é a outra metade da regra da §23. Sem isso,
        # aplicar "sem link" com um filtro de páginas ligado e ver "aplicado em 12"
        # sugere que o livro inteiro foi tratado — e o usuário só descobre o
        # contrário no PDF exportado.
        escondidos = sum(
            1
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).isHidden()
        )
        aviso = f" ({escondidos} fora do filtro não foram tocados)" if escondidos else ""
        self.status_label.setText(
            f"{', '.join(sorted(marcados))} aplicado(s) em {tocados} diagrama(s).{aviso}"
        )
        self.batch_edited.emit(tocados)

    def _entry_at(self, key: Optional[tuple[str, int]]) -> Optional[OverlayOperation]:
        """A operação por trás de uma chave da grade, ou `None`."""
        if not key:
            return None
        kind, index = str(key[0]), int(key[1])
        origem = self._operations if kind == KIND_OPERATION else self._candidates
        if 0 <= index < len(origem):
            return origem[index]
        return None

    def _selected_key(self) -> Optional[tuple[str, int]]:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        key = item.data(QtCore.Qt.UserRole)
        return (str(key[0]), int(key[1])) if key else None

    def _sync_footer_enabled(self) -> None:
        """O rodapé serve a duas coisas, e tem de ficar ativo para as duas.

        Ligá-lo só ao item **corrente** deixava o lote inutilizável no caso mais
        natural de todos: `Ctrl+A` seleciona tudo sem definir um corrente, e o grupo
        inteiro — inclusive o botão do lote, que mora dentro dele — nascia
        desabilitado por herança. O usuário via a linha do lote e não conseguia
        clicar nela.
        """
        tem_corrente = self._entry_at(self._selected_key()) is not None
        tem_lote = len(self._selected_keys()) > 1
        self.footer_box.setEnabled(tem_corrente or tem_lote)

    def _show_entry_in_footer(self, key: Optional[tuple[str, int]]) -> None:
        entry = self._entry_at(key)
        self._sync_footer_enabled()
        if entry is None or key is None:
            self.footer_label.setText("Nenhum diagrama selecionado")
            return

        kind, index = key
        rotulo = "Substituição" if kind == KIND_OPERATION else "Candidato"
        self.footer_label.setText(
            f"{rotulo} {index + 1:03d} · pág {entry.page_num + 1} · {entry.fen[:28]}"
        )
        # A guarda é o que impede o preenchimento de disparar `valueChanged` e
        # gravar de volta no objeto que acabou de ser lido — com o efeito colateral
        # de marcar o projeto como alterado só por clicar numa miniatura.
        self._loading_footer = True
        try:
            self.side_combo.setCurrentIndex(0 if entry.side_to_move != "b" else 1)
            self.move_spin.setValue(max(1, int(entry.fullmove_number)))
            escolha = getattr(entry, "include_lichess_link", None)
            self.lichess_combo.setCurrentIndex(
                {None: 0, True: 1, False: 2}[None if escolha is None else bool(escolha)]
            )
            self.border_spin.setValue(float(getattr(entry, "border_width_pt", 0.0)))
        finally:
            self._loading_footer = False

    def _apply_footer_to_entry(self) -> None:
        if self._loading_footer:
            return
        # Com vários selecionados o rodapé deixa de ser "edite este" e passa a ser
        # "valores a aplicar": quem grava é o botão do lote. Sem esta guarda, montar
        # os valores para o lote editava de passagem o item corrente, e o usuário
        # ficava com **dois** passos de desfazer para o que ele fez como um — o
        # primeiro Ctrl+Z desfazia o lote e deixava um diagrama alterado no meio.
        if len(self._selected_keys()) > 1:
            return
        key = self._selected_key()
        entry = self._entry_at(key)
        if entry is None or key is None:
            return

        entry.side_to_move = str(self.side_combo.currentData() or "w")
        entry.fullmove_number = max(1, int(self.move_spin.value()))
        entry.include_lichess_link = self.lichess_combo.currentData()
        entry.border_width_pt = float(self.border_spin.value())

        self._refresh_caption(key)
        self._queue_thumbnail_refresh(key)
        self.entry_edited.emit(key[0], key[1])

    def _refresh_caption(self, key: tuple[str, int]) -> None:
        row = self._rows.get(key)
        entry = self._entry_at(key)
        if row is None or entry is None:
            return
        widget_item = self.list_widget.item(row)
        if widget_item is not None:
            widget_item.setText(self._caption(self._items[row], entry))

    def _on_current_changed(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        del previous
        key = None
        if current is not None:
            raw = current.data(QtCore.Qt.UserRole)
            key = (str(raw[0]), int(raw[1])) if raw else None
        self._show_entry_in_footer(key)

    # -- montagem ------------------------------------------------------

    def _populate_placeholders(self) -> None:
        """Cria as células antes das miniaturas existirem.

        A grade aparece cheia na hora e as imagens vão chegando. O contrário —
        esperar tudo para mostrar algo — deixaria a janela vazia por segundos num
        livro grande.

        Limpa antes de montar: desde o `rebind` (§59.7) este método é chamado mais de
        uma vez, e as chaves do `_rows` são **posições dentro das listas** — reusá-las
        depois de um desfazer apontaria cada célula para o diagrama do vizinho.
        """
        self.list_widget.clear()
        self._rows.clear()
        if not self._items:
            self.status_label.setText(
                "Nenhum diagrama para mostrar. Adicione substituições ou reconheça o PDF."
            )
            return
        for row, item in enumerate(self._items):
            widget_item = QtWidgets.QListWidgetItem(self._caption(item, self._entry_at(item.key)))
            widget_item.setData(QtCore.Qt.UserRole, item.key)
            widget_item.setTextAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
            self.list_widget.addItem(widget_item)
            self._rows[item.key] = row
        self._update_status(len(self._items))

    @staticmethod
    def _caption(item: GalleryItem, entry: Optional[OverlayOperation] = None) -> str:
        """Legenda da célula.

        O `GalleryItem` é congelado de propósito — é o que atravessa para o worker —
        e por isso carrega só o que não muda: página, FEN e confiança. O que o
        rodapé edita vem do `entry`, que é a operação viva. Sem ele a grade
        continuaria anunciando a escolha antiga depois da edição.
        """
        confidence = "" if item.confidence is None else f" · conf {float(item.confidence):.2f}"
        marker = "" if item.kind == KIND_OPERATION else " · candidato"
        # Só aparece quando o diagrama **discorda** da opção global: é a exceção que
        # se precisa achar na grade, e marcar os dois casos não marcaria nada.
        escolha = getattr(entry, "include_lichess_link", None) if entry is not None else None
        link = "" if escolha is None else (" · com link" if escolha else " · sem link")
        fen = item.fen[:22] + ("..." if len(item.fen) > 22 else "")
        return f"pág {item.page_num + 1}{marker}{confidence}{link}\n{fen}"

    def _queue_thumbnail_refresh(self, key: tuple[str, int]) -> None:
        """Marca uma miniatura para refazer, e refaz se der.

        Editar o rodapé muda o "depois" do diagrama — a borda some, o link aparece —
        e uma grade que continua mostrando o resultado antigo é pior que uma grade
        sem miniatura: ela **afirma** algo que deixou de ser verdade.

        Nunca há dois workers ao mesmo tempo. Enquanto o render inicial roda, a
        chave só entra na fila; quem a esvazia é o `_on_completed`. Dois workers
        sobre as mesmas chaves entregariam resultados fora de ordem, e o que
        chegasse por último venceria — que pode ser o mais velho.
        """
        self._dirty_keys.add(key)
        self._flush_thumbnail_refresh()

    def _flush_thumbnail_refresh(self) -> None:
        if self._worker is not None or not self._dirty_keys:
            return
        pendentes = [item for item in self._items if item.key in self._dirty_keys]
        self._dirty_keys.clear()
        if not pendentes:
            return
        pdf_path, erase_operations, whiteout, include_lichess_link, erase_coordinates = (
            self._render_args
        )
        self._start_worker(
            pdf_path,
            self._operations,
            self._candidates,
            erase_operations,
            whiteout,
            include_lichess_link,
            erase_coordinates,
            items=pendentes,
        )

    def rebind(
        self,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation] = (),
        erase_operations: Optional[Sequence[EraseOperation]] = None,
    ) -> None:
        """Reaponta a grade para as listas atuais da janela principal (§59.7).

        Desfazer não muta as listas: ele as **substitui** por cópias restauradas do
        histórico. As referências guardadas aqui (§52.3) viram órfãs, e o rodapé
        continuaria escrevendo num objeto que ninguém mais lê — a edição sumiria sem
        erro nenhum, que é o pior jeito de sumir. O navegador já resolvia isso pelo
        mesmo caminho (§54); a galeria tinha ficado de fora.

        A grade exige mais que o navegador, e por isso aqui não basta reapontar: as
        chaves são **posições dentro das listas**, então um desfazer que removeu o
        diagrama 4 muda o significado de todas as chaves acima dele. Reapontar sem
        reconstruir trocaria um defeito silencioso por outro — o rodapé passaria a
        editar o vizinho do que está na tela.
        """
        self.stop_worker()
        # As chaves pendentes são do conjunto antigo: refazer miniatura por elas
        # pintaria a célula errada.
        self._dirty_keys.clear()

        self._operations = operations
        self._candidates = candidates
        pdf_path, erases, whiteout, include_lichess_link, erase_coordinates = self._render_args
        if erase_operations is not None:
            erases = erase_operations
            self._render_args = (
                pdf_path,
                erases,
                whiteout,
                include_lichess_link,
                erase_coordinates,
            )

        self._items = build_items(operations, candidates)
        self._populate_placeholders()
        self._sync_filter_range()
        self._apply_filter()
        self._show_entry_in_footer(self._selected_key())

        self.progress_bar.setRange(0, max(1, len(self._items)))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(bool(self._items))
        if self._items:
            self._start_worker(
                pdf_path,
                operations,
                candidates,
                erases,
                whiteout,
                include_lichess_link,
                erase_coordinates,
            )

    def _start_worker(
        self,
        pdf_path: str,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation],
        whiteout: bool,
        include_lichess_link: bool,
        erase_coordinates: bool,
        items: Optional[Sequence[GalleryItem]] = None,
    ) -> None:
        alvo = list(self._items if items is None else items)
        worker = GalleryWorker(
            pdf_path,
            alvo,
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
            # Pelo `_update_status`, não por texto direto: com filtro ligado, a frase
            # fixa apagaria a contagem do recorte e diria que estão todos à vista.
            self._update_status()
        self._worker = None
        # Edições feitas enquanto o render corria esperam aqui. Cancelado também
        # esvazia a fila: quem cancelou fechou a janela, e refazer miniatura para
        # um diálogo que está indo embora é trabalho para ninguém ver.
        if canceled:
            self._dirty_keys.clear()
        else:
            self._flush_thumbnail_refresh()

    # -- interação -----------------------------------------------------

    def _on_item_activated(self, item: QtWidgets.QListWidgetItem) -> None:
        key = item.data(QtCore.Qt.UserRole)
        if not key:
            return
        # Ctrl e Shift são gestos de **seleção**, não de navegação. Sem esta
        # guarda, montar uma seleção de 20 diagramas levaria a janela principal a
        # 20 páginas diferentes pelo caminho — 20 renders para chegar onde nem se
        # queria ir.
        modificadores = QtWidgets.QApplication.keyboardModifiers()
        if modificadores & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier):
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
