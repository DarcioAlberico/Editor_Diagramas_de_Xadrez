"""Navegador de diagramas: um por vez, grande, com as etiquetas ao lado (§54).

### O que faltava

A galeria (§22.5) responde "onde estão os diagramas deste livro?" — centenas de
pares de 150 px, e um rodapé que edita o selecionado. É uma vista de *conjunto*, e
por isso o diagrama nela é pequeno de propósito: cabem oito por tela porque
nenhum precisa ser grande para ser **reconhecido**.

Conferir as **etiquetas** de um diagrama é a pergunta oposta, e 150 px não a
respondem. O número do lance está impresso no livro, em corpo pequeno, ao lado ou
abaixo do tabuleiro; de quem é a vez costuma estar na legenda ("as brancas
jogam"). Copiar isso para os campos exige *ler a página* — e é isso que esta
janela faz: um diagrama por vez, no maior tamanho que a janela der, os campos
logo abaixo e o próximo a um clique.

### As etiquetas não mudam um pixel — e é por isso que a janela mostra a FEN

`side_to_move` e `fullmove_number` não entram no desenho do tabuleiro. O que sai
delas no PDF é a **FEN do link Lichess** (`operation_full_fen`), mais o relatório
(§26) e a exportação de diagramas isolados (§39). Uma janela que mostrasse só as
duas imagens deixaria justamente o campo mais importante sem retorno nenhum: o
usuário trocaria `vez de jogar` e veria a mesma figura dos dois lados, sem saber
se a troca pegou.

Por isso, além do par de imagens, o painel mostra:

* a **FEN final**, a mesma string que vai para o link;
* o **link** que o PDF terá — ou o aviso de que este diagrama não terá nenhum;
* a **auditoria de legalidade** (§37), que é o único juiz automático do lado a
  jogar: é ela que diz "com as brancas a jogar, o rei preto está em xeque — o
  lado a jogar provavelmente está trocado".

### O que a janela não faz

Aplicar ou descartar candidato. Os dois mudam o **tamanho** das listas, e as
chaves desta janela (como as da galeria) são posições dentro delas: um descarte
no meio faria o navegador passar a editar o vizinho sem avisar. Quem quer aplicar
usa `Ir para este diagrama`, que leva a janela principal até ele — onde os botões
moram.

### Thread

O render é o `GalleryWorker` da galeria, com `zoom` e margem maiores. O contrato é
o do Sprint 5.1: o worker abre o **seu próprio** documento a partir do caminho, e
o que cruza a fronteira são `bytes`. Um item por vez, e nunca dois workers ao
mesmo tempo — `refresh_now` cancela e espera o anterior, como o estilo em lote
(§36).
"""
from __future__ import annotations

from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from . import legality
from .gallery import KIND_OPERATION, GalleryItem, GalleryWorker, build_items
from .logging_config import get_logger
from .pdf_service import operation_full_fen, operation_lichess_url, wants_lichess_link
from .theme import PRIMARY_BUTTON_STYLE, SECTION_STYLE, warning_text_color
from .types import EraseOperation, OverlayOperation
from .widgets import BeforeAfterWidget

logger = get_logger("navigator")

#: Zoom do recorte. 3.0 dá ~480 px num diagrama de 160 pt: é o que sustenta a
#: imagem grande sem serrilhado. A galeria usa 2.0 porque reduz para 150.
NAV_ZOOM = 3.0

#: Margem em volta do diagrama. Maior que a da grade (0.10) porque aqui é preciso
#: ler o que o livro escreveu **em volta** do tabuleiro — a legenda com o número
#: do lance, e o rótulo `Lichess` que a exportação põe logo abaixo.
NAV_MARGIN_RATIO = 0.20

#: Altura mínima do par de imagens. Elas crescem com a janela; isto é o piso.
MIN_IMAGE_HEIGHT = 220


