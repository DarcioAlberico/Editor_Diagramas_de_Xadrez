from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import legality, local_ocr
from .autosave import (
    DEFAULT_INTERVAL_SEC,
    MIN_INTERVAL_SEC,
    autosave_path_for_pdf,
    is_autosave_path,
    write_project_atomically,
)
from .diagram_export import (
    DEFAULT_FORMAT as DEFAULT_DIAGRAM_FORMAT,
    DEFAULT_SIZE_PX as DEFAULT_DIAGRAM_SIZE_PX,
    FORMATS as DIAGRAM_FORMATS,
    INDEX_NAME,
    normalize_format as normalize_diagram_format,
)
from .feedback import export_training_samples
from .gallery import KIND_CANDIDATE, KIND_OPERATION, GalleryDialog
from .fen import extract_piece_placement, normalize_piece_placement, validate_piece_placement
from .history import ChangeHistory
from .logging_config import get_logger, log_file_path, setup_logging
from .navigator import DiagramNavigatorDialog
from .ocr_api import default_endpoint
from .ocr_workflow import RecognitionMixin
from .orientation import auto_orient
from .pdf_service import (
    PdfService,
    RenderedPage,
    clear_board_render_cache,
)
from .project_diff import diff_files, format_diff
from .project_state import (
    ProjectSchemaError,
    ProjectState,
    fingerprint_file,
    load_project_state_with_report,
)
from .recognition import (
    default_engine_mode,
    ENGINE_LABELS,
    ENGINE_LOCAL,
    ENGINE_MODES,
    ENGINE_REMOTE,
    REINFORCE_BELOW_CONFIDENCE,
    make_engine,
    mode_uses_network,
    normalize_mode,
)
from .report import export_report
from .study_panel import StudyDialog, StudyPanel
from .study_workflow import StudyWorkflowMixin
from .style_batch import StyleBatchDialog, StyleProposal, count_affected
from .theme import (
    CONTEXT_STYLE,
    DESTRUCTIVE_BUTTON_STYLE,
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    SECTION_STYLE,
    comment_highlight_colors,
    is_dark_theme,
    warning_text_color,
)
from .types import EraseOperation, OcrBoardResult, OverlayOperation, StudyPosition
from .widgets import BeforeAfterWidget, BoardEditorWidget, SelectablePageWidget, board_transform_icon
from .workers import BatchOcrWorker, DiagramExportWorker, ExportWorker

logger = get_logger("app")


# `is_dark_theme`, as cores semânticas e o QSS reutilizado saíram para `theme.py`;
# `StudyPanel`/`StudyDialog` para `study_panel.py` (§22.3). Continuam reexportados
# daqui porque `chess_pdf_editor.app.StudyPanel` já era o caminho público.
__all__ = [
    "MainWindow",
    "StudyDialog",
    "StudyPanel",
    "comment_highlight_colors",
    "is_dark_theme",
    "main",
    "warning_text_color",
]


