from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

import chess
from PySide6 import QtCore, QtGui, QtWidgets

from . import local_ocr
from .autosave import (
    DEFAULT_INTERVAL_SEC,
    MIN_INTERVAL_SEC,
    autosave_path_for_pdf,
    is_autosave_path,
    write_project_atomically,
)
from .feedback import export_training_samples
from .fen import extract_piece_placement, normalize_piece_placement, validate_piece_placement
from .history import ChangeHistory
from .logging_config import get_logger, log_file_path, setup_logging
from .ocr_api import default_endpoint
from .orientation import auto_orient
from .pdf_service import (
    PdfService,
    RenderedPage,
    clear_board_render_cache,
    crop_from_rendered_page,
)
from .project_state import (
    ProjectSchemaError,
    ProjectState,
    fingerprint_file,
    load_project_state_with_report,
)
from .recognition import (
    DEFAULT_ENGINE_MODE,
    ENGINE_LABELS,
    ENGINE_LOCAL,
    ENGINE_MODES,
    ENGINE_REMOTE,
    RecognitionError,
    make_engine,
    mode_uses_network,
    normalize_mode,
)
from .report import export_report
from .types import EraseOperation, OcrBoardResult, OverlayOperation, StudyPosition
from .widgets import BeforeAfterWidget, BoardEditorWidget, SelectablePageWidget, StudyBoardWidget
from .workers import BatchOcrWorker, ExportWorker

logger = get_logger("app")


def is_dark_theme() -> bool:
    """Tema escuro ativo? Usado so onde a cor nao pode vir da paleta."""
    try:
        scheme = QtWidgets.QApplication.styleHints().colorScheme()
        if scheme == QtCore.Qt.ColorScheme.Dark:
            return True
        if scheme == QtCore.Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QtGui.QPalette.Window).lightness() < 128


def comment_highlight_colors() -> tuple[QtGui.QColor, QtGui.QColor]:
    """Fundo e texto do marcador de lance comentado."""
    if is_dark_theme():
        return (QtGui.QColor("#4a3b00"), QtGui.QColor("#ffe9a3"))
    return (QtGui.QColor("#fff7d6"), QtGui.QColor("#5f3b00"))


def warning_text_color() -> str:
    return "#ff8a8a" if is_dark_theme() else "#b02020"


class StudyPanel(QtWidgets.QWidget):
    about_to_change_line = QtCore.Signal()
    pgn_imported = QtCore.Signal(object)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tabuleiro de Estudo")
        self.resize(980, 760)
        self._syncing_move_list = False
        self._pgn_provider: Optional[Callable[[], str]] = None
        self._commented_plies: set[int] = set()
        self._commented_paths: set[str] = set()

        self.study_board = StudyBoardWidget(cell_size=58)
        self.study_board.state_changed.connect(self._on_state_changed)
        self.study_board.line_changed.connect(self._on_line_changed)

        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItem("Brancas", "w")
        self.side_combo.addItem("Pretas", "b")
        self.arrow_check = QtWidgets.QCheckBox("Seta do último lance")
        self.arrow_check.setChecked(True)
        self.arrow_check.toggled.connect(self.study_board.set_show_last_move_arrow)

        self.btn_undo = QtWidgets.QPushButton("Desfazer")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo = QtWidgets.QPushButton("Refazer")
        self.btn_redo.clicked.connect(self._redo)
        self.btn_reset = QtWidgets.QPushButton("Resetar Linha")
        self.btn_reset.clicked.connect(self._reset)
        self.btn_prev_variation = QtWidgets.QPushButton("Var. anterior")
        self.btn_prev_variation.clicked.connect(lambda: self._switch_variation(-1))
        self.btn_next_variation = QtWidgets.QPushButton("Var. próxima")
        self.btn_next_variation.clicked.connect(lambda: self._switch_variation(1))
        self.btn_flip = QtWidgets.QPushButton("Virar Tabuleiro")
        self.btn_flip.clicked.connect(self.study_board.flip_board)
        self.btn_copy_fen = QtWidgets.QPushButton("Copiar FEN")
        self.btn_copy_fen.clicked.connect(self._copy_fen)
        self.btn_copy_pgn = QtWidgets.QPushButton("Copiar PGN")
        self.btn_copy_pgn.clicked.connect(self._copy_pgn)
        self.btn_save_pgn = QtWidgets.QPushButton("Salvar PGN")
        self.btn_save_pgn.clicked.connect(self._save_pgn)
        self.btn_import_pgn = QtWidgets.QPushButton("Importar PGN")
        self.btn_import_pgn.clicked.connect(self._import_pgn)

        self.moves_tree = QtWidgets.QTreeWidget()
        self.moves_tree.setHeaderLabels(["Lance", "SAN"])
        self.moves_tree.setUniformRowHeights(True)
        self.moves_tree.setAlternatingRowColors(True)
        self.moves_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.moves_tree.itemClicked.connect(self._on_san_tree_item_clicked)

        self.status_label = QtWidgets.QLabel("Clique nas peças para estudar linhas legais.")
        self.status_label.setWordWrap(True)

        controls_top = QtWidgets.QHBoxLayout()
        controls_top.addWidget(QtWidgets.QLabel("Vez de jogar:"))
        controls_top.addWidget(self.side_combo)
        controls_top.addWidget(self.arrow_check)
        controls_top.addWidget(self.btn_import_pgn)
        controls_top.addStretch(1)

        controls_1 = QtWidgets.QHBoxLayout()
        controls_1.addWidget(self.btn_undo)
        controls_1.addWidget(self.btn_redo)
        controls_1.addWidget(self.btn_reset)
        controls_1.addWidget(self.btn_flip)

        controls_2 = QtWidgets.QHBoxLayout()
        controls_2.addWidget(self.btn_prev_variation)
        controls_2.addWidget(self.btn_next_variation)
        controls_2.addWidget(self.btn_copy_fen)
        controls_2.addWidget(self.btn_copy_pgn)
        controls_2.addWidget(self.btn_save_pgn)

        side_panel = QtWidgets.QVBoxLayout()
        side_panel.addWidget(QtWidgets.QLabel("Lista SAN"))
        side_panel.addWidget(self.moves_tree, 1)

        side_widget = QtWidgets.QWidget()
        side_widget.setLayout(side_panel)
        side_widget.setMinimumWidth(250)

        self.study_center_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.study_center_splitter.setChildrenCollapsible(False)
        self.study_center_splitter.addWidget(self.study_board)
        self.study_center_splitter.addWidget(side_widget)
        self.study_center_splitter.setStretchFactor(0, 3)
        self.study_center_splitter.setStretchFactor(1, 2)
        self.study_center_splitter.setSizes([620, 300])

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(controls_top)
        root.addLayout(controls_1)
        root.addLayout(controls_2)
        root.addWidget(self.status_label)
        root.addWidget(self.study_center_splitter, 1)
        self._on_line_changed([], 0)

    def set_pgn_provider(self, provider: Optional[Callable[[], str]]) -> None:
        self._pgn_provider = provider

    def set_commented_plies(self, plies: object) -> None:
        self._commented_plies = set()
        self._commented_paths = set()
        for value in plies or []:
            if isinstance(value, int) or str(value).isdigit():
                self._commented_plies.add(int(value))
            elif str(value).strip():
                self._commented_paths.add(str(value))
        self._on_line_changed(self.study_board.san_line(), self.study_board.current_ply())

    def _export_pgn_text(self) -> str:
        if self._pgn_provider is not None:
            return self._pgn_provider()
        return self.study_board.current_pgn()

    def load_piece_placement(
        self,
        piece_placement: str,
        side_to_move: Optional[str] = None,
        fullmove_number: int = 1,
    ) -> None:
        side = str(side_to_move or self.side_combo.currentData() or "w")
        if side not in {"w", "b"}:
            side = "w"
        fullmove = max(1, int(fullmove_number))
        self.side_combo.setCurrentIndex(1 if side == "b" else 0)
        try:
            normalized = normalize_piece_placement(extract_piece_placement(piece_placement))
            self.study_board.set_start_fen(f"{normalized} {side} - - 0 {fullmove}")
            self.set_commented_plies(set())
            self.status_label.setText("Posição carregada do editor.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN inválida", str(exc))

    def _undo(self) -> None:
        self.about_to_change_line.emit()
        if not self.study_board.undo_move():
            self.status_label.setText("Nada para desfazer.")

    def _redo(self) -> None:
        self.about_to_change_line.emit()
        if not self.study_board.redo_move():
            self.status_label.setText("Nada para refazer.")

    def _reset(self) -> None:
        self.about_to_change_line.emit()
        self.study_board.clear_moves()

    def _switch_variation(self, offset: int) -> None:
        self.about_to_change_line.emit()
        if not self.study_board.select_sibling_variation(offset):
            self.status_label.setText("Não há outra variante neste lance.")

    def _copy_fen(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.study_board.current_fen())
        self.status_label.setText("FEN copiado.")

    def _copy_pgn(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self._export_pgn_text())
        self.status_label.setText("PGN copiado.")

    def _save_pgn(self) -> None:
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Salvar PGN",
            "study_game.pgn",
            "PGN (*.pgn)",
        )
        if not out_path:
            return
        try:
            Path(out_path).write_text(self._export_pgn_text(), encoding="utf-8")
            self.status_label.setText(f"PGN salvo em: {out_path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao salvar PGN", str(exc))

    def _import_pgn(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Importar PGN",
            "",
            "PGN (*.pgn);;Todos os arquivos (*.*)",
        )
        if not file_path:
            return
        try:
            self.about_to_change_line.emit()
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            move_comments = self.study_board.load_pgn_text(text)
            self.pgn_imported.emit(move_comments)
            self.status_label.setText(f"PGN importado: {file_path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao importar PGN", str(exc))

    def _on_san_tree_item_clicked(self, item: QtWidgets.QTreeWidgetItem, col: int) -> None:
        del col
        if self._syncing_move_list:
            return
        path_key = str(item.data(0, QtCore.Qt.UserRole) or "")
        if not path_key:
            return
        self.about_to_change_line.emit()
        self.study_board.goto_path(path_key)

    @staticmethod
    def _format_san_rows(
        moves: list[str],
        start_turn: str,
        start_fullmove_number: int,
    ) -> list[tuple[str, Optional[str], Optional[int], Optional[str], Optional[int]]]:
        rows: list[tuple[str, Optional[str], Optional[int], Optional[str], Optional[int]]] = []
        side = "b" if start_turn == "b" else "w"
        move_no = max(1, int(start_fullmove_number))
        ply_idx = 0
        while ply_idx < len(moves):
            if side == "b":
                rows.append((f"{move_no}...", None, None, moves[ply_idx], ply_idx + 1))
                ply_idx += 1
                move_no += 1
                side = "w"
                continue

            white_san = moves[ply_idx]
            white_ply = ply_idx + 1
            ply_idx += 1
            if ply_idx < len(moves):
                black_san = moves[ply_idx]
                black_ply = ply_idx + 1
                rows.append((f"{move_no}.", white_san, white_ply, black_san, black_ply))
                ply_idx += 1
                move_no += 1
                side = "w"
            else:
                rows.append((f"{move_no}.", white_san, white_ply, None, None))
                side = "b"
        return rows

    def _on_line_changed(self, san_line: object, cursor: int) -> None:
        del san_line, cursor
        self._syncing_move_list = True
        try:
            self._refresh_san_tree()
        finally:
            self._syncing_move_list = False

    def _refresh_san_tree(self) -> None:
        self.moves_tree.clear()
        selected_item: Optional[QtWidgets.QTreeWidgetItem] = None

        def add_entries(
            parent: QtWidgets.QTreeWidget | QtWidgets.QTreeWidgetItem,
            entries: list[dict[str, object]],
        ) -> None:
            nonlocal selected_item
            for entry in entries:
                path = str(entry.get("path", ""))
                ply = int(entry.get("ply", 0))
                san = str(entry.get("san", ""))
                has_comment = path in self._commented_paths or ply in self._commented_plies
                item = QtWidgets.QTreeWidgetItem([str(entry.get("label", "")), f"{san} *" if has_comment else san])
                item.setData(0, QtCore.Qt.UserRole, path)
                if has_comment:
                    font = item.font(1)
                    font.setBold(True)
                    item.setFont(1, font)
                    background, foreground = comment_highlight_colors()
                    item.setBackground(1, background)
                    item.setForeground(1, foreground)
                    item.setToolTip(1, "Este lance tem comentário.")
                if bool(entry.get("current", False)):
                    selected_item = item
                parent.addChild(item) if isinstance(parent, QtWidgets.QTreeWidgetItem) else parent.addTopLevelItem(item)
                add_entries(item, list(entry.get("children", [])))

        add_entries(self.moves_tree, self.study_board.move_tree())
        self.moves_tree.expandAll()
        self.moves_tree.resizeColumnToContents(0)
        if selected_item is not None:
            self.moves_tree.setCurrentItem(selected_item)
        else:
            self.moves_tree.clearSelection()

    def _on_state_changed(self, message: str) -> None:
        self.status_label.setText(message)


class StudyDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tabuleiro de Estudo")
        self.resize(980, 760)
        self.panel = StudyPanel(self)
        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self.panel)

    def load_piece_placement(
        self,
        piece_placement: str,
        side_to_move: Optional[str] = None,
        fullmove_number: int = 1,
    ) -> None:
        self.panel.load_piece_placement(
            piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
        )


class MainWindow(QtWidgets.QMainWindow):
    # Azul de destaque com texto branco funciona em tema claro e escuro; o resto
    # sai da paleta do sistema para nao sumir quando o tema muda.
    _PRIMARY_BUTTON_STYLE = (
        "QPushButton { background-color: #1f6feb; color: #ffffff; font-weight: 600; "
        "padding: 6px 10px; border-radius: 4px; } "
        "QPushButton:hover { background-color: #3b82f6; } "
        "QPushButton:disabled { background-color: palette(button); color: palette(mid); }"
    )
    _SECONDARY_BUTTON_STYLE = ""
    _CONTEXT_STYLE = (
        "QLabel { background-color: palette(alternate-base); color: palette(window-text); "
        "border: 1px solid palette(mid); border-radius: 5px; padding: 8px; }"
    )
    _SECTION_STYLE = "QLabel { font-weight: 600; margin-top: 6px; }"

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
        self.study_dialog: Optional[StudyDialog] = None

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
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self._refresh_result_preview)

        self.page_widget = SelectablePageWidget()
        self.page_widget.selection_changed.connect(self._on_selection_changed)
        self.page_widget.point_clicked.connect(self._on_page_clicked)
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
            self.settings.value("recognition_engine", DEFAULT_ENGINE_MODE, str)
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
        self.include_lichess_link_check = QtWidgets.QCheckBox("Incluir link Lichess no PDF exportado")
        self.include_lichess_link_check.setChecked(bool(self.settings.value("include_lichess_link", True, bool)))
        self.include_lichess_link_check.toggled.connect(
            lambda checked: self.settings.setValue("include_lichess_link", bool(checked))
        )
        self.include_lichess_link_check.toggled.connect(
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
        self.fen_ops_list = QtWidgets.QListWidget()
        self.fen_ops_list.itemClicked.connect(self._on_fen_operation_clicked)
        self.fen_ops_list_delete_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Delete),
            self.fen_ops_list,
        )
        self.fen_ops_list_delete_shortcut.activated.connect(self._remove_selected_fen_operation)
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

        self.auto_apply_check = QtWidgets.QCheckBox("Aplicar automaticamente ao reconhecer página/PDF")
        self.auto_apply_check.setChecked(bool(self.settings.value("auto_apply_recognition", False, bool)))
        self.auto_apply_check.setToolTip(
            "Desligado: as detecções entram na fila de candidatos para você conferir antes de aplicar."
        )
        self.auto_apply_check.toggled.connect(self._on_auto_apply_toggled)

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
        self.lichess_link_label = QtWidgets.QLabel()
        self.lichess_link_label.setTextFormat(QtCore.Qt.RichText)
        self.lichess_link_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.lichess_link_label.setOpenExternalLinks(True)
        self.lichess_link_label.setToolTip("Abrir posição atual no Lichess")
        self.btn_add_eraser = QtWidgets.QPushButton("Adicionar apagamento")
        self.btn_add_eraser.clicked.connect(self._add_eraser_from_selection)
        self.btn_remove = QtWidgets.QPushButton("Remover")
        self.btn_remove.clicked.connect(self._remove_selected_change)
        self.btn_remove_fen = QtWidgets.QPushButton("Remover posição")
        self.btn_remove_fen.clicked.connect(self._remove_selected_fen_operation)
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

        self.btn_rotate = QtWidgets.QPushButton("Rotacionar 90°")
        self.btn_rotate.clicked.connect(self.board_editor.rotate_clockwise)
        self.btn_flip = QtWidgets.QPushButton("Espelhar Vertical")
        self.btn_flip.clicked.connect(self.board_editor.flip_vertical)
        self.btn_auto_orient = QtWidgets.QPushButton("Auto-orientar")
        self.btn_auto_orient.setToolTip(
            "Testa as 4 rotações e aplica a mais plausível (reis, peões e sentido do avanço)."
        )
        self.btn_auto_orient.clicked.connect(self._auto_orient_position)
        self.btn_clear_board = QtWidgets.QPushButton("Limpar Tabuleiro")
        self.btn_clear_board.clicked.connect(self.board_editor.clear_board)

        top_editor = QtWidgets.QWidget()
        top_editor_layout = QtWidgets.QVBoxLayout(top_editor)
        top_editor_layout.addWidget(QtWidgets.QLabel("Editor de Tabuleiro"))
        top_editor_layout.addWidget(self.board_editor, 0, QtCore.Qt.AlignLeft)
        controls = QtWidgets.QGridLayout()
        controls.addWidget(self.btn_auto_orient, 0, 0, 1, 2)
        controls.addWidget(self.btn_rotate, 1, 0)
        controls.addWidget(self.btn_flip, 1, 1)
        controls.addWidget(self.btn_clear_board, 2, 0, 1, 2)
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
        ocr_tab_layout.addWidget(self.auto_apply_check)
        ocr_tab_layout.addWidget(self.engine_status_label)

        # A secao de conferencia so aparece quando ha o que conferir.
        self.candidates_section = QtWidgets.QWidget()
        candidates_layout = QtWidgets.QVBoxLayout(self.candidates_section)
        candidates_layout.setContentsMargins(0, 0, 0, 0)
        self.candidates_label = self._section_label("2 · Conferir")
        candidates_layout.addWidget(self.candidates_label)
        self.candidates_list.setMinimumHeight(96)
        candidates_layout.addWidget(self.candidates_list, 1)
        candidate_actions = QtWidgets.QGridLayout()
        candidate_actions.addWidget(self.btn_apply_candidate, 0, 0)
        candidate_actions.addWidget(self.btn_discard_candidate, 0, 1)
        candidate_actions.addWidget(self.btn_apply_all_candidates, 1, 0)
        candidate_actions.addWidget(self.btn_discard_all_candidates, 1, 1)
        candidates_layout.addLayout(candidate_actions)
        self.candidates_section.setVisible(False)
        ocr_tab_layout.addWidget(self.candidates_section)

        preview_layout = QtWidgets.QVBoxLayout()
        preview_layout.addWidget(self.btn_toggle_preview)
        preview_layout.addWidget(self.before_after)
        self.compare_group = self._make_collapsible_group("3 · Conferir a prévia", preview_layout, checked=True)
        self.compare_group.toggled.connect(lambda checked: self._schedule_preview_refresh(immediate=True))
        ocr_tab_layout.addWidget(self.compare_group)

        ocr_tab_layout.addWidget(self._section_label("4 · Aplicar"))
        ocr_tab_layout.addWidget(self.btn_add)
        ocr_tab_layout.addWidget(self.btn_add_eraser)

        self.changes_label = self._section_label("5 · Alterações")
        ocr_tab_layout.addWidget(self.changes_label)
        self.changes_list.setMinimumHeight(110)
        ocr_tab_layout.addWidget(self.changes_list, 3)
        right_actions = QtWidgets.QHBoxLayout()
        right_actions.addWidget(self.btn_remove)
        right_actions.addWidget(self.btn_clear)
        right_actions.addStretch(1)
        ocr_tab_layout.addLayout(right_actions)

        ocr_advanced_layout = QtWidgets.QVBoxLayout()
        ocr_advanced_layout.addWidget(self.whiteout_check)
        ocr_advanced_layout.addWidget(QtWidgets.QLabel("Motor de reconhecimento"))
        ocr_advanced_layout.addWidget(self.engine_combo)
        ocr_advanced_layout.addWidget(QtWidgets.QLabel("Modelo local (.pt)"))
        model_layout = QtWidgets.QHBoxLayout()
        model_layout.addWidget(self.local_model_edit, 1)
        model_layout.addWidget(self.btn_select_local_model)
        ocr_advanced_layout.addLayout(model_layout)
        ocr_advanced_layout.addWidget(QtWidgets.QLabel("Endpoint OCR"))
        ocr_advanced_layout.addWidget(self.endpoint_edit)
        ocr_advanced_layout.addWidget(QtWidgets.QLabel("Fonte Merida (.ttf/.otf)"))
        font_layout = QtWidgets.QHBoxLayout()
        font_layout.addWidget(self.merida_font_edit, 1)
        font_layout.addWidget(self.btn_select_merida)
        font_layout.addWidget(self.btn_clear_merida)
        ocr_advanced_layout.addLayout(font_layout)
        ocr_tab_layout.addWidget(self._make_collapsible_group("Avançado", ocr_advanced_layout, checked=False))

        fens_tab = QtWidgets.QWidget()
        fens_tab_layout = QtWidgets.QVBoxLayout(fens_tab)
        fens_tab_layout.addWidget(self._section_label("FEN"))
        fens_tab_layout.addWidget(self.fen_edit)
        fens_tab_layout.addWidget(self.warnings)
        fens_tab_layout.addWidget(self._section_label("FENs das substituições"))
        fen_meta = QtWidgets.QGridLayout()
        fen_meta.addWidget(QtWidgets.QLabel("Vez de jogar"), 0, 0)
        fen_meta.addWidget(self.fen_side_combo, 0, 1)
        fen_meta.addWidget(QtWidgets.QLabel("Número do lance"), 1, 0)
        fen_meta.addWidget(self.fen_move_spin, 1, 1)
        fens_tab_layout.addLayout(fen_meta)
        self.fen_ops_list.setMinimumHeight(120)
        fens_tab_layout.addWidget(self.fen_ops_list, 1)
        fen_actions = QtWidgets.QHBoxLayout()
        fen_actions.addWidget(self.btn_remove_fen)
        fen_actions.addStretch(1)
        fens_tab_layout.addLayout(fen_actions)
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
        appearance_grid.addWidget(self.apply_style_all_check, 7, 0, 1, 2)
        whiteout_tab_layout.addWidget(
            self._make_collapsible_group("Ajustes avançados", appearance_grid, checked=False)
        )
        whiteout_tab_layout.addStretch(1)
        # Cada aba rola: sem isso o conteudo e comprimido abaixo do minimo e o
        # Qt corta o texto dos botoes e rotulos.
        self.edit_tabs.addTab(self._scrollable(ocr_tab), "OCR")
        self.edit_tabs.addTab(self._scrollable(fens_tab), "FEN")
        self.edit_tabs.addTab(self._scrollable(whiteout_tab), "Aparência")
        self.edit_tabs.setMinimumHeight(220)
        bottom_layout.addWidget(self.edit_tabs, 2)

        self.right_vertical_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.right_vertical_splitter.setChildrenCollapsible(False)
        self.right_vertical_splitter.addWidget(top_editor)
        self.right_vertical_splitter.addWidget(bottom_panel)
        self.right_vertical_splitter.setStretchFactor(0, 2)
        self.right_vertical_splitter.setStretchFactor(1, 5)
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
        self.study_workspace.setMinimumHeight(self.study_workspace.sizeHint().height())

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
        self.side_stack.setMinimumWidth(380)

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
    ) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(title)
        group.setCheckable(True)
        group.setChecked(checked)
        group.setLayout(layout)
        group.toggled.connect(lambda visible, target_layout=layout: self._set_layout_visible(target_layout, visible))
        self._set_layout_visible(layout, checked)
        return group

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
        if not hasattr(self, "edit_context_label") or not hasattr(self, "act_save_pdf"):
            return

        has_pdf = bool(self.pdf_service and self.current_render)
        self.act_toggle_preview.setEnabled(has_pdf)
        self.btn_toggle_preview.setEnabled(has_pdf)
        self.btn_toggle_preview.setText(
            "Voltar ao PDF original" if self._showing_preview else "Ver resultado na página"
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

        self.edit_context_label.setText("Selecione um diagrama na página para começar.")
        self._set_primary_button(None)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        self.act_open_pdf = QtGui.QAction("Abrir PDF", self)
        self.act_open_pdf.setShortcut(QtGui.QKeySequence.Open)
        self.act_open_pdf.triggered.connect(self._open_pdf_dialog)
        toolbar.addAction(self.act_open_pdf)

        self.act_save_pdf = QtGui.QAction("Exportar PDF", self)
        self.act_save_pdf.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        self.act_save_pdf.triggered.connect(self._save_output_pdf)
        toolbar.addAction(self.act_save_pdf)

        self.act_save_project = QtGui.QAction("Salvar Projeto", self)
        self.act_save_project.setShortcut(QtGui.QKeySequence.Save)
        self.act_save_project.triggered.connect(self._save_project_dialog)
        toolbar.addAction(self.act_save_project)

        self.act_load_project = QtGui.QAction("Carregar Projeto", self)
        self.act_load_project.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
        self.act_load_project.triggered.connect(self._load_project_dialog)
        toolbar.addAction(self.act_load_project)

        toolbar.addSeparator()

        self.act_undo = QtGui.QAction("Desfazer", self)
        self.act_undo.setShortcut(QtGui.QKeySequence.Undo)
        self.act_undo.setToolTip("Desfaz a última alteração (Ctrl+Z)")
        self.act_undo.setEnabled(False)
        self.act_undo.triggered.connect(self._undo_change)
        toolbar.addAction(self.act_undo)

        self.act_redo = QtGui.QAction("Refazer", self)
        self.act_redo.setShortcuts([QtGui.QKeySequence.Redo, QtGui.QKeySequence("Ctrl+Y")])
        self.act_redo.setToolTip("Refaz a alteração desfeita (Ctrl+Y)")
        self.act_redo.setEnabled(False)
        self.act_redo.triggered.connect(self._redo_change)
        toolbar.addAction(self.act_redo)

        toolbar.addSeparator()

        self.act_mode_read = QtGui.QAction("Leitura", self)
        self.act_mode_read.setCheckable(True)
        self.act_mode_read.triggered.connect(lambda: self._set_mode("read"))
        toolbar.addAction(self.act_mode_read)

        self.act_mode_study = QtGui.QAction("Estudo", self)
        self.act_mode_study.setCheckable(True)
        self.act_mode_study.triggered.connect(lambda: self._set_mode("study"))
        toolbar.addAction(self.act_mode_study)

        self.act_mode_edit = QtGui.QAction("Edição", self)
        self.act_mode_edit.setCheckable(True)
        self.act_mode_edit.triggered.connect(lambda: self._set_mode("edit"))
        toolbar.addAction(self.act_mode_edit)

        self.mode_group = QtGui.QActionGroup(self)
        self.mode_group.setExclusive(True)
        for action in (self.act_mode_read, self.act_mode_study, self.act_mode_edit):
            self.mode_group.addAction(action)

        toolbar.addSeparator()

        self.act_prev = QtGui.QAction("Página -", self)
        self.act_prev.setShortcut(QtGui.QKeySequence.MoveToPreviousChar)
        self.act_prev.triggered.connect(self._prev_page)
        toolbar.addAction(self.act_prev)

        self.act_next = QtGui.QAction("Página +", self)
        self.act_next.setShortcut(QtGui.QKeySequence.MoveToNextChar)
        self.act_next.triggered.connect(self._next_page)
        toolbar.addAction(self.act_next)

        toolbar.addWidget(QtWidgets.QLabel("  Página: "))
        toolbar.addWidget(self.page_spin)

        toolbar.addWidget(QtWidgets.QLabel("  Zoom: "))
        toolbar.addWidget(self.zoom_spin)

        toolbar.addSeparator()
        toolbar.addAction(self.act_toggle_preview)

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

        self.act_export_report = QtGui.QAction("Exportar relatório...", self)
        self.act_export_report.setShortcut(QtGui.QKeySequence("Ctrl+Shift+E"))
        self.act_export_report.triggered.connect(self._export_report_dialog)

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
        file_menu.addSeparator()
        file_menu.addAction(self.act_save_pdf)
        file_menu.addAction(self.act_export_report)
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

        diagrams_menu = self.menuBar().addMenu("Diagramas")
        diagrams_menu.addAction(self.act_snap_selection)
        diagrams_menu.addAction(self.act_auto_orient)
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
            self.statusBar().showMessage("Modo leitura.")
            return
        self.side_stack.setVisible(True)
        if mode == "study":
            self.side_stack.setCurrentIndex(1)
            self.act_mode_study.setChecked(True)
            self.statusBar().showMessage("Modo estudo.")
            return
        self.side_stack.setCurrentIndex(0)
        self.act_mode_edit.setChecked(True)
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
        if self._export_worker is not None and not self._export_worker.wait(15000):
            logger.warning("Exportação ainda em andamento no fechamento")
        self._export_worker = None

        # Autosave final: fechar a janela nunca pode custar o trabalho da sessao.
        if self._autosave_dirty:
            self._autosave_now(quiet=True)

        self._save_splitter_state(self.main_splitter, "main_splitter_state")
        self._save_splitter_state(self.right_vertical_splitter, "right_vertical_splitter_state")
        self._save_splitter_state(self.study_workspace, "study_workspace_splitter_state")
        if self.study_dialog:
            self.study_dialog.close()
            self.study_dialog = None
        if self.pdf_service:
            self.pdf_service.close()
            self.pdf_service = None
        super().closeEvent(event)

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
        self._open_pdf(file_path, clear_ops=True)

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
        if self.pdf_service:
            self.pdf_service.close()
        self.pdf_service = PdfService(file_path)
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
        suffix = " [prévia do resultado]" if self._showing_preview else ""
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
            self.statusBar().showMessage(
                "Prévia ligada: a página mostra como o PDF vai ficar. Ctrl+D volta ao original."
            )
        else:
            self._show_original_render()
            self.statusBar().showMessage("Prévia desligada: mostrando o PDF original.")
        self._schedule_preview_refresh(immediate=True)

    def _show_original_render(self) -> None:
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
        want_page = self.preview_result_enabled and bool(operations or erasers)

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

        try:
            if want_page:
                self.current_preview_render = self.pdf_service.render_page_with_operations(
                    self.current_page,
                    zoom,
                    operations,
                    erase_operations=erasers,
                    whiteout=whiteout,
                    include_lichess_link=include_link,
                )
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
                )
                self.before_after.set_images(before_png, after_png)
            elif self.compare_group.isChecked():
                self.before_after.set_message(
                    "Selecione um diagrama e monte a posição para ver o antes e depois."
                )
        except Exception as exc:
            self.current_preview_render = None
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

    def _selected_fen_operation_index(self) -> Optional[int]:
        item = self.fen_ops_list.currentItem()
        if not item:
            return None
        idx = int(item.data(QtCore.Qt.UserRole))
        if 0 <= idx < len(self.operations):
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
        idx = self._selected_operation_index()
        if idx is None:
            fen_item = self.fen_ops_list.currentItem()
            if fen_item is not None:
                candidate = int(fen_item.data(QtCore.Qt.UserRole))
                if 0 <= candidate < len(self.operations):
                    idx = candidate

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
        if not (0 <= idx < len(self.operations)):
            return
        self._syncing_fen_tab = True
        try:
            self.fen_ops_list.setCurrentRow(idx)
            op = self.operations[idx]
            self.fen_side_combo.setCurrentIndex(0 if op.side_to_move != "b" else 1)
            self.fen_move_spin.setValue(max(1, int(op.fullmove_number)))
        finally:
            self._syncing_fen_tab = False
        self._update_lichess_link()

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

            self.statusBar().showMessage(
                "Nenhum diagrama conhecido nesse clique. Selecione a área e use Reconhecer seleção ou Estudar seleção."
            )
            return

        idx = self._operation_index_at_image_point(x, y)
        if idx is None:
            return
        self._set_current_operation(idx)
        self._focus_operation(idx)

    def _selected_study_position_index(self) -> Optional[int]:
        item = self.study_positions_list.currentItem()
        if not item:
            return None
        idx = int(item.data(QtCore.Qt.UserRole))
        if 0 <= idx < len(self.study_positions):
            return idx
        return None

    def _refresh_study_positions_list(self) -> None:
        selected_idx = self._selected_study_position_index()
        self._syncing_study_positions = True
        try:
            self.study_positions_list.clear()
            for idx, pos in enumerate(self.study_positions):
                note = (pos.comment_before or pos.note).strip().replace("\n", " ")
                suffix = f" | {note[:32]}{'...' if len(note) > 32 else ''}" if note else ""
                side_label = "pretas" if pos.side_to_move == "b" else "brancas"
                item = QtWidgets.QListWidgetItem(
                    f"{idx + 1:03d} | pag {pos.page_num + 1} | {side_label} | "
                    f"{pos.fen[:28]}{'...' if len(pos.fen) > 28 else ''}{suffix}"
                )
                item.setData(QtCore.Qt.UserRole, idx)
                self.study_positions_list.addItem(item)
            if selected_idx is not None and 0 <= selected_idx < len(self.study_positions):
                self.study_positions_list.setCurrentRow(selected_idx)
            elif self.study_positions:
                self.study_positions_list.setCurrentRow(len(self.study_positions) - 1)
            else:
                self.study_comment_before_edit.clear()
                self.study_comment_after_edit.clear()
                self._update_study_comment_target_label()
        finally:
            self._syncing_study_positions = False

    @staticmethod
    def _study_comment_key(ply: int) -> str:
        return str(max(0, int(ply)))

    def _current_study_comment_key(self) -> str:
        return self.study_panel.study_board.current_path_key()

    @staticmethod
    def _study_move_reference(
        ply: int,
        san_line: list[str],
        start_turn: str,
        start_fullmove_number: int,
    ) -> str:
        if ply <= 0:
            return "posição inicial"
        rows = StudyPanel._format_san_rows(san_line, start_turn, start_fullmove_number)
        for move_label, white_san, white_ply, black_san, black_ply in rows:
            move_no = move_label.rstrip(".")
            if white_ply == ply and white_san:
                return f"{move_no}. {white_san}"
            if black_ply == ply and black_san:
                return f"{move_no}... {black_san}"
        return f"lance {ply}"

    def _update_study_comment_target_label(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            self.study_comment_target_label.setText("Comentando: selecione uma posição de estudo")
            return
        reference = self._study_move_reference(
            self.study_panel.study_board.current_ply(),
            self.study_panel.study_board.san_line(),
            self.study_panel.study_board.start_turn(),
            self.study_panel.study_board.start_fullmove_number(),
        )
        self.study_comment_target_label.setText(f"Comentando: {reference}")

    @staticmethod
    def _study_comment_sort_key(key: str) -> tuple[int, str]:
        if str(key).isdigit():
            return (int(key), str(key))
        if key == "0":
            return (0, key)
        return (len(str(key).split("|")), str(key))

    @staticmethod
    def _study_comment_ply(key: str) -> Optional[int]:
        if str(key).isdigit():
            return int(key)
        if key == "0":
            return 0
        if str(key).strip():
            return len(str(key).split("|"))
        return None

    @staticmethod
    def _study_comments_for_pgn(pos: StudyPosition) -> dict[object, dict[str, str]]:
        out: dict[object, dict[str, str]] = {}
        for key, values in pos.move_comments.items():
            try:
                out_key: object = max(0, int(key))
            except Exception:
                out_key = str(key)
            before = str(values.get("before", ""))
            after = str(values.get("after", ""))
            if before.strip() or after.strip():
                out[out_key] = {"before": before, "after": after}
        return out

    def _refresh_study_move_comment_markers(self, pos: Optional[StudyPosition] = None) -> None:
        if pos is None:
            idx = self._selected_study_position_index()
            pos = self.study_positions[idx] if idx is not None else None
        comment_keys = pos.move_comments.keys() if pos is not None else set()
        commented_items: list[object] = []
        for key in comment_keys:
            ply = self._study_comment_ply(str(key))
            if ply is not None and ply > 0:
                commented_items.append(ply)
            if not str(key).isdigit() and str(key) != "0":
                commented_items.append(str(key))
        self.study_panel.set_commented_plies(commented_items)

    def _current_study_comments(self, pos: StudyPosition) -> tuple[str, str]:
        key = self._current_study_comment_key()
        if not pos.move_comments and key == "0":
            return (pos.comment_before or pos.note, pos.comment_after)
        values = pos.move_comments.get(key, {})
        return (str(values.get("before", "")), str(values.get("after", "")))

    def _set_current_study_comments(self, pos: StudyPosition, before: str, after: str) -> None:
        key = self._current_study_comment_key()
        if before.strip() or after.strip():
            pos.move_comments[key] = {"before": before, "after": after}
        else:
            pos.move_comments.pop(key, None)
        if key == "0":
            pos.comment_before = before
            pos.comment_after = after
            pos.note = before
        elif not pos.comment_before and not pos.comment_after and pos.move_comments:
            first_key = sorted(pos.move_comments, key=self._study_comment_sort_key)[0]
            first = pos.move_comments[first_key]
            pos.comment_before = str(first.get("before", ""))
            pos.comment_after = str(first.get("after", ""))
            pos.note = pos.comment_before or pos.comment_after

    def _flush_current_study_comment(self) -> None:
        if self._syncing_study_positions:
            return
        idx = self._selected_study_position_index()
        if idx is None:
            return
        self._flush_study_comment_for_index(idx)

    def _flush_study_comment_for_index(self, idx: int) -> None:
        if not (0 <= idx < len(self.study_positions)):
            return
        pos = self.study_positions[idx]
        self._set_current_study_comments(
            pos,
            self.study_comment_before_edit.toPlainText(),
            self.study_comment_after_edit.toPlainText(),
        )
        self._update_study_position_pgn(pos)
        self._refresh_study_move_comment_markers(pos)

    def _sync_study_position_line(self, pos: StudyPosition) -> None:
        max_ply = len(self.study_panel.study_board.san_line())
        kept_comments: dict[str, dict[str, str]] = {}
        for key, values in pos.move_comments.items():
            ply = self._study_comment_ply(str(key))
            if ply is None:
                kept_comments[key] = values
                continue
            if ply == 0 or ply <= max_ply:
                kept_comments[key] = values
        pos.move_comments = kept_comments
        self._set_study_comment_summary(pos)
        self._update_study_position_pgn(pos)
        self._refresh_study_move_comment_markers(pos)

    @staticmethod
    def _set_study_comment_summary(pos: StudyPosition) -> None:
        pos.comment_before = ""
        pos.comment_after = ""
        pos.note = ""
        if not pos.move_comments:
            return
        try:
            first_key = sorted(pos.move_comments, key=MainWindow._study_comment_sort_key)[0]
        except Exception:
            first_key = next(iter(pos.move_comments))
        first = pos.move_comments[first_key]
        pos.comment_before = str(first.get("before", ""))
        pos.comment_after = str(first.get("after", ""))
        pos.note = pos.comment_before or pos.comment_after

    def _refresh_study_comment_fields_for_current_ply(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            self._update_study_comment_target_label()
            return
        pos = self.study_positions[idx]
        before, after = self._current_study_comments(pos)
        self._syncing_study_positions = True
        try:
            self.study_comment_before_edit.setPlainText(before)
            self.study_comment_after_edit.setPlainText(after)
            self._update_study_comment_target_label()
        finally:
            self._syncing_study_positions = False

    def _update_study_position_pgn(self, pos: StudyPosition) -> None:
        pos.pgn = self.study_panel.study_board.current_pgn(
            move_comments=self._study_comments_for_pgn(pos),
            include_all=True,
        )

    def _study_export_pgn(self) -> str:
        idx = self._selected_study_position_index()
        if idx is None:
            return self.study_panel.study_board.current_pgn()
        self._flush_study_comment_for_index(idx)
        self._refresh_study_positions_list()
        return self.study_positions[idx].pgn

    def _on_study_pgn_imported(self, move_comments: object) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            self.study_panel.set_commented_plies(set())
            return
        pos = self.study_positions[idx]
        start_fen = self.study_panel.study_board.start_fen()
        parts = start_fen.split()
        if len(parts) >= 6:
            pos.fen = parts[0]
            pos.side_to_move = "b" if parts[1] == "b" else "w"
            try:
                pos.fullmove_number = max(1, int(parts[5]))
            except Exception:
                pos.fullmove_number = 1
        pos.move_comments = {}
        for ply, values in dict(move_comments or {}).items():
            if not isinstance(values, dict):
                continue
            try:
                key = str(max(0, int(ply))) if str(ply).isdigit() else str(ply)
            except Exception:
                continue
            before = str(values.get("before", ""))
            after = str(values.get("after", ""))
            if before.strip() or after.strip():
                pos.move_comments[key] = {"before": before, "after": after}
        self._set_study_comment_summary(pos)
        self._update_study_position_pgn(pos)
        self._refresh_study_move_comment_markers(pos)
        self._refresh_study_comment_fields_for_current_ply()
        self._refresh_study_positions_list()

    def _on_study_ply_changed(self) -> None:
        if self._syncing_study_positions:
            return
        idx = self._selected_study_position_index()
        if idx is not None:
            self._sync_study_position_line(self.study_positions[idx])
        self._refresh_study_comment_fields_for_current_ply()

    def _load_study_position(self, pos: StudyPosition) -> None:
        self.study_panel.load_piece_placement(
            pos.fen,
            side_to_move=pos.side_to_move,
            fullmove_number=pos.fullmove_number,
        )
        if pos.pgn.strip():
            try:
                self.study_panel.study_board.load_pgn_text(pos.pgn)
            except Exception:
                self.study_panel.load_piece_placement(
                    pos.fen,
                    side_to_move=pos.side_to_move,
                    fullmove_number=pos.fullmove_number,
                )

    def _focus_study_position(self, idx: int) -> None:
        if not (0 <= idx < len(self.study_positions)):
            return
        self._flush_current_study_comment()
        self._activate_study_position(idx, set_mode=True)

    def _activate_study_position(self, idx: int, set_mode: bool = False) -> None:
        if not (0 <= idx < len(self.study_positions)):
            return
        pos = self.study_positions[idx]
        if self.pdf_service:
            self.current_page = min(max(0, pos.page_num), self.pdf_service.page_count - 1)
            self._render_current_page()
            if self.current_render:
                rect_img = self.pdf_service.pdf_rect_to_image_rect(
                    self.current_page,
                    pos.rect_pdf,
                    self.current_render.matrix,
                )
                self.page_widget.set_selection_rect(rect_img)
        self._syncing_study_positions = True
        try:
            self.study_positions_list.setCurrentRow(idx)
            self._load_study_position(pos)
        finally:
            self._syncing_study_positions = False
        self._refresh_study_move_comment_markers(pos)
        self._refresh_study_comment_fields_for_current_ply()
        if set_mode:
            self._set_mode("study")

    def _on_study_position_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        idx = int(item.data(QtCore.Qt.UserRole))
        self._focus_study_position(idx)

    def _on_study_position_selected(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        if self._syncing_study_positions or current is None:
            return
        if previous is not None:
            previous_idx = int(previous.data(QtCore.Qt.UserRole))
            self._flush_study_comment_for_index(previous_idx)
        idx = int(current.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.study_positions)):
            return
        self._activate_study_position(idx)

    def _on_study_comment_changed(self) -> None:
        if self._syncing_study_positions:
            return
        idx = self._selected_study_position_index()
        if idx is None:
            return
        self._flush_study_comment_for_index(idx)
        self._refresh_study_positions_list()

    def _save_current_study_line(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            QtWidgets.QMessageBox.information(self, "Estudo", "Selecione uma posição de estudo primeiro.")
            return
        self._flush_study_comment_for_index(idx)
        self.statusBar().showMessage("Linha de estudo atualizada.")
        self._refresh_study_positions_list()

    def _remove_selected_study_position(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            return
        del self.study_positions[idx]
        self._refresh_study_positions_list()
        self.statusBar().showMessage(f"Posição de estudo removida. Total: {len(self.study_positions)}")

    def _load_editor_position_into_study(self) -> None:
        text = self.fen_edit.text().strip()
        if not text:
            text = self.board_editor.piece_placement()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN inválida", str(exc))
            return
        side_to_move, fullmove_number = self._current_fen_defaults()
        self.study_panel.load_piece_placement(
            piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
        )
        self._set_mode("study")

    @staticmethod
    def _make_starting_study_position(page_num: int) -> StudyPosition:
        return StudyPosition(
            page_num=max(0, int(page_num)),
            rect_pdf=(0.0, 0.0, 0.0, 0.0),
            fen=chess.STARTING_BOARD_FEN,
            side_to_move="w",
            fullmove_number=1,
        )

    def _study_starting_position(self) -> None:
        self._flush_current_study_comment()
        page_num = self.current_page if self.pdf_service else 0
        pos = self._make_starting_study_position(page_num)
        self.study_positions.append(pos)
        self._refresh_study_positions_list()
        self._focus_study_position(len(self.study_positions) - 1)
        self.statusBar().showMessage("Partida inicial enviada para estudo.")

    def _text_from_current_selection(self) -> str:
        if not self.current_render or not self.pdf_service:
            raise ValueError("Abra um PDF primeiro.")
        selection = self.page_widget.selection_rect()
        if not selection:
            raise ValueError("Selecione a área do texto no PDF.")
        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )
        text = self.pdf_service.extract_text_from_pdf_rect(self.current_page, rect_pdf)
        if not text:
            raise ValueError("Nenhum texto selecionável encontrado nessa área.")
        return text

    @staticmethod
    def _append_text_to_plain_edit(edit: QtWidgets.QPlainTextEdit, text: str) -> None:
        existing = edit.toPlainText().strip()
        if existing:
            edit.setPlainText(f"{existing}\n\n{text.strip()}")
        else:
            edit.setPlainText(text.strip())
        cursor = edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        edit.setTextCursor(cursor)

    def _copy_pdf_text_to_study_comment(self, target: str) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            QtWidgets.QMessageBox.information(self, "Estudo", "Selecione ou crie uma posição de estudo primeiro.")
            return
        try:
            text = self._text_from_current_selection()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Texto do PDF", str(exc))
            return

        edit = self.study_comment_after_edit if target == "after" else self.study_comment_before_edit
        self._append_text_to_plain_edit(edit, text)
        QtWidgets.QApplication.clipboard().setText(text)
        self._save_current_study_line()
        self.statusBar().showMessage("Texto copiado do PDF e salvo no comentário.")

    def _study_selection(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem seleção", "Selecione o diagrama na página.")
            return
        text = self.fen_edit.text().strip() or self.board_editor.piece_placement()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
            validate_piece_placement(piece_placement)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN inválida", str(exc))
            return
        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )
        side_to_move, fullmove_number = self._current_fen_defaults()
        pos = StudyPosition(
            page_num=self.current_page,
            rect_pdf=rect_pdf,
            fen=piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
        )
        self.study_positions.append(pos)
        self._refresh_study_positions_list()
        self._focus_study_position(len(self.study_positions) - 1)
        self.statusBar().showMessage(f"Posição enviada para estudo. Total: {len(self.study_positions)}")

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
            self.fen_ops_list.setCurrentRow(-1)
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

    def _on_fen_operation_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        if self._syncing_fen_tab:
            return
        idx = int(item.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.operations)):
            return
        self._set_current_operation(idx)
        self._focus_operation(idx)

    def _on_fen_meta_changed(self, value: int) -> None:
        del value
        if self._loading_ui or self._syncing_fen_tab:
            return
        idx = self._selected_operation_index()
        if idx is None:
            fen_item = self.fen_ops_list.currentItem()
            if fen_item is not None:
                candidate = int(fen_item.data(QtCore.Qt.UserRole))
                if 0 <= candidate < len(self.operations):
                    idx = candidate
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
        """Testa as 4 rotações e aplica a mais plausível."""
        piece_placement = self.board_editor.piece_placement()
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
        message = f"Posição girada {result.rotation}° (vantagem {result.margin:.1f})."
        if result.ambiguous:
            message += " Margem apertada — confira antes de aplicar."
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
        self.warnings.setText("\n".join(warnings) if warnings else "")

    def _recognize_selection(self) -> None:
        if not self.current_render:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de rodar OCR.")
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem seleção", "Selecione uma região da página.")
            return

        if not self._confirm_remote_upload("Reconhecer seleção", 1):
            return

        cursor_set = False
        try:
            engine = self._make_engine()
            crop_png = crop_from_rendered_page(self.current_render.image_png, selection)
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            # A seleção já é o tabuleiro: se o detector não achar contorno, o motor
            # local lê a imagem inteira em vez de devolver "nada encontrado".
            prediction = engine.predict(
                crop_png,
                filename="selection.png",
                assume_whole_image=True,
            )
        except RecognitionError as exc:
            QtWidgets.QMessageBox.warning(self, "Falha OCR", str(exc))
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro OCR", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        if not prediction.results:
            QtWidgets.QMessageBox.information(self, "OCR", "Nenhum tabuleiro detectado na seleção.")
            return

        best = prediction.results[0]
        self._last_ocr_result = best
        self._loading_ui = True
        self.board_editor.set_piece_placement(best.fen)
        self.fen_edit.setText(best.fen)
        self._loading_ui = False
        self._update_warnings(best.fen)
        self._update_edit_context_state()

        sx0, sy0, sx1, sy1 = selection
        sw = max(1.0, sx1 - sx0)
        sh = max(1.0, sy1 - sy0)
        rx0 = sx0 + (best.xc - best.width / 2.0) * sw
        ry0 = sy0 + (best.yc - best.height / 2.0) * sh
        rx1 = sx0 + (best.xc + best.width / 2.0) * sw
        ry1 = sy0 + (best.yc + best.height / 2.0) * sh
        self.page_widget.set_selection_rect((rx0, ry0, rx1, ry1))
        # A posicao reconhecida pertence a esta area: libera a previa do rascunho.
        self._anchor_from_selection()
        self._schedule_preview_refresh(immediate=True)

        info = (
            f"OCR concluído. id={prediction.request_id or '-'} "
            f"status={prediction.status} boards={len(prediction.results)}"
        )
        if prediction.message:
            info += f" msg={prediction.message}"
        self.statusBar().showMessage(info)
        self._update_edit_context_state()

    def _recognize_current_page(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de rodar OCR.")
            return

        if not self._confirm_remote_upload("Reconhecer página", 1):
            return

        cursor_set = False
        try:
            engine = self._make_engine()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            prediction = engine.predict(
                self.current_render.image_png,
                filename=f"page_{self.current_page + 1}.png",
            )
        except RecognitionError as exc:
            QtWidgets.QMessageBox.warning(self, "Falha OCR", str(exc))
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro OCR", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        if not prediction.results:
            QtWidgets.QMessageBox.information(self, "OCR", "Nenhum tabuleiro detectado nesta página.")
            return

        auto_apply = self.auto_apply_check.isChecked()
        added_count = 0
        skipped_count = 0
        first_rect: Optional[tuple[float, float, float, float]] = None
        for result in prediction.results:
            try:
                piece_placement = normalize_piece_placement(extract_piece_placement(result.fen))
                validate_piece_placement(piece_placement)
            except Exception:
                skipped_count += 1
                continue

            sw = max(1.0, float(self.current_render.width_px))
            sh = max(1.0, float(self.current_render.height_px))
            rect_img = (
                (result.xc - result.width / 2.0) * sw,
                (result.yc - result.height / 2.0) * sh,
                (result.xc + result.width / 2.0) * sw,
                (result.yc + result.height / 2.0) * sh,
            )
            rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
                self.current_page,
                rect_img,
                self.current_render.matrix,
            )
            pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
            side_to_move, fullmove_number = self._current_fen_defaults()
            op = OverlayOperation(
                page_num=self.current_page,
                rect_pdf=rect_pdf,
                fen=piece_placement,
                side_to_move=side_to_move,
                fullmove_number=fullmove_number,
                source="ocr-page",
                confidence=result.confidence,
                whiteout_padding_pt=(pad_left + pad_top + pad_right + pad_bottom) / 4.0,
                whiteout_padding_left_pt=pad_left,
                whiteout_padding_top_pt=pad_top,
                whiteout_padding_right_pt=pad_right,
                whiteout_padding_bottom_pt=pad_bottom,
                border_width_pt=float(self.op_border_spin.value()),
            )
            if self._has_similar_operation(op):
                skipped_count += 1
                continue
            if auto_apply:
                self.operations.append(op)
            else:
                op.source = "ocr-page-candidato"
                self.candidates.append(op)
            added_count += 1
            if first_rect is None:
                first_rect = rect_img
                self._last_ocr_result = result
                self._loading_ui = True
                self.board_editor.set_piece_placement(piece_placement)
                self.fen_edit.setText(piece_placement)
                self._loading_ui = False
                self._update_warnings(piece_placement)

        self._refresh_operations_list()
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        if first_rect is not None:
            self.page_widget.set_selection_rect(first_rect)
            self._anchor_from_selection()
            if auto_apply:
                self._set_current_operation(len(self.operations) - added_count)
                self._select_change("operation", len(self.operations) - added_count)
            else:
                self.candidates_list.setCurrentRow(len(self.candidates) - added_count)
        self._update_edit_context_state()
        self._schedule_preview_refresh(immediate=True)
        if added_count:
            self._commit_history(
                f"reconhecer página ({added_count} {'substituições' if auto_apply else 'candidatos'})"
            )
        if auto_apply:
            self.statusBar().showMessage(
                f"Página reconhecida. aplicadas={added_count}, ignoradas={skipped_count}"
            )
        else:
            self.statusBar().showMessage(
                f"Página reconhecida. candidatos={added_count}, ignorados={skipped_count}. "
                "Confira cada um e clique em Aplicar."
            )

    @staticmethod
    def _rect_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix0 = max(ax0, bx0)
        iy0 = max(ay0, by0)
        ix1 = min(ax1, bx1)
        iy1 = min(ay1, by1)
        iw = max(0.0, ix1 - ix0)
        ih = max(0.0, iy1 - iy0)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
        area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union

    @staticmethod
    def _rect_area(rect: tuple[float, float, float, float]) -> float:
        x0, y0, x1, y1 = rect
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def _has_similar_operation(self, candidate: OverlayOperation, iou_threshold: float = 0.90) -> bool:
        # Uma deteccao ja na fila de candidatos tambem conta como duplicata.
        for op in (*self.operations, *self.candidates):
            if op.page_num != candidate.page_num:
                continue
            if self._rect_iou(op.rect_pdf, candidate.rect_pdf) >= iou_threshold:
                return True
        return False

    # ------------------------------------------------------------------
    # OCR em lote (Sprint 5.1: fora da thread da UI)
    # ------------------------------------------------------------------
    #
    # Antes: 898 requisicoes HTTP sequenciais na thread da UI, com
    # `processEvents()` segurando a janela. O Windows marcava o app como "nao
    # respondendo" e o botao Cancelar so era lido no proximo processEvents.
    #
    # Agora o `BatchOcrWorker` renderiza e reconhece numa thread propria (com o
    # seu proprio `fitz.Document`) e manda cada pagina pronta por sinal. Todos os
    # `_on_batch_ocr_*` abaixo rodam na thread da UI — conexao entre threads e
    # `QueuedConnection` por padrao —, entao eles podem mexer nas listas e nos
    # widgets a vontade.

    def _recognize_full_pdf(self) -> None:
        if not self.pdf_service or not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de rodar OCR.")
            return
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            QtWidgets.QMessageBox.information(
                self,
                "OCR em lote",
                "Já existe um reconhecimento em andamento.",
            )
            return

        total_pages = self.pdf_service.page_count
        if total_pages <= 0:
            QtWidgets.QMessageBox.information(self, "OCR", "PDF sem páginas para processar.")
            return

        start_page = int(self.ocr_full_next_page)
        if start_page < 0 or start_page >= total_pages:
            start_page = 0
        remaining_pages = total_pages - start_page
        if not self._confirm_remote_upload("Detectar no PDF", remaining_pages):
            return
        if start_page > 0:
            self.statusBar().showMessage(
                f"OCR em lote retomando da página {start_page + 1}/{total_pages}..."
            )

        # Congelado no inicio do lote: mudar o estilo no meio da execucao nao
        # pode fazer metade das deteccoes sair com outro padding.
        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
        side_to_move, fullmove_number = self._current_fen_defaults()
        self._ocr_batch = {
            "auto_apply": self.auto_apply_check.isChecked(),
            "total_pages": total_pages,
            "start_page": start_page,
            "added": 0,
            "skipped": 0,
            "skipped_too_large": 0,
            "pages_with_hits": 0,
            "errors": 0,
            "padding": (pad_left, pad_top, pad_right, pad_bottom),
            "side_to_move": side_to_move,
            "fullmove_number": fullmove_number,
            "border_width_pt": float(self.op_border_spin.value()),
            "max_area_ratio": 0.50,
        }

        progress = QtWidgets.QProgressDialog(
            "Reconhecendo diagramas no PDF inteiro...",
            "Cancelar",
            0,
            remaining_pages,
            self,
        )
        progress.setWindowTitle("OCR em lote")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        # O ciclo de vida do dialogo e nosso: ele so fecha quando o worker
        # confirmar que terminou, senao um cancelamento deixaria a thread orfa.
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(self._cancel_batch_ocr)
        self._ocr_progress = progress

        worker = BatchOcrWorker(
            self.current_pdf_path,
            start_page,
            total_pages,
            endpoint=self._ocr_endpoint(),
            zoom=2.0,
            parent=self,
            engine_mode=self._engine_mode(),
            model_path=self._local_model_path(),
        )
        worker.progress.connect(self._on_batch_ocr_progress)
        worker.page_done.connect(self._on_batch_ocr_page_done)
        worker.page_failed.connect(self._on_batch_ocr_page_failed)
        worker.completed.connect(self._on_batch_ocr_completed)
        self._ocr_worker = worker
        logger.info("OCR em lote: paginas %d..%d", start_page + 1, total_pages)
        worker.start()

    def _cancel_batch_ocr(self) -> None:
        if self._ocr_worker is not None:
            self._ocr_worker.cancel()
        if self._ocr_progress is not None:
            self._ocr_progress.setLabelText("Cancelando após a página atual...")

    def _on_batch_ocr_progress(self, page_num: int, total: int) -> None:
        if self._ocr_progress is None:
            return
        batch = self._ocr_batch
        self._ocr_progress.setLabelText(
            f"Página {page_num + 1}/{batch['total_pages']} — {batch['added']} encontrado(s)"
        )
        self._ocr_progress.setValue(page_num - batch["start_page"])

    def _on_batch_ocr_page_failed(self, page_num: int, message: str) -> None:
        self._ocr_batch["errors"] += 1
        logger.warning("OCR em lote falhou na página %d: %s", page_num + 1, message)

    def _on_batch_ocr_page_done(self, page_num: int, detections: list) -> None:
        if not detections:
            return
        batch = self._ocr_batch
        batch["pages_with_hits"] += 1
        pad_left, pad_top, pad_right, pad_bottom = batch["padding"]
        auto_apply = batch["auto_apply"]
        touches_current_page = False

        for detection in detections:
            try:
                piece_placement = normalize_piece_placement(extract_piece_placement(detection.fen))
                validate_piece_placement(piece_placement)
            except Exception:
                batch["skipped"] += 1
                continue

            # Um "diagrama" maior que metade da pagina quase sempre e a pagina
            # inteira detectada por engano.
            if detection.area_ratio > batch["max_area_ratio"]:
                batch["skipped_too_large"] += 1
                continue

            op = OverlayOperation(
                page_num=detection.page_num,
                rect_pdf=detection.rect_pdf,
                fen=piece_placement,
                side_to_move=batch["side_to_move"],
                fullmove_number=batch["fullmove_number"],
                source="ocr-auto",
                confidence=detection.confidence,
                whiteout_padding_pt=(pad_left + pad_top + pad_right + pad_bottom) / 4.0,
                whiteout_padding_left_pt=pad_left,
                whiteout_padding_top_pt=pad_top,
                whiteout_padding_right_pt=pad_right,
                whiteout_padding_bottom_pt=pad_bottom,
                border_width_pt=batch["border_width_pt"],
            )
            if self._has_similar_operation(op):
                batch["skipped"] += 1
                continue

            if auto_apply:
                self.operations.append(op)
            else:
                op.source = "ocr-auto-candidato"
                self.candidates.append(op)
            batch["added"] += 1
            if op.page_num == self.current_page:
                touches_current_page = True

        # As listas so sao remontadas no fim do lote (900 paginas x remontar a
        # lista inteira seria mais caro que o proprio OCR). A pagina visivel e a
        # excecao: ali o usuario ve a deteccao aparecer.
        if touches_current_page:
            self._refresh_page_overlays()

    def _on_batch_ocr_completed(self, next_page: int, canceled: bool) -> None:
        batch = self._ocr_batch
        total_pages = batch["total_pages"]
        auto_apply = batch["auto_apply"]
        added_count = batch["added"]

        if self._ocr_progress is not None:
            self._ocr_progress.close()
            self._ocr_progress = None
        if self._ocr_worker is not None:
            self._ocr_worker.wait()
            self._ocr_worker.deleteLater()
            self._ocr_worker = None

        if canceled:
            self.ocr_full_next_page = max(0, min(int(next_page), total_pages - 1))
        else:
            self.ocr_full_next_page = 0

        self._refresh_operations_list()
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        self._update_edit_context_state()
        if added_count:
            self._commit_history(
                f"Detectar no PDF ({added_count} {'substituições' if auto_apply else 'candidatos'})"
            )
        self._mark_project_dirty()

        label = "aplicadas" if auto_apply else "candidatos"
        status = (
            f"OCR em lote concluído. {label}={added_count}, ignoradas={batch['skipped']}, "
            f"grandes_descartadas={batch['skipped_too_large']}, "
            f"páginas com detecção={batch['pages_with_hits']}, falhas={batch['errors']}"
        )
        if canceled:
            status += f" (cancelado pelo usuário; retomada na página {self.ocr_full_next_page + 1})"
        if not auto_apply and added_count:
            status += ". Confira os candidatos antes de aplicar."
        self.statusBar().showMessage(status)
        logger.info("%s", status)

        QtWidgets.QMessageBox.information(self, "OCR em lote", status)

        if not auto_apply and added_count:
            self.candidates_list.setCurrentRow(0)
            self._focus_candidate(0)
            return

        # A exportacao automatica so faz sentido quando as substituicoes ja
        # foram aplicadas sem conferencia.
        if not canceled and added_count > 0 and self.current_pdf_path:
            auto_path = str(
                Path(self.current_pdf_path).with_name(Path(self.current_pdf_path).stem + "_hq.pdf")
            )
            self._save_output_pdf(auto_save_path=auto_path)

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

        source = "ocr" if self._last_ocr_result is not None else "manual"
        confidence = self._last_ocr_result.confidence if self._last_ocr_result else None
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
        selected_idx = self._selected_operation_index()
        if selected_idx is None:
            fen_item = self.fen_ops_list.currentItem()
            if fen_item is not None:
                candidate = int(fen_item.data(QtCore.Qt.UserRole))
                if 0 <= candidate < len(self.operations):
                    selected_idx = candidate

        self.fen_ops_list.clear()
        for idx, op in enumerate(self.operations):
            fen_item = QtWidgets.QListWidgetItem(
                f"{idx + 1:03d} | pág {op.page_num + 1} | {self._operation_full_fen(op)}"
            )
            fen_item.setData(QtCore.Qt.UserRole, idx)
            self.fen_ops_list.addItem(fen_item)

        if selected_idx is not None and 0 <= selected_idx < len(self.operations):
            self._current_operation_index = selected_idx
            self._select_operation_in_fen_tab(selected_idx)
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
            self.changes_label.setText(f"Alterações ({total_changes})")

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

    # ------------------------------------------------------------------
    # Fila de candidatos (deteccoes aguardando conferencia)
    # ------------------------------------------------------------------

    def _on_auto_apply_toggled(self, checked: bool) -> None:
        self.settings.setValue("auto_apply_recognition", bool(checked))
        if checked:
            self.statusBar().showMessage(
                "Reconhecer página/PDF vai aplicar as substituições direto."
            )
        else:
            self.statusBar().showMessage(
                "Reconhecer página/PDF vai listar candidatos para você conferir antes de aplicar."
            )

    def _selected_candidate_index(self) -> Optional[int]:
        item = self.candidates_list.currentItem()
        if not item:
            return None
        data = item.data(QtCore.Qt.UserRole)
        if data is None:
            return None
        idx = int(data)
        return idx if 0 <= idx < len(self.candidates) else None

    def _refresh_candidates_list(self, keep_row: Optional[int] = None) -> None:
        previous = self._selected_candidate_index() if keep_row is None else keep_row
        self._loading_ui = True
        try:
            self.candidates_list.clear()
            self.candidates_label.setText(f"2 · Conferir ({len(self.candidates)})")
            # Fila vazia e o estado normal: esconder a secao inteira devolve
            # espaco vertical para o que importa.
            self.candidates_section.setVisible(bool(self.candidates))
            for idx, candidate in enumerate(self.candidates):
                item = QtWidgets.QListWidgetItem(
                    f"{idx + 1:03d} | pág {candidate.page_num + 1} | "
                    f"{candidate.fen[:28]}{'...' if len(candidate.fen) > 28 else ''}"
                )
                item.setData(QtCore.Qt.UserRole, idx)
                self.candidates_list.addItem(item)
            if previous is not None and self.candidates:
                self.candidates_list.setCurrentRow(min(previous, len(self.candidates) - 1))
        finally:
            self._loading_ui = False
        self._update_candidate_buttons()
        self._refresh_page_overlays()

    def _update_candidate_buttons(self) -> None:
        has_selection = self._selected_candidate_index() is not None
        has_any = bool(self.candidates)
        self.btn_apply_candidate.setEnabled(has_selection)
        self.btn_discard_candidate.setEnabled(has_selection)
        self.btn_apply_all_candidates.setEnabled(has_any)
        self.btn_discard_all_candidates.setEnabled(has_any)

    def _on_candidate_selected(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        del previous
        self._update_candidate_buttons()
        if self._loading_ui or current is None:
            return
        data = current.data(QtCore.Qt.UserRole)
        if data is None:
            return
        self._focus_candidate(int(data))

    def _on_candidate_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        data = item.data(QtCore.Qt.UserRole)
        if data is not None:
            self._focus_candidate(int(data))

    def _focus_candidate(self, idx: int) -> None:
        """Leva a pagina/selecao/posicao do candidato para o editor.

        A previa passa a mostrar como aquele diagrama ficaria, sem que nada
        tenha sido aplicado ao PDF ainda.
        """
        if not (0 <= idx < len(self.candidates)) or not self.pdf_service:
            return
        candidate = self.candidates[idx]
        self.current_page = min(max(0, candidate.page_num), self.pdf_service.page_count - 1)
        self._render_current_page()
        if self.current_render:
            rect_img = self.pdf_service.pdf_rect_to_image_rect(
                self.current_page,
                candidate.rect_pdf,
                self.current_render.matrix,
            )
            self.page_widget.set_selection_rect(rect_img)
        self._loading_ui = True
        try:
            self.board_editor.set_piece_placement(candidate.fen)
            self.fen_edit.setText(candidate.fen)
            self.fen_side_combo.setCurrentIndex(1 if candidate.side_to_move == "b" else 0)
            self.fen_move_spin.setValue(max(1, int(candidate.fullmove_number)))
        finally:
            self._loading_ui = False
        self._set_position_anchor(candidate.rect_pdf)
        self._update_warnings(candidate.fen)
        self._update_lichess_link()
        self._update_edit_context_state()
        self._schedule_preview_refresh(immediate=True)
        self.statusBar().showMessage(
            f"Candidato {idx + 1}/{len(self.candidates)} na página {candidate.page_num + 1}. "
            "Confira a posição e clique em Aplicar."
        )

    def _apply_selected_candidate(self) -> None:
        idx = self._selected_candidate_index()
        if idx is None:
            return
        candidate = self.candidates[idx]
        # Se o usuario corrigiu a posicao enquanto conferia, aplica o que esta
        # na tela (o rascunho da previa), nao a deteccao original. So vale se o
        # rascunho for mesmo deste candidato: navegar para outra pagina ou
        # selecionar outra area nao pode sequestrar o Aplicar.
        draft = self._draft_operation()
        applied = candidate
        if (
            draft is not None
            and draft.page_num == candidate.page_num
            and self._rect_iou(draft.rect_pdf, candidate.rect_pdf) >= 0.40
        ):
            applied = draft
        self.operations.append(applied)
        del self.candidates[idx]
        self._refresh_operations_list()
        self._refresh_candidates_list(keep_row=idx)
        self._select_change("operation", len(self.operations) - 1)
        self._refresh_page_overlays()
        self._commit_history("aplicar candidato")
        self.statusBar().showMessage(
            f"Candidato aplicado. Substituições: {len(self.operations)} | "
            f"Candidatos restantes: {len(self.candidates)}"
        )
        if self.candidates:
            self._focus_candidate(min(idx, len(self.candidates) - 1))

    def _discard_selected_candidate(self) -> None:
        idx = self._selected_candidate_index()
        if idx is None:
            return
        del self.candidates[idx]
        self._refresh_candidates_list(keep_row=idx)
        self._commit_history("descartar candidato")
        self.statusBar().showMessage(f"Candidato descartado. Restantes: {len(self.candidates)}")
        if self.candidates:
            self._focus_candidate(min(idx, len(self.candidates) - 1))
        else:
            self._schedule_preview_refresh(immediate=True)

    def _apply_all_candidates(self) -> None:
        if not self.candidates:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Aplicar todos",
            f"Aplicar {len(self.candidates)} candidato(s) sem conferir um a um?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.operations.extend(self.candidates)
        applied = len(self.candidates)
        self.candidates = []
        self._refresh_operations_list()
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        self._commit_history(f"aplicar {applied} candidato(s)")
        self.statusBar().showMessage(f"{applied} candidato(s) aplicados. Total: {len(self.operations)}")

    def _discard_all_candidates(self) -> None:
        if not self.candidates:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Descartar todos",
            f"Descartar {len(self.candidates)} candidato(s)?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        discarded = len(self.candidates)
        self.candidates = []
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        self._schedule_preview_refresh(immediate=True)
        self._commit_history(f"descartar {discarded} candidato(s)")
        self.statusBar().showMessage("Candidatos descartados.")

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

    def _remove_selected_fen_operation(self) -> None:
        idx = self._selected_fen_operation_index()
        if idx is None:
            return
        self._remove_operation_at_index(idx)

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
        progress = QtWidgets.QProgressDialog("Exportando PDF...", "", 0, 0, self)
        progress.setWindowTitle("Exportar PDF")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setCancelButton(None)  # `apply_operations_to_pdf` nao e interrompivel
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._export_progress = progress

        worker = ExportWorker(
            self.current_pdf_path,
            out_path,
            self.operations,
            erase_operations=self.erase_operations,
            whiteout=self.whiteout_check.isChecked(),
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            parent=self,
        )
        worker.done.connect(self._on_export_done)
        worker.failed.connect(self._on_export_failed)
        self._export_worker = worker
        self.statusBar().showMessage(f"Exportando para {out_path}...")
        worker.start()

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

        self._open_pdf(state.source_pdf, clear_ops=False)
        self.operations = state.operations
        self.erase_operations = state.erase_operations
        self.study_positions = state.study_positions
        self.candidates = list(getattr(state, "candidates", []))
        self._position_anchor = None
        self.include_lichess_link_check.setChecked(bool(getattr(state, "include_lichess_link", True)))
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

    from .resources import asset_roots, is_frozen

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    problems: list[str] = []

    print(f"congelado: {is_frozen()}")
    print("raízes de assets:")
    for root in asset_roots():
        print(f"  - {root}")

    model = local_ocr.default_model_path()
    if model is None:
        problems.append(f"classificador não encontrado ({local_ocr.unavailable_reason()})")
    else:
        print(f"classificador: {model}")

    if not local_ocr.dependencies_available():
        # A razão importa: num bundle, "ausente" quase sempre quer dizer "excluído
        # por engano no .spec", e o nome do módulo que falhou é o que aponta qual.
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
            print(f"classificador carregado em {(time.perf_counter() - started) * 1000:.0f} ms")

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