class DiagramNavigatorDialog(QtWidgets.QDialog):
    """Um diagrama por vez: como está no PDF, como vai ficar, e as etiquetas."""

    #: (tipo, índice) do diagrama a mostrar na janela principal.
    entry_activated = QtCore.Signal(str, int)

    #: (tipo, índice) do diagrama cujas etiquetas mudaram.
    entry_edited = QtCore.Signal(str, int)

    #: Espera entre o último passo num campo e o commit + re-render. Sem ela,
    #: arrastar o spin do lance de 1 até 40 empilharia 39 passos de desfazer —
    #: num histórico de 60 — e dispararia 39 renders para ver 39 vezes a mesma
    #: figura. Com ela, uma mexida vira **um** passo, que é como o usuário a fez.
    EDIT_DELAY_MS = 300

    def __init__(
        self,
        pdf_path: str,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation] = (),
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
        start_key: Optional[tuple[str, int]] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Navegador de diagramas")
        self.resize(980, 820)

        self._pdf_path = str(pdf_path)
        # Guardados por **referência**, como na galeria (§52.3): os objetos aqui
        # são os mesmos da janela principal, então os campos editam o diagrama de
        # verdade e não uma cópia que alguém teria de reconciliar depois.
        self._operations = operations
        self._candidates = candidates
        self._erase_operations = erase_operations
        self._whiteout = bool(whiteout)
        self._global_lichess = bool(include_lichess_link)
        self._erase_coordinates = bool(erase_coordinates)

        self._items = build_items(operations, candidates)
        self._position = self._position_of(start_key)
        self._worker: Optional[GalleryWorker] = None
        #: Guarda contra o preenchimento dos campos gravar de volta no objeto que
        #: acabou de ser lido — e marcar o projeto como alterado por navegar.
        self._loading = False
        self._pending_edit_key: Optional[tuple[str, int]] = None

        self._edit_timer = QtCore.QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(self.EDIT_DELAY_MS)
        self._edit_timer.timeout.connect(self._flush_edit)

        self.before_after = BeforeAfterWidget(
            thumb_height=MIN_IMAGE_HEIGHT,
            before_title="No PDF (como está)",
            after_title="Como vai ficar",
            expanding=True,
        )
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._build_nav_bar())
        layout.addWidget(self.before_after, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self._build_tags_box())
        layout.addWidget(self._build_buttons())

        self._disable_auto_default()
        self._show_current()

    # -- montagem ------------------------------------------------------

    def _disable_auto_default(self) -> None:
        """Nenhum botão responde ao Enter.

        Num `QDialog` todo `QPushButton` nasce `autoDefault`, e o campo com foco
        ao abrir é o da posição. Enter depois de digitar "147" acionaria o
        primeiro botão da janela em vez de confirmar o número — no melhor caso
        `Anterior`, no pior `Fechar`. Aqui Enter não tem o que acionar: o spin já
        aplica o valor enquanto se digita.
        """
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _build_nav_bar(self) -> QtWidgets.QWidget:
        """Anterior/próximo, o "n de N" e o rótulo do diagrama corrente.

        O campo do meio é um spin e não um rótulo: num livro de 300 diagramas,
        voltar ao 147 depois de ter chegado ao 260 seria 113 cliques em
        `Anterior`. Ele digita o número e chega.
        """
        self.btn_prev = QtWidgets.QPushButton("◀ Anterior")
        self.btn_prev.setShortcut(QtGui.QKeySequence("Alt+Left"))
        self.btn_prev.setToolTip("Diagrama anterior (Alt+←)")
        self.btn_prev.clicked.connect(lambda: self._go_to(self._position - 1))

        self.btn_next = QtWidgets.QPushButton("Próximo ▶")
        self.btn_next.setShortcut(QtGui.QKeySequence("Alt+Right"))
        self.btn_next.setToolTip("Próximo diagrama (Alt+→)")
        self.btn_next.clicked.connect(lambda: self._go_to(self._position + 1))

        self.position_spin = QtWidgets.QSpinBox()
        self.position_spin.setRange(1, max(1, len(self._items)))
        # Sem o mínimo, as setas comem o campo e o número fica cortado num livro
        # de quatro dígitos — que é justamente onde este campo serve para algo.
        self.position_spin.setMinimumWidth(72)
        self.position_spin.setToolTip("Ir para o n-ésimo diagrama, na ordem do livro")
        self.position_spin.valueChanged.connect(lambda value: self._go_to(value - 1))

        self.count_label = QtWidgets.QLabel("")

        self.header_label = QtWidgets.QLabel("")
        self.header_label.setStyleSheet(SECTION_STYLE)
        self.header_label.setWordWrap(True)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.btn_prev)
        row.addWidget(self.position_spin)
        row.addWidget(self.count_label)
        row.addWidget(self.btn_next)
        row.addSpacing(12)
        row.addWidget(self.header_label, 1)

        bar = QtWidgets.QWidget()
        bar.setLayout(row)
        self.nav_bar = bar
        return bar

    def _build_tags_box(self) -> QtWidgets.QWidget:
        """As etiquetas do diagrama, e o que elas produzem.

        Os dois primeiros campos são o motivo desta janela existir; `Link` e
        `Borda` vêm junto por serem o resto do que vale por diagrama e cabe numa
        linha. Padding continua de fora, aqui como na galeria: são quatro números,
        e quatro números pedem o formulário da aba `Ajustes`, não uma barra.
        """
        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItem("Brancas", "w")
        self.side_combo.addItem("Pretas", "b")
        self.side_combo.setToolTip("De quem é a vez na posição — vai para a FEN do link")
        self.side_combo.currentIndexChanged.connect(self._apply_fields_to_entry)

        self.move_spin = QtWidgets.QSpinBox()
        self.move_spin.setRange(1, 9999)
        self.move_spin.setToolTip("Número do lance impresso no livro")
        self.move_spin.valueChanged.connect(self._apply_fields_to_entry)

        # Três estados, como o campo do modelo: `Padrão` deixa a opção global
        # valer, e é diferente de um `Não` escolhido para este diagrama (§52.1).
        self.lichess_combo = QtWidgets.QComboBox()
        self.lichess_combo.addItem("Padrão", None)
        self.lichess_combo.addItem("Com link", True)
        self.lichess_combo.addItem("Sem link", False)
        self.lichess_combo.currentIndexChanged.connect(self._apply_fields_to_entry)

        self.border_spin = QtWidgets.QDoubleSpinBox()
        self.border_spin.setRange(0.0, 10.0)
        self.border_spin.setSingleStep(0.25)
        self.border_spin.setSuffix(" pt")
        self.border_spin.valueChanged.connect(self._apply_fields_to_entry)

        fields = QtWidgets.QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.addWidget(QtWidgets.QLabel("Vez de jogar"))
        fields.addWidget(self.side_combo)
        fields.addSpacing(12)
        fields.addWidget(QtWidgets.QLabel("Número do lance"))
        fields.addWidget(self.move_spin)
        fields.addSpacing(12)
        fields.addWidget(QtWidgets.QLabel("Link Lichess"))
        fields.addWidget(self.lichess_combo)
        fields.addSpacing(12)
        fields.addWidget(QtWidgets.QLabel("Borda"))
        fields.addWidget(self.border_spin)
        fields.addStretch(1)

        self.fen_value = QtWidgets.QLineEdit()
        self.fen_value.setReadOnly(True)
        self.fen_value.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.fen_value.setToolTip(
            "A FEN que as etiquetas produzem. É esta string que vai para o link "
            "do PDF, para o relatório e para a exportação de diagramas."
        )

        self.link_label = QtWidgets.QLabel("")
        self.link_label.setTextFormat(QtCore.Qt.RichText)
        self.link_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.link_label.setOpenExternalLinks(True)
        self.link_label.setWordWrap(True)

        self.legality_label = QtWidgets.QLabel("")
        self.legality_label.setWordWrap(True)

        fen_row = QtWidgets.QHBoxLayout()
        fen_row.setContentsMargins(0, 0, 0, 0)
        fen_row.addWidget(QtWidgets.QLabel("FEN final"))
        fen_row.addWidget(self.fen_value, 1)

        box = QtWidgets.QGroupBox("Etiquetas deste diagrama")
        inner = QtWidgets.QVBoxLayout(box)
        inner.addLayout(fields)
        inner.addLayout(fen_row)
        inner.addWidget(self.link_label)
        inner.addWidget(self.legality_label)
        self.tags_box = box
        return box

    def _build_buttons(self) -> QtWidgets.QWidget:
        self.btn_focus = QtWidgets.QPushButton("Ir para este diagrama")
        self.btn_focus.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.btn_focus.setToolTip(
            "Leva a janela principal até esta página e seleciona o diagrama — "
            "é lá que se corrige a posição, aplica candidato ou remove"
        )
        self.btn_focus.clicked.connect(self._activate_current)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        # O texto padrão do botão sai no idioma do sistema, e numa máquina em
        # inglês a janela inteira em português terminava num `Close`.
        buttons.button(QtWidgets.QDialogButtonBox.Close).setText("Fechar")
        buttons.rejected.connect(self.reject)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.btn_focus)
        row.addStretch(1)
        row.addWidget(buttons)

        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        return holder

    # -- posição corrente ----------------------------------------------

    def _position_of(self, key: Optional[tuple[str, int]], fallback: int = 0) -> int:
        if key is not None:
            alvo = (str(key[0]), int(key[1]))
            for index, item in enumerate(self._items):
                if item.key == alvo:
                    return index
        return max(0, min(int(fallback), len(self._items) - 1))

    def _current_item(self) -> Optional[GalleryItem]:
        if 0 <= self._position < len(self._items):
            return self._items[self._position]
        return None

    def _current_key(self) -> Optional[tuple[str, int]]:
        item = self._current_item()
        return None if item is None else item.key

    def _entry_at(self, key: Optional[tuple[str, int]]) -> Optional[OverlayOperation]:
        """A operação por trás de uma chave, ou `None` se ela não existe mais."""
        if not key:
            return None
        kind, index = str(key[0]), int(key[1])
        origem = self._operations if kind == KIND_OPERATION else self._candidates
        if 0 <= index < len(origem):
            return origem[index]
        return None

    def current_entry(self) -> Optional[OverlayOperation]:
        return self._entry_at(self._current_key())

    def _go_to(self, position: int) -> None:
        if not self._items:
            return
        position = max(0, min(int(position), len(self._items) - 1))
        if position == self._position:
            # O spin também chega aqui ao ser sincronizado; sem esta saída, cada
            # troca de diagrama renderizaria duas vezes.
            self._sync_nav_bar()
            return
        # A edição pendente é do diagrama que está saindo, e tem de ser entregue
        # antes: o sinal leva a chave junto, mas o render que viria com ele seria
        # do diagrama errado — daí o `render=False`.
        self._flush_edit(render=False)
        self._position = position
        self._show_current()

    def _show_current(self) -> None:
        entry = self.current_entry()
        self._sync_nav_bar()
        self._load_fields(entry)
        self._update_details(entry)
        self.refresh_now()

    def _sync_nav_bar(self) -> None:
        total = len(self._items)
        # `blockSignals`, e não a guarda `_loading`: o spin é a origem de `_go_to`,
        # e sincronizá-lo sem bloquear reentraria na navegação que acabou de rodar.
        self.position_spin.blockSignals(True)
        self.position_spin.setRange(1, max(1, total))
        self.position_spin.setValue(self._position + 1 if total else 1)
        self.position_spin.blockSignals(False)
        self.position_spin.setEnabled(total > 1)
        self.count_label.setText(f"de {total}" if total else "—")
        self.btn_prev.setEnabled(self._position > 0)
        self.btn_next.setEnabled(self._position + 1 < total)

    def _load_fields(self, entry: Optional[OverlayOperation]) -> None:
        self.tags_box.setEnabled(entry is not None)
        self.btn_focus.setEnabled(entry is not None)
        if entry is None:
            return
        self._loading = True
        try:
            self.side_combo.setCurrentIndex(1 if entry.side_to_move == "b" else 0)
            self.move_spin.setValue(max(1, int(entry.fullmove_number)))
            escolha = getattr(entry, "include_lichess_link", None)
            self.lichess_combo.setCurrentIndex(
                {None: 0, True: 1, False: 2}[None if escolha is None else bool(escolha)]
            )
            self.border_spin.setValue(float(getattr(entry, "border_width_pt", 0.0)))
        finally:
            self._loading = False

    def _update_details(self, entry: Optional[OverlayOperation]) -> None:
        """Cabeçalho, FEN final, link e legalidade — tudo o que é texto.

        Roda na hora a cada mexida, sem esperar o `_edit_timer`: são strings, e
        são justamente o retorno que as etiquetas não dão na imagem. Fazê-las
        esperar pelo render seria esconder a única resposta imediata que estes
        campos têm.
        """
        item = self._current_item()
        if entry is None or item is None:
            self.header_label.setText(
                "Nenhum diagrama" if not self._items else "Este diagrama não está mais na lista."
            )
            self.fen_value.clear()
            self.link_label.clear()
            self.legality_label.clear()
            return

        rotulo = "Substituição" if item.kind == KIND_OPERATION else "Candidato"
        pendente = "" if item.kind == KIND_OPERATION else " · ainda não aplicado"
        confianca = (
            "" if item.confidence is None else f" · confiança {float(item.confidence):.2f}"
        )
        self.header_label.setText(
            f"{rotulo} {item.index + 1:03d} · página {item.page_num + 1}{confianca}{pendente}"
        )

        full_fen = operation_full_fen(entry)
        self.fen_value.setText(full_fen)
        url = operation_lichess_url(entry)
        if wants_lichess_link(entry, self._global_lichess):
            self.link_label.setText(f'O PDF vai levar este link: <a href="{url}">Lichess</a>')
        else:
            self.link_label.setText(
                f'Sem link no PDF · <a href="{url}">conferir a posição no Lichess</a>'
            )
        self.link_label.setToolTip(url)

        # A auditoria é o único juiz automático do `vez de jogar`: ela testa os
        # dois lados e sabe dizer quando só o indicado dá posição ilegal (§37).
        findings = legality.audit(str(entry.fen), str(entry.side_to_move))
        if not findings:
            self.legality_label.setText("Legalidade: nada a apontar.")
            self.legality_label.setStyleSheet("QLabel { color: palette(mid); }")
            return
        self.legality_label.setText("\n".join(legality.labels(findings)))
        self.legality_label.setStyleSheet(f"QLabel {{ color: {warning_text_color()}; }}")

    # -- edição --------------------------------------------------------

    def _apply_fields_to_entry(self) -> None:
        if self._loading:
            return
        key = self._current_key()
        entry = self._entry_at(key)
        if entry is None or key is None:
            return

        entry.side_to_move = str(self.side_combo.currentData() or "w")
        entry.fullmove_number = max(1, int(self.move_spin.value()))
        entry.include_lichess_link = self.lichess_combo.currentData()
        entry.border_width_pt = float(self.border_spin.value())

        self._update_details(entry)
        self._pending_edit_key = key
        self._edit_timer.start()

    def _flush_edit(self, render: bool = True) -> None:
        """Entrega a edição pendente: um sinal para fora, um render aqui."""
        self._edit_timer.stop()
        key = self._pending_edit_key
        self._pending_edit_key = None
        if key is None:
            return
        self.entry_edited.emit(key[0], key[1])
        if render and key == self._current_key():
            self.refresh_now()

    def _discard_pending_edit(self) -> None:
        """Esquece a edição pendente sem avisar ninguém.

        Só para quando as listas trocam por baixo (ver `rebind`): ali o objeto que
        a edição tocou deixou de pertencer ao app, e anunciá-lo mandaria a janela
        principal comitar um índice que agora aponta para outro diagrama.
        """
        self._edit_timer.stop()
        self._pending_edit_key = None

    def _activate_current(self) -> None:
        key = self._current_key()
        if key is None or self._entry_at(key) is None:
            return
        self.entry_activated.emit(key[0], key[1])

    def rebind(
        self,
        operations: Sequence[OverlayOperation],
        candidates: Sequence[OverlayOperation] = (),
        erase_operations: Optional[Sequence[EraseOperation]] = None,
    ) -> None:
        """Reaponta a janela para as listas atuais da janela principal.

        Desfazer não muta as listas: ele as **substitui** por cópias restauradas
        do histórico (§22.3). As referências guardadas aqui viram órfãs, e
        continuar editando por elas escreveria num objeto que ninguém mais lê — a
        edição sumiria sem erro nenhum, que é o pior jeito de sumir.

        A posição é preservada pela chave, não pelo número: um desfazer que
        removeu o diagrama 4 faz o 7 virar o 6, e ficar no "sétimo" seria ficar
        noutro diagrama.
        """
        self._discard_pending_edit()
        self._operations = operations
        self._candidates = candidates
        if erase_operations is not None:
            self._erase_operations = erase_operations
        key = self._current_key()
        self._items = build_items(operations, candidates)
        self._position = self._position_of(key, fallback=self._position)
        self._show_current()

    # -- render --------------------------------------------------------

    def refresh_now(self) -> None:
        """Renderiza o par do diagrama corrente, um item só."""
        self.stop_worker()
        item = self._current_item()
        if item is None or self._entry_at(item.key) is None:
            self.before_after.set_message(
                "Nenhum diagrama para mostrar."
                if not self._items
                else "Este diagrama saiu da lista. Feche e abra o navegador de novo."
            )
            self.status_label.setText("")
            return

        worker = GalleryWorker(
            self._pdf_path,
            [item],
            self._operations,
            self._candidates,
            erase_operations=self._erase_operations,
            whiteout=self._whiteout,
            include_lichess_link=self._global_lichess,
            erase_coordinates=self._erase_coordinates,
            zoom=NAV_ZOOM,
            margin_ratio=NAV_MARGIN_RATIO,
            parent=self,
        )
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.item_failed.connect(self._on_item_failed)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        self.status_label.setText("Renderizando o diagrama...")
        worker.start()

    def _on_thumbnail_ready(self, key: object, before_png: bytes, after_png: bytes) -> None:
        chave = tuple(key) if isinstance(key, (list, tuple)) else key
        # Um sinal já enfileirado sobrevive ao `cancel()`. Sem esta comparação,
        # sair correndo com Alt+→ pintaria o diagrama anterior por cima do atual.
        if chave != self._current_key():
            return
        self.before_after.set_images(bytes(before_png), bytes(after_png))

    def _on_item_failed(self, key: object, message: str) -> None:
        del key
        logger.warning("Render do navegador falhou: %s", message)
        self.status_label.setText(f"Não foi possível renderizar este diagrama: {message}")

    def _on_completed(self, canceled: bool) -> None:
        self._worker = None
        if not canceled:
            self.status_label.setText("")

    def stop_worker(self) -> None:
        """Para o render e espera a thread sair — a lição do `closeEvent` do
        Sprint 5.1: uma `QThread` viva mexendo em diálogo destruído derruba o app."""
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        if not worker.wait(5000):  # pragma: no cover - só num render patológico
            logger.warning("Worker do navegador não terminou em 5s; encerrando à força")
            worker.terminate()
            worker.wait(1000)
        self._worker = None

    def _finish(self) -> None:
        # A edição pendente não pode morrer com a janela: ela já está no objeto,
        # e sem o sinal a janela principal nunca a comitaria no histórico — um
        # `Ctrl+Z` depois desfaria a ação anterior e deixaria esta de pé.
        self._flush_edit(render=False)
        self.stop_worker()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._finish()
        super().closeEvent(event)

    def reject(self) -> None:
        self._finish()
        super().reject()