class MainWindow(RecognitionMixin, StudyWorkflowMixin, QtWidgets.QMainWindow):
    # Os valores vivem em `theme.py`; aqui ficam só os nomes antigos, para as ~30
    # referências `self._CONTEXT_STYLE` continuarem valendo sem uma passada de
    # renomeação que engordaria o diff do refactor sem mudar nada.
    _PRIMARY_BUTTON_STYLE = PRIMARY_BUTTON_STYLE
    _DESTRUCTIVE_BUTTON_STYLE = DESTRUCTIVE_BUTTON_STYLE
    _SECONDARY_BUTTON_STYLE = SECONDARY_BUTTON_STYLE
    _CONTEXT_STYLE = CONTEXT_STYLE
    _SECTION_STYLE = SECTION_STYLE

    def __init__(self, settings: Optional[QtCore.QSettings] = None) -> None:
        super().__init__()
        self.setWindowTitle("Chess PDF Editor")
        self.resize(1500, 900)
        # Injetavel (§22.4): um teste passa um QSettings apontando para um .ini
        # descartavel em vez de mexer nas preferencias reais do usuario.
        self.settings = settings or QtCore.QSettings("ChessPdfEditor", "ChessPdfEditor")

        self.pdf_service: Optional[PdfService] = None
        self.current_pdf_path: Optional[str] = None
        self.current_render: Optional[RenderedPage] = None
        self.current_preview_render: Optional[RenderedPage] = None
        self.current_page = 0
        self.ocr_full_next_page = 0
        self.project_path: Optional[str] = None
        self.operations: list[OverlayOperation] = []
        self.erase_operations: list[EraseOperation] = []
        self.study_positions: list[StudyPosition] = []
        self.candidates: list[OverlayOperation] = []
        # Area que originou a posicao carregada no editor. Sem isso a previa
        # desenharia a FEN do diagrama anterior sobre uma selecao nova.
        self._position_anchor: Optional[tuple[int, tuple[float, float, float, float]]] = None
        self._loading_ui = False
        self._syncing_fen_tab = False
        self._syncing_study_positions = False
        self._last_ocr_result: Optional[OcrBoardResult] = None
        # (posicao antes, posicao aplicada) do ultimo `Auto-orientar`, para o mesmo
        # atalho poder desfazer uma decisao que a heuristica errou (§42).
        self._auto_orient_undo: Optional[tuple[str, str]] = None
        self.study_dialog: Optional[StudyDialog] = None
        self.gallery_dialog: Optional[GalleryDialog] = None
        self.navigator_dialog: Optional[DiagramNavigatorDialog] = None
        self.project_diff_dialog: Optional[QtWidgets.QDialog] = None

        # Undo/redo do modo edicao (Sprint 5.2). O modo Estudo tem o seu proprio,
        # dentro do StudyBoardWidget.
        self.history = ChangeHistory()
        self._restoring_history = False
        # Mudanca de padding/borda vem em rajada (cada passo do spinbox e um
        # sinal); um unico commit atrasado vira uma entrada so no historico.
        self._style_history_timer = QtCore.QTimer(self)
        self._style_history_timer.setSingleShot(True)
        self._style_history_timer.setInterval(600)
        self._style_history_timer.timeout.connect(
            lambda: self._commit_history("Aparência das substituições")
        )

        # Autosave (Sprint 5.3).
        self._autosave_path: Optional[str] = None
        self._autosave_dirty = False
        self.autosave_enabled = bool(self.settings.value("autosave_enabled", True, bool))
        self.autosave_interval_sec = max(
            MIN_INTERVAL_SEC,
            int(self.settings.value("autosave_interval_sec", DEFAULT_INTERVAL_SEC, int) or DEFAULT_INTERVAL_SEC),
        )
        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setInterval(self.autosave_interval_sec * 1000)
        self._autosave_timer.timeout.connect(self._on_autosave_timeout)

        # Trabalho em segundo plano (Sprint 5.1).
        self._ocr_worker: Optional[BatchOcrWorker] = None
        self._ocr_progress: Optional[QtWidgets.QProgressDialog] = None
        self._ocr_batch: dict = {}
        self._export_worker: Optional[ExportWorker] = None
        self._export_progress: Optional[QtWidgets.QProgressDialog] = None
        self._diagram_export_worker: Optional[DiagramExportWorker] = None
        self._diagram_export_progress: Optional[QtWidgets.QProgressDialog] = None

        # Previa ao vivo: a pagina pode exibir o PDF original ou o resultado das
        # alteracoes pendentes (incluindo a substituicao ainda nao confirmada).
        self.preview_result_enabled = bool(self.settings.value("preview_result_enabled", False, bool))
        self._showing_preview = False
        self._refreshing_view = False
        self.act_toggle_preview = QtGui.QAction("Prévia do resultado", self)
        self.act_toggle_preview.setCheckable(True)
        self.act_toggle_preview.setShortcut(QtGui.QKeySequence("Ctrl+D"))
        self.act_toggle_preview.setToolTip(
            "Mostra a página como ela ficará depois das alterações (Ctrl+D)"
        )
        self.act_toggle_preview.setChecked(self.preview_result_enabled)
        self.act_toggle_preview.toggled.connect(self._on_toggle_preview)

        # Comparacao "cortina" (Sprint 9.7). A previa cheia responde "como vai
        # ficar"; a cortina responde "o que mudou", que e outra pergunta: com a
        # pagina inteira trocada de uma vez, o olho nao acha a diferenca.
        self.compare_curtain_enabled = bool(
            self.settings.value("compare_curtain_enabled", False, bool)
        )
        self._curtain_active = False
        self.curtain_fraction = self._clamp_curtain_fraction(
            self.settings.value("compare_curtain_fraction", 0.5, float)
        )
        self.act_toggle_curtain = QtGui.QAction("Comparar com cortina", self)
        self.act_toggle_curtain.setCheckable(True)
        self.act_toggle_curtain.setShortcut(QtGui.QKeySequence("Ctrl+Shift+D"))
        self.act_toggle_curtain.setToolTip(
            "Arraste uma linha sobre a página: original de um lado, resultado do "
            "outro (Ctrl+Shift+D)"
        )
        self.act_toggle_curtain.setChecked(self.compare_curtain_enabled)
        self.act_toggle_curtain.toggled.connect(self._on_toggle_curtain)

        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self._refresh_result_preview)

        self.page_widget = SelectablePageWidget()
        self.page_widget.selection_changed.connect(self._on_selection_changed)
        self.page_widget.point_clicked.connect(self._on_page_clicked)
        self.page_widget.curtain_moved.connect(self._on_curtain_moved)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.page_widget)

        self.board_editor = BoardEditorWidget()
        self.board_editor.board_changed.connect(self._on_board_changed)
        self.fen_edit = QtWidgets.QLineEdit()
        self.fen_edit.setPlaceholderText("piece placement FEN (ex.: 8/8/8/8/8/8/8/8)")
        self.fen_edit.editingFinished.connect(self._on_fen_edited)

        self.warnings = QtWidgets.QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"color: {warning_text_color()};")

        # O endpoint vinha hardcoded aqui E em ocr_api.py (§22.4). Agora o padrao
        # tem um dono so, o usuario pode trocar, e a escolha sobrevive ao fechar.
        saved_endpoint = (self.settings.value("ocr_endpoint", "", str) or "").strip()
        self.endpoint_edit = QtWidgets.QLineEdit(saved_endpoint or default_endpoint())
        self.endpoint_edit.setPlaceholderText(default_endpoint())
        self.endpoint_edit.setToolTip(
            "Serviço de reconhecimento. Vazio usa o padrão "
            f"({default_endpoint()}); a variável CHESS_OCR_ENDPOINT tem precedência sobre o padrão."
        )
        self.endpoint_edit.editingFinished.connect(self._on_endpoint_edited)

        # Motor de reconhecimento (Sprint 7). O padrão é o híbrido: reconhece na
        # máquina e só recorre ao serviço externo onde a confiança local ficar baixa.
        self.engine_combo = QtWidgets.QComboBox()
        for mode in ENGINE_MODES:
            self.engine_combo.addItem(ENGINE_LABELS[mode], mode)
        saved_engine = normalize_mode(
            self.settings.value("recognition_engine", default_engine_mode(), str)
        )
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(saved_engine)))
        self.engine_combo.currentIndexChanged.connect(lambda _index: self._on_engine_mode_changed())
        self.engine_status_label = QtWidgets.QLabel("")
        self.engine_status_label.setWordWrap(True)
        self.engine_status_label.setStyleSheet(self._CONTEXT_STYLE)
        self.local_model_edit = QtWidgets.QLineEdit(
            (self.settings.value("local_model_path", "", str) or "").strip()
        )
        self.local_model_edit.setPlaceholderText(str(local_ocr.bundled_model_path()))
        self.local_model_edit.setToolTip(
            "Classificador local (.pt). Vazio usa o modelo distribuído com o app; a "
            f"variável {local_ocr.MODEL_ENV_VAR} tem precedência."
        )
        self.local_model_edit.editingFinished.connect(self._on_local_model_edited)
        self.btn_select_local_model = QtWidgets.QPushButton("Selecionar modelo...")
        self.btn_select_local_model.clicked.connect(self._select_local_model)

        self.whiteout_check = QtWidgets.QCheckBox("Aplicar whiteout antes do overlay")
        self.whiteout_check.setChecked(True)
        self.whiteout_check.toggled.connect(lambda checked: self._schedule_preview_refresh(immediate=True))

        # Detecção por clique único (§38). Ligada por padrão: o clique que não acerta
        # diagrama nenhum já limpava a seleção, então achar a borda ali é melhor que
        # o que acontecia antes. Desligável para quem prefere só arrastar.
        self.click_detects_diagram = bool(
            self.settings.value("click_detects_diagram", True, bool)
        )
        self.click_detects_check = QtWidgets.QCheckBox("Clique único detecta o diagrama")
        self.click_detects_check.setChecked(self.click_detects_diagram)
        self.click_detects_check.setToolTip(
            "Clicar dentro de um tabuleiro na página seleciona as bordas dele, sem "
            "precisar arrastar. Precisa do detector local (OpenCV)."
        )
        self.click_detects_check.toggled.connect(self._on_click_detects_toggled)
        # "Por padrão" e não "incluir": desde a §52 cada diagrama pode ter a sua
        # própria escolha, e esta caixa manda apenas em quem não escolheu. Deixar o
        # rótulo antigo faria dela uma promessa que ela deixou de poder cumprir —
        # desmarcá-la não tira o link de um diagrama que pediu para tê-lo.
        self.include_lichess_link_check = QtWidgets.QCheckBox("Link Lichess por padrão")
        self.include_lichess_link_check.setToolTip(
            "Vale para os diagramas sem escolha própria.\n"
            "Para decidir um a um, use o rodapé da galeria (Ctrl+G)."
        )
        self.include_lichess_link_check.setChecked(bool(self.settings.value("include_lichess_link", True, bool)))
        self.include_lichess_link_check.toggled.connect(
            lambda checked: self.settings.setValue("include_lichess_link", bool(checked))
        )
        self.include_lichess_link_check.toggled.connect(
            lambda checked: self._schedule_preview_refresh(immediate=True)
        )

        # As coordenadas do diagrama ORIGINAL (a-h/1-8) ficam fora do whiteout e
        # sobrevivem à substituição, emoldurando o diagrama novo com as letrinhas
        # do antigo. Até aqui a saída era um apagamento manual por diagrama.
        self.erase_coordinates_check = QtWidgets.QCheckBox(
            "Apagar coordenadas do diagrama original"
        )
        self.erase_coordinates_check.setChecked(
            bool(self.settings.value("erase_coordinates", False, bool))
        )
        self.erase_coordinates_check.setToolTip(
            "Apaga as letras e números que o livro imprimiu em volta do tabuleiro. "
            "Só apaga onde encontra uma fileira delas, para não comer a legenda."
        )
        self.erase_coordinates_check.toggled.connect(
            lambda checked: self.settings.setValue("erase_coordinates", bool(checked))
        )
        self.erase_coordinates_check.toggled.connect(
            lambda checked: self._schedule_preview_refresh(immediate=True)
        )

        self.before_after = BeforeAfterWidget()
        self.btn_toggle_preview = QtWidgets.QPushButton("Ver resultado na página")
        self.btn_toggle_preview.setCheckable(True)
        self.btn_toggle_preview.setToolTip(
            "Alterna entre o PDF original e o resultado das alterações (Ctrl+D)"
        )
        self.btn_toggle_preview.setChecked(self.preview_result_enabled)
        self.btn_toggle_preview.toggled.connect(self.act_toggle_preview.setChecked)
        self.act_toggle_preview.toggled.connect(self.btn_toggle_preview.setChecked)

        self.btn_toggle_curtain = QtWidgets.QPushButton("Comparar com cortina")
        self.btn_toggle_curtain.setCheckable(True)
        self.btn_toggle_curtain.setToolTip(self.act_toggle_curtain.toolTip())
        self.btn_toggle_curtain.setChecked(self.compare_curtain_enabled)
        self.btn_toggle_curtain.toggled.connect(self.act_toggle_curtain.setChecked)
        self.act_toggle_curtain.toggled.connect(self.btn_toggle_curtain.setChecked)
        self.merida_font_edit = QtWidgets.QLineEdit()
        self.merida_font_edit.setPlaceholderText("Caminho da fonte Merida (.ttf/.otf)")
        self.btn_select_merida = QtWidgets.QPushButton("Selecionar Fonte...")
        self.btn_select_merida.clicked.connect(self._select_merida_font)
        self.btn_clear_merida = QtWidgets.QPushButton("Limpar")
        self.btn_clear_merida.clicked.connect(self._clear_merida_font)

        # Substituicao/apagamento em foco. Antes isso vivia em QListWidgets que
        # nunca chegaram a ser exibidos depois da unificacao em "Alterações".
        self._current_operation_index: Optional[int] = None
        self._current_eraser_index: Optional[int] = None

        self.changes_list = QtWidgets.QListWidget()
        self.changes_list.currentItemChanged.connect(self._on_change_selected)
        self.changes_list.itemDoubleClicked.connect(self._on_change_double_clicked)
        self.changes_list_delete_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Delete), self.changes_list)
        self.changes_list_delete_shortcut.activated.connect(self._remove_selected_change)
        self.fen_side_combo = QtWidgets.QComboBox()
        self.fen_side_combo.addItem("Brancas", "w")
        self.fen_side_combo.addItem("Pretas", "b")
        self.fen_side_combo.currentIndexChanged.connect(self._on_fen_meta_changed)
        self.fen_move_spin = QtWidgets.QSpinBox()
        self.fen_move_spin.setRange(1, 500)
        self.fen_move_spin.setValue(1)
        self.fen_move_spin.valueChanged.connect(self._on_fen_meta_changed)
        self.study_positions_list = QtWidgets.QListWidget()
        self.study_positions_list.itemDoubleClicked.connect(self._on_study_position_double_clicked)
        self.study_positions_list.currentItemChanged.connect(self._on_study_position_selected)
        self.study_comment_target_label = QtWidgets.QLabel("Comentando: selecione uma posição de estudo")
        self.study_comment_target_label.setWordWrap(True)
        self.study_comment_target_label.setStyleSheet(self._CONTEXT_STYLE)
        self.study_comment_before_edit = QtWidgets.QPlainTextEdit()
        self.study_comment_before_edit.setPlaceholderText("Texto antes do lance selecionado")
        self.study_comment_before_edit.setMaximumHeight(78)
        self.study_comment_before_edit.textChanged.connect(self._on_study_comment_changed)
        self.study_comment_after_edit = QtWidgets.QPlainTextEdit()
        self.study_comment_after_edit.setPlaceholderText("Texto depois do lance selecionado")
        self.study_comment_after_edit.setMaximumHeight(78)
        self.study_comment_after_edit.textChanged.connect(self._on_study_comment_changed)

        # Texto curto porque rótulo de `QCheckBox` **não quebra linha**: o mínimo
        # dele é a frase inteira, e a frase inteira ("Aplicar automaticamente ao
        # reconhecer página/PDF") pedia 600 px num painel de 598. O que sobra
        # disso não é um rótulo apertado, é uma barra de rolagem horizontal na aba.
        # O que se perdeu do texto está na dica, que é onde cabe.
        self.auto_apply_check = QtWidgets.QCheckBox("Aplicar sem conferir")
        self.auto_apply_check.setChecked(bool(self.settings.value("auto_apply_recognition", False, bool)))
        self.auto_apply_check.setToolTip(
            "Ligado: reconhecer página ou PDF aplica as detecções direto.\n"
            "Desligado: elas entram na fila de conferência para você revisar antes."
        )
        self.auto_apply_check.toggled.connect(self._on_auto_apply_toggled)

        # Cópia em JSON de cada reconhecimento, ao lado do PDF (§55). Ligada por
        # padrão: o custo é um arquivo por reconhecimento, e o que ela evita é
        # perder de vez um lote de oito minutos. Desligável para quem trabalha
        # sobre pasta de rede ou só de leitura.
        self.save_recognition_json_check = QtWidgets.QCheckBox(
            "Salvar JSON de cada reconhecimento"
        )
        self.save_recognition_json_check.setChecked(
            bool(self.settings.value("save_recognition_snapshot", True, bool))
        )
        self.save_recognition_json_check.setToolTip(
            "Grava, na pasta do PDF, um arquivo por reconhecimento — com data e hora "
            "no nome, sem sobrescrever o anterior.\n"
            "É um projeto: para recuperar, use Arquivo > Carregar projeto."
        )
        self.save_recognition_json_check.toggled.connect(
            self._on_save_recognition_json_toggled
        )

        self.candidates_list = QtWidgets.QListWidget()
        self.candidates_list.currentItemChanged.connect(self._on_candidate_selected)
        self.candidates_list.itemDoubleClicked.connect(self._on_candidate_double_clicked)
        self.candidates_apply_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Return), self.candidates_list
        )
        self.candidates_apply_shortcut.activated.connect(self._apply_selected_candidate)
        self.candidates_discard_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Delete), self.candidates_list
        )
        self.candidates_discard_shortcut.activated.connect(self._discard_selected_candidate)
        self.btn_apply_candidate = QtWidgets.QPushButton("Aplicar")
        self.btn_apply_candidate.clicked.connect(self._apply_selected_candidate)
        self.btn_discard_candidate = QtWidgets.QPushButton("Descartar")
        self.btn_discard_candidate.clicked.connect(self._discard_selected_candidate)
        self.btn_apply_all_candidates = QtWidgets.QPushButton("Aplicar todos")
        self.btn_apply_all_candidates.clicked.connect(self._apply_all_candidates)
        self.btn_discard_all_candidates = QtWidgets.QPushButton("Descartar todos")
        self.btn_discard_all_candidates.clicked.connect(self._discard_all_candidates)

        # Fila de revisão (Sprint 9.1). Reconhecer um livro de 898 páginas passou a
        # levar 8,5 min; conferir os ~2.000 candidatos um a um é que virou o gargalo.
        # A confiança por tabuleiro — que só existe desde o Sprint 7 — permite atacar
        # primeiro o que tem chance real de estar errado.
        self.candidates_only_uncertain = QtWidgets.QCheckBox("Só leituras incertas")
        self.candidates_only_uncertain.setChecked(
            bool(self.settings.value("candidates_only_uncertain", False, bool))
        )
        self.candidates_only_uncertain.setToolTip(
            "Esconde os candidatos cuja confiança está acima do limiar. "
            "Confiança desconhecida conta como incerta."
        )
        self.candidates_only_uncertain.toggled.connect(self._on_candidate_filter_changed)
        self.candidates_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.candidates_threshold_spin.setRange(0.0, 1.0)
        self.candidates_threshold_spin.setSingleStep(0.05)
        self.candidates_threshold_spin.setDecimals(2)
        self.candidates_threshold_spin.setPrefix("< ")
        self.candidates_threshold_spin.setValue(
            float(self.settings.value("candidates_threshold", REINFORCE_BELOW_CONFIDENCE, float))
        )
        self.candidates_threshold_spin.setToolTip(
            "Abaixo desta confiança o candidato é considerado incerto."
        )
        self.candidates_threshold_spin.valueChanged.connect(
            lambda _value: self._on_candidate_filter_changed()
        )
        self.candidates_worst_first = QtWidgets.QCheckBox("Mais incertos primeiro")
        self.candidates_worst_first.setChecked(
            bool(self.settings.value("candidates_worst_first", False, bool))
        )
        self.candidates_worst_first.setToolTip(
            "Ordena por confiança crescente em vez da ordem das páginas."
        )
        self.candidates_worst_first.toggled.connect(self._on_candidate_filter_changed)

        self.btn_snap = QtWidgets.QPushButton("Ajustar seleção à borda")
        self.btn_snap.setToolTip(
            "Encosta a seleção nas bordas reais do tabuleiro (Ctrl+B). "
            "Precisa do motor local instalado."
        )
        self.btn_snap.clicked.connect(self._snap_selection_to_board)
        self.btn_ocr = QtWidgets.QPushButton("Reconhecer seleção")
        self.btn_ocr.clicked.connect(self._recognize_selection)
        self.btn_ocr_page = QtWidgets.QPushButton("Reconhecer página")
        self.btn_ocr_page.clicked.connect(self._recognize_current_page)
        self.btn_ocr_full = QtWidgets.QPushButton("Detectar no PDF")
        self.btn_ocr_full.clicked.connect(self._recognize_full_pdf)
        self.btn_add = QtWidgets.QPushButton("Adicionar substituição")
        self.btn_add.clicked.connect(self._add_operation)
        self.pad_left_spin = QtWidgets.QDoubleSpinBox()
        self.pad_left_spin.setRange(0.0, 30.0)
        self.pad_left_spin.setSingleStep(0.5)
        self.pad_left_spin.setDecimals(1)
        self.pad_left_spin.setSuffix(" pt")
        self.pad_left_spin.setValue(1.5)
        self.pad_left_spin.valueChanged.connect(self._on_operation_style_changed)
        self.pad_top_spin = QtWidgets.QDoubleSpinBox()
        self.pad_top_spin.setRange(0.0, 30.0)
        self.pad_top_spin.setSingleStep(0.5)
        self.pad_top_spin.setDecimals(1)
        self.pad_top_spin.setSuffix(" pt")
        self.pad_top_spin.setValue(1.5)
        self.pad_top_spin.valueChanged.connect(self._on_operation_style_changed)
        self.pad_right_spin = QtWidgets.QDoubleSpinBox()
        self.pad_right_spin.setRange(0.0, 30.0)
        self.pad_right_spin.setSingleStep(0.5)
        self.pad_right_spin.setDecimals(1)
        self.pad_right_spin.setSuffix(" pt")
        self.pad_right_spin.setValue(1.5)
        self.pad_right_spin.valueChanged.connect(self._on_operation_style_changed)
        self.pad_bottom_spin = QtWidgets.QDoubleSpinBox()
        self.pad_bottom_spin.setRange(0.0, 30.0)
        self.pad_bottom_spin.setSingleStep(0.5)
        self.pad_bottom_spin.setDecimals(1)
        self.pad_bottom_spin.setSuffix(" pt")
        self.pad_bottom_spin.setValue(1.5)
        self.pad_bottom_spin.valueChanged.connect(self._on_operation_style_changed)
        self.op_border_spin = QtWidgets.QDoubleSpinBox()
        self.op_border_spin.setRange(0.0, 12.0)
        self.op_border_spin.setSingleStep(0.25)
        self.op_border_spin.setDecimals(2)
        self.op_border_spin.setSuffix(" pt")
        self.op_border_spin.setValue(0.0)
        self.op_border_spin.valueChanged.connect(self._on_operation_style_changed)
        self.apply_style_all_check = QtWidgets.QCheckBox("Aplicar em todas as substituições")
        self.apply_style_all_check.setChecked(True)
        self.apply_style_all_check.setToolTip(
            "Ligado, mexer no padding ou na borda reescreve o estilo de todas as "
            "substituições na hora. Para ver o efeito no livro antes de decidir, use "
            "«Experimentar em todas...»."
        )
        self.btn_style_batch = QtWidgets.QPushButton("Experimentar em todas...")
        self.btn_style_batch.setToolTip(
            "Grade de miniaturas com o estilo atual e o proposto, em diagramas de "
            "todo o livro. Nada muda até você aplicar."
        )
        self.btn_style_batch.clicked.connect(self._open_style_batch_dialog)
        self.lichess_link_label = QtWidgets.QLabel()
        self.lichess_link_label.setTextFormat(QtCore.Qt.RichText)
        self.lichess_link_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.lichess_link_label.setOpenExternalLinks(True)
        self.lichess_link_label.setToolTip("Abrir posição atual no Lichess")
        self.btn_add_eraser = QtWidgets.QPushButton("Adicionar apagamento")
        self.btn_add_eraser.clicked.connect(self._add_eraser_from_selection)
        self.btn_remove = QtWidgets.QPushButton("Remover")
        self.btn_remove.clicked.connect(self._remove_selected_change)
        self.btn_clear = QtWidgets.QPushButton("Limpar")
        self.btn_clear.clicked.connect(self._clear_changes)

        self.btn_study_selection = QtWidgets.QPushButton("Estudar seleção")
        self.btn_study_selection.clicked.connect(self._study_selection)
        self.btn_study_initial = QtWidgets.QPushButton("Partida inicial")
        self.btn_study_initial.clicked.connect(self._study_starting_position)
        self.btn_save_study_line = QtWidgets.QPushButton("Atualizar linha")
        self.btn_save_study_line.clicked.connect(self._save_current_study_line)
        self.btn_pdf_text_to_before = QtWidgets.QPushButton("Texto -> antes")
        self.btn_pdf_text_to_before.clicked.connect(lambda: self._copy_pdf_text_to_study_comment("before"))
        self.btn_pdf_text_to_after = QtWidgets.QPushButton("Texto -> depois")
        self.btn_pdf_text_to_after.clicked.connect(lambda: self._copy_pdf_text_to_study_comment("after"))
        self.btn_remove_study_position = QtWidgets.QPushButton("Remover posição")
        self.btn_remove_study_position.clicked.connect(self._remove_selected_study_position)

        # Os quatro comandos do tabuleiro numa linha só (antes: três linhas,
        # 72 px). Os rótulos inteiros não cabem — somam 794 px e a linha tem 422
        # na largura mínima do painel —, então os três mecânicos viram ícone e
        # `Auto-orientar` fica com o texto. A escolha de qual manter não é
        # arbitrária: girar e espelhar o usuário confere olhando o tabuleiro, e
        # auto-orientar é o único que decide sozinho, ou seja, o único que
        # ninguém adivinha por um desenho.
        self.btn_rotate = QtWidgets.QPushButton()
        self.btn_rotate.setIcon(board_transform_icon("rotate"))
        self.btn_rotate.setToolTip("Rotacionar 90° (Ctrl+R)")
        self.btn_rotate.clicked.connect(self.board_editor.rotate_clockwise)
        self.btn_flip = QtWidgets.QPushButton()
        self.btn_flip.setIcon(board_transform_icon("flip"))
        self.btn_flip.setToolTip("Espelhar vertical (Ctrl+M)")
        self.btn_flip.clicked.connect(self.board_editor.flip_vertical)
        self.btn_auto_orient = QtWidgets.QPushButton("Auto-orientar")
        self.btn_auto_orient.setToolTip(
            "Testa as 4 rotações e aplica a mais plausível (reis, peões e sentido do avanço). Ctrl+Shift+R"
        )
        self.btn_auto_orient.clicked.connect(self._auto_orient_position)
        self.btn_clear_board = QtWidgets.QPushButton()
        self.btn_clear_board.setIcon(board_transform_icon("clear"))
        self.btn_clear_board.setToolTip("Limpar tabuleiro (Ctrl+Shift+L)")
        self.btn_clear_board.clicked.connect(self.board_editor.clear_board)

        # §20.5: comando destrutivo pesa menos que ação principal. Numa lista só,
        # para o conjunto ser auditável — antes cada um destes tinha o mesmo peso
        # visual de `Adicionar substituição`, e nada os distinguia.
        self.destructive_buttons = (
            self.btn_remove,
            self.btn_clear,
            self.btn_discard_candidate,
            self.btn_discard_all_candidates,
            # `Limpar Tabuleiro` entrou aqui junto com a linha única. Nas três
            # linhas ele tinha uma para si e o peso se lia da posição; lado a
            # lado com girar e espelhar, ele passaria a ter o mesmo peso dos
            # outros três — e é o único que joga trabalho fora.
            self.btn_clear_board,
        )
        for button in self.destructive_buttons:
            button.setStyleSheet(self._DESTRUCTIVE_BUTTON_STYLE)

        top_editor = QtWidgets.QWidget()
        top_editor_layout = QtWidgets.QVBoxLayout(top_editor)
        top_editor_layout.addWidget(QtWidgets.QLabel("Editor de Tabuleiro"))
        top_editor_layout.addWidget(self.board_editor, 0, QtCore.Qt.AlignLeft)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.btn_auto_orient)
        controls.addWidget(self.btn_rotate)
        controls.addWidget(self.btn_flip)
        # O vão separa o que constrói do que joga fora. Sem ele os quatro ficam
        # equidistantes e `Limpar` vira o quarto botão de uma série, que é
        # exatamente a leitura que a §41.4 evita.
        controls.addStretch(1)
        controls.addWidget(self.btn_clear_board)
        top_editor_layout.addLayout(controls)
        top_editor_layout.addStretch(1)

        bottom_panel = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom_panel)
        self.edit_tabs = QtWidgets.QTabWidget()
        ocr_tab = QtWidgets.QWidget()
        ocr_tab_layout = QtWidgets.QVBoxLayout(ocr_tab)
        self.edit_context_label = QtWidgets.QLabel("")
        self.edit_context_label.setWordWrap(True)
        self.edit_context_label.setStyleSheet(self._CONTEXT_STYLE)
        ocr_tab_layout.addWidget(self.edit_context_label)

        ocr_tab_layout.addWidget(self._section_label("1 · Reconhecer"))
        ocr_tab_layout.addWidget(self.btn_snap)
        ocr_tab_layout.addWidget(self.btn_ocr)
        ocr_actions = QtWidgets.QHBoxLayout()
        ocr_actions.addWidget(self.btn_ocr_page)
        ocr_actions.addWidget(self.btn_ocr_full)
        ocr_tab_layout.addLayout(ocr_actions)
        # `auto_apply_check` e `engine_status_label` saíram daqui para a aba
        # `Ajustes` (§51.3). Nenhum dos dois é etapa: um é preferência, o outro é
        # um parágrafo de estado que não se lê duas vezes. Juntos custavam 87 px
        # **acima** do passo 5, que é a única altura que conta para a rolagem.

        # A fila de conferência tem aba própria (§51.2): conferir um lote é outra
        # atividade, não o passo 2 de editar um diagrama. Aqui ficava uma seção
        # que aparecia e sumia, empurrando os passos 3, 4 e 5 para baixo quando
        # cheia — a lista sozinha pede 324 px.
        candidates_tab = QtWidgets.QWidget()
        candidates_tab_layout = QtWidgets.QVBoxLayout(candidates_tab)
        candidates_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.candidates_section = QtWidgets.QWidget()
        candidates_tab_layout.addWidget(self.candidates_section)
        candidates_layout = QtWidgets.QVBoxLayout(self.candidates_section)
        candidates_layout.setContentsMargins(0, 0, 0, 0)
        self.candidates_label = self._section_label("Conferir")
        candidates_layout.addWidget(self.candidates_label)
        candidates_filter = QtWidgets.QHBoxLayout()
        candidates_filter.setContentsMargins(0, 0, 0, 0)
        candidates_filter.addWidget(self.candidates_only_uncertain)
        candidates_filter.addWidget(self.candidates_threshold_spin)
        candidates_filter.addStretch(1)
        candidates_layout.addLayout(candidates_filter)
        candidates_layout.addWidget(self.candidates_worst_first)
        self.candidates_list.setMinimumHeight(96)
        candidates_layout.addWidget(self.candidates_list, 1)
        candidate_actions = QtWidgets.QGridLayout()
        candidate_actions.addWidget(self.btn_apply_candidate, 0, 0)
        candidate_actions.addWidget(self.btn_discard_candidate, 0, 1)
        candidate_actions.addWidget(self.btn_apply_all_candidates, 1, 0)
        candidate_actions.addWidget(self.btn_discard_all_candidates, 1, 1)
        candidates_layout.addLayout(candidate_actions)
        # O `candidates_section` continua sendo um widget **dentro** da aba, e não
        # a aba: quem o esconde é o mesmo código de antes (`ocr_workflow`), e com
        # ele fica de pé o contrato que a §29 cobra — o filtro pode esvaziar a
        # lista sem fazer sumir o controle que desliga o filtro. A aba tem a sua
        # própria visibilidade, ligada a haver candidatos (`_sync_candidates_tab`).
        self.candidates_section.setVisible(False)

        preview_layout = QtWidgets.QVBoxLayout()
        preview_layout.addWidget(self.btn_toggle_preview)
        preview_layout.addWidget(self.btn_toggle_curtain)
        preview_layout.addWidget(self.before_after)
        # O fluxo renumerou de 1–5 para 1–4 quando a conferência saiu para a sua
        # aba (§51.2). A alternativa era manter os números antigos e conviver com
        # um buraco no 2, que é pior que qualquer das duas opções: o número de uma
        # etapa serve para dizer *quantas faltam*, e uma sequência furada não diz.
        # `Conferir` fica fora da numeração de propósito — não é uma etapa deste
        # fluxo, é o que se faz depois de um lote.
        self.compare_group = self._make_collapsible_group(
            "2 · Conferir a prévia", preview_layout, checked=True, key="preview"
        )
        self.compare_group.toggled.connect(lambda checked: self._schedule_preview_refresh(immediate=True))
        ocr_tab_layout.addWidget(self.compare_group)

        ocr_tab_layout.addWidget(self._section_label("3 · Aplicar"))
        ocr_tab_layout.addWidget(self.btn_add)
        ocr_tab_layout.addWidget(self.btn_add_eraser)

        self.changes_label = self._section_label("4 · Alterações")
        ocr_tab_layout.addWidget(self.changes_label)
        self.changes_list.setMinimumHeight(110)
        ocr_tab_layout.addWidget(self.changes_list, 3)
        right_actions = QtWidgets.QHBoxLayout()
        right_actions.addWidget(self.btn_remove)
        right_actions.addWidget(self.btn_clear)
        right_actions.addStretch(1)
        ocr_tab_layout.addLayout(right_actions)

        # O grupo `Avançado` inteiro saiu da aba do fluxo para a aba `Ajustes`
        # (§51.3), repartido em dois pelo assunto: o que muda a **aparência** do
        # diagrama gravado e o que configura o **reconhecimento**. Estavam juntos
        # aqui por serem ambos "avançado", que é uma categoria sobre o usuário e
        # não sobre a coisa.

        fens_tab = QtWidgets.QWidget()
        fens_tab_layout = QtWidgets.QVBoxLayout(fens_tab)
        fens_tab_layout.addWidget(self._section_label("FEN"))
        fens_tab_layout.addWidget(self.fen_edit)
        fens_tab_layout.addWidget(self.warnings)
        # A `fen_ops_list` saiu (§51.4). Ela era uma segunda vista da mesma lista
        # de operações que a `changes_list` já mostra — com a sua própria seleção,
        # o seu próprio botão de remover e o seu próprio atalho de apagar. A §20.4
        # pedia "lista única de alterações" e só metade tinha sido feita:
        # substituições e apagamentos foram unificados entre si, e esta continuou
        # ao lado. Os campos abaixo agora seguem a seleção da lista única.
        self.fen_meta_label = self._section_label("Substituição selecionada")
        fens_tab_layout.addWidget(self.fen_meta_label)
        fen_meta = QtWidgets.QGridLayout()
        fen_meta.addWidget(QtWidgets.QLabel("Vez de jogar"), 0, 0)
        fen_meta.addWidget(self.fen_side_combo, 0, 1)
        fen_meta.addWidget(QtWidgets.QLabel("Número do lance"), 1, 0)
        fen_meta.addWidget(self.fen_move_spin, 1, 1)
        fens_tab_layout.addLayout(fen_meta)
        fens_tab_layout.addStretch(1)

        whiteout_tab = QtWidgets.QWidget()
        whiteout_tab_layout = QtWidgets.QVBoxLayout(whiteout_tab)
        appearance_grid = QtWidgets.QGridLayout()
        appearance_grid.addWidget(QtWidgets.QLabel("Padding esq."), 0, 0)
        appearance_grid.addWidget(self.pad_left_spin, 0, 1)
        appearance_grid.addWidget(QtWidgets.QLabel("Padding topo"), 1, 0)
        appearance_grid.addWidget(self.pad_top_spin, 1, 1)
        appearance_grid.addWidget(QtWidgets.QLabel("Padding dir."), 2, 0)
        appearance_grid.addWidget(self.pad_right_spin, 2, 1)
        appearance_grid.addWidget(QtWidgets.QLabel("Padding base"), 3, 0)
        appearance_grid.addWidget(self.pad_bottom_spin, 3, 1)
        appearance_grid.addWidget(QtWidgets.QLabel("Borda"), 4, 0)
        appearance_grid.addWidget(self.op_border_spin, 4, 1)
        appearance_grid.addWidget(QtWidgets.QLabel("Análise"), 5, 0)
        appearance_grid.addWidget(self.lichess_link_label, 5, 1)
        appearance_grid.addWidget(self.include_lichess_link_check, 6, 0, 1, 2)
        appearance_grid.addWidget(self.erase_coordinates_check, 7, 0, 1, 2)
        appearance_grid.addWidget(self.apply_style_all_check, 8, 0, 1, 2)
        appearance_grid.addWidget(self.whiteout_check, 10, 0, 1, 2)
        appearance_grid.addWidget(QtWidgets.QLabel("Fonte Merida (.ttf/.otf)"), 11, 0, 1, 2)
        # Campo numa linha e botões na seguinte, e não os três lado a lado: o
        # `Caminho da fonte Merida (.ttf/.otf)` de placeholder mais `Selecionar
        # Fonte...` mais `Limpar` estouram a largura do painel, e o que aparece
        # não é um botão cortado — é uma **barra de rolagem horizontal** na aba
        # inteira, que é o defeito mais caro que um layout pode ter aqui.
        appearance_grid.addWidget(self.merida_font_edit, 12, 0, 1, 2)
        font_buttons = QtWidgets.QHBoxLayout()
        font_buttons.addWidget(self.btn_select_merida)
        font_buttons.addWidget(self.btn_clear_merida)
        font_buttons.addStretch(1)
        appearance_grid.addLayout(font_buttons, 13, 0, 1, 2)
        appearance_grid.addWidget(self.btn_style_batch, 14, 0, 1, 2)
        appearance_group = self._make_collapsible_group(
            "Aparência do diagrama", appearance_grid, checked=False, key="appearance_advanced"
        )
        whiteout_tab_layout.addWidget(appearance_group)

        # O reconhecimento é configuração, não etapa — e não é aparência. Fica na
        # mesma aba porque as duas são "o que eu ajusto uma vez e esqueço", que é
        # o que a aba passou a significar.
        engine_layout = QtWidgets.QVBoxLayout()
        engine_layout.addWidget(self.engine_status_label)
        engine_layout.addWidget(self.auto_apply_check)
        engine_layout.addWidget(self.save_recognition_json_check)
        engine_layout.addWidget(self.click_detects_check)
        engine_layout.addWidget(QtWidgets.QLabel("Motor de reconhecimento"))
        engine_layout.addWidget(self.engine_combo)
        engine_layout.addWidget(QtWidgets.QLabel("Modelo local (.pt)"))
        engine_layout.addWidget(self.local_model_edit)
        model_buttons = QtWidgets.QHBoxLayout()
        model_buttons.addWidget(self.btn_select_local_model)
        model_buttons.addStretch(1)
        engine_layout.addLayout(model_buttons)
        engine_layout.addWidget(QtWidgets.QLabel("Endpoint OCR"))
        engine_layout.addWidget(self.endpoint_edit)
        engine_group = self._make_collapsible_group(
            "Reconhecimento", engine_layout, checked=False, key="ocr_advanced"
        )
        whiteout_tab_layout.addWidget(engine_group)
        whiteout_tab_layout.addStretch(1)

        #: Os grupos de configuração da aba `Ajustes`, que a §20.5 exige recolhidos
        #: por padrão. Declarados aqui em vez de reconhecidos pela palavra
        #: "Avançado" no título: a auditoria os procurava por substring, o que
        #: prendia o critério ao texto do rótulo — renomeá-los fazia o teste passar
        #: a não olhar nada, em silêncio. Note que `compare_group` também é um
        #: grupo recolhível e **não** entra aqui: ele abre expandido de propósito.
        self.settings_groups = (appearance_group, engine_group)

        # Cada aba rola: sem isso o conteudo e comprimido abaixo do minimo e o
        # Qt corta o texto dos botoes e rotulos.
        self.edit_tabs.addTab(self._scrollable(ocr_tab), "Diagrama")
        self._candidates_tab_index = self.edit_tabs.addTab(
            self._scrollable(candidates_tab), "Conferir"
        )
        self.edit_tabs.addTab(self._scrollable(fens_tab), "FEN")
        self.edit_tabs.addTab(self._scrollable(whiteout_tab), "Ajustes")
        # A aba de conferência só existe quando há o que conferir. Sem isto ela
        # seria uma aba permanentemente vazia em quem nunca roda um lote.
        self.edit_tabs.setTabVisible(self._candidates_tab_index, False)
        self.edit_tabs.setMinimumHeight(220)
        bottom_layout.addWidget(self.edit_tabs, 2)

        self.right_vertical_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.right_vertical_splitter.setChildrenCollapsible(False)
        self.right_vertical_splitter.addWidget(top_editor)
        self.right_vertical_splitter.addWidget(bottom_panel)
        self.right_vertical_splitter.setStretchFactor(0, 2)
        self.right_vertical_splitter.setStretchFactor(1, 5)
        # O topo pede o que o editor precisa para a casa em tamanho cheio. O valor
        # fixo de 360 que estava aqui era menor que isso e vinha sendo corrigido
        # em silêncio pelo Qt, que dava ao topo o seu *mínimo* — o que funcionava
        # só enquanto o mínimo era o tamanho cheio. Com o editor adaptativo o
        # mínimo virou a casa pequena, e o pedido baixo passou a encolher o
        # tabuleiro numa tela grande.
        self.right_vertical_splitter.setSizes([360, 560])

        self.study_panel = StudyPanel(self)
        self.study_panel.set_pgn_provider(self._study_export_pgn)
        self.study_panel.study_board.line_changed.connect(lambda san_line, cursor: self._on_study_ply_changed())
        self.study_panel.about_to_change_line.connect(self._flush_current_study_comment)
        self.study_panel.pgn_imported.connect(self._on_study_pgn_imported)
        study_positions_panel = QtWidgets.QWidget()
        study_positions_layout = QtWidgets.QVBoxLayout(study_positions_panel)
        study_positions_layout.addWidget(self._section_label("Posições deste PDF"))
        study_positions_layout.addWidget(self.study_positions_list, 1)
        study_actions = QtWidgets.QGridLayout()
        study_actions.addWidget(self.btn_study_selection, 0, 0)
        study_actions.addWidget(self.btn_study_initial, 0, 1)
        study_actions.addWidget(self.btn_save_study_line, 0, 2)
        study_actions.addWidget(self.btn_pdf_text_to_before, 1, 0)
        study_actions.addWidget(self.btn_pdf_text_to_after, 1, 1)
        study_actions.addWidget(self.btn_remove_study_position, 1, 2)
        for col in range(3):
            study_actions.setColumnStretch(col, 1)
        study_positions_layout.addLayout(study_actions)
        study_positions_layout.addWidget(self.study_comment_target_label)
        study_positions_layout.addWidget(self._section_label("Antes do lance selecionado"))
        study_positions_layout.addWidget(self.study_comment_before_edit)
        study_positions_layout.addWidget(self._section_label("Depois do lance selecionado"))
        study_positions_layout.addWidget(self.study_comment_after_edit)

        self.study_workspace = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.study_workspace.setChildrenCollapsible(False)
        self.study_workspace.addWidget(self.study_panel)
        self.study_workspace.addWidget(study_positions_panel)
        self.study_workspace.setStretchFactor(0, 4)
        self.study_workspace.setStretchFactor(1, 2)
        self.study_workspace.setSizes([620, 360])
        # Antes: `setMinimumHeight(sizeHint().height())`, que dava 1.136 px — mais
        # alto que a janela padrão de 900. O painel nunca cabia, e a área rolável
        # que existe para o caso apertado virava obrigatória o tempo todo. O piso
        # agora é o tabuleiro de estudo mais uma folga; abaixo disso ele rola.
        self.study_workspace.setMinimumHeight(560)

        self.study_scroll = QtWidgets.QScrollArea()
        self.study_scroll.setWidget(self.study_workspace)
        self.study_scroll.setWidgetResizable(True)
        self.study_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.study_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.study_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        self.side_stack = QtWidgets.QStackedWidget()
        self.side_stack.addWidget(self.right_vertical_splitter)
        self.side_stack.addWidget(self.study_scroll)

        # O QScrollArea do PDF usa widgetResizable(False) e por isso reporta um
        # sizeHint minusculo: sem largura minima o splitter o esmagava ate ~58px
        # e o visor de PDF sumia numa instalacao nova.
        scroll.setMinimumWidth(360)
        # O 380 fixo que estava aqui virou uma pergunta ao próprio widget. Com a
        # paleta ao lado do tabuleiro o topo passou a pedir 452 px, e um número
        # escrito à mão teria de ser corrigido junto — o par mantido nos dois
        # lados que a §45 documenta como forma de defeito. Pior: num tema com
        # moldura mais grossa que a desta máquina o número certo é outro, e
        # ninguém estaria aqui para medir. Abaixo do que o editor pede, ele sai
        # cortado.
        self.side_stack.setMinimumWidth(max(380, top_editor.minimumSizeHint().width()))

        self.main_splitter = QtWidgets.QSplitter()
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(scroll)
        self.main_splitter.addWidget(self.side_stack)
        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 2)
        if not self._restore_splitter_state(self.main_splitter, "main_splitter_state"):
            self.main_splitter.setSizes([int(self.width() * 0.6), int(self.width() * 0.4)])
        self._restore_splitter_state(self.right_vertical_splitter, "right_vertical_splitter_state")
        self._restore_splitter_state(self.study_workspace, "study_workspace_splitter_state")
        self.setCentralWidget(self.main_splitter)

        self.page_spin = QtWidgets.QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setValue(1)
        self.page_spin.valueChanged.connect(self._on_page_spin_changed)

        self.zoom_spin = QtWidgets.QDoubleSpinBox()
        self.zoom_spin.setRange(0.5, 6.0)
        self.zoom_spin.setSingleStep(0.25)
        self.zoom_spin.setValue(2.0)
        self.zoom_spin.valueChanged.connect(self._on_zoom_changed)

        self._build_toolbar()
        self._load_merida_font_setting()
        self.statusBar().showMessage("Abra um PDF para iniciar.")
        self._on_board_changed(self.board_editor.piece_placement())
        self._try_restore_last_session()
        self._update_lichess_link()
        self._refresh_study_positions_list()
        self._refresh_changes_list()
        self._refresh_candidates_list()
        self._update_edit_context_state()
        self._update_engine_status_label()
        # A sessao restaurada (se houve) e a linha de base do historico.
        self._reset_history("sessão restaurada" if self.current_pdf_path else "inicio")
        self._start_autosave_timer()

    def _make_collapsible_group(
        self,
        title: str,
        layout: QtWidgets.QLayout,
        checked: bool = False,
        key: Optional[str] = None,
    ) -> QtWidgets.QGroupBox:
        """Grupo recolhível. Com `key`, o estado sobrevive ao fechar o app.

        A persistência existe por uma medição (§41.3): no visor de 1500×900 a aba
        `OCR` pede 743 px e recebe 222, então quem quiser o fluxo básico sem rolagem
        precisa recolher `3 · Conferir a prévia`. Sem lembrar disso, o usuário
        refazia o mesmo clique a cada abertura — e recolher a prévia **por padrão**
        seria esconder justamente o que o app faz de melhor.
        """
        group = QtWidgets.QGroupBox(title)
        group.setCheckable(True)
        if key:
            checked = bool(self.settings.value(self._group_setting(key), checked, bool))
        group.setChecked(checked)
        group.setLayout(layout)
        group.toggled.connect(lambda visible, target_layout=layout: self._set_layout_visible(target_layout, visible))
        if key:
            group.toggled.connect(
                lambda visible, setting=self._group_setting(key): self.settings.setValue(
                    setting, bool(visible)
                )
            )
        self._set_layout_visible(layout, checked)
        return group

    @staticmethod
    def _group_setting(key: str) -> str:
        return f"group_expanded/{key}"

    def _section_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet(self._SECTION_STYLE)
        return label

    @staticmethod
    def _scrollable(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        """Embrulha um painel numa area rolavel que respeita a altura minima."""
        area = QtWidgets.QScrollArea()
        area.setWidget(widget)
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        return area

    @classmethod
    def _set_layout_visible(cls, layout: QtWidgets.QLayout, visible: bool) -> None:
        for idx in range(layout.count()):
            item = layout.itemAt(idx)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setVisible(visible)
            if child_layout is not None:
                cls._set_layout_visible(child_layout, visible)

    def _current_piece_placement_or_none(self) -> Optional[str]:
        text = self.fen_edit.text().strip()
        if not text:
            return None
        try:
            return normalize_piece_placement(extract_piece_placement(text))
        except Exception:
            return None

    def _current_fen_has_pieces(self) -> bool:
        piece_placement = self._current_piece_placement_or_none()
        if not piece_placement:
            return False
        return any(ch in "PNBRQKpnbrqk" for ch in piece_placement)

    def _set_primary_button(self, target: Optional[QtWidgets.QPushButton]) -> None:
        for button in (self.btn_ocr, self.btn_ocr_page, self.btn_ocr_full, self.btn_add, self.btn_add_eraser):
            button.setStyleSheet(self._SECONDARY_BUTTON_STYLE)
        if target is not None:
            target.setStyleSheet(self._PRIMARY_BUTTON_STYLE)

    def _update_edit_context_state(self) -> None:
        if (
            not hasattr(self, "edit_context_label")
            or not hasattr(self, "act_save_pdf")
            # Criada mais tarde que `act_save_pdf`, junto da galeria.
            or not hasattr(self, "act_style_batch")
            or not hasattr(self, "act_export_diagrams")
        ):
            return

        has_pdf = bool(self.pdf_service and self.current_render)
        self.act_toggle_preview.setEnabled(has_pdf)
        self.btn_toggle_preview.setEnabled(has_pdf)
        self.btn_toggle_preview.setText(
            "Voltar ao PDF original"
            if (self._showing_preview and not self._curtain_active)
            else "Ver resultado na página"
        )
        self.act_toggle_curtain.setEnabled(has_pdf)
        self.btn_toggle_curtain.setEnabled(has_pdf)
        self.btn_toggle_curtain.setText(
            "Voltar ao PDF original" if self._curtain_active else "Comparar com cortina"
        )
        has_selection = bool(self.page_widget.selection_rect()) if has_pdf else False
        has_position = self._current_fen_has_pieces()
        has_changes = bool(self.operations or self.erase_operations)

        self.btn_ocr.setEnabled(has_selection)
        self.btn_ocr_page.setEnabled(has_pdf)
        self.btn_ocr_full.setEnabled(has_pdf)
        self.btn_add.setEnabled(has_selection and has_position)
        self.btn_add_eraser.setEnabled(has_selection)
        self.btn_remove.setEnabled(self._selected_change() is not None)
        self.btn_clear.setEnabled(bool(self.operations or self.erase_operations))
        self.btn_style_batch.setEnabled(has_pdf and bool(self.operations))
        self.act_style_batch.setEnabled(has_pdf and bool(self.operations))
        # Exportar diagramas não precisa do PDF aberto — renderiza da FEN — mas
        # precisa de substituições.
        self.act_export_diagrams.setEnabled(bool(self.operations))
        self.act_save_pdf.setEnabled(has_changes)
        self.act_recognize_selection.setEnabled(has_selection)
        self.act_recognize_page.setEnabled(has_pdf)
        self.act_recognize_full.setEnabled(has_pdf)
        self.act_add_operation.setEnabled(has_selection and has_position)

        if not has_pdf:
            self.edit_context_label.setText("Abra um PDF para iniciar o fluxo de edição.")
            self._set_primary_button(None)
            return

        if has_selection and has_position:
            self.edit_context_label.setText(
                "Seleção e posição prontas. Adicione a substituição ou reconheça novamente se quiser revisar o OCR."
            )
            self._set_primary_button(self.btn_add)
            return

        if has_selection:
            self.edit_context_label.setText("Seleção pronta. Reconheça o diagrama ou adicione um apagamento.")
            self._set_primary_button(self.btn_ocr)
            return

        if has_changes:
            self.edit_context_label.setText(
                "Alterações pendentes. Revise a lista ou exporte o PDF quando terminar."
            )
            self._set_primary_button(None)
            return

        # O estado em que o PDF está aberto e ainda não há nada feito era o único
        # do fluxo sem ação principal — o rótulo mandava selecionar um diagrama e
        # nenhum botão se destacava. É justamente aqui que o lote responde melhor
        # que a mão: `Detectar no PDF` acha os diagramas do livro inteiro e enche
        # a fila de conferência (§23), em vez de exigir 898 seleções.
        #
        # `_set_primary_button` já listava os dois botões de lote entre os
        # candidatos desde que foi escrito; o que faltava era um estado elegê-los.
        # Só um de cada vez, que é a regra da §20.5, e o escolhido é o do livro
        # inteiro: quem quer só esta página tem `Reconhecer página` ao lado, com
        # o mesmo tamanho de sempre.
        #
        # O texto é curto de propósito. A primeira versão explicava o lote por
        # extenso, quebrava em duas linhas e custava 28 px — num painel onde o
        # que falta para o fluxo caber em 900 px são 79 (§50.6). O botão em
        # destaque já diz o que ele faz.
        self.edit_context_label.setText("Selecione um diagrama ou detecte todos.")
        self._set_primary_button(self.btn_ocr_full)

    def _icon(self, standard: QtWidgets.QStyle.StandardPixmap) -> QtGui.QIcon:
        """Ícone do tema do sistema. Sem arquivos de asset para empacotar."""
        return self.style().standardIcon(standard)

    def _build_toolbar(self) -> None:
        """Barra de comandos globais.

        Medida antes desta versão: 2.223 px de `sizeHint` — a barra transbordava
        para o menu `»` **em qualquer tela**, inclusive 1920. Não é excesso de
        itens (a §20.2 já define quais devem estar aqui, e são estes), é excesso
        de texto: doze rótulos escritos por extenso.

        A regra aplicada: fica com texto o que ancora o fluxo (`Abrir PDF`,
        `Exportar PDF`) e o que não tem ícone óbvio; vira ícone com dica o que é
        universalmente reconhecível (desfazer/refazer, navegação, salvar/abrir) e
        já tem atalho de teclado; e os três modos, que eram três botões, viram um
        controle só que **mostra o modo atual** — informação que antes exigia
        olhar qual dos três estava afundado.
        """
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)

        self.act_open_pdf = QtGui.QAction(
            self._icon(QtWidgets.QStyle.SP_DialogOpenButton), "Abrir PDF", self
        )
        self.act_open_pdf.setShortcut(QtGui.QKeySequence.Open)
        self.act_open_pdf.triggered.connect(self._open_pdf_dialog)
        toolbar.addAction(self.act_open_pdf)

        self.act_save_pdf = QtGui.QAction(
            self._icon(QtWidgets.QStyle.SP_DialogSaveButton), "Exportar PDF", self
        )
        self.act_save_pdf.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        self.act_save_pdf.triggered.connect(self._save_output_pdf)
        toolbar.addAction(self.act_save_pdf)

        self.act_save_project = QtGui.QAction(
            self._icon(QtWidgets.QStyle.SP_DriveHDIcon), "Salvar Projeto", self
        )
        self.act_save_project.setShortcut(QtGui.QKeySequence.Save)
        self.act_save_project.setToolTip("Salvar projeto (Ctrl+S)")
        self.act_save_project.triggered.connect(self._save_project_dialog)
        toolbar.addAction(self.act_save_project)

        self.act_load_project = QtGui.QAction(
            self._icon(QtWidgets.QStyle.SP_DirOpenIcon), "Carregar Projeto", self
        )
        self.act_load_project.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
        self.act_load_project.setToolTip("Carregar projeto (Ctrl+Shift+O)")
        self.act_load_project.triggered.connect(self._load_project_dialog)
        toolbar.addAction(self.act_load_project)

        toolbar.addSeparator()

        self.act_undo = QtGui.QAction(self._icon(QtWidgets.QStyle.SP_ArrowBack), "Desfazer", self)
        self.act_undo.setShortcut(QtGui.QKeySequence.Undo)
        self.act_undo.setToolTip("Desfaz a última alteração (Ctrl+Z)")
        self.act_undo.setEnabled(False)
        self.act_undo.triggered.connect(self._undo_change)
        toolbar.addAction(self.act_undo)

        self.act_redo = QtGui.QAction(self._icon(QtWidgets.QStyle.SP_ArrowForward), "Refazer", self)
        self.act_redo.setShortcuts([QtGui.QKeySequence.Redo, QtGui.QKeySequence("Ctrl+Y")])
        self.act_redo.setToolTip("Refaz a alteração desfeita (Ctrl+Y)")
        self.act_redo.setEnabled(False)
        self.act_redo.triggered.connect(self._redo_change)
        toolbar.addAction(self.act_redo)

        toolbar.addSeparator()

        self.act_mode_read = QtGui.QAction("Leitura", self)
        self.act_mode_read.setCheckable(True)
        self.act_mode_read.triggered.connect(lambda: self._set_mode("read"))

        self.act_mode_study = QtGui.QAction("Estudo", self)
        self.act_mode_study.setCheckable(True)
        self.act_mode_study.triggered.connect(lambda: self._set_mode("study"))

        self.act_mode_edit = QtGui.QAction("Edição", self)
        self.act_mode_edit.setCheckable(True)
        self.act_mode_edit.triggered.connect(lambda: self._set_mode("edit"))

        self.mode_group = QtGui.QActionGroup(self)
        self.mode_group.setExclusive(True)
        for action in (self.act_mode_read, self.act_mode_study, self.act_mode_edit):
            self.mode_group.addAction(action)

        # Um botão só, com o modo atual escrito nele. Os três QAction continuam
        # existindo (checáveis, no menu `Modo` e usados pelos testes); o que muda
        # é que a barra mostra um controle em vez de três.
        self.mode_button = QtWidgets.QToolButton()
        self.mode_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.mode_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        mode_menu = QtWidgets.QMenu(self.mode_button)
        for action in (self.act_mode_read, self.act_mode_study, self.act_mode_edit):
            mode_menu.addAction(action)
        self.mode_button.setMenu(mode_menu)
        self.mode_button.setText("Modo: Edição")
        toolbar.addWidget(self.mode_button)

        toolbar.addSeparator()

        self.act_prev = QtGui.QAction(self._icon(QtWidgets.QStyle.SP_ArrowLeft), "Página -", self)
        self.act_prev.setShortcut(QtGui.QKeySequence.MoveToPreviousChar)
        self.act_prev.setToolTip("Página anterior (←)")
        self.act_prev.triggered.connect(self._prev_page)
        toolbar.addAction(self.act_prev)

        self.act_next = QtGui.QAction(self._icon(QtWidgets.QStyle.SP_ArrowRight), "Página +", self)
        self.act_next.setShortcut(QtGui.QKeySequence.MoveToNextChar)
        self.act_next.setToolTip("Próxima página (→)")
        self.act_next.triggered.connect(self._next_page)
        toolbar.addAction(self.act_next)

        toolbar.addWidget(QtWidgets.QLabel(" Pág. "))
        toolbar.addWidget(self.page_spin)

        toolbar.addWidget(QtWidgets.QLabel(" Zoom "))
        toolbar.addWidget(self.zoom_spin)

        toolbar.addSeparator()
        self.act_toggle_preview.setIcon(self._icon(QtWidgets.QStyle.SP_FileDialogContentsView))
        toolbar.addAction(self.act_toggle_preview)

        self.act_toggle_curtain.setIcon(self._icon(QtWidgets.QStyle.SP_FileDialogListView))
        toolbar.addAction(self.act_toggle_curtain)

        # Só estes ficam com texto: são as âncoras do fluxo. O resto tem ícone
        # reconhecível, dica e atalho — e é o que faz a barra caber na tela.
        for action in (
            self.act_save_project,
            self.act_load_project,
            self.act_undo,
            self.act_redo,
            self.act_prev,
            self.act_next,
            self.act_toggle_preview,
            self.act_toggle_curtain,
        ):
            button = toolbar.widgetForAction(action)
            if button is not None:
                button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)

        self.act_study_selection = QtGui.QAction("Estudar seleção", self)
        self.act_study_selection.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.act_study_selection.triggered.connect(self._study_selection)

        self.act_study_initial = QtGui.QAction("Partida inicial", self)
        self.act_study_initial.triggered.connect(self._study_starting_position)

        self.act_pdf_text_to_before = QtGui.QAction("Texto da seleção → comentário antes", self)
        self.act_pdf_text_to_before.triggered.connect(lambda: self._copy_pdf_text_to_study_comment("before"))

        self.act_pdf_text_to_after = QtGui.QAction("Texto da seleção → comentário depois", self)
        self.act_pdf_text_to_after.triggered.connect(lambda: self._copy_pdf_text_to_study_comment("after"))

        self.act_snap_selection = QtGui.QAction("Ajustar seleção à borda", self)
        self.act_snap_selection.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        self.act_snap_selection.setToolTip(
            "Encosta a seleção nas bordas reais do tabuleiro (Ctrl+B)"
        )
        self.act_snap_selection.triggered.connect(self._snap_selection_to_board)

        self.act_auto_orient = QtGui.QAction("Auto-orientar posição", self)
        self.act_auto_orient.setShortcut(QtGui.QKeySequence("Ctrl+Shift+R"))
        self.act_auto_orient.triggered.connect(self._auto_orient_position)

        # Girar, espelhar e limpar perderam o rótulo ao caber numa linha só, e a
        # regra que o `test_toolbar` já cobra da barra vale aqui: quem ficou sem
        # texto continua alcançável pelo teclado e se explica pela dica. Antes
        # desta mudança os três existiam **só** como botão — sem ação, sem menu e
        # sem atalho —, então isto não é compensação, é o que faltava.
        self.act_rotate_board = QtGui.QAction(
            board_transform_icon("rotate"), "Rotacionar 90°", self
        )
        self.act_rotate_board.setShortcut(QtGui.QKeySequence("Ctrl+R"))
        self.act_rotate_board.setToolTip("Gira a posição 90° no sentido horário (Ctrl+R)")
        self.act_rotate_board.triggered.connect(self.board_editor.rotate_clockwise)

        self.act_flip_board = QtGui.QAction(
            board_transform_icon("flip"), "Espelhar vertical", self
        )
        self.act_flip_board.setShortcut(QtGui.QKeySequence("Ctrl+M"))
        self.act_flip_board.setToolTip("Troca as fileiras de cima pelas de baixo (Ctrl+M)")
        self.act_flip_board.triggered.connect(self.board_editor.flip_vertical)

        self.act_clear_board = QtGui.QAction(
            board_transform_icon("clear"), "Limpar tabuleiro", self
        )
        self.act_clear_board.setShortcut(QtGui.QKeySequence("Ctrl+Shift+L"))
        self.act_clear_board.setToolTip("Esvazia as 64 casas do editor (Ctrl+Shift+L)")
        self.act_clear_board.triggered.connect(self.board_editor.clear_board)

        self.act_export_report = QtGui.QAction("Exportar relatório...", self)
        self.act_export_report.setShortcut(QtGui.QKeySequence("Ctrl+Shift+E"))
        self.act_export_report.triggered.connect(self._export_report_dialog)

        self.act_style_batch = QtGui.QAction("Experimentar estilo em todas...", self)
        self.act_style_batch.setToolTip(
            "Grade com o estilo atual e o proposto em diagramas de todo o livro"
        )
        self.act_style_batch.triggered.connect(self._open_style_batch_dialog)

        self.act_gallery = QtGui.QAction("Galeria de diagramas", self)
        self.act_gallery.setShortcut(QtGui.QKeySequence("Ctrl+G"))
        self.act_gallery.setToolTip(
            "Todos os diagramas do livro em miniatura, antes e depois (Ctrl+G)"
        )
        self.act_gallery.triggered.connect(self._open_gallery)

        self.act_navigator = QtGui.QAction("Navegador de diagramas", self)
        self.act_navigator.setShortcut(QtGui.QKeySequence("Ctrl+Shift+G"))
        self.act_navigator.setToolTip(
            "Um diagrama por vez, grande, com o número do lance e a vez de jogar "
            "ao lado (Ctrl+Shift+G)"
        )
        self.act_navigator.triggered.connect(self._open_navigator)

        self.act_compare_projects = QtGui.QAction("Comparar projetos...", self)
        self.act_compare_projects.setToolTip(
            "Lista o que mudou entre dois projetos salvos — útil ao reprocessar um livro"
        )
        self.act_compare_projects.triggered.connect(self._compare_projects_dialog)

        self.act_export_diagrams = QtGui.QAction("Exportar diagramas isolados...", self)
        self.act_export_diagrams.setToolTip(
            "Um arquivo PNG, SVG ou PDF por diagrama substituído, para reaproveitar fora"
        )
        self.act_export_diagrams.triggered.connect(self._export_diagrams_dialog)

        self.act_export_training = QtGui.QAction("Exportar correções para treino...", self)
        self.act_export_training.setToolTip(
            "Grava os diagramas corrigidos no formato do dataset que treina o motor local"
        )
        self.act_export_training.triggered.connect(self._export_training_samples_dialog)

        self.act_recognize_selection = QtGui.QAction("Reconhecer seleção", self)
        self.act_recognize_selection.triggered.connect(self._recognize_selection)

        self.act_recognize_page = QtGui.QAction("Reconhecer página", self)
        self.act_recognize_page.triggered.connect(self._recognize_current_page)

        self.act_recognize_full = QtGui.QAction("Detectar no PDF", self)
        self.act_recognize_full.triggered.connect(self._recognize_full_pdf)

        self.act_add_operation = QtGui.QAction("Adicionar substituição", self)
        self.act_add_operation.triggered.connect(self._add_operation)

        self._build_menus()
        self._set_mode("edit")

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("Arquivo")
        file_menu.addAction(self.act_open_pdf)
        file_menu.addAction(self.act_load_project)
        file_menu.addAction(self.act_save_project)
        file_menu.addAction(self.act_compare_projects)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save_pdf)
        file_menu.addAction(self.act_export_report)
        file_menu.addAction(self.act_export_diagrams)
        file_menu.addAction(self.act_export_training)
        file_menu.addSeparator()
        file_menu.addAction("Sair", self.close)

        edit_menu = self.menuBar().addMenu("Editar")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)

        mode_menu = self.menuBar().addMenu("Modo")
        mode_menu.addAction(self.act_mode_read)
        mode_menu.addAction(self.act_mode_study)
        mode_menu.addAction(self.act_mode_edit)

        study_menu = self.menuBar().addMenu("Estudo")
        study_menu.addAction(self.act_study_selection)
        study_menu.addAction(self.act_study_initial)
        study_menu.addAction(self.act_pdf_text_to_before)
        study_menu.addAction(self.act_pdf_text_to_after)
        study_menu.addSeparator()
        study_menu.addAction("Carregar FEN do editor", self._load_editor_position_into_study)
        study_menu.addAction("Copiar FEN", self.study_panel._copy_fen)
        study_menu.addAction("Copiar PGN", self.study_panel._copy_pgn)
        study_menu.addAction("Salvar PGN", self.study_panel._save_pgn)
        study_menu.addAction("Virar tabuleiro", self.study_panel.study_board.flip_board)
        study_menu.addAction("Resetar linha", self.study_panel._reset)

        pdf_menu = self.menuBar().addMenu("PDF")
        pdf_menu.addAction(self.act_prev)
        pdf_menu.addAction(self.act_next)
        pdf_menu.addAction("Ajustar zoom a 100%", lambda: self.zoom_spin.setValue(1.0))
        pdf_menu.addAction("Ajustar zoom a 200%", lambda: self.zoom_spin.setValue(2.0))
        pdf_menu.addSeparator()
        pdf_menu.addAction(self.act_toggle_preview)
        pdf_menu.addAction(self.act_toggle_curtain)

        diagrams_menu = self.menuBar().addMenu("Diagramas")
        diagrams_menu.addAction(self.act_gallery)
        diagrams_menu.addAction(self.act_navigator)
        diagrams_menu.addAction(self.act_style_batch)
        diagrams_menu.addSeparator()
        diagrams_menu.addAction(self.act_snap_selection)
        diagrams_menu.addAction(self.act_auto_orient)
        diagrams_menu.addAction(self.act_rotate_board)
        diagrams_menu.addAction(self.act_flip_board)
        diagrams_menu.addAction(self.act_clear_board)
        diagrams_menu.addSeparator()
        diagrams_menu.addAction(self.act_recognize_selection)
        diagrams_menu.addAction(self.act_recognize_page)
        diagrams_menu.addAction(self.act_recognize_full)
        diagrams_menu.addSeparator()
        diagrams_menu.addAction(self.act_add_operation)
        diagrams_menu.addAction("Adicionar apagamento", self._add_eraser_from_selection)

        settings_menu = self.menuBar().addMenu("Configurações")
        settings_menu.addAction("Selecionar fonte Merida", self._select_merida_font)
        settings_menu.addAction("Limpar fonte Merida", self._clear_merida_font)
        settings_menu.addSeparator()
        self.act_autosave = QtGui.QAction("Autosave do projeto", self)
        self.act_autosave.setCheckable(True)
        self.act_autosave.setChecked(self.autosave_enabled)
        self.act_autosave.setToolTip(
            f"Salva o projeto a cada {self.autosave_interval_sec}s e ao fechar."
        )
        self.act_autosave.toggled.connect(self._on_autosave_toggled)
        settings_menu.addAction(self.act_autosave)
        settings_menu.addAction("Salvar agora", lambda: self._autosave_now(quiet=False))
        if log_file_path() is not None:
            settings_menu.addSeparator()
            settings_menu.addAction("Abrir pasta de logs", self._open_log_folder)

    def _set_mode(self, mode: str) -> None:
        if mode == "read":
            self.side_stack.setVisible(False)
            self.act_mode_read.setChecked(True)
            self.mode_button.setText("Modo: Leitura")
            self.statusBar().showMessage("Modo leitura.")
            return
        self.side_stack.setVisible(True)
        if mode == "study":
            self.side_stack.setCurrentIndex(1)
            self.act_mode_study.setChecked(True)
            self.mode_button.setText("Modo: Estudo")
            self.statusBar().showMessage("Modo estudo.")
            return
        self.side_stack.setCurrentIndex(0)
        self.act_mode_edit.setChecked(True)
        self.mode_button.setText("Modo: Edição")
        self.statusBar().showMessage("Modo edição.")

    def _restore_splitter_state(self, splitter: QtWidgets.QSplitter, setting_key: str) -> bool:
        state = self.settings.value(setting_key, None)
        if isinstance(state, QtCore.QByteArray) and not state.isEmpty():
            return bool(splitter.restoreState(state))
        return False

    def _save_splitter_state(self, splitter: QtWidgets.QSplitter, setting_key: str) -> None:
        self.settings.setValue(setting_key, splitter.saveState())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._preview_timer.stop()
        self._style_history_timer.stop()
        self._autosave_timer.stop()

        # Uma QThread destruida enquanto roda derruba o processo. Cancelar e
        # esperar e obrigatorio, e o `wait` tem teto para nao travar o fechamento
        # se o worker estiver preso numa requisicao HTTP lenta.
        if self._ocr_worker is not None:
            self._ocr_worker.cancel()
            if not self._ocr_worker.wait(5000):
                logger.warning("Worker de OCR não terminou a tempo; encerrando mesmo assim")
                self._ocr_worker.terminate()
                self._ocr_worker.wait(1000)
            self._ocr_worker = None
        if self._export_worker is not None:
            # Desde que a exportação é interrompível, fechar a janela não precisa
            # mais esperar um livro inteiro terminar de gravar: pede parada e
            # espera só o resto da página corrente.
            self._export_worker.cancel()
            if not self._export_worker.wait(15000):
                logger.warning("Exportação ainda em andamento no fechamento")
        self._export_worker = None

        if self._diagram_export_worker is not None:
            # Cancelar aqui mantém os arquivos já gravados (§39): são N arquivos
            # independentes, e fechar a janela não é motivo para jogá-los fora.
            self._diagram_export_worker.cancel()
            if not self._diagram_export_worker.wait(15000):
                logger.warning("Exportação de diagramas ainda em andamento no fechamento")
                self._diagram_export_worker.terminate()
                self._diagram_export_worker.wait(1000)
        self._diagram_export_worker = None

        # Autosave final: fechar a janela nunca pode custar o trabalho da sessao.
        if self._autosave_dirty:
            self._autosave_now(quiet=True)

        self._save_splitter_state(self.main_splitter, "main_splitter_state")
        self._save_splitter_state(self.right_vertical_splitter, "right_vertical_splitter_state")
        self._save_splitter_state(self.study_workspace, "study_workspace_splitter_state")
        if self.study_dialog:
            self.study_dialog.close()
            self.study_dialog = None
        if self.gallery_dialog:
            # A galeria tem a sua própria QThread de miniaturas; `close()` a
            # cancela e espera, pelo mesmo motivo do worker de OCR acima.
            self.gallery_dialog.close()
            self.gallery_dialog = None
        if self.navigator_dialog:
            # Mesma história, e mais uma: o `close()` do navegador entrega a
            # edição pendente antes de sair (§54), então fechar o app no meio de
            # um ajuste não custa o ajuste.
            self.navigator_dialog.close()
            self.navigator_dialog = None
        if self.pdf_service:
            self.pdf_service.close()
            self.pdf_service = None
        super().closeEvent(event)

    def _open_gallery(self) -> None:
        """Grade com todos os diagramas do livro, antes e depois (§22.5).

        Não-modal de propósito: clicar numa miniatura leva a janela principal até
        aquele diagrama, e a galeria continua aberta ao lado. É o que faz dela uma
        forma de *navegar* o livro, e não só de olhá-lo.
        """
        if not self.pdf_service or not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        if not self.operations and not self.candidates:
            QtWidgets.QMessageBox.information(
                self,
                "Galeria",
                "Nenhum diagrama ainda. Adicione substituições ou reconheça o PDF.",
            )
            return

        if self.gallery_dialog is not None:
            # Reabrir com o estado atual é mais previsível que atualizar a janela
            # existente enquanto ela ainda renderiza miniaturas do estado antigo.
            self.gallery_dialog.close()
            self.gallery_dialog = None

        dialog = GalleryDialog(
            self.current_pdf_path,
            self.operations,
            candidates=self.candidates,
            erase_operations=self.erase_operations,
            whiteout=self.whiteout_check.isChecked(),
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            erase_coordinates=self.erase_coordinates_check.isChecked(),
            parent=self,
        )
        dialog.entry_activated.connect(self._focus_gallery_entry)
        dialog.entry_edited.connect(self._on_gallery_entry_edited)
        dialog.batch_edited.connect(self._on_gallery_batch_edited)
        dialog.finished.connect(lambda _result: setattr(self, "gallery_dialog", None))
        self.gallery_dialog = dialog
        dialog.show()

    def _on_gallery_entry_edited(self, kind: str, index: int) -> None:
        """A galeria mexeu num diagrama: o resto da janela tem de saber."""
        self._apply_entry_edit(kind, index, "editar diagrama na galeria")

    def _on_navigator_entry_edited(self, kind: str, index: int) -> None:
        """O navegador mexeu nas etiquetas de um diagrama.

        Mesmo caminho da galeria, outro rótulo: o texto do `Desfazer` diz de onde
        a alteração veio, e "editar diagrama na galeria" numa alteração feita no
        navegador mandaria o usuário procurar no lugar errado.
        """
        self._apply_entry_edit(kind, index, "editar etiquetas no navegador")

    def _apply_entry_edit(self, kind: str, index: int, label: str) -> None:
        """Reconcilia a janela com um diagrama editado por outra janela.

        As duas editam o **mesmo objeto** que a janela principal guarda, então o
        dado já está certo quando este método roda. O que falta é tudo o que
        derivava dele e não se atualiza sozinho: as listas, a prévia, o link, e o
        histórico — sem o commit, um `Ctrl+Z` depois da edição desfaria a *ação
        anterior* e deixaria esta de pé, que é o pior desfazer possível.
        """
        if kind == KIND_CANDIDATE:
            self._refresh_candidates_list()
        else:
            self._refresh_operations_list()
            self._refresh_changes_list()
            if self._selected_operation_index() == index:
                self._select_operation_in_fen_tab(index)
        self._update_lichess_link()
        self._schedule_preview_refresh()
        self._commit_history(label)

    def _open_navigator(self) -> None:
        """Um diagrama por vez, grande, com as etiquetas ao lado (§54).

        Não-modal como a galeria, e pelo mesmo motivo: `Ir para este diagrama`
        leva a janela principal até ele sem fechar o navegador, que é o que
        permite conferir a página de verdade e voltar a andar na fila.

        Abre no diagrama que já está selecionado aqui. Reabrir sempre no primeiro
        seria pedir para reencontrar à mão, no navegador, o que a janela
        principal já tinha na mão.
        """
        if not self.pdf_service or not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        if not self.operations and not self.candidates:
            QtWidgets.QMessageBox.information(
                self,
                "Navegador de diagramas",
                "Nenhum diagrama ainda. Adicione substituições ou reconheça o PDF.",
            )
            return

        if self.navigator_dialog is not None:
            # Reabrir com o estado atual é mais previsível que atualizar a janela
            # existente enquanto ela ainda renderiza o estado antigo — a mesma
            # decisão da galeria.
            self.navigator_dialog.close()
            self.navigator_dialog = None

        dialog = DiagramNavigatorDialog(
            self.current_pdf_path,
            self.operations,
            candidates=self.candidates,
            erase_operations=self.erase_operations,
            whiteout=self.whiteout_check.isChecked(),
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            erase_coordinates=self.erase_coordinates_check.isChecked(),
            start_key=self._current_diagram_key(),
            parent=self,
        )
        dialog.entry_activated.connect(self._focus_gallery_entry)
        dialog.entry_edited.connect(self._on_navigator_entry_edited)
        dialog.finished.connect(lambda _result: setattr(self, "navigator_dialog", None))
        self.navigator_dialog = dialog
        dialog.show()

    def _current_diagram_key(self) -> Optional[tuple[str, int]]:
        """O diagrama em foco na janela principal, como chave da galeria."""
        index = self._selected_operation_index()
        if index is not None and 0 <= index < len(self.operations):
            return (KIND_OPERATION, index)
        item = self.candidates_list.currentItem()
        if item is not None:
            raw = item.data(QtCore.Qt.UserRole)
            if raw is not None and 0 <= int(raw) < len(self.candidates):
                return (KIND_CANDIDATE, int(raw))
        return None

    def _on_gallery_batch_edited(self, total: int) -> None:
        """A galeria aplicou os mesmos valores em vários diagramas.

        Um commit só, e com a contagem no rótulo — o desfazer de um lote é o lote
        inteiro. N commits fariam o usuário apertar Ctrl+Z trezentas vezes para
        voltar de uma decisão que ele tomou com um clique, o que na prática é o
        mesmo que não poder desfazer.
        """
        self._refresh_operations_list()
        self._refresh_changes_list()
        self._refresh_candidates_list()
        self._update_lichess_link()
        self._schedule_preview_refresh()
        self._commit_history(f"editar {total} diagrama(s) na galeria")
        self.statusBar().showMessage(f"{total} diagrama(s) alterado(s) pela galeria.")

    def _focus_gallery_entry(self, kind: str, index: int) -> None:
        """Leva a janela principal até o diagrama escolhido na galeria."""
        if kind == KIND_CANDIDATE:
            if 0 <= index < len(self.candidates):
                self._focus_candidate(index)
                self._select_candidate_row(index)
            return
        if 0 <= index < len(self.operations):
            self._set_current_operation(index)
            self._focus_operation(index)
            self._select_change("operation", index)

    def _select_candidate_row(self, index: int) -> None:
        """Seleciona na lista de candidatos o item de índice real `index`."""
        for row in range(self.candidates_list.count()):
            item = self.candidates_list.item(row)
            if item is not None and item.data(QtCore.Qt.UserRole) == index:
                self.candidates_list.setCurrentRow(row)
                return

    def _open_study_dialog(self) -> None:
        if self.study_dialog is None:
            self.study_dialog = StudyDialog(self)
        side_to_move, fullmove_number = self._current_fen_defaults()
        self.study_dialog.load_piece_placement(
            self.board_editor.piece_placement(),
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
        )
        self.study_dialog.show()
        self.study_dialog.raise_()
        self.study_dialog.activateWindow()

    # ------------------------------------------------------------------
    # Historico de alteracoes (undo/redo do modo edicao — Sprint 5.2)
    # ------------------------------------------------------------------

    def _commit_history(self, label: str) -> None:
        """Grava o estado atual das alteracoes no historico.

        Chamado **depois** da mutacao. Durante um undo/redo o commit e suprimido,
        senao restaurar um estado empilharia esse proprio estado de novo.
        """
        if self._restoring_history:
            return
        if self.history.commit(label, self.operations, self.erase_operations, self.candidates):
            self._mark_project_dirty()
        self._update_history_actions()

    def _reset_history(self, label: str = "inicio") -> None:
        self.history.reset(self.operations, self.erase_operations, self.candidates, label=label)
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        act_undo = getattr(self, "act_undo", None)
        act_redo = getattr(self, "act_redo", None)
        if act_undo is None or act_redo is None:
            return  # ainda montando a toolbar
        act_undo.setEnabled(self.history.can_undo)
        act_redo.setEnabled(self.history.can_redo)
        undo_label = self.history.undo_label
        redo_label = self.history.redo_label
        act_undo.setText(f"Desfazer {undo_label}".strip() if undo_label else "Desfazer")
        act_redo.setText(f"Refazer {redo_label}".strip() if redo_label else "Refazer")

    def _apply_history_snapshot(self, snapshot, verb: str) -> None:
        self._restoring_history = True
        try:
            self.operations = snapshot.restore_operations()
            self.erase_operations = snapshot.restore_erase_operations()
            self.candidates = snapshot.restore_candidates()
            # Indices apontavam para itens que podem ter sumido.
            self._current_operation_index = None
            self._current_eraser_index = None
            self._position_anchor = None
            self._refresh_operations_list()
            self._refresh_erasers_list()
            self._refresh_candidates_list()
            self._refresh_page_overlays()
            self._update_edit_context_state()
            self._schedule_preview_refresh(immediate=True)
            if self.navigator_dialog is not None:
                # As três listas acima foram **substituídas**, não mutadas: as
                # referências que o navegador guarda apontam agora para listas
                # órfãs, e editar por elas gravaria num objeto que ninguém lê
                # (§54). Reapontar é mais barato que fechar a janela na cara de
                # quem só apertou Ctrl+Z.
                self.navigator_dialog.rebind(
                    self.operations, self.candidates, self.erase_operations
                )
            if self.gallery_dialog is not None:
                # Pelo mesmo motivo, e a galeria tinha ficado de fora (§59.7): ela
                # guarda as mesmas referências, e o rodapé dela edita por elas.
                self.gallery_dialog.rebind(
                    self.operations, self.candidates, self.erase_operations
                )
        finally:
            self._restoring_history = False
        self._mark_project_dirty()
        self._update_history_actions()
        self.statusBar().showMessage(f"{verb}: {snapshot.label}" if snapshot.label else verb)

    def _undo_change(self) -> None:
        if self._is_study_mode():
            # No modo Estudo o Ctrl+Z pertence a linha de lances.
            self.study_panel._undo()
            return
        label = self.history.undo_label
        snapshot = self.history.undo()
        if snapshot is None:
            self.statusBar().showMessage("Nada para desfazer.")
            return
        self._apply_history_snapshot(snapshot, f"Desfeito {label}".strip())

    def _redo_change(self) -> None:
        if self._is_study_mode():
            self.study_panel._redo()
            return
        snapshot = self.history.redo()
        if snapshot is None:
            self.statusBar().showMessage("Nada para refazer.")
            return
        self._apply_history_snapshot(snapshot, "Refeito")

    # ------------------------------------------------------------------
    # Autosave (Sprint 5.3)
    # ------------------------------------------------------------------

    def _mark_project_dirty(self) -> None:
        """Ha trabalho novo que ainda nao foi para o disco."""
        self._autosave_dirty = True

    def _on_autosave_toggled(self, checked: bool) -> None:
        self.autosave_enabled = bool(checked)
        self.settings.setValue("autosave_enabled", self.autosave_enabled)
        self._start_autosave_timer()
        self.statusBar().showMessage(
            f"Autosave ligado (a cada {self.autosave_interval_sec}s)."
            if self.autosave_enabled
            else "Autosave desligado."
        )

    def _open_log_folder(self) -> None:
        path = log_file_path()
        if path is None:
            QtWidgets.QMessageBox.information(self, "Logs", "O log está indo apenas para o console.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent)))

    def _current_project_state(self) -> Optional[ProjectState]:
        if not self.current_pdf_path:
            return None
        try:
            fingerprint = fingerprint_file(self.current_pdf_path)
        except Exception:
            logger.warning("Fingerprint do PDF falhou: %s", self.current_pdf_path, exc_info=True)
            fingerprint = {}
        return ProjectState(
            source_pdf=self.current_pdf_path,
            source_pdf_fingerprint=fingerprint,
            operations=self.operations,
            erase_operations=self.erase_operations,
            study_positions=self.study_positions,
            candidates=self.candidates,
            current_page=self.current_page,
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            erase_coordinates=self.erase_coordinates_check.isChecked(),
            ocr_full_next_page=self.ocr_full_next_page,
        )

    def _autosave_target(self) -> Optional[str]:
        """Onde o autosave grava.

        Se o usuario ja escolheu um arquivo de projeto, e nele — foi o arquivo
        que ele pediu. Senao, num caminho estavel derivado do PDF, dentro do
        diretorio do app, para nao espalhar `.json` pelas pastas dele.
        """
        if self.project_path:
            return self.project_path
        if not self.current_pdf_path:
            return None
        if self._autosave_path is None:
            self._autosave_path = str(autosave_path_for_pdf(self.current_pdf_path))
        return self._autosave_path

    def _start_autosave_timer(self) -> None:
        if self.autosave_enabled and self.current_pdf_path:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()

    def _on_autosave_timeout(self) -> None:
        if not self._autosave_dirty:
            return
        self._autosave_now(quiet=True)

    def _autosave_now(self, quiet: bool = False) -> bool:
        """Grava o estado atual. Nunca interrompe o usuario com um modal."""
        if not self.autosave_enabled:
            return False
        target = self._autosave_target()
        state = self._current_project_state()
        if not target or state is None:
            return False
        try:
            write_project_atomically(target, state)
        except Exception:
            # Falhar o autosave nao pode derrubar nem travar o app; a proxima
            # tentativa acontece no proximo tique.
            logger.exception("Autosave falhou em %s", target)
            if not quiet:
                self.statusBar().showMessage("Autosave falhou — veja o log.")
            return False

        self._autosave_dirty = False
        # Um autosave restauravel na proxima sessao: o caminho entra na mesma
        # chave que `_try_restore_last_project` ja consulta.
        self._remember_last_project_path(target)
        logger.info("Autosave gravado: %s", target)
        if not quiet:
            self.statusBar().showMessage(f"Autosave: {target}")
        return True

    # ------------------------------------------------------------------
    # Configuracao do reconhecimento (Sprint 7)
    # ------------------------------------------------------------------

    def _ocr_endpoint(self) -> Optional[str]:
        """Endpoint escolhido pelo usuario, ou None para a cadeia padrao."""
        text = self.endpoint_edit.text().strip()
        return text or None

    def _local_model_path(self) -> Optional[str]:
        text = self.local_model_edit.text().strip()
        return text or None

    def _engine_mode(self) -> str:
        return normalize_mode(self.engine_combo.currentData())

    def _make_engine(self):
        return make_engine(
            self._engine_mode(),
            endpoint=self._ocr_endpoint(),
            model_path=self._local_model_path(),
        )

    def _on_engine_mode_changed(self) -> None:
        mode = self._engine_mode()
        self.settings.setValue("recognition_engine", mode)
        self._update_engine_status_label()
        self.statusBar().showMessage(f"Motor de reconhecimento: {ENGINE_LABELS[mode]}")

    def _update_engine_status_label(self) -> None:
        """Diz, antes de clicar, se o motor local está pronto e o que sai da máquina."""
        mode = self._engine_mode()
        reason = local_ocr.unavailable_reason(self._local_model_path())
        if reason and mode != ENGINE_REMOTE:
            self.engine_status_label.setText(reason)
            self.engine_status_label.setStyleSheet(f"color: {warning_text_color()};")
            return
        if mode == ENGINE_LOCAL:
            text = "Nenhuma página sai desta máquina."
        elif mode == ENGINE_REMOTE:
            text = f"Todas as páginas são enviadas para {self._ocr_endpoint() or default_endpoint()}."
        else:
            text = (
                "Reconhece localmente; envia ao serviço externo só as páginas em que a "
                "confiança ficar abaixo de 0,80."
            )
        self.engine_status_label.setText(text)
        self.engine_status_label.setStyleSheet(self._CONTEXT_STYLE)

    def _select_local_model(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Selecionar modelo local",
            self.local_model_edit.text().strip() or str(local_ocr.bundled_model_path().parent),
            "Modelo PyTorch (*.pt);;Todos os arquivos (*)",
        )
        if not file_path:
            return
        self.local_model_edit.setText(file_path)
        self._on_local_model_edited()

    def _on_local_model_edited(self) -> None:
        self.settings.setValue("local_model_path", self.local_model_edit.text().strip())
        self._update_engine_status_label()

    def _confirm_remote_upload(self, scope: str, page_count: int) -> bool:
        """Aviso explícito antes do primeiro envio de páginas para fora (§7.3).

        O produto passou o MVP inteiro mandando o livro do usuário para um servidor de
        terceiros sem dizer isso em lugar nenhum. Agora que existe alternativa local, a
        pergunta é legítima — e só é feita uma vez, porque repeti-la a cada página
        transformaria um aviso em ruído que ninguém lê.
        """
        mode = self._engine_mode()
        if not mode_uses_network(mode):
            return True
        if bool(self.settings.value("remote_privacy_ack", False, bool)):
            return True

        endpoint = self._ocr_endpoint() or default_endpoint()
        if mode == ENGINE_REMOTE:
            what = f"{page_count} página(s) renderizada(s) do seu PDF serão enviadas"
        else:
            what = (
                f"até {page_count} página(s) renderizada(s) do seu PDF podem ser enviadas "
                "(só as que o motor local ler com confiança baixa)"
            )

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Enviar páginas para um serviço externo?")
        box.setText(f"{scope}: {what} para <b>{endpoint}</b>.")
        box.setInformativeText(
            "Esse servidor é operado por terceiros e não é controlado por este aplicativo.\n\n"
            "Para não enviar nada, escolha o motor «Somente local (offline)» em Avançado."
        )
        remember = QtWidgets.QCheckBox("Não perguntar de novo neste computador")
        box.setCheckBox(remember)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        box.button(QtWidgets.QMessageBox.Yes).setText("Enviar")
        box.button(QtWidgets.QMessageBox.Cancel).setText("Cancelar")

        if box.exec() != QtWidgets.QMessageBox.Yes:
            self.statusBar().showMessage("Reconhecimento cancelado: nada foi enviado.")
            return False
        if remember.isChecked():
            self.settings.setValue("remote_privacy_ack", True)
        return True

    def _on_endpoint_edited(self) -> None:
        endpoint = self.endpoint_edit.text().strip()
        self.settings.setValue("ocr_endpoint", endpoint)
        self._update_engine_status_label()
        if endpoint:
            self.statusBar().showMessage(f"Endpoint OCR: {endpoint}")
        else:
            self.statusBar().showMessage(f"Endpoint OCR padrão: {default_endpoint()}")

    def _load_merida_font_setting(self) -> None:
        env_font = os.getenv("CHESS_MERIDA_FONT", "").strip()
        if env_font and Path(env_font).exists():
            self._apply_merida_font_path(env_font, persist=False)
            return

        saved = (self.settings.value("merida_font_path", "", str) or "").strip()
        if saved and Path(saved).exists():
            self._apply_merida_font_path(saved, persist=False)
        elif saved:
            self.merida_font_edit.setText(saved)

    def _select_merida_font(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Selecionar fonte Merida",
            self.merida_font_edit.text().strip() or "",
            "Fontes (*.ttf *.otf);;Todos os arquivos (*.*)",
        )
        if not file_path:
            return
        self._apply_merida_font_path(file_path, persist=True)

    def _clear_merida_font(self) -> None:
        self.merida_font_edit.clear()
        os.environ.pop("CHESS_MERIDA_FONT", None)
        self.settings.setValue("merida_font_path", "")
        clear_board_render_cache()
        self._schedule_preview_refresh(immediate=True)
        self.statusBar().showMessage("Fonte Merida desativada. Usando fallback de render.")

    def _apply_merida_font_path(self, file_path: str, persist: bool) -> None:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            QtWidgets.QMessageBox.warning(self, "Fonte inválida", "Arquivo de fonte não encontrado.")
            return
        if p.suffix.lower() not in {".ttf", ".otf"}:
            QtWidgets.QMessageBox.warning(self, "Fonte inválida", "Selecione um arquivo .ttf ou .otf.")
            return

        resolved = str(p.resolve())
        self.merida_font_edit.setText(resolved)
        os.environ["CHESS_MERIDA_FONT"] = resolved
        if persist:
            self.settings.setValue("merida_font_path", resolved)
        clear_board_render_cache()
        self._schedule_preview_refresh(immediate=True)
        self.statusBar().showMessage(f"Fonte Merida configurada: {resolved}")

    def _open_pdf_dialog(self) -> None:
        start_dir = self._last_pdf_dialog_dir()
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Abrir PDF", start_dir, "PDF (*.pdf)")
        if not file_path:
            return
        try:
            self._open_pdf(file_path, clear_ops=True)
        except Exception as exc:
            # O livro que estava aberto continua aberto (§59.5): o que falta é dizer
            # ao usuário por que o que ele pediu não aconteceu, em vez de mandar o
            # rastro para um console que ele não está olhando.
            logger.warning("Falha ao abrir o PDF %s", file_path, exc_info=True)
            QtWidgets.QMessageBox.critical(
                self,
                "Não foi possível abrir o PDF",
                f"{file_path}\n\n{exc}",
            )

    def _remember_last_project_path(self, project_path: str) -> None:
        self.settings.setValue("last_project_path", project_path)

    def _remember_last_pdf_path(self, pdf_path: str) -> None:
        resolved = str(Path(pdf_path).resolve())
        self.settings.setValue("last_pdf_path", resolved)
        self.settings.setValue("last_pdf_dir", str(Path(resolved).parent))

    def _last_pdf_dialog_dir(self) -> str:
        last_dir = (self.settings.value("last_pdf_dir", "", str) or "").strip()
        if last_dir and Path(last_dir).exists():
            return last_dir
        last_pdf = (self.settings.value("last_pdf_path", "", str) or "").strip()
        if last_pdf:
            parent = Path(last_pdf).parent
            if parent.exists():
                return str(parent)
        return ""

    def _try_restore_last_session(self) -> None:
        if self._try_restore_last_project():
            return
        self._try_restore_last_pdf()

    def _try_restore_last_project(self) -> bool:
        last_project = (self.settings.value("last_project_path", "", str) or "").strip()
        if not last_project:
            return False
        if not Path(last_project).exists():
            self.settings.remove("last_project_path")
            return False
        self.project_path = last_project
        loaded = self._load_project_from_path(last_project, show_dialogs=False)
        if loaded:
            self.statusBar().showMessage(f"Projeto restaurado: {last_project}")
        return loaded

    def _try_restore_last_pdf(self) -> bool:
        last_pdf = (self.settings.value("last_pdf_path", "", str) or "").strip()
        if not last_pdf:
            return False
        if not Path(last_pdf).exists():
            self.settings.remove("last_pdf_path")
            return False
        try:
            self._open_pdf(last_pdf, clear_ops=True)
        except Exception:
            self.settings.remove("last_pdf_path")
            return False
        self.statusBar().showMessage(f"PDF restaurado: {last_pdf}")
        return True

    def _open_pdf(self, file_path: str, clear_ops: bool) -> None:
        """Troca o livro aberto. Levanta sem tocar em nada se o arquivo não abrir.

        A ordem é o ponto (§59.5): abrir **antes** de fechar. Na ordem inversa, um
        arquivo que não é PDF — renomeado, truncado, baixado pela metade — fechava o
        documento anterior e deixava `self.pdf_service` apontando para ele, fechado. A
        janela não sabia disso: o caminho antigo continuava em `current_pdf_path`, a
        página desenhada continuava na tela, e o próprio `closeEvent` passava a
        estourar. Um clique no arquivo errado deixava o app num estado de que ele não
        saía nem fechando.
        """
        service = PdfService(file_path)
        if service.page_count <= 0:
            # `_render_current_page` pediria `doc[0]`. O PyMuPDF se recusa a *gravar*
            # um PDF assim, mas nada impede outro produtor de fazê-lo.
            service.close()
            raise ValueError("PDF sem páginas: não há o que exibir nem editar.")

        if self.navigator_dialog is not None:
            # Outro livro, outras páginas: o navegador ficaria renderizando o
            # caminho antigo e editando diagramas que não são mais do projeto.
            self.navigator_dialog.close()
            self.navigator_dialog = None
        if self.gallery_dialog is not None:
            # Cada palavra do comentário acima vale para a galeria, e ela ficou de
            # fora quando foi escrito (§59.6). Ela guarda o caminho do PDF **e** as
            # referências para as listas (§52.3): depois desta linha as miniaturas
            # seriam do livro anterior e o rodapé editaria uma lista substituída por
            # `[]` — sem erro nenhum, que é o pior jeito de uma edição sumir.
            self.gallery_dialog.close()
            self.gallery_dialog = None
        if self.pdf_service:
            self.pdf_service.close()
        self.pdf_service = service
        self.current_pdf_path = file_path
        self._remember_last_pdf_path(file_path)
        # Outro livro, outro destino de autosave.
        self._autosave_path = None
        self.current_page = 0
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, self.pdf_service.page_count))
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        if clear_ops:
            self.operations = []
            self.erase_operations = []
            self.study_positions = []
            self.candidates = []
            self.ocr_full_next_page = 0
            self._position_anchor = None
            self._refresh_operations_list()
            self._refresh_erasers_list()
            self._refresh_study_positions_list()
            self._refresh_candidates_list()
        self._render_current_page()
        self._update_edit_context_state()
        if clear_ops:
            # Abrir um livro novo zera o que da para desfazer: o passado
            # pertencia ao livro anterior.
            self._reset_history("abrir PDF")
            self.project_path = None
            self._autosave_dirty = False
        self._start_autosave_timer()
        logger.info("PDF aberto: %s (%d páginas)", file_path, self.pdf_service.page_count)
        self.statusBar().showMessage(f"PDF aberto: {file_path}")

    def _render_current_page(self) -> None:
        if not self.pdf_service:
            return
        self.current_page = max(0, min(self.current_page, self.pdf_service.page_count - 1))
        zoom = float(self.zoom_spin.value())
        self.current_render = self.pdf_service.render_page(self.current_page, zoom=zoom)
        self.current_preview_render = None
        self._showing_preview = False
        self._curtain_active = False
        self._apply_page_pixmap(self.current_render, preserve_selection=False)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.current_page + 1)
        self.page_spin.blockSignals(False)
        self._update_window_title()
        self._update_edit_context_state()
        self._schedule_preview_refresh()

    def _update_window_title(self) -> None:
        if not self.pdf_service:
            self.setWindowTitle("Chess PDF Editor")
            return
        if self._curtain_active:
            suffix = " [comparação: antes | depois]"
        elif self._showing_preview:
            suffix = " [prévia do resultado]"
        else:
            suffix = ""
        self.setWindowTitle(
            f"Chess PDF Editor - {Path(self.current_pdf_path or '').name} - "
            f"Página {self.current_page + 1}/{self.pdf_service.page_count}{suffix}"
        )

    def _apply_page_pixmap(self, render: RenderedPage, preserve_selection: bool) -> None:
        """Troca o bitmap exibido sem perder a selecao ativa do usuario."""
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(render.image_png, "PNG")
        self._refreshing_view = True
        try:
            selection = self.page_widget.selection_rect() if preserve_selection else None
            self.page_widget.set_page_pixmap(pixmap)
            # O passo das setas é em pontos PDF; o widget precisa do zoom em vigor
            # para converter. `matrix[0]` é o fator horizontal do render.
            self.page_widget.set_points_scale(abs(render.matrix[0]) or 1.0)
            if selection is not None:
                self.page_widget.set_selection_rect(selection)
        finally:
            self._refreshing_view = False
        self._refresh_page_overlays()
        self._update_window_title()
        self._update_edit_context_state()

    # ------------------------------------------------------------------
    # Previa ao vivo do resultado
    # ------------------------------------------------------------------

    def _on_toggle_preview(self, checked: bool) -> None:
        self.preview_result_enabled = bool(checked)
        self.settings.setValue("preview_result_enabled", self.preview_result_enabled)
        if self.preview_result_enabled:
            # As duas são formas de olhar o mesmo resultado, e disputariam a
            # página: a prévia cheia apagaria o lado "antes" da cortina.
            self.act_toggle_curtain.setChecked(False)
            self.statusBar().showMessage(
                "Prévia ligada: a página mostra como o PDF vai ficar. Ctrl+D volta ao original."
            )
        else:
            self._show_original_render()
            self.statusBar().showMessage("Prévia desligada: mostrando o PDF original.")
        self._schedule_preview_refresh(immediate=True)

    def _on_toggle_curtain(self, checked: bool) -> None:
        self.compare_curtain_enabled = bool(checked)
        self.settings.setValue("compare_curtain_enabled", self.compare_curtain_enabled)
        if self.compare_curtain_enabled:
            self.act_toggle_preview.setChecked(False)
            self.statusBar().showMessage(
                "Cortina ligada: arraste a linha para comparar antes e depois. "
                "Ctrl+Shift+D volta ao original."
            )
        else:
            self._show_original_render()
            self.statusBar().showMessage("Cortina desligada: mostrando o PDF original.")
        self._schedule_preview_refresh(immediate=True)

    @staticmethod
    def _clamp_curtain_fraction(value: object) -> float:
        try:
            fraction = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return min(1.0, max(0.0, fraction))

    def _on_curtain_moved(self, fraction: float) -> None:
        """Guarda onde o usuário deixou a linha: trocar de página não a reseta."""
        self.curtain_fraction = self._clamp_curtain_fraction(fraction)
        self.settings.setValue("compare_curtain_fraction", self.curtain_fraction)

    def _apply_curtain_view(self, before: RenderedPage, after: RenderedPage) -> None:
        """Página original como base, resultado revelado à direita da linha."""
        self._showing_preview = True
        self._curtain_active = True
        self._apply_page_pixmap(before, preserve_selection=True)
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(after.image_png, "PNG")
        self.page_widget.set_curtain_fraction(self.curtain_fraction)
        self.page_widget.set_curtain_pixmap(pixmap)

    def _clear_curtain(self) -> None:
        self._curtain_active = False
        self.page_widget.set_curtain_pixmap(None)

    def _show_original_render(self) -> None:
        self._clear_curtain()
        if self.current_render is None or not self._showing_preview:
            return
        self._showing_preview = False
        self._apply_page_pixmap(self.current_render, preserve_selection=True)

    def _schedule_preview_refresh(self, immediate: bool = False) -> None:
        if self._refreshing_view or not self.pdf_service:
            return
        if immediate:
            self._preview_timer.stop()
            self._refresh_result_preview()
            return
        self._preview_timer.start()

    def _set_position_anchor(self, rect_pdf: Optional[tuple[float, float, float, float]]) -> None:
        """Marca a area que originou a posicao atualmente carregada no editor."""
        self._position_anchor = None if rect_pdf is None else (self.current_page, tuple(rect_pdf))

    def _anchor_from_selection(self) -> None:
        if not self.pdf_service or not self.current_render:
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            return
        self._set_position_anchor(
            self.pdf_service.image_rect_to_pdf_rect(
                self.current_page,
                selection,
                self.current_render.matrix,
            )
        )

    def _position_matches_selection(self, rect_pdf: tuple[float, float, float, float]) -> bool:
        """A posicao do editor pertence mesmo a esta selecao?

        Sem essa checagem, selecionar o segundo diagrama de uma pagina faria a
        previa desenhar a posicao do primeiro sobre a area nova.
        """
        if self._position_anchor is None:
            return False
        anchor_page, anchor_rect = self._position_anchor
        if anchor_page != self.current_page:
            return False
        return self._rect_iou(anchor_rect, rect_pdf) >= 0.40

    def _draft_operation(self) -> Optional[OverlayOperation]:
        """Substituicao ainda nao confirmada, montada da selecao + FEN atuais."""
        if not self.pdf_service or not self.current_render:
            return None
        selection = self.page_widget.selection_rect()
        if not selection:
            return None
        piece_placement = self._current_piece_placement_or_none()
        if not piece_placement or not any(ch in "PNBRQKpnbrqk" for ch in piece_placement):
            return None

        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )
        if not self._position_matches_selection(rect_pdf):
            return None

        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
        side_to_move, fullmove_number = self._current_fen_defaults()
        return OverlayOperation(
            page_num=self.current_page,
            rect_pdf=rect_pdf,
            fen=piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
            source="draft",
            whiteout_padding_pt=(pad_left + pad_top + pad_right + pad_bottom) / 4.0,
            whiteout_padding_left_pt=pad_left,
            whiteout_padding_top_pt=pad_top,
            whiteout_padding_right_pt=pad_right,
            whiteout_padding_bottom_pt=pad_bottom,
            border_width_pt=float(self.op_border_spin.value()),
        )

    def _preview_operations(
        self,
    ) -> tuple[list[OverlayOperation], list[EraseOperation], Optional[OverlayOperation]]:
        draft = self._draft_operation()
        operations: list[OverlayOperation] = []
        for op in self.operations:
            if op.page_num != self.current_page:
                continue
            # Editar uma substituicao ja existente: o rascunho manda, para que a
            # previa reflita a edicao em andamento em vez da versao salva.
            if draft is not None and self._rect_iou(op.rect_pdf, draft.rect_pdf) >= 0.80:
                continue
            operations.append(op)
        if draft is not None:
            operations.append(draft)
        erasers = [op for op in self.erase_operations if op.page_num == self.current_page]
        return (operations, erasers, draft)

    def _compare_rect_pdf(self, draft: Optional[OverlayOperation]) -> Optional[tuple[float, float, float, float]]:
        if draft is not None:
            return draft.rect_pdf
        if self.page_widget.selection_rect():
            # Ha uma selecao ativa sem posicao correspondente: mostrar as
            # miniaturas de outro diagrama so confundiria.
            return None
        selected = self._selected_change()
        if selected is not None and selected[0] == "operation":
            return self.operations[selected[1]].rect_pdf
        idx = self._selected_operation_index()
        if idx is not None and self.operations[idx].page_num == self.current_page:
            return self.operations[idx].rect_pdf
        return None

    @staticmethod
    def _expanded_rect(rect_pdf: tuple[float, float, float, float], ratio: float = 0.10) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = rect_pdf
        margin = max(6.0, max(x1 - x0, y1 - y0) * ratio)
        return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)

    def _refresh_result_preview(self) -> None:
        if not self.pdf_service or not self.current_render:
            self.before_after.set_message("Abra um PDF para comparar antes e depois.")
            return

        operations, erasers, draft = self._preview_operations()
        compare_rect = self._compare_rect_pdf(draft)
        want_thumbs = self.compare_group.isChecked() and compare_rect is not None
        # Pagina sem nenhuma alteracao: a previa seria identica ao original,
        # entao evita o custo de montar e renderizar o documento de previa.
        has_changes = bool(operations or erasers)
        want_curtain = self.compare_curtain_enabled and has_changes
        want_page = (self.preview_result_enabled and has_changes) or want_curtain

        if not want_page and not want_thumbs:
            self.current_preview_render = None
            if self.compare_group.isChecked():
                self.before_after.set_message(
                    "Selecione um diagrama e monte a posição para ver o antes e depois."
                )
            self._show_original_render()
            return

        zoom = float(self.zoom_spin.value())
        whiteout = self.whiteout_check.isChecked()
        include_link = self.include_lichess_link_check.isChecked()
        erase_coordinates = self.erase_coordinates_check.isChecked()

        try:
            if want_page:
                self.current_preview_render = self.pdf_service.render_page_with_operations(
                    self.current_page,
                    zoom,
                    operations,
                    erase_operations=erasers,
                    whiteout=whiteout,
                    include_lichess_link=include_link,
                    erase_coordinates=erase_coordinates,
                )
                if want_curtain:
                    self._apply_curtain_view(self.current_render, self.current_preview_render)
                else:
                    self._clear_curtain()
                    self._showing_preview = True
                    self._apply_page_pixmap(self.current_preview_render, preserve_selection=True)
            else:
                self.current_preview_render = None
                self._show_original_render()

            if want_thumbs and compare_rect is not None:
                region = self._expanded_rect(compare_rect)
                before_png = self.pdf_service.render_region(self.current_page, 2.0, region)
                after_png = self.pdf_service.render_region_with_operations(
                    self.current_page,
                    2.0,
                    region,
                    operations,
                    erase_operations=erasers,
                    whiteout=whiteout,
                    include_lichess_link=include_link,
                    erase_coordinates=erase_coordinates,
                )
                self.before_after.set_images(before_png, after_png)
            elif self.compare_group.isChecked():
                self.before_after.set_message(
                    "Selecione um diagrama e monte a posição para ver o antes e depois."
                )
        except Exception as exc:
            self.current_preview_render = None
            self._clear_curtain()
            self.before_after.set_message(f"Não foi possível gerar a prévia: {exc}")
            self.statusBar().showMessage(f"Falha ao gerar prévia: {exc}")

    def _prev_page(self) -> None:
        if not self.pdf_service:
            return
        if self.current_page > 0:
            self.current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if not self.pdf_service:
            return
        if self.current_page < self.pdf_service.page_count - 1:
            self.current_page += 1
            self._render_current_page()

    def _on_page_spin_changed(self, value: int) -> None:
        if self._loading_ui or not self.pdf_service:
            return
        self.current_page = max(0, min(value - 1, self.pdf_service.page_count - 1))
        self._render_current_page()

    def _on_zoom_changed(self, value: float) -> None:
        if self._loading_ui or not self.pdf_service:
            return
        self._render_current_page()

    def _selected_operation_index(self) -> Optional[int]:
        idx = self._current_operation_index
        if idx is not None and 0 <= idx < len(self.operations):
            return idx
        return None

    def _set_current_operation(self, idx: Optional[int]) -> None:
        """Define a substituicao em foco e sincroniza os paineis dependentes."""
        if idx is None or not (0 <= idx < len(self.operations)):
            self._current_operation_index = None
            self._update_edit_context_state()
            return

        self._current_operation_index = idx
        self._update_edit_context_state()
        if self._loading_ui:
            return
        self._select_operation_in_fen_tab(idx)
        self._update_lichess_link()
        if self.apply_style_all_check.isChecked():
            return
        op = self.operations[idx]
        self._loading_ui = True
        self.pad_left_spin.setValue(float(op.whiteout_padding_left_pt))
        self.pad_top_spin.setValue(float(op.whiteout_padding_top_pt))
        self.pad_right_spin.setValue(float(op.whiteout_padding_right_pt))
        self.pad_bottom_spin.setValue(float(op.whiteout_padding_bottom_pt))
        self.op_border_spin.setValue(float(op.border_width_pt))
        self._loading_ui = False
        self._update_edit_context_state()

    def _selected_eraser_index(self) -> Optional[int]:
        idx = self._current_eraser_index
        if idx is not None and 0 <= idx < len(self.erase_operations):
            return idx
        return None

    def _selected_change(self) -> Optional[tuple[str, int]]:
        item = self.changes_list.currentItem()
        if not item:
            return None
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        kind = str(data[0])
        try:
            idx = int(data[1])
        except Exception:
            return None
        if kind == "operation" and 0 <= idx < len(self.operations):
            return (kind, idx)
        if kind == "eraser" and 0 <= idx < len(self.erase_operations):
            return (kind, idx)
        return None

    def _current_fen_defaults(self) -> tuple[str, int]:
        side_to_move = str(self.fen_side_combo.currentData() or "w")
        if side_to_move not in {"w", "b"}:
            side_to_move = "w"
        fullmove_number = max(1, int(self.fen_move_spin.value()))
        return (side_to_move, fullmove_number)

    @staticmethod
    def _operation_full_fen(op: OverlayOperation) -> str:
        side = op.side_to_move if op.side_to_move in {"w", "b"} else "w"
        fullmove = max(1, int(op.fullmove_number))
        return f"{op.fen} {side} - - 0 {fullmove}"

    @staticmethod
    def _build_lichess_analysis_url(full_fen: str) -> str:
        normalized = " ".join(full_fen.split())
        parts = normalized.split(" ")
        if not parts:
            return "https://lichess.org/analysis"
        piece_placement = parts[0]
        if len(parts) == 1:
            return f"https://lichess.org/analysis/{piece_placement}"
        fen_tail = " ".join(parts[1:])
        encoded_tail = bytes(QtCore.QUrl.toPercentEncoding(" " + fen_tail)).decode("ascii")
        return f"https://lichess.org/analysis/{piece_placement}{encoded_tail}"

    def _current_full_fen_for_lichess(self) -> Optional[str]:
        # O desvio que existia aqui — "se nada está selecionado, olhe a seleção da
        # aba FEN" — era o preço de haver duas listas com duas seleções. Com uma
        # lista só, `_selected_operation_index` é a resposta inteira (§51.4).
        idx = self._selected_operation_index()
        if idx is not None and 0 <= idx < len(self.operations):
            return self._operation_full_fen(self.operations[idx])

        text = self.fen_edit.text().strip()
        if not text:
            return None
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
        except Exception:
            return None
        side_to_move, fullmove_number = self._current_fen_defaults()
        return f"{piece_placement} {side_to_move} - - 0 {fullmove_number}"

    def _update_lichess_link(self) -> None:
        full_fen = self._current_full_fen_for_lichess()
        if not full_fen:
            self.lichess_link_label.setText("Lichess (FEN inválida)")
            self.lichess_link_label.setToolTip("Informe uma FEN válida para abrir no Lichess.")
            return
        url = self._build_lichess_analysis_url(full_fen)
        self.lichess_link_label.setText(f'<a href="{url}">Lichess</a>')
        self.lichess_link_label.setToolTip(full_fen)

    def _select_operation_in_fen_tab(self, idx: int) -> None:
        """Põe os campos da aba FEN na substituição `idx`.

        Não mexe mais em lista nenhuma: a aba FEN deixou de ter a sua (§51.4) e
        passou a mostrar os metadados de quem estiver selecionado na lista única.
        """
        if not (0 <= idx < len(self.operations)):
            return
        self._syncing_fen_tab = True
        try:
            op = self.operations[idx]
            self.fen_side_combo.setCurrentIndex(0 if op.side_to_move != "b" else 1)
            self.fen_move_spin.setValue(max(1, int(op.fullmove_number)))
        finally:
            self._syncing_fen_tab = False
        self._update_fen_meta_label(idx)
        self._update_lichess_link()

    def _sync_candidates_tab(self) -> None:
        """Mostra a aba de conferência quando há o que conferir, e vai para ela.

        A ida é só na **transição** de vazia para cheia — que é o fim de um lote.
        Trocar de aba a cada refresh arrancaria o usuário do lugar toda vez que ele
        mexesse no filtro, e o filtro fica justamente dentro desta aba.
        """
        tabs = self.edit_tabs
        index = self._candidates_tab_index
        tinha = tabs.isTabVisible(index)
        total = len(self.candidates)
        tabs.setTabVisible(index, bool(total))
        tabs.setTabText(index, f"Conferir ({total})" if total else "Conferir")
        if total and not tinha:
            tabs.setCurrentIndex(index)

    def _update_fen_meta_label(self, idx: Optional[int]) -> None:
        """Diz de quem são os campos abaixo.

        Com a lista na outra aba, sem isto os dois campos ficariam sem dono
        visível — e editá-los mexeria numa substituição que o usuário não está
        vendo.
        """
        if idx is None or not (0 <= idx < len(self.operations)):
            self.fen_meta_label.setText("Nenhuma substituição selecionada")
            self.fen_side_combo.setEnabled(False)
            self.fen_move_spin.setEnabled(False)
            return
        op = self.operations[idx]
        self.fen_meta_label.setText(f"Substituição {idx + 1:03d} · pág {op.page_num + 1}")
        self.fen_side_combo.setEnabled(True)
        self.fen_move_spin.setEnabled(True)

    def _focus_operation(self, idx: int) -> None:
        if not (0 <= idx < len(self.operations)) or not self.pdf_service:
            return
        op = self.operations[idx]
        self._select_operation_in_fen_tab(idx)
        self.current_page = op.page_num
        self._render_current_page()
        if self.current_render:
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                op.rect_pdf,
                self.current_render.matrix,
            )
            self.page_widget.set_selection_rect(rect_img)
        self._loading_ui = True
        self.board_editor.set_piece_placement(op.fen)
        self.fen_edit.setText(op.fen)
        self._loading_ui = False
        self._set_position_anchor(op.rect_pdf)
        self._update_warnings(op.fen)
        self._update_lichess_link()
        self._select_change("operation", idx)

    def _current_whiteout_padding(self) -> tuple[float, float, float, float]:
        left = float(self.pad_left_spin.value())
        top = float(self.pad_top_spin.value())
        right = float(self.pad_right_spin.value())
        bottom = float(self.pad_bottom_spin.value())
        return (left, top, right, bottom)

    def _refresh_page_overlays(self) -> None:
        if not self.pdf_service or not self.current_render or self._showing_preview:
            # Na previa a pagina ja mostra o resultado real; as marcacoes de
            # trabalho sairiam do caminho do "como vai ficar".
            self.page_widget.set_operation_rects([])
            self.page_widget.set_eraser_rects([])
            self.page_widget.set_study_rects([])
            self.page_widget.set_candidate_rects([])
            return

        op_rects: list[tuple[float, float, float, float]] = []
        for op in self.operations:
            if op.page_num != self.current_page:
                continue
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                op.rect_pdf,
                self.current_render.matrix,
            )
            op_rects.append(rect_img)
        self.page_widget.set_operation_rects(op_rects)

        eraser_rects: list[tuple[float, float, float, float]] = []
        for op in self.erase_operations:
            if op.page_num != self.current_page:
                continue
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                op.rect_pdf,
                self.current_render.matrix,
            )
            eraser_rects.append(rect_img)
        self.page_widget.set_eraser_rects(eraser_rects)

        study_rects: list[tuple[float, float, float, float]] = []
        for pos in self.study_positions:
            if pos.page_num != self.current_page:
                continue
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                pos.rect_pdf,
                self.current_render.matrix,
            )
            study_rects.append(rect_img)
        self.page_widget.set_study_rects(study_rects)

        candidate_rects: list[tuple[float, float, float, float]] = []
        for candidate in self.candidates:
            if candidate.page_num != self.current_page:
                continue
            candidate_rects.append(
                self.pdf_service.pdf_rect_to_image_rect(
                    self.current_page,
                    candidate.rect_pdf,
                    self.current_render.matrix,
                )
            )
        self.page_widget.set_candidate_rects(candidate_rects)

    def _on_selection_changed(self, rect: object) -> None:
        if self._refreshing_view:
            return
        if rect is None:
            self.statusBar().showMessage("Seleção limpa.")
            self._update_edit_context_state()
            self._schedule_preview_refresh()
            return
        self.statusBar().showMessage(self._selection_status_text(rect))
        self._update_edit_context_state()
        self._schedule_preview_refresh()

    def _selection_status_text(self, rect: tuple[float, float, float, float]) -> str:
        """Tamanho da seleção em pontos PDF — a unidade do ajuste fino."""
        if self.pdf_service and self.current_render:
            try:
                px0, py0, px1, py1 = self.pdf_service.image_rect_to_pdf_rect(
                    self.current_page, rect, self.current_render.matrix
                )
            except Exception:
                logger.debug("Falha ao converter a seleção para pontos", exc_info=True)
            else:
                return (
                    f"Seleção: {px1 - px0:.2f} × {py1 - py0:.2f} pt em "
                    f"({px0:.2f}, {py0:.2f}) · setas movem, Shift+setas 0,25 pt, "
                    "Ctrl+setas redimensionam"
                )
        x0, y0, x1, y1 = rect
        return f"Seleção na imagem: x0={x0:.1f}, y0={y0:.1f}, x1={x1:.1f}, y1={y1:.1f}"

    def _operation_index_at_image_point(self, x: float, y: float) -> Optional[int]:
        if not self.pdf_service or not self.current_render:
            return None

        best_idx: Optional[int] = None
        best_area: Optional[float] = None
        point = QtCore.QPointF(float(x), float(y))
        for idx, op in enumerate(self.operations):
            if op.page_num != self.current_page:
                continue
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                op.rect_pdf,
                self.current_render.matrix,
            )
            rect = QtCore.QRectF(
                QtCore.QPointF(rect_img[0], rect_img[1]),
                QtCore.QPointF(rect_img[2], rect_img[3]),
            ).normalized()
            rect = rect.adjusted(-4.0, -4.0, 4.0, 4.0)
            if not rect.contains(point):
                continue
            area = rect.width() * rect.height()
            if best_idx is None or area < float(best_area):
                best_idx = idx
                best_area = area
        return best_idx

    def _study_position_index_at_image_point(self, x: float, y: float) -> Optional[int]:
        if not self.pdf_service or not self.current_render:
            return None

        best_idx: Optional[int] = None
        best_area: Optional[float] = None
        point = QtCore.QPointF(float(x), float(y))
        for idx, pos in enumerate(self.study_positions):
            if pos.page_num != self.current_page:
                continue
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                pos.rect_pdf,
                self.current_render.matrix,
            )
            rect = QtCore.QRectF(
                QtCore.QPointF(rect_img[0], rect_img[1]),
                QtCore.QPointF(rect_img[2], rect_img[3]),
            ).normalized()
            rect = rect.adjusted(-4.0, -4.0, 4.0, 4.0)
            if not rect.contains(point):
                continue
            area = rect.width() * rect.height()
            if best_idx is None or area < float(best_area):
                best_idx = idx
                best_area = area
        return best_idx

    def _is_study_mode(self) -> bool:
        return self.side_stack.isVisible() and self.side_stack.currentIndex() == 1

    def _load_operation_into_study(self, idx: int) -> None:
        if not (0 <= idx < len(self.operations)):
            return
        op = self.operations[idx]
        self.study_panel.load_piece_placement(
            op.fen,
            side_to_move=op.side_to_move,
            fullmove_number=op.fullmove_number,
        )
        self._set_current_operation(idx)
        self._set_mode("study")
        self.statusBar().showMessage(f"Diagrama da página {op.page_num + 1} carregado no estudo.")

    def _on_page_clicked(self, point: object) -> None:
        if not isinstance(point, tuple) or len(point) != 2:
            return
        x = float(point[0])
        y = float(point[1])
        if self._is_study_mode():
            study_idx = self._study_position_index_at_image_point(x, y)
            if study_idx is not None:
                self._focus_study_position(study_idx)
                return

            op_idx = self._operation_index_at_image_point(x, y)
            if op_idx is not None:
                self._load_operation_into_study(op_idx)
                return

            if self._select_board_at_point(x, y):
                return
            self.statusBar().showMessage(
                "Nenhum diagrama conhecido nesse clique. Selecione a área e use Reconhecer seleção ou Estudar seleção."
            )
            return

        idx = self._operation_index_at_image_point(x, y)
        if idx is None:
            # Clique em área livre: em vez de só limpar a seleção, tenta achar o
            # tabuleiro que está embaixo do cursor (§38).
            self._select_board_at_point(x, y)
            return
        self._set_current_operation(idx)
        self._focus_operation(idx)

    def _on_save_recognition_json_toggled(self, checked: bool) -> None:
        self.settings.setValue("save_recognition_snapshot", bool(checked))
        self.statusBar().showMessage(
            "Cada reconhecimento passa a gravar um JSON na pasta do PDF."
            if checked
            else "Os reconhecimentos deixam de gravar JSON próprio."
        )

    def _on_click_detects_toggled(self, checked: bool) -> None:
        self.click_detects_diagram = bool(checked)
        self.settings.setValue("click_detects_diagram", self.click_detects_diagram)
        if not self.click_detects_diagram:
            self.statusBar().showMessage(
                "Detecção por clique desligada: selecione o diagrama arrastando."
            )
        elif not local_ocr.dependencies_available():
            # Ligar sem o detector instalado não faria nada, e o usuário merece
            # saber disso agora e não no primeiro clique sem efeito.
            self.statusBar().showMessage(
                f"Detecção por clique precisa do detector local: {local_ocr.unavailable_reason()}"
            )

    def _select_board_at_point(self, x: float, y: float) -> bool:
        """Seleciona o tabuleiro sob o ponto clicado. Devolve se achou algum.

        O clique que não acerta diagrama nenhum já limpava a seleção; achar a borda
        do tabuleiro ali é estritamente melhor que isso. Silencioso de propósito
        quando não há nada: um clique perdido não deve virar diálogo.
        """
        if not self.click_detects_diagram or not self.current_render:
            return False
        if not local_ocr.dependencies_available():
            return False

        try:
            # ~40 ms numa página A4 a zoom 2.0 (§38.2), então roda no clique mesmo,
            # sem worker — a alternativa seria uma seleção que aparece depois.
            from .local_ocr.engine import board_rect_at

            rect = board_rect_at(self.current_render.image_png, (x, y))
        except Exception:
            logger.warning("Falha ao detectar tabuleiro no clique", exc_info=True)
            return False

        if rect is None:
            return False

        # Sem âncora de propósito (§21.5): a área foi encontrada, mas nenhuma posição
        # pertence a ela ainda. Ancorar aqui faria a prévia desenhar a FEN do
        # diagrama anterior sobre este, que é justamente o susto que a §21.5 tirou.
        self.page_widget.set_selection_rect(rect)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        self.statusBar().showMessage(
            f"Diagrama detectado no clique: {width:.0f}×{height:.0f} px. "
            "Reconhecer seleção lê a posição."
        )
        return True

    def _on_change_selected(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        del previous
        self._update_edit_context_state()
        if self._loading_ui or current is None:
            return
        data = current.data(QtCore.Qt.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind = str(data[0])
        idx = int(data[1])
        if kind == "operation" and 0 <= idx < len(self.operations):
            self._current_eraser_index = None
            self._set_current_operation(idx)
            self._update_lichess_link()
            self._schedule_preview_refresh()
            return
        if kind == "eraser":
            self._current_eraser_index = idx if 0 <= idx < len(self.erase_operations) else None
            self._set_current_operation(None)
            # Um apagamento não tem FEN: os campos da aba FEN ficam sem dono, e
            # dizê-lo é melhor que deixá-los mostrando os da substituição anterior.
            self._update_fen_meta_label(None)
            self._update_lichess_link()
            self._schedule_preview_refresh()

    def _on_change_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind = str(data[0])
        idx = int(data[1])
        if kind == "operation":
            if 0 <= idx < len(self.operations):
                self._focus_operation(idx)
            return
        if kind == "eraser":
            self._focus_eraser(idx)

    def _on_operation_style_changed(self, value: float) -> None:
        del value
        if self._loading_ui:
            return
        if not self.operations:
            # Sem substituicoes salvas o ajuste ainda vale para o rascunho.
            self._schedule_preview_refresh()
            return
        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
        border = float(self.op_border_spin.value())
        if self.apply_style_all_check.isChecked():
            targets = self.operations
        else:
            idx = self._selected_operation_index()
            if idx is None:
                return
            targets = [self.operations[idx]]
        for op in targets:
            op.whiteout_padding_left_pt = pad_left
            op.whiteout_padding_top_pt = pad_top
            op.whiteout_padding_right_pt = pad_right
            op.whiteout_padding_bottom_pt = pad_bottom
            op.whiteout_padding_pt = (pad_left + pad_top + pad_right + pad_bottom) / 4.0
            op.border_width_pt = border
        self._refresh_operations_list()
        self._refresh_page_overlays()
        self._schedule_preview_refresh()
        # Cada passo do spinbox emite um sinal; um commit por passo encheria o
        # historico de estados intermediarios que ninguem quer desfazer um a um.
        self._style_history_timer.start()

    def _current_style_proposal(self) -> StyleProposal:
        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
        return StyleProposal(
            padding_left_pt=pad_left,
            padding_top_pt=pad_top,
            padding_right_pt=pad_right,
            padding_bottom_pt=pad_bottom,
            border_width_pt=float(self.op_border_spin.value()),
        )

    def _open_style_batch_dialog(self) -> None:
        """Experimenta um estilo no livro inteiro antes de aplicá-lo (§36)."""
        if not self.pdf_service or not self.current_pdf_path:
            QtWidgets.QMessageBox.information(
                self, "Estilo em lote", "Abra um PDF para experimentar o estilo."
            )
            return
        if not self.operations:
            QtWidgets.QMessageBox.information(
                self,
                "Estilo em lote",
                "Nenhuma substituição salva. Adicione ao menos uma para comparar estilos.",
            )
            return

        dialog = StyleBatchDialog(
            self.current_pdf_path,
            self.operations,
            erase_operations=self.erase_operations,
            whiteout=self.whiteout_check.isChecked(),
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            erase_coordinates=self.erase_coordinates_check.isChecked(),
            proposal=self._current_style_proposal(),
            parent=self,
        )
        try:
            accepted = dialog.exec() == QtWidgets.QDialog.Accepted
            proposal = dialog.proposal()
        finally:
            dialog.stop_worker()
        if not accepted:
            self.statusBar().showMessage("Estilo em lote cancelado: nada mudou.")
            return
        self._apply_style_to_all(proposal)

    def _apply_style_to_all(self, proposal: StyleProposal) -> None:
        affected = count_affected(self.operations, proposal)
        if affected == 0:
            self.statusBar().showMessage(
                "Estilo em lote: as substituições já estavam com esse estilo."
            )
            return
        for op in self.operations:
            proposal.apply_in_place(op)
        # Os spinboxes do painel passam a mostrar o que foi aplicado. `_loading_ui`
        # impede que cada `setValue` reentre em `_on_operation_style_changed` e
        # reaplique o mesmo estilo N vezes, cada uma pedindo um commit.
        self._loading_ui = True
        try:
            self.pad_left_spin.setValue(proposal.padding_left_pt)
            self.pad_top_spin.setValue(proposal.padding_top_pt)
            self.pad_right_spin.setValue(proposal.padding_right_pt)
            self.pad_bottom_spin.setValue(proposal.padding_bottom_pt)
            self.op_border_spin.setValue(proposal.border_width_pt)
        finally:
            self._loading_ui = False
        self._refresh_operations_list()
        self._refresh_page_overlays()
        self._schedule_preview_refresh(immediate=True)
        # Uma entrada só no histórico: desfazer devolve o estilo de todas de uma
        # vez, que é como o usuário pensa na ação que acabou de tomar.
        self._commit_history("Estilo de todas as substituições")
        self.statusBar().showMessage(
            f"Estilo aplicado em {affected} de {len(self.operations)} substituição(ões)."
        )

    def _on_fen_meta_changed(self, value: int) -> None:
        del value
        if self._loading_ui or self._syncing_fen_tab:
            return
        # Mesmo desvio do `_current_full_fen_for_lichess`, e some pela mesma
        # razão: só havia uma segunda seleção para consultar porque havia uma
        # segunda lista (§51.4).
        idx = self._selected_operation_index()
        if idx is None:
            return
        op = self.operations[idx]
        side_to_move, fullmove_number = self._current_fen_defaults()
        op.side_to_move = side_to_move
        op.fullmove_number = fullmove_number
        self._refresh_operations_list()
        self._set_current_operation(idx)
        self._update_lichess_link()

    # ------------------------------------------------------------------
    # Ajuste fino do diagrama (Sprint 6.2 e 6.3)
    # ------------------------------------------------------------------

    def _snap_selection_to_board(self) -> None:
        """Encosta a seleção nas bordas do tabuleiro que estiver embaixo dela."""
        if not self.current_render:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de ajustar a seleção.")
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(
                self, "Sem seleção", "Desenhe uma seleção em volta do diagrama."
            )
            return
        # O ajuste precisa só do detector (OpenCV); o classificador pode faltar.
        if not local_ocr.dependencies_available():
            QtWidgets.QMessageBox.information(
                self, "Ajuste indisponível", local_ocr.unavailable_reason()
            )
            return

        cursor_set = False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            # O ajuste só usa o detector por contorno: não carrega modelo, então
            # funciona mesmo sem o classificador instalado.
            from .local_ocr.engine import refine_rect

            refined = refine_rect(self.current_render.image_png, selection)
        except Exception as exc:
            logger.warning("Falha ao ajustar a seleção", exc_info=True)
            QtWidgets.QMessageBox.warning(self, "Falha ao ajustar", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        if refined is None:
            self.statusBar().showMessage(
                "Nenhuma borda de tabuleiro encontrada perto da seleção — nada foi alterado."
            )
            return

        before = selection
        self.page_widget.set_selection_rect(refined)
        moved = max(abs(refined[i] - before[i]) for i in range(4))
        self.statusBar().showMessage(
            f"Seleção ajustada à borda do tabuleiro (maior correção: {moved:.1f} px)."
        )
        self._schedule_preview_refresh(immediate=True)

    def _auto_orient_position(self) -> None:
        """Testa as 4 rotações e aplica a mais plausível. Repetir desfaz (§42).

        A heurística pode errar **com confiança**: um estudo de promoção mútua, em que
        os peões dos dois lados já passaram uns pelos outros, é lido como de cabeça
        para baixo com margem folgada (§42.1 tem o exemplo, tirado de um diagrama de
        livro real). Então o comando tem de ser reversível pelo mesmo atalho, e tem de
        mostrar em que se baseou — o motivo diz "peões apontam o sentido oposto", que é
        exatamente o que faz o usuário reconhecer o próprio caso.
        """
        piece_placement = self.board_editor.piece_placement()

        # Segundo toque, com a posição intacta desde o primeiro: desfaz.
        if self._auto_orient_undo is not None:
            previous, applied = self._auto_orient_undo
            if piece_placement == applied:
                self._auto_orient_undo = None
                self.board_editor.set_piece_placement(previous)
                self.statusBar().showMessage(
                    "Auto-orientação desfeita: a posição voltou como estava."
                )
                return
            # Mexeu na posição depois de girar: o desfazer perdeu o sentido.
            self._auto_orient_undo = None

        try:
            result = auto_orient(piece_placement)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Auto-orientar", f"Posição inválida: {exc}")
            return

        if not result.changed:
            detail = "; ".join(result.best.reasons) if result.best.reasons else ""
            self.statusBar().showMessage(
                "A orientação atual já é a mais plausível"
                + (f" ({detail})." if detail else ".")
            )
            return

        self.board_editor.set_piece_placement(result.piece_placement)
        self._auto_orient_undo = (piece_placement, result.piece_placement)
        message = f"Posição girada {result.rotation}° (vantagem {result.margin:.1f})"
        # Os motivos apareciam só quando *nada* girava — ou seja, faltavam justamente
        # quando o usuário precisa julgar se a decisão foi boa.
        reasons = "; ".join(result.runner_up.reasons or result.best.reasons)
        message += f" — {reasons}." if reasons else "."
        if result.ambiguous:
            message += " Margem apertada, confira antes de aplicar."
        message += " Ctrl+Shift+R de novo desfaz."
        self.statusBar().showMessage(message)

    def _export_report_dialog(self) -> None:
        """Relatório de alterações em CSV ou JSON (§6.4)."""
        if not (self.operations or self.erase_operations or self.candidates):
            QtWidgets.QMessageBox.information(
                self, "Relatório", "Não há alterações para relatar."
            )
            return

        suggested = "relatorio.csv"
        if self.current_pdf_path:
            suggested = str(Path(self.current_pdf_path).with_suffix("")) + "_relatorio.csv"
        file_path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Exportar relatório de alterações",
            suggested,
            "CSV (*.csv);;JSON (*.json)",
        )
        if not file_path:
            return
        # O diálogo do Qt não acrescenta extensão em todas as plataformas, e o formato
        # do relatório é decidido por ela.
        if not Path(file_path).suffix:
            file_path += ".json" if "json" in (selected_filter or "").lower() else ".csv"

        try:
            rows = export_report(
                file_path,
                operations=self.operations,
                erase_operations=self.erase_operations,
                candidates=self.candidates,
                source_pdf=self.current_pdf_path,
                extra={"motor": self._engine_mode()},
            )
        except Exception as exc:
            logger.exception("Falha ao exportar relatório para %s", file_path)
            QtWidgets.QMessageBox.critical(self, "Relatório", f"Falha ao exportar: {exc}")
            return

        with_warnings = sum(1 for row in rows if row.avisos)
        self.statusBar().showMessage(
            f"Relatório gravado: {file_path} ({len(rows)} linha(s), {with_warnings} com aviso)."
        )

    def _compare_projects_dialog(self) -> None:
        """Diff entre dois projetos salvos (§40).

        Compara arquivos, e não o estado em memória: a pergunta é "o que mudou entre
        o processamento de ontem e o de hoje", e os dois lados dela estão em disco.
        """
        before_path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Projeto anterior", "", "Projeto (*.json)"
        )
        if not before_path:
            return
        after_path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Projeto novo", before_path, "Projeto (*.json)"
        )
        if not after_path:
            return

        try:
            diff = diff_files(before_path, after_path)
        except Exception as exc:
            logger.exception("Falha ao comparar projetos")
            QtWidgets.QMessageBox.critical(
                self, "Comparar projetos", f"Não foi possível comparar: {exc}"
            )
            return

        self._show_project_diff(diff, before_path, after_path)

    def _show_project_diff(self, diff, before_path: str, after_path: str) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Comparar projetos")
        dialog.resize(900, 640)

        header = QtWidgets.QLabel(
            f"<b>antes:</b> {Path(before_path).name}<br><b>depois:</b> {Path(after_path).name}"
        )
        header.setWordWrap(True)
        warning = QtWidgets.QLabel("")
        warning.setWordWrap(True)
        if not diff.same_source:
            # Antes de qualquer número: comparar projetos de livros diferentes não
            # quer dizer nada, e o usuário tem de ler isso primeiro.
            warning.setText(
                "⚠ Os dois projetos apontam para PDFs diferentes. O diff abaixo "
                "provavelmente não quer dizer nada."
            )
            warning.setStyleSheet(f"color: {warning_text_color()};")

        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        text.setPlainText(format_diff(diff))
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        text.setFont(font)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy_button = buttons.addButton("Copiar", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(text.toPlainText())
        )
        buttons.rejected.connect(dialog.reject)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(header)
        if warning.text():
            layout.addWidget(warning)
        layout.addWidget(text, 1)
        layout.addWidget(buttons)
        self.project_diff_dialog = dialog
        dialog.exec()
        self.project_diff_dialog = None

    def _export_diagrams_dialog(self) -> None:
        """Um arquivo por diagrama substituído, para reaproveitar fora (§39)."""
        if not self.operations:
            QtWidgets.QMessageBox.information(
                self,
                "Exportar diagramas",
                "Nenhuma substituição para exportar. Adicione ao menos uma.",
            )
            return
        if self._diagram_export_worker is not None:
            QtWidgets.QMessageBox.information(
                self, "Exportar diagramas", "Já há uma exportação de diagramas em andamento."
            )
            return

        fmt, size, accepted = self._ask_diagram_export_options()
        if not accepted:
            return

        suggested = str(Path(self.current_pdf_path or ".").with_suffix("")) + "_diagramas"
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Pasta para os diagramas", suggested
        )
        if not out_dir:
            return

        total = len(self.operations)
        progress = QtWidgets.QProgressDialog(
            f"Exportando {total} diagrama(s)...", "Cancelar", 0, total, self
        )
        progress.setWindowTitle("Exportar diagramas")
        # Mesmo cuidado do PDF: o ciclo de vida do diálogo é nosso, senão cancelar
        # deixaria a thread órfã.
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(self._cancel_diagram_export)
        self._diagram_export_progress = progress

        worker = DiagramExportWorker(
            self.operations,
            out_dir,
            fmt=fmt,
            size_px=size,
            parent=self,
        )
        worker.done.connect(self._on_diagram_export_done)
        worker.failed.connect(self._on_diagram_export_failed)
        worker.canceled.connect(self._on_diagram_export_canceled)
        worker.progress.connect(self._on_diagram_export_progress)
        self._diagram_export_worker = worker
        self.statusBar().showMessage(f"Exportando diagramas para {out_dir}...")
        worker.start()

    def _ask_diagram_export_options(self) -> tuple[str, int, bool]:
        """Formato e tamanho, lembrados entre sessões."""
        saved_format = normalize_diagram_format(
            self.settings.value("diagram_export_format", DEFAULT_DIAGRAM_FORMAT, str)
        )
        saved_size = int(
            self.settings.value("diagram_export_size", DEFAULT_DIAGRAM_SIZE_PX, int)
            or DEFAULT_DIAGRAM_SIZE_PX
        )

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Exportar diagramas isolados")
        format_combo = QtWidgets.QComboBox()
        for value in DIAGRAM_FORMATS:
            format_combo.addItem(value.upper(), value)
        format_combo.setCurrentIndex(max(0, format_combo.findData(saved_format)))
        size_spin = QtWidgets.QSpinBox()
        size_spin.setRange(64, 4096)
        size_spin.setSingleStep(64)
        size_spin.setSuffix(" px")
        size_spin.setValue(max(64, saved_size))
        hint = QtWidgets.QLabel(
            "PNG e PDF usam o mesmo desenho do PDF exportado. SVG usa o desenho do "
            "python-chess, para abrir como vetor editável em outro programa.\n"
            f"Um {INDEX_NAME} acompanha os arquivos, com página e FEN de cada um."
        )
        hint.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Formato", format_combo)
        form.addRow("Tamanho", size_spin)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return (saved_format, saved_size, False)

        fmt = normalize_diagram_format(str(format_combo.currentData()))
        size = int(size_spin.value())
        self.settings.setValue("diagram_export_format", fmt)
        self.settings.setValue("diagram_export_size", size)
        return (fmt, size, True)

    def _cancel_diagram_export(self) -> None:
        if self._diagram_export_worker is not None:
            self._diagram_export_worker.cancel()
        if self._diagram_export_progress is not None:
            self._diagram_export_progress.setLabelText(
                "Parando... os diagramas já gravados serão mantidos."
            )

    def _on_diagram_export_progress(self, done: int, total: int) -> None:
        if self._diagram_export_progress is None:
            return
        self._diagram_export_progress.setMaximum(max(1, total))
        self._diagram_export_progress.setValue(done)
        self._diagram_export_progress.setLabelText(
            f"Exportando diagramas... {done} de {total}"
        )

    def _finish_diagram_export(self) -> None:
        if self._diagram_export_progress is not None:
            self._diagram_export_progress.close()
            self._diagram_export_progress = None
        if self._diagram_export_worker is not None:
            self._diagram_export_worker.wait()
            self._diagram_export_worker.deleteLater()
            self._diagram_export_worker = None

    def _on_diagram_export_done(self, written: int, failures: int, index_path: str) -> None:
        self._finish_diagram_export()
        message = f"{written} diagrama(s) exportado(s)."
        if failures:
            message += f" {failures} falharam — veja o log."
        if index_path:
            message += f"\nÍndice: {index_path}"
        self.statusBar().showMessage(f"{written} diagrama(s) exportado(s).")
        QtWidgets.QMessageBox.information(self, "Exportar diagramas", message)

    def _on_diagram_export_canceled(self, written: int, skipped: int) -> None:
        self._finish_diagram_export()
        # Ao contrário do PDF, cancelar aqui não desfaz nada: dizer quantos ficaram
        # é o que impede o usuário de achar que exportou o livro todo.
        self.statusBar().showMessage(
            f"Exportação interrompida: {written} gravado(s), {skipped} de fora."
        )
        QtWidgets.QMessageBox.information(
            self,
            "Exportar diagramas",
            f"Interrompido a seu pedido.\n{written} diagrama(s) gravado(s) e mantido(s); "
            f"{skipped} não foram exportados.",
        )

    def _on_diagram_export_failed(self, message: str) -> None:
        self._finish_diagram_export()
        self.statusBar().showMessage("Falha ao exportar diagramas.")
        QtWidgets.QMessageBox.critical(
            self, "Exportar diagramas", f"Falha ao exportar: {message}"
        )

    def _export_training_samples_dialog(self) -> None:
        """Manda as correções desta sessão para o dataset de treino (§6.5)."""
        if not self.operations or not self.pdf_service:
            QtWidgets.QMessageBox.information(
                self,
                "Correções para treino",
                "Não há substituições confirmadas para exportar.",
            )
            return

        destination = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Pasta do dataset (recebe samples/ e labels.csv)",
            self.settings.value("training_export_dir", "", str) or "",
        )
        if not destination:
            return
        self.settings.setValue("training_export_dir", destination)

        cursor_set = False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            exported = export_training_samples(
                destination,
                self.pdf_service,
                self.operations,
                source_pdf=self.current_pdf_path,
            )
        except Exception as exc:
            logger.exception("Falha ao exportar amostras de treino para %s", destination)
            QtWidgets.QMessageBox.critical(self, "Correções para treino", f"Falha: {exc}")
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        skipped = len(self.operations) - len(exported)
        message = f"{len(exported)} diagrama(s) exportado(s) para {destination}."
        if skipped:
            message += f" {skipped} ignorado(s) (região não renderizável)."
        self.statusBar().showMessage(message)

    def _on_board_changed(self, piece_placement: str) -> None:
        if self._loading_ui:
            return
        # Daqui para baixo é edição **da pessoa**: o reconhecimento preenche o
        # tabuleiro sob `_loading_ui`, que é a guarda que já distinguia os dois. Uma
        # posição mexida à mão deixa de ser a leitura do motor, e continuar carregando
        # a procedência dele mentiria no relatório e na fila de revisão (§59.17.2).
        self._last_ocr_result = None
        # Edicao manual: a posicao passa a pertencer a selecao ativa.
        self._anchor_from_selection()
        self._loading_ui = True
        self.fen_edit.setText(piece_placement)
        self._loading_ui = False
        self._update_warnings(piece_placement)
        self._update_lichess_link()
        self._update_edit_context_state()
        self._schedule_preview_refresh()

    def _on_fen_edited(self) -> None:
        if self._loading_ui:
            return
        # Mesma razão do `_on_board_changed`: FEN digitada é posição de humano.
        self._last_ocr_result = None
        text = self.fen_edit.text().strip()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
            self._anchor_from_selection()
            self._loading_ui = True
            self.board_editor.set_piece_placement(piece_placement)
            self.fen_edit.setText(piece_placement)
            self._loading_ui = False
            self._update_warnings(piece_placement)
            self._update_lichess_link()
            self._update_edit_context_state()
            self._schedule_preview_refresh()
        except Exception as exc:
            # Sair do campo com a FEN pela metade nao pode virar modal: o aviso
            # vai para o rotulo logo abaixo, que existe exatamente para isso.
            self._loading_ui = False
            self.warnings.setText(f"FEN inválida: {exc}")
            self._update_lichess_link()
            self._update_edit_context_state()

    def _update_warnings(self, piece_placement: str) -> None:
        try:
            warnings = validate_piece_placement(piece_placement)
        except Exception as exc:
            self.warnings.setText(f"Erro: {exc}")
            return
        # A auditoria de legalidade (§37) pega o que a validação da escrita deixa
        # passar: rei em xeque do lado errado, material que exigiria promoções que
        # não aconteceram. `LEGACY_CODES` sai porque `validate_piece_placement` já
        # disse aquilo com as suas palavras.
        side_to_move, _fullmove = self._current_fen_defaults()
        warnings = list(warnings) + legality.labels(
            legality.audit(piece_placement, side_to_move),
            skip_codes=legality.LEGACY_CODES,
        )
        self.warnings.setText("\n".join(warnings) if warnings else "")

    def _add_operation(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return

        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem seleção", "Selecione a área do diagrama na página.")
            return

        fen_text = self.fen_edit.text().strip()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(fen_text))
            validate_piece_placement(piece_placement)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN inválida", str(exc))
            return

        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )

        # A procedência do OCR só vale enquanto a posição ainda pertence à **área** de
        # onde ela foi lida (§59.17.2). `_last_ocr_result` nunca era zerado, então
        # depois do primeiro reconhecimento da sessão toda substituição montada à mão
        # nascia com `source="ocr"` e a confiança de outro diagrama — corrompendo as
        # duas coisas que dependem disso: a coluna `origem` do relatório (§26), que
        # existe para dizer se um humano olhou aquilo, e a fila de revisão (§29), que
        # ordena por confiança e acabava julgando um diagrama pelo número de outro.
        #
        # É o mesmo teste que `_draft_operation` já faz para a prévia; a outra metade
        # da rede está em `_on_board_changed`/`_on_fen_edited`, que zeram o resultado
        # quando a pessoa mexe no tabuleiro. Uma só das duas deixaria buraco: esta não
        # pega quem corrige a posição sem sair da área, e aquela não pega quem muda de
        # página sem tocar no tabuleiro.
        from_ocr = self._last_ocr_result is not None and self._position_matches_selection(rect_pdf)
        source = "ocr" if from_ocr else "manual"
        confidence = self._last_ocr_result.confidence if from_ocr else None
        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
        side_to_move, fullmove_number = self._current_fen_defaults()
        op = OverlayOperation(
            page_num=self.current_page,
            rect_pdf=rect_pdf,
            fen=piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
            source=source,
            confidence=confidence,
            whiteout_padding_pt=(pad_left + pad_top + pad_right + pad_bottom) / 4.0,
            whiteout_padding_left_pt=pad_left,
            whiteout_padding_top_pt=pad_top,
            whiteout_padding_right_pt=pad_right,
            whiteout_padding_bottom_pt=pad_bottom,
            border_width_pt=float(self.op_border_spin.value()),
        )
        self.operations.append(op)
        self._refresh_operations_list()
        self._set_current_operation(len(self.operations) - 1)
        self._select_change("operation", len(self.operations) - 1)
        self._refresh_page_overlays()
        self._update_edit_context_state()
        self._commit_history("adicionar substituição")
        self.statusBar().showMessage(f"Substituição adicionada. Total: {len(self.operations)}")

    def _refresh_operations_list(self) -> None:
        # Só reconstruía a `fen_ops_list`, que saiu (§51.4). O que sobrou — manter
        # os campos da aba FEN apontando para a substituição certa — continua
        # sendo o serviço que este método presta a quem o chama.
        selected_idx = self._selected_operation_index()
        if selected_idx is not None and 0 <= selected_idx < len(self.operations):
            self._current_operation_index = selected_idx
            self._select_operation_in_fen_tab(selected_idx)
        else:
            self._update_fen_meta_label(None)
        self._update_lichess_link()
        self._refresh_changes_list()
        self._update_edit_context_state()

    def _refresh_changes_list(self, selected: Optional[tuple[str, int]] = None) -> None:
        self._rebuild_changes_list(selected)
        # Qualquer mudanca na lista de alteracoes muda o resultado exportado.
        self._schedule_preview_refresh()

    def _rebuild_changes_list(self, selected: Optional[tuple[str, int]] = None) -> None:
        if selected is None:
            selected = self._selected_change()

        self.changes_list.clear()
        total_changes = len(self.operations) + len(self.erase_operations)
        if hasattr(self, "changes_label"):
            # O número da etapa fica: sem ele o fluxo da §20.2 mostrava 1, 2, 3, 4 e
            # depois um "Alterações" solto — e a contagem some no primeiro refresh,
            # que acontece já no arranque.
            self.changes_label.setText(f"4 · Alterações ({total_changes})")

        if total_changes == 0:
            item = QtWidgets.QListWidgetItem("Nenhuma alteração adicionada.")
            item.setData(QtCore.Qt.UserRole, None)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            item.setForeground(self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text))
            self.changes_list.addItem(item)
            self._update_edit_context_state()
            return

        for idx, op in enumerate(self.operations):
            text = (
                f"{idx + 1:03d} | Diagrama | pag {op.page_num + 1} | "
                f"{op.fen[:28]}{'...' if len(op.fen) > 28 else ''}"
            )
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, ("operation", idx))
            self.changes_list.addItem(item)

        offset = len(self.operations)
        for idx, op in enumerate(self.erase_operations):
            x0, y0, x1, y1 = op.rect_pdf
            w = max(0.0, x1 - x0)
            h = max(0.0, y1 - y0)
            item = QtWidgets.QListWidgetItem(
                f"{offset + idx + 1:03d} | Apagamento | pag {op.page_num + 1} | {w:.1f}x{h:.1f} pt"
            )
            item.setData(QtCore.Qt.UserRole, ("eraser", idx))
            self.changes_list.addItem(item)

        if selected is None:
            self._update_edit_context_state()
            return

        for row in range(self.changes_list.count()):
            item = self.changes_list.item(row)
            if item and item.data(QtCore.Qt.UserRole) == selected:
                self._loading_ui = True
                self.changes_list.setCurrentRow(row)
                self._loading_ui = False
                break
        self._update_edit_context_state()

    def _select_change(self, kind: str, idx: int) -> None:
        for row in range(self.changes_list.count()):
            item = self.changes_list.item(row)
            if item and item.data(QtCore.Qt.UserRole) == (kind, idx):
                self.changes_list.setCurrentRow(row)
                return

    def _add_eraser_from_selection(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem seleção", "Selecione uma área para apagar.")
            return
        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )
        self.erase_operations.append(EraseOperation(page_num=self.current_page, rect_pdf=rect_pdf))
        self._refresh_erasers_list()
        self._select_change("eraser", len(self.erase_operations) - 1)
        self._refresh_page_overlays()
        self._update_edit_context_state()
        self._commit_history("adicionar apagamento")
        self.statusBar().showMessage(f"Apagamento adicionado. Total: {len(self.erase_operations)}")

    def _refresh_erasers_list(self) -> None:
        self._refresh_changes_list()
        self._update_edit_context_state()

    def _focus_eraser(self, idx: int) -> None:
        if not (0 <= idx < len(self.erase_operations)) or not self.pdf_service:
            return
        op = self.erase_operations[idx]
        self.current_page = op.page_num
        self._render_current_page()
        if self.current_render:
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                op.rect_pdf,
                self.current_render.matrix,
            )
            self.page_widget.set_selection_rect(rect_img)
        self._select_change("eraser", idx)

    def _remove_operation_at_index(self, idx: int) -> None:
        if not (0 <= idx < len(self.operations)):
            return

        del self.operations[idx]
        self._refresh_operations_list()

        if self.operations:
            next_idx = min(idx, len(self.operations) - 1)
            self._set_current_operation(next_idx)
            self._focus_operation(next_idx)
            self._select_change("operation", next_idx)
        else:
            self.page_widget.clear_selection()
            self._update_lichess_link()

        self._refresh_page_overlays()
        self._update_edit_context_state()
        self._commit_history("remover substituição")
        self.statusBar().showMessage(f"Substituição removida. Total: {len(self.operations)}")

    def _remove_selected_change(self) -> None:
        selected = self._selected_change()
        if selected is None:
            return
        kind, idx = selected
        if kind == "operation":
            self._remove_operation_at_index(idx)
            return
        if kind == "eraser" and 0 <= idx < len(self.erase_operations):
            del self.erase_operations[idx]
            self._refresh_erasers_list()
            self._refresh_page_overlays()
            if self.erase_operations:
                self._select_change("eraser", min(idx, len(self.erase_operations) - 1))
            self._commit_history("remover apagamento")
            self.statusBar().showMessage(f"Apagamento removido. Total: {len(self.erase_operations)}")

    def _clear_operations(self) -> None:
        if not self.operations:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Limpar substituições",
            "Remover todas as substituições pendentes?",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.operations.clear()
            self._refresh_operations_list()
            self._refresh_page_overlays()
            self._update_edit_context_state()
            self._commit_history("limpar substituições")

    def _clear_changes(self) -> None:
        if not self.operations and not self.erase_operations:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Limpar alterações",
            "Remover todas as substituições e apagamentos pendentes?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.operations.clear()
        self.erase_operations.clear()
        self._refresh_operations_list()
        self._refresh_erasers_list()
        self._refresh_page_overlays()
        self.page_widget.clear_selection()
        self._update_edit_context_state()
        self._commit_history("limpar alterações")

    def _save_output_pdf(self, auto_save_path: Optional[str] = None) -> None:
        if not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        if not self.operations and not self.erase_operations:
            QtWidgets.QMessageBox.warning(
                self,
                "Sem alterações",
                "Nenhuma substituição ou apagamento foi adicionado.",
            )
            return
        if self._export_worker is not None and self._export_worker.isRunning():
            QtWidgets.QMessageBox.information(
                self,
                "Exportar PDF",
                "Já existe uma exportação em andamento.",
            )
            return

        if auto_save_path:
            out_path = auto_save_path
            if Path(out_path).exists():
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Substituir arquivo",
                    f"Já existe um arquivo em:\n{out_path}\n\nSubstituir?",
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    self.statusBar().showMessage("Exportação automática cancelada.")
                    return
        else:
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Salvar PDF de saída",
                str(Path(self.current_pdf_path).with_name(Path(self.current_pdf_path).stem + "_hq.pdf")),
                "PDF (*.pdf)",
            )
            if not out_path:
                return

        # A gravacao roda num worker: um livro grande com centenas de diagramas
        # levava dezenas de segundos com a janela congelada (Sprint 5.1).
        progress = QtWidgets.QProgressDialog("Exportando PDF...", "Cancelar", 0, 0, self)
        progress.setWindowTitle("Exportar PDF")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        # O ciclo de vida do dialogo e nosso: ele so fecha quando o worker
        # confirmar que terminou, senao um cancelamento deixaria a thread orfa.
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(self._cancel_export)
        self._export_progress = progress

        worker = ExportWorker(
            self.current_pdf_path,
            out_path,
            self.operations,
            erase_operations=self.erase_operations,
            whiteout=self.whiteout_check.isChecked(),
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            erase_coordinates=self.erase_coordinates_check.isChecked(),
            parent=self,
        )
        worker.done.connect(self._on_export_done)
        worker.failed.connect(self._on_export_failed)
        worker.canceled.connect(self._on_export_canceled)
        worker.progress.connect(self._on_export_progress)
        self._export_worker = worker
        self.statusBar().showMessage(f"Exportando para {out_path}...")
        worker.start()

    def _cancel_export(self) -> None:
        if self._export_worker is not None:
            self._export_worker.cancel()
        if self._export_progress is not None:
            self._export_progress.setLabelText("Cancelando a exportação...")

    def _on_export_progress(self, done: int, total: int) -> None:
        if self._export_progress is None:
            return
        self._export_progress.setMaximum(max(1, total))
        self._export_progress.setValue(done)
        self._export_progress.setLabelText(f"Exportando PDF... página {done} de {total}")

    def _finish_export(self) -> None:
        if self._export_progress is not None:
            self._export_progress.close()
            self._export_progress = None
        if self._export_worker is not None:
            self._export_worker.wait()
            self._export_worker.deleteLater()
            self._export_worker = None

    def _on_export_done(self, out_path: str) -> None:
        self._finish_export()
        self.statusBar().showMessage(f"PDF salvo em {out_path}")
        QtWidgets.QMessageBox.information(self, "Concluído", f"PDF salvo em:\n{out_path}")

    def _on_export_canceled(self) -> None:
        self._finish_export()
        # Sem modal: o usuário acabou de clicar em Cancelar e sabe o que pediu.
        # A frase importante é a de que nada foi gravado.
        self.statusBar().showMessage("Exportação cancelada. Nenhum arquivo foi gravado.")

    def _on_export_failed(self, message: str) -> None:
        self._finish_export()
        self.statusBar().showMessage("Falha ao exportar o PDF.")
        QtWidgets.QMessageBox.critical(self, "Erro ao exportar", message)

    def _save_project_dialog(self) -> None:
        if not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de salvar projeto.")
            return
        # Um autosave da sessao anterior nao serve de sugestao de nome: ele mora
        # no diretorio interno do app, nao onde o usuario guarda os projetos.
        suggested = self.project_path if self.project_path and not is_autosave_path(self.project_path) else ""
        project_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salvar projeto", suggested or "project_state.json", "JSON (*.json)"
        )
        if not project_path:
            return

        state = self._current_project_state()
        if state is None:
            return
        try:
            write_project_atomically(project_path, state)
        except Exception as exc:
            logger.exception("Falha ao salvar projeto em %s", project_path)
            QtWidgets.QMessageBox.critical(self, "Erro ao salvar projeto", str(exc))
            return
        self.project_path = project_path
        self._autosave_dirty = False
        self._remember_last_project_path(project_path)
        self.statusBar().showMessage(f"Projeto salvo: {project_path}")

    def _load_project_from_path(self, project_path: str, show_dialogs: bool = True) -> bool:
        try:
            state, migration = load_project_state_with_report(project_path)
        except ProjectSchemaError as exc:
            # Formato mais novo que este app: carregar descartaria campos e o
            # autosave gravaria a perda por cima em até 2 minutos. Recusar é o
            # comportamento seguro, e o usuário precisa saber por quê.
            logger.warning("Projeto recusado por schema incompatível: %s", project_path)
            if show_dialogs:
                QtWidgets.QMessageBox.critical(self, "Projeto de versão mais nova", str(exc))
            return False
        except Exception as exc:
            logger.warning("Falha ao carregar o projeto %s", project_path, exc_info=True)
            if show_dialogs:
                QtWidgets.QMessageBox.critical(self, "Erro ao carregar projeto", str(exc))
            return False

        if not Path(state.source_pdf).exists():
            if show_dialogs:
                QtWidgets.QMessageBox.warning(
                    self,
                    "PDF não encontrado",
                    f"O PDF original não existe:\n{state.source_pdf}",
                )
            return False

        try:
            self._open_pdf(state.source_pdf, clear_ops=False)
        except Exception as exc:
            # O PDF existe mas não abre (§59.5). Recusar o projeto inteiro é o certo:
            # sem o livro não há o que editar, e seguir carregaria as operações sobre
            # o livro **anterior**, que continua aberto.
            logger.warning("Projeto %s aponta para um PDF que não abre", project_path, exc_info=True)
            if show_dialogs:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Erro ao carregar projeto",
                    f"O PDF do projeto não pôde ser aberto:\n{state.source_pdf}\n\n{exc}",
                )
            return False
        self.operations = state.operations
        self.erase_operations = state.erase_operations
        self.study_positions = state.study_positions
        self.candidates = list(getattr(state, "candidates", []))
        self._position_anchor = None
        self.include_lichess_link_check.setChecked(bool(getattr(state, "include_lichess_link", True)))
        self.erase_coordinates_check.setChecked(bool(getattr(state, "erase_coordinates", False)))
        self.ocr_full_next_page = max(0, int(getattr(state, "ocr_full_next_page", 0)))
        self.current_page = min(
            max(0, state.current_page),
            (self.pdf_service.page_count - 1) if self.pdf_service else 0,
        )
        self.project_path = project_path
        self._remember_last_project_path(project_path)
        self._refresh_operations_list()
        self._refresh_erasers_list()
        self._refresh_study_positions_list()
        self._refresh_candidates_list()
        self._render_current_page()
        # O projeto recem-carregado e a linha de base: nao da para desfazer
        # "para tras" dele, e nada esta pendente de gravacao.
        self._reset_history("carregar projeto")
        self._autosave_dirty = False
        self._start_autosave_timer()

        try:
            current_fp = fingerprint_file(state.source_pdf)
            if state.source_pdf_fingerprint and current_fp.get("sha256") != state.source_pdf_fingerprint.get("sha256"):
                logger.warning(
                    "Fingerprint divergente ao carregar %s (PDF: %s)", project_path, state.source_pdf
                )
                if show_dialogs:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Aviso de integridade",
                        "O PDF atual difere do PDF usado quando o projeto foi salvo.",
                    )
        except Exception:
            logger.warning("Não foi possível conferir o fingerprint de %s", state.source_pdf, exc_info=True)

        logger.info(
            "Projeto carregado: %s (%d substituições, %d candidatos)",
            project_path,
            len(self.operations),
            len(self.candidates),
        )
        if migration.migrated:
            # O próximo salvamento grava no formato novo; isso não pode ser surpresa.
            self.statusBar().showMessage(
                f"Projeto carregado e atualizado: {migration.describe()}."
            )
        else:
            self.statusBar().showMessage(f"Projeto carregado: {project_path}")
        return True

    def _load_project_dialog(self) -> None:
        project_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Carregar projeto", self.project_path or "", "JSON (*.json)"
        )
        if not project_path:
            return
        self._load_project_from_path(project_path, show_dialogs=True)


def self_test() -> int:
    """Confere que o app se encontra por dentro. Usado pelo build (Sprint 8.1).

    Existe por causa de um modo de falha específico do empacotamento: caminhos que
    funcionam rodando do repositório (`Path(__file__).parents[N]`, `Path.cwd()`)
    param de funcionar dentro do executável, e o sintoma aparece só quando alguém
    abre o `.exe` numa máquina limpa — o app abre, mas o motor local diz "modelo
    não encontrado" sem dizer por quê.

    Não abre janela: constrói a `MainWindow` offscreen, que é o que exercita de
    verdade a carga de assets, e imprime o que achou.

    **Não toca o perfil do usuário.** A janela recebe um `QSettings` descartável e
    o autosave vai para um diretório temporário — sem isso, um auto-teste de build
    reabriria a última sessão real (PDF e projeto inclusive) e o `closeEvent`
    poderia gravar por cima do trabalho de alguém.
    """
    import os
    import tempfile
    import time

    from .resources import asset_roots, build_variant, is_frozen, is_light_build

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    problems: list[str] = []

    print(f"congelado: {is_frozen()}")
    print(f"variante: {build_variant()}")
    print("raízes de assets:")
    for root in asset_roots():
        print(f"  - {root}")

    if is_light_build():
        # O contrato da variante light é o **oposto** do da completa, então o
        # auto-teste checa o oposto: torch ou o classificador presentes aqui
        # significam que a exclusão do `.spec` não pegou, e o download "menor" saiu
        # com as centenas de MB que ele existe para não ter (§44.4). A construção da
        # janela, logo abaixo, continua valendo — é o que prova que o app abre.
        if local_ocr.dependencies_available():
            problems.append(
                "build light, mas as dependências do motor local estão no bundle — "
                "a exclusão do .spec não pegou"
            )
        else:
            print(f"motor local ausente, como esperado: {local_ocr.unavailable_reason()}")
        bundled = local_ocr.bundled_model_path()
        if bundled.is_file():
            problems.append(f"build light, mas o classificador veio no bundle: {bundled}")
    else:
        model = local_ocr.default_model_path()
        if model is None:
            problems.append(f"classificador não encontrado ({local_ocr.unavailable_reason()})")
        else:
            print(f"classificador: {model}")

        if not local_ocr.dependencies_available():
            # A razão importa: num bundle, "ausente" quase sempre quer dizer
            # "excluído por engano no .spec", e o nome do módulo que falhou é o que
            # aponta qual.
            problems.append(local_ocr.unavailable_reason())
        elif model is not None:
            # Carregar os pesos de verdade é o que prova que o torch empacotado e o
            # `.pt` empacotado funcionam **juntos**. Só importar torch não prova.
            started = time.perf_counter()
            try:
                local_ocr.get_recognizer().warm_up()
            except Exception as exc:
                problems.append(f"o classificador não carregou: {exc}")
            else:
                print(
                    f"classificador carregado em {(time.perf_counter() - started) * 1000:.0f} ms"
                )

    try:
        with tempfile.TemporaryDirectory(prefix="chess-pdf-selftest-") as scratch:
            os.environ["CHESS_PDF_EDITOR_AUTOSAVE_DIR"] = scratch
            settings = QtCore.QSettings(
                str(Path(scratch) / "settings.ini"), QtCore.QSettings.IniFormat
            )
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            window = MainWindow(settings=settings)
            engine_mode = window._engine_mode()
            has_pieces = bool(window.board_editor._buttons)
            window.close()
            del app
        print(f"janela construída; motor padrão: {engine_mode}; tabuleiro: {has_pieces}")
    except Exception as exc:  # pragma: no cover - caminho de build
        problems.append(f"falha ao construir a janela: {exc}")

    if problems:
        for problem in problems:
            print(f"FALHA: {problem}", file=sys.stderr)
        return 1
    print("auto-teste: tudo no lugar")
    return 0


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        setup_logging()
        sys.exit(self_test())

    setup_logging()
    logger.info("Chess PDF Editor iniciando (log em %s)", log_file_path() or "stderr")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Chess PDF Editor")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
