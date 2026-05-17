from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .fen import extract_piece_placement, normalize_piece_placement, validate_piece_placement
from .ocr_api import OcrApiClient, OcrApiError
from .pdf_service import PdfService, apply_operations_to_pdf, crop_from_rendered_page
from .project_state import ProjectState, fingerprint_file, load_project_state, save_project_state
from .types import EraseOperation, OcrBoardResult, OverlayOperation, StudyPosition
from .widgets import BoardEditorWidget, SelectablePageWidget, StudyBoardWidget


class StudyPanel(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tabuleiro de Estudo")
        self.resize(980, 760)
        self._syncing_move_list = False

        self.study_board = StudyBoardWidget(cell_size=58)
        self.study_board.state_changed.connect(self._on_state_changed)
        self.study_board.line_changed.connect(self._on_line_changed)

        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItem("Brancas", "w")
        self.side_combo.addItem("Pretas", "b")
        self.arrow_check = QtWidgets.QCheckBox("Seta do ultimo lance")
        self.arrow_check.setChecked(True)
        self.arrow_check.toggled.connect(self.study_board.set_show_last_move_arrow)

        self.btn_undo = QtWidgets.QPushButton("Desfazer")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo = QtWidgets.QPushButton("Refazer")
        self.btn_redo.clicked.connect(self._redo)
        self.btn_reset = QtWidgets.QPushButton("Resetar Linha")
        self.btn_reset.clicked.connect(self._reset)
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

        self.moves_list = QtWidgets.QListWidget()
        self.moves_list.currentRowChanged.connect(self._on_san_row_changed)

        self.status_label = QtWidgets.QLabel("Clique nas pecas para estudar linhas legais.")
        self.status_label.setWordWrap(True)

        controls_top = QtWidgets.QHBoxLayout()
        controls_top.addWidget(QtWidgets.QLabel("Lado a jogar no FEN do editor:"))
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
        controls_2.addWidget(self.btn_copy_fen)
        controls_2.addWidget(self.btn_copy_pgn)
        controls_2.addWidget(self.btn_save_pgn)

        side_panel = QtWidgets.QVBoxLayout()
        side_panel.addWidget(QtWidgets.QLabel("Lista SAN"))
        side_panel.addWidget(self.moves_list, 1)

        center_layout = QtWidgets.QHBoxLayout()
        center_layout.addWidget(self.study_board, 1, QtCore.Qt.AlignCenter)
        side_widget = QtWidgets.QWidget()
        side_widget.setLayout(side_panel)
        side_widget.setMinimumWidth(250)
        center_layout.addWidget(side_widget)

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(controls_top)
        root.addLayout(center_layout, 1)
        root.addLayout(controls_1)
        root.addLayout(controls_2)
        root.addWidget(self.status_label)
        self._on_line_changed([], 0)

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
            self.status_label.setText("Posicao carregada do editor.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN invalido", str(exc))

    def _undo(self) -> None:
        if not self.study_board.undo_move():
            self.status_label.setText("Nada para desfazer.")

    def _redo(self) -> None:
        if not self.study_board.redo_move():
            self.status_label.setText("Nada para refazer.")

    def _reset(self) -> None:
        self.study_board.clear_moves()

    def _copy_fen(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.study_board.current_fen())
        self.status_label.setText("FEN copiado.")

    def _copy_pgn(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.study_board.current_pgn())
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
            Path(out_path).write_text(self.study_board.current_pgn(), encoding="utf-8")
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
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            self.study_board.load_pgn_text(text)
            self.status_label.setText(f"PGN importado: {file_path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao importar PGN", str(exc))

    def _on_san_row_changed(self, row: int) -> None:
        if self._syncing_move_list:
            return
        item = self.moves_list.item(row) if row >= 0 else None
        data = item.data(QtCore.Qt.UserRole) if item is not None else None
        if isinstance(data, tuple) and len(data) == 2:
            ply = int(data[1])
        else:
            ply = 0
        self.study_board.goto_ply(ply)

    @staticmethod
    def _format_san_rows(
        moves: list[str],
        start_turn: str,
        start_fullmove_number: int,
    ) -> list[tuple[str, int, int]]:
        rows: list[tuple[str, int, int]] = []
        side = "b" if start_turn == "b" else "w"
        move_no = max(1, int(start_fullmove_number))
        ply_idx = 0
        while ply_idx < len(moves):
            if side == "b":
                start_ply = ply_idx + 1
                rows.append((f"{move_no}... {moves[ply_idx]}", start_ply, start_ply))
                ply_idx += 1
                move_no += 1
                side = "w"
                continue

            start_ply = ply_idx + 1
            white_san = moves[ply_idx]
            ply_idx += 1
            if ply_idx < len(moves):
                black_san = moves[ply_idx]
                end_ply = ply_idx + 1
                rows.append((f"{move_no}. {white_san} {black_san}", start_ply, end_ply))
                ply_idx += 1
                move_no += 1
                side = "w"
            else:
                rows.append((f"{move_no}. {white_san}", start_ply, start_ply))
                side = "b"
        return rows

    def _on_line_changed(self, san_line: object, cursor: int) -> None:
        moves = list(san_line) if isinstance(san_line, list) else list(san_line or [])
        self._syncing_move_list = True
        try:
            self.moves_list.clear()
            selected_row = -1
            rows = self._format_san_rows(
                moves,
                self.study_board.start_turn(),
                self.study_board.start_fullmove_number(),
            )
            for row_idx, (label, start_ply, end_ply) in enumerate(rows):
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, (start_ply, end_ply))
                self.moves_list.addItem(item)
                if start_ply <= cursor <= end_ply:
                    selected_row = row_idx
            self.moves_list.setCurrentRow(selected_row)
        finally:
            self._syncing_move_list = False

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
    _PRIMARY_BUTTON_STYLE = (
        "QPushButton { background-color: #1f6feb; color: white; font-weight: 600; "
        "padding: 6px 10px; border-radius: 4px; } "
        "QPushButton:disabled { background-color: #9bbce8; color: #f4f7fb; }"
    )
    _SECONDARY_BUTTON_STYLE = ""
    _CONTEXT_STYLE = (
        "QLabel { background-color: #f3f6fa; color: #223042; border: 1px solid #d8e0ea; "
        "border-radius: 5px; padding: 8px; }"
    )
    _SECTION_STYLE = "QLabel { color: #223042; font-weight: 600; margin-top: 6px; }"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chess PDF Editor")
        self.resize(1500, 900)
        self.settings = QtCore.QSettings("ChessPdfEditor", "ChessPdfEditor")

        self.pdf_service: Optional[PdfService] = None
        self.current_pdf_path: Optional[str] = None
        self.current_render = None
        self.current_page = 0
        self.ocr_full_next_page = 0
        self.project_path: Optional[str] = None
        self.operations: list[OverlayOperation] = []
        self.erase_operations: list[EraseOperation] = []
        self.study_positions: list[StudyPosition] = []
        self._loading_ui = False
        self._syncing_fen_tab = False
        self._syncing_study_positions = False
        self._last_ocr_result: Optional[OcrBoardResult] = None
        self.study_dialog: Optional[StudyDialog] = None

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
        self.warnings.setStyleSheet("color: #b02020;")

        self.endpoint_edit = QtWidgets.QLineEdit("https://helpman.komtera.lt/chessocr/predict")
        self.endpoint_edit.setPlaceholderText("OCR endpoint")
        self.whiteout_check = QtWidgets.QCheckBox("Aplicar whiteout antes do overlay")
        self.whiteout_check.setChecked(True)
        self.include_lichess_link_check = QtWidgets.QCheckBox("Incluir link Lichess no PDF exportado")
        self.include_lichess_link_check.setChecked(bool(self.settings.value("include_lichess_link", True, bool)))
        self.include_lichess_link_check.toggled.connect(
            lambda checked: self.settings.setValue("include_lichess_link", bool(checked))
        )
        self.merida_font_edit = QtWidgets.QLineEdit()
        self.merida_font_edit.setPlaceholderText("Caminho da fonte Merida (.ttf/.otf)")
        self.btn_select_merida = QtWidgets.QPushButton("Selecionar Fonte...")
        self.btn_select_merida.clicked.connect(self._select_merida_font)
        self.btn_clear_merida = QtWidgets.QPushButton("Limpar")
        self.btn_clear_merida.clicked.connect(self._clear_merida_font)

        self.ops_list = QtWidgets.QListWidget()
        self.ops_list.itemDoubleClicked.connect(self._on_operation_double_clicked)
        self.ops_list.currentItemChanged.connect(self._on_operation_selected)
        self.ops_list_delete_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Delete), self.ops_list)
        self.ops_list_delete_shortcut.activated.connect(self._remove_selected_operation)
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
        self.fen_ops_list.setStyleSheet(
            "QListWidget::item:selected { background-color: #bfe3ff; color: #102030; }"
        )
        self.fen_side_combo = QtWidgets.QComboBox()
        self.fen_side_combo.addItem("Brancas", "w")
        self.fen_side_combo.addItem("Pretas", "b")
        self.fen_side_combo.currentIndexChanged.connect(self._on_fen_meta_changed)
        self.fen_move_spin = QtWidgets.QSpinBox()
        self.fen_move_spin.setRange(1, 500)
        self.fen_move_spin.setValue(1)
        self.fen_move_spin.valueChanged.connect(self._on_fen_meta_changed)
        self.erasers_list = QtWidgets.QListWidget()
        self.erasers_list.itemDoubleClicked.connect(self._on_eraser_double_clicked)
        self.erasers_list.currentItemChanged.connect(lambda current, previous: self._update_edit_context_state())
        self.study_positions_list = QtWidgets.QListWidget()
        self.study_positions_list.itemDoubleClicked.connect(self._on_study_position_double_clicked)
        self.study_positions_list.currentItemChanged.connect(self._on_study_position_selected)
        self.study_comment_before_edit = QtWidgets.QPlainTextEdit()
        self.study_comment_before_edit.setPlaceholderText("Comentario antes da linha")
        self.study_comment_before_edit.setMaximumHeight(78)
        self.study_comment_before_edit.textChanged.connect(self._on_study_comment_changed)
        self.study_comment_after_edit = QtWidgets.QPlainTextEdit()
        self.study_comment_after_edit.setPlaceholderText("Comentario depois da linha")
        self.study_comment_after_edit.setMaximumHeight(78)
        self.study_comment_after_edit.textChanged.connect(self._on_study_comment_changed)

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
        self.apply_style_all_check = QtWidgets.QCheckBox("Aplicar em todas as substituicoes")
        self.apply_style_all_check.setChecked(True)
        self.lichess_link_label = QtWidgets.QLabel()
        self.lichess_link_label.setTextFormat(QtCore.Qt.RichText)
        self.lichess_link_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.lichess_link_label.setOpenExternalLinks(True)
        self.lichess_link_label.setToolTip("Abrir posicao atual no Lichess")
        self.btn_add_eraser = QtWidgets.QPushButton("Adicionar apagamento")
        self.btn_add_eraser.clicked.connect(self._add_eraser_from_selection)
        self.btn_remove = QtWidgets.QPushButton("Remover")
        self.btn_remove.clicked.connect(self._remove_selected_change)
        self.btn_remove_fen = QtWidgets.QPushButton("Remover posicao")
        self.btn_remove_fen.clicked.connect(self._remove_selected_fen_operation)
        self.btn_clear = QtWidgets.QPushButton("Limpar")
        self.btn_clear.clicked.connect(self._clear_changes)
        self.btn_remove_eraser = QtWidgets.QPushButton("Remover")
        self.btn_remove_eraser.clicked.connect(self._remove_selected_eraser)
        self.btn_clear_erasers = QtWidgets.QPushButton("Limpar")
        self.btn_clear_erasers.clicked.connect(self._clear_erasers)
        self.btn_study_selection = QtWidgets.QPushButton("Estudar selecao")
        self.btn_study_selection.clicked.connect(self._study_selection)
        self.btn_save_study_line = QtWidgets.QPushButton("Atualizar linha")
        self.btn_save_study_line.clicked.connect(self._save_current_study_line)
        self.btn_pdf_text_to_before = QtWidgets.QPushButton("Texto -> antes")
        self.btn_pdf_text_to_before.clicked.connect(lambda: self._copy_pdf_text_to_study_comment("before"))
        self.btn_pdf_text_to_after = QtWidgets.QPushButton("Texto -> depois")
        self.btn_pdf_text_to_after.clicked.connect(lambda: self._copy_pdf_text_to_study_comment("after"))
        self.btn_remove_study_position = QtWidgets.QPushButton("Remover posicao")
        self.btn_remove_study_position.clicked.connect(self._remove_selected_study_position)

        self.btn_rotate = QtWidgets.QPushButton("Rotacionar 90°")
        self.btn_rotate.clicked.connect(self.board_editor.rotate_clockwise)
        self.btn_flip = QtWidgets.QPushButton("Espelhar Vertical")
        self.btn_flip.clicked.connect(self.board_editor.flip_vertical)
        self.btn_clear_board = QtWidgets.QPushButton("Limpar Tabuleiro")
        self.btn_clear_board.clicked.connect(self.board_editor.clear_board)

        top_editor = QtWidgets.QWidget()
        top_editor_layout = QtWidgets.QVBoxLayout(top_editor)
        top_editor_layout.addWidget(QtWidgets.QLabel("Editor de Tabuleiro"))
        top_editor_layout.addWidget(self.board_editor, 0, QtCore.Qt.AlignLeft)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.btn_rotate)
        controls.addWidget(self.btn_flip)
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
        ocr_tab_layout.addWidget(self._section_label("Reconhecimento"))
        ocr_tab_layout.addWidget(self.btn_ocr)
        ocr_actions = QtWidgets.QHBoxLayout()
        ocr_actions.addWidget(self.btn_ocr_page)
        ocr_actions.addWidget(self.btn_ocr_full)
        ocr_tab_layout.addLayout(ocr_actions)

        ocr_tab_layout.addWidget(self._section_label("Aplicar"))
        ocr_tab_layout.addWidget(self.btn_add)
        ocr_tab_layout.addWidget(self.btn_add_eraser)

        self.changes_label = self._section_label("Alterações")
        ocr_tab_layout.addWidget(self.changes_label)
        ocr_tab_layout.addWidget(self.changes_list, 3)
        right_actions = QtWidgets.QHBoxLayout()
        right_actions.addWidget(self.btn_remove)
        right_actions.addWidget(self.btn_clear)
        right_actions.addStretch(1)
        ocr_tab_layout.addLayout(right_actions)

        ocr_advanced_layout = QtWidgets.QVBoxLayout()
        ocr_advanced_layout.addWidget(self.whiteout_check)
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
        fen_meta.addWidget(QtWidgets.QLabel("Numero do lance"), 1, 0)
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
        self.edit_tabs.addTab(ocr_tab, "OCR")
        self.edit_tabs.addTab(fens_tab, "FEN")
        self.edit_tabs.addTab(whiteout_tab, "Aparência")
        self.edit_tabs.setMinimumHeight(220)
        bottom_layout.addWidget(self.edit_tabs, 2)

        right_vertical_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_vertical_splitter.setChildrenCollapsible(False)
        right_vertical_splitter.addWidget(top_editor)
        right_vertical_splitter.addWidget(bottom_panel)
        right_vertical_splitter.setStretchFactor(0, 2)
        right_vertical_splitter.setStretchFactor(1, 5)
        right_vertical_splitter.setSizes([360, 560])

        self.study_panel = StudyPanel(self)
        study_positions_panel = QtWidgets.QWidget()
        study_positions_layout = QtWidgets.QVBoxLayout(study_positions_panel)
        study_positions_layout.addWidget(self._section_label("Posições deste PDF"))
        study_positions_layout.addWidget(self.study_positions_list, 1)
        study_positions_layout.addWidget(self._section_label("Comentário antes"))
        study_positions_layout.addWidget(self.study_comment_before_edit)
        study_positions_layout.addWidget(self._section_label("Comentário depois"))
        study_positions_layout.addWidget(self.study_comment_after_edit)
        study_actions = QtWidgets.QHBoxLayout()
        study_actions.addWidget(self.btn_study_selection)
        study_actions.addWidget(self.btn_save_study_line)
        study_actions.addWidget(self.btn_pdf_text_to_before)
        study_actions.addWidget(self.btn_pdf_text_to_after)
        study_actions.addWidget(self.btn_remove_study_position)
        study_positions_layout.addLayout(study_actions)

        study_workspace = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        study_workspace.setChildrenCollapsible(False)
        study_workspace.addWidget(self.study_panel)
        study_workspace.addWidget(study_positions_panel)
        study_workspace.setStretchFactor(0, 4)
        study_workspace.setStretchFactor(1, 2)
        study_workspace.setSizes([560, 260])

        self.side_stack = QtWidgets.QStackedWidget()
        self.side_stack.addWidget(right_vertical_splitter)
        self.side_stack.addWidget(study_workspace)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(self.side_stack)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

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
        self._try_restore_last_project()
        self._update_lichess_link()
        self._refresh_study_positions_list()
        self._refresh_changes_list()
        self._update_edit_context_state()

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
        if not hasattr(self, "edit_context_label"):
            return

        has_pdf = bool(self.pdf_service and self.current_render)
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
        self.btn_remove_eraser.setEnabled(self.erasers_list.currentItem() is not None)
        self.btn_clear_erasers.setEnabled(bool(self.erase_operations))
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

        self.act_mode_read = QtGui.QAction("Leitura", self)
        self.act_mode_read.setCheckable(True)
        self.act_mode_read.triggered.connect(lambda: self._set_mode("read"))
        toolbar.addAction(self.act_mode_read)

        self.act_mode_study = QtGui.QAction("Estudo", self)
        self.act_mode_study.setCheckable(True)
        self.act_mode_study.triggered.connect(lambda: self._set_mode("study"))
        toolbar.addAction(self.act_mode_study)

        self.act_mode_edit = QtGui.QAction("Edicao", self)
        self.act_mode_edit.setCheckable(True)
        self.act_mode_edit.triggered.connect(lambda: self._set_mode("edit"))
        toolbar.addAction(self.act_mode_edit)

        self.mode_group = QtGui.QActionGroup(self)
        self.mode_group.setExclusive(True)
        for action in (self.act_mode_read, self.act_mode_study, self.act_mode_edit):
            self.mode_group.addAction(action)

        toolbar.addSeparator()

        self.act_prev = QtGui.QAction("Pagina -", self)
        self.act_prev.setShortcut(QtGui.QKeySequence.MoveToPreviousChar)
        self.act_prev.triggered.connect(self._prev_page)
        toolbar.addAction(self.act_prev)

        self.act_next = QtGui.QAction("Pagina +", self)
        self.act_next.setShortcut(QtGui.QKeySequence.MoveToNextChar)
        self.act_next.triggered.connect(self._next_page)
        toolbar.addAction(self.act_next)

        toolbar.addWidget(QtWidgets.QLabel("  Pagina: "))
        toolbar.addWidget(self.page_spin)

        toolbar.addWidget(QtWidgets.QLabel("  Zoom: "))
        toolbar.addWidget(self.zoom_spin)

        self.act_study_selection = QtGui.QAction("Estudar selecao", self)
        self.act_study_selection.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.act_study_selection.triggered.connect(self._study_selection)

        self.act_pdf_text_to_before = QtGui.QAction("Texto da selecao -> comentario antes", self)
        self.act_pdf_text_to_before.triggered.connect(lambda: self._copy_pdf_text_to_study_comment("before"))

        self.act_pdf_text_to_after = QtGui.QAction("Texto da selecao -> comentario depois", self)
        self.act_pdf_text_to_after.triggered.connect(lambda: self._copy_pdf_text_to_study_comment("after"))

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
        file_menu.addSeparator()
        file_menu.addAction("Sair", self.close)

        mode_menu = self.menuBar().addMenu("Modo")
        mode_menu.addAction(self.act_mode_read)
        mode_menu.addAction(self.act_mode_study)
        mode_menu.addAction(self.act_mode_edit)

        study_menu = self.menuBar().addMenu("Estudo")
        study_menu.addAction(self.act_study_selection)
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

        diagrams_menu = self.menuBar().addMenu("Diagramas")
        diagrams_menu.addAction(self.act_recognize_selection)
        diagrams_menu.addAction(self.act_recognize_page)
        diagrams_menu.addAction(self.act_recognize_full)
        diagrams_menu.addSeparator()
        diagrams_menu.addAction(self.act_add_operation)
        diagrams_menu.addAction("Adicionar apagamento", self._add_eraser_from_selection)

        settings_menu = self.menuBar().addMenu("Configuracoes")
        settings_menu.addAction("Selecionar fonte Merida", self._select_merida_font)
        settings_menu.addAction("Limpar fonte Merida", self._clear_merida_font)

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
        self.statusBar().showMessage("Modo edicao.")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
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
        self.statusBar().showMessage("Fonte Merida desativada. Usando fallback de render.")

    def _apply_merida_font_path(self, file_path: str, persist: bool) -> None:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            QtWidgets.QMessageBox.warning(self, "Fonte invalida", "Arquivo de fonte nao encontrado.")
            return
        if p.suffix.lower() not in {".ttf", ".otf"}:
            QtWidgets.QMessageBox.warning(self, "Fonte invalida", "Selecione um arquivo .ttf ou .otf.")
            return

        resolved = str(p.resolve())
        self.merida_font_edit.setText(resolved)
        os.environ["CHESS_MERIDA_FONT"] = resolved
        if persist:
            self.settings.setValue("merida_font_path", resolved)
        self.statusBar().showMessage(f"Fonte Merida configurada: {resolved}")

    def _open_pdf_dialog(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Abrir PDF", "", "PDF (*.pdf)")
        if not file_path:
            return
        self._open_pdf(file_path, clear_ops=True)

    def _remember_last_project_path(self, project_path: str) -> None:
        self.settings.setValue("last_project_path", project_path)

    def _try_restore_last_project(self) -> None:
        last_project = (self.settings.value("last_project_path", "", str) or "").strip()
        if not last_project:
            return
        if not Path(last_project).exists():
            self.settings.remove("last_project_path")
            return
        self.project_path = last_project
        loaded = self._load_project_from_path(last_project, show_dialogs=False)
        if loaded:
            self.statusBar().showMessage(f"Projeto restaurado: {last_project}")

    def _open_pdf(self, file_path: str, clear_ops: bool) -> None:
        if self.pdf_service:
            self.pdf_service.close()
        self.pdf_service = PdfService(file_path)
        self.current_pdf_path = file_path
        self.current_page = 0
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, self.pdf_service.page_count))
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        if clear_ops:
            self.operations = []
            self.erase_operations = []
            self.study_positions = []
            self.ocr_full_next_page = 0
            self._refresh_operations_list()
            self._refresh_erasers_list()
            self._refresh_study_positions_list()
        self._render_current_page()
        self._update_edit_context_state()
        self.statusBar().showMessage(f"PDF aberto: {file_path}")

    def _render_current_page(self) -> None:
        if not self.pdf_service:
            return
        self.current_page = max(0, min(self.current_page, self.pdf_service.page_count - 1))
        zoom = float(self.zoom_spin.value())
        self.current_render = self.pdf_service.render_page(self.current_page, zoom=zoom)
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(self.current_render.image_png, "PNG")
        self.page_widget.set_page_pixmap(pixmap)
        self._refresh_page_overlays()
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.current_page + 1)
        self.page_spin.blockSignals(False)
        self.setWindowTitle(
            f"Chess PDF Editor - {Path(self.current_pdf_path or '').name} - Pagina {self.current_page + 1}/{self.pdf_service.page_count}"
        )
        self._update_edit_context_state()

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
        item = self.ops_list.currentItem()
        if not item:
            return None
        idx = int(item.data(QtCore.Qt.UserRole))
        if 0 <= idx < len(self.operations):
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
            self.lichess_link_label.setText('<span style="color:#6c6c6c;">Lichess (FEN invalido)</span>')
            self.lichess_link_label.setToolTip("Informe uma FEN valida para abrir no Lichess.")
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
        if not self.pdf_service or not self.current_render:
            self.page_widget.set_operation_rects([])
            self.page_widget.set_eraser_rects([])
            self.page_widget.set_study_rects([])
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

    def _on_selection_changed(self, rect: object) -> None:
        if rect is None:
            self.statusBar().showMessage("Selecao limpa.")
            self._update_edit_context_state()
            return
        x0, y0, x1, y1 = rect
        self.statusBar().showMessage(
            f"Selecao imagem: x0={x0:.1f}, y0={y0:.1f}, x1={x1:.1f}, y1={y1:.1f}"
        )
        self._update_edit_context_state()

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
        self.ops_list.setCurrentRow(idx)
        self._set_mode("study")
        self.statusBar().showMessage(f"Diagrama da pagina {op.page_num + 1} carregado no estudo.")

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
                "Nenhum diagrama conhecido nesse clique. Selecione a area e use Reconhecer seleção ou Estudar selecao."
            )
            return

        idx = self._operation_index_at_image_point(x, y)
        if idx is None:
            return
        self.ops_list.setCurrentRow(idx)
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
        finally:
            self._syncing_study_positions = False

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
            self.study_comment_before_edit.setPlainText(pos.comment_before or pos.note)
            self.study_comment_after_edit.setPlainText(pos.comment_after)
        finally:
            self._syncing_study_positions = False
        self._load_study_position(pos)
        self._set_mode("study")

    def _on_study_position_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        idx = int(item.data(QtCore.Qt.UserRole))
        self._focus_study_position(idx)

    def _on_study_position_selected(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        del previous
        if self._syncing_study_positions or current is None:
            return
        idx = int(current.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.study_positions)):
            return
        self._syncing_study_positions = True
        try:
            pos = self.study_positions[idx]
            self.study_comment_before_edit.setPlainText(pos.comment_before or pos.note)
            self.study_comment_after_edit.setPlainText(pos.comment_after)
        finally:
            self._syncing_study_positions = False

    def _on_study_comment_changed(self) -> None:
        if self._syncing_study_positions:
            return
        idx = self._selected_study_position_index()
        if idx is None:
            return
        self.study_positions[idx].comment_before = self.study_comment_before_edit.toPlainText()
        self.study_positions[idx].comment_after = self.study_comment_after_edit.toPlainText()
        self.study_positions[idx].note = self.study_positions[idx].comment_before
        self._refresh_study_positions_list()

    def _save_current_study_line(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            QtWidgets.QMessageBox.information(self, "Estudo", "Selecione uma posicao de estudo primeiro.")
            return
        before = self.study_comment_before_edit.toPlainText()
        after = self.study_comment_after_edit.toPlainText()
        self.study_positions[idx].comment_before = before
        self.study_positions[idx].comment_after = after
        self.study_positions[idx].note = before
        self.study_positions[idx].pgn = self.study_panel.study_board.current_pgn(
            comment_before=before,
            comment_after=after,
        )
        self.statusBar().showMessage("Linha de estudo atualizada.")
        self._refresh_study_positions_list()

    def _remove_selected_study_position(self) -> None:
        idx = self._selected_study_position_index()
        if idx is None:
            return
        del self.study_positions[idx]
        self._refresh_study_positions_list()
        self.statusBar().showMessage(f"Posicao de estudo removida. Total: {len(self.study_positions)}")

    def _load_editor_position_into_study(self) -> None:
        text = self.fen_edit.text().strip()
        if not text:
            text = self.board_editor.piece_placement()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN invalido", str(exc))
            return
        side_to_move, fullmove_number = self._current_fen_defaults()
        self.study_panel.load_piece_placement(
            piece_placement,
            side_to_move=side_to_move,
            fullmove_number=fullmove_number,
        )
        self._set_mode("study")

    def _text_from_current_selection(self) -> str:
        if not self.current_render or not self.pdf_service:
            raise ValueError("Abra um PDF primeiro.")
        selection = self.page_widget.selection_rect()
        if not selection:
            raise ValueError("Selecione a area do texto no PDF.")
        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
            self.current_page,
            selection,
            self.current_render.matrix,
        )
        text = self.pdf_service.extract_text_from_pdf_rect(self.current_page, rect_pdf)
        if not text:
            raise ValueError("Nenhum texto selecionavel encontrado nessa area.")
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
            QtWidgets.QMessageBox.information(self, "Estudo", "Selecione ou crie uma posicao de estudo primeiro.")
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
        self.statusBar().showMessage("Texto copiado do PDF e salvo no comentario.")

    def _study_selection(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return
        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem selecao", "Selecione o diagrama na pagina.")
            return
        text = self.fen_edit.text().strip() or self.board_editor.piece_placement()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
            validate_piece_placement(piece_placement)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN invalido", str(exc))
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
        self.statusBar().showMessage(f"Posicao enviada para estudo. Total: {len(self.study_positions)}")

    def _on_operation_selected(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        del previous
        self._update_edit_context_state()
        if self._loading_ui:
            return
        if current is None:
            return
        idx = int(current.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.operations)):
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
            self.ops_list.setCurrentRow(idx)
            self._select_operation_in_fen_tab(idx)
            self._update_lichess_link()
            return
        if kind == "eraser":
            self.erasers_list.setCurrentRow(idx if 0 <= idx < len(self.erase_operations) else -1)
            self.ops_list.setCurrentRow(-1)
            self.fen_ops_list.setCurrentRow(-1)
            self._update_lichess_link()

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

    def _on_fen_operation_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        if self._syncing_fen_tab:
            return
        idx = int(item.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.operations)):
            return
        self.ops_list.setCurrentRow(idx)
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
        self.ops_list.setCurrentRow(idx)
        self._update_lichess_link()

    def _on_board_changed(self, piece_placement: str) -> None:
        if self._loading_ui:
            return
        self._loading_ui = True
        self.fen_edit.setText(piece_placement)
        self._loading_ui = False
        self._update_warnings(piece_placement)
        self._update_lichess_link()
        self._update_edit_context_state()

    def _on_fen_edited(self) -> None:
        if self._loading_ui:
            return
        text = self.fen_edit.text().strip()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(text))
            self._loading_ui = True
            self.board_editor.set_piece_placement(piece_placement)
            self.fen_edit.setText(piece_placement)
            self._loading_ui = False
            self._update_warnings(piece_placement)
            self._update_lichess_link()
            self._update_edit_context_state()
        except Exception as exc:
            self._loading_ui = False
            self._update_lichess_link()
            self._update_edit_context_state()
            QtWidgets.QMessageBox.warning(self, "FEN invalido", str(exc))

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
            QtWidgets.QMessageBox.warning(self, "Sem selecao", "Selecione uma regiao da pagina.")
            return

        endpoint = self.endpoint_edit.text().strip() or None
        client = OcrApiClient(endpoint=endpoint)

        cursor_set = False
        try:
            crop_png = crop_from_rendered_page(self.current_render.image_png, selection)
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            prediction = client.predict(crop_png, filename="selection.png")
        except OcrApiError as exc:
            QtWidgets.QMessageBox.warning(self, "Falha OCR", str(exc))
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro OCR", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        if not prediction.results:
            QtWidgets.QMessageBox.information(self, "OCR", "Nenhum tabuleiro detectado na selecao.")
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

        info = (
            f"OCR concluido. id={prediction.request_id or '-'} "
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

        endpoint = self.endpoint_edit.text().strip() or None
        client = OcrApiClient(endpoint=endpoint)
        cursor_set = False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            prediction = client.predict(
                self.current_render.image_png,
                filename=f"page_{self.current_page + 1}.png",
            )
        except OcrApiError as exc:
            QtWidgets.QMessageBox.warning(self, "Falha OCR", str(exc))
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro OCR", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        if not prediction.results:
            QtWidgets.QMessageBox.information(self, "OCR", "Nenhum tabuleiro detectado nesta pagina.")
            return

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
            self.operations.append(op)
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
        self._refresh_page_overlays()
        if first_rect is not None:
            self.page_widget.set_selection_rect(first_rect)
            self.ops_list.setCurrentRow(len(self.operations) - added_count)
            self._select_change("operation", len(self.operations) - added_count)
        self._update_edit_context_state()
        self.statusBar().showMessage(
            f"Pagina reconhecida. adicionadas={added_count}, ignoradas={skipped_count}"
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
        for op in self.operations:
            if op.page_num != candidate.page_num:
                continue
            if self._rect_iou(op.rect_pdf, candidate.rect_pdf) >= iou_threshold:
                return True
        return False

    def _recognize_full_pdf(self) -> None:
        if not self.pdf_service or not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de rodar OCR.")
            return

        endpoint = self.endpoint_edit.text().strip() or None
        client = OcrApiClient(endpoint=endpoint)
        total_pages = self.pdf_service.page_count
        if total_pages <= 0:
            QtWidgets.QMessageBox.information(self, "OCR", "PDF sem paginas para processar.")
            return
        start_page = int(self.ocr_full_next_page)
        if start_page < 0 or start_page >= total_pages:
            start_page = 0
        remaining_pages = total_pages - start_page
        if start_page > 0:
            self.statusBar().showMessage(f"OCR em lote retomando da pagina {start_page + 1}/{total_pages}...")

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
        progress.setValue(0)

        added_count = 0
        skipped_count = 0
        skipped_too_large = 0
        pages_with_hits = 0
        error_count = 0
        canceled = False
        max_diagram_area_ratio = 0.50
        next_page_for_resume = start_page

        cursor_set = False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True

            for page_num in range(start_page, total_pages):
                if progress.wasCanceled():
                    canceled = True
                    break

                progress.setLabelText(f"Pagina {page_num + 1}/{total_pages}...")
                progress.setValue(page_num - start_page)
                QtWidgets.QApplication.processEvents()
                try:
                    try:
                        rendered = self.pdf_service.render_page(page_num, zoom=2.0)
                    except Exception:
                        error_count += 1
                        continue
                    try:
                        pred = client.predict(rendered.image_png, filename=f"page_{page_num + 1}.png")
                    except OcrApiError:
                        error_count += 1
                        continue
                    except Exception:
                        error_count += 1
                        continue

                    if not pred.results:
                        continue
                    pages_with_hits += 1

                    for result in pred.results:
                        try:
                            piece_placement = normalize_piece_placement(extract_piece_placement(result.fen))
                            validate_piece_placement(piece_placement)
                        except Exception:
                            skipped_count += 1
                            continue

                        sw = max(1.0, float(rendered.width_px))
                        sh = max(1.0, float(rendered.height_px))
                        x0 = (result.xc - result.width / 2.0) * sw
                        y0 = (result.yc - result.height / 2.0) * sh
                        x1 = (result.xc + result.width / 2.0) * sw
                        y1 = (result.yc + result.height / 2.0) * sh
                        rect_pdf = self.pdf_service.image_rect_to_pdf_rect(
                            page_num,
                            (x0, y0, x1, y1),
                            rendered.matrix,
                        )
                        page_rect = self.pdf_service.doc[page_num].rect
                        page_area = max(1.0, page_rect.width * page_rect.height)
                        diagram_area_ratio = self._rect_area(rect_pdf) / page_area
                        if diagram_area_ratio > max_diagram_area_ratio:
                            skipped_too_large += 1
                            continue

                        pad_left, pad_top, pad_right, pad_bottom = self._current_whiteout_padding()
                        side_to_move, fullmove_number = self._current_fen_defaults()
                        op = OverlayOperation(
                            page_num=page_num,
                            rect_pdf=rect_pdf,
                            fen=piece_placement,
                            side_to_move=side_to_move,
                            fullmove_number=fullmove_number,
                            source="ocr-auto",
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
                        self.operations.append(op)
                        added_count += 1
                finally:
                    next_page_for_resume = page_num + 1

            progress.setValue(remaining_pages)
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()
            progress.close()

        if canceled:
            self.ocr_full_next_page = max(0, min(next_page_for_resume, total_pages - 1))
        else:
            self.ocr_full_next_page = 0

        self._refresh_operations_list()
        self._refresh_page_overlays()
        self._update_edit_context_state()

        status = (
            f"OCR em lote concluido. adicionadas={added_count}, ignoradas={skipped_count}, "
            f"grandes_descartadas={skipped_too_large}, paginas com deteccao={pages_with_hits}, falhas={error_count}"
        )
        if canceled:
            status += f" (cancelado pelo usuario; retomada na pagina {self.ocr_full_next_page + 1})"
        self.statusBar().showMessage(status)

        QtWidgets.QMessageBox.information(self, "OCR em lote", status)
        
        if not canceled and added_count > 0:
            auto_path = str(Path(self.current_pdf_path).with_name(Path(self.current_pdf_path).stem + "_hq.pdf"))
            self._save_output_pdf(auto_save_path=auto_path)

    def _add_operation(self) -> None:
        if not self.current_render or not self.pdf_service:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF primeiro.")
            return

        selection = self.page_widget.selection_rect()
        if not selection:
            QtWidgets.QMessageBox.warning(self, "Sem selecao", "Selecione a area do diagrama na pagina.")
            return

        fen_text = self.fen_edit.text().strip()
        try:
            piece_placement = normalize_piece_placement(extract_piece_placement(fen_text))
            validate_piece_placement(piece_placement)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "FEN invalido", str(exc))
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
        self.ops_list.setCurrentRow(len(self.operations) - 1)
        self._select_change("operation", len(self.operations) - 1)
        self._refresh_page_overlays()
        self._update_edit_context_state()
        self.statusBar().showMessage(f"Substituicao adicionada. Total: {len(self.operations)}")

    def _refresh_operations_list(self) -> None:
        selected_idx = self._selected_operation_index()
        if selected_idx is None:
            fen_item = self.fen_ops_list.currentItem()
            if fen_item is not None:
                candidate = int(fen_item.data(QtCore.Qt.UserRole))
                if 0 <= candidate < len(self.operations):
                    selected_idx = candidate

        self.ops_list.clear()
        self.fen_ops_list.clear()
        for idx, op in enumerate(self.operations):
            pad_desc = (
                f"E{op.whiteout_padding_left_pt:.1f}/T{op.whiteout_padding_top_pt:.1f}/"
                f"D{op.whiteout_padding_right_pt:.1f}/B{op.whiteout_padding_bottom_pt:.1f}pt"
            )
            text = (
                f"{idx + 1:03d} | pag {op.page_num + 1} | "
                f"pad={pad_desc} | borda={op.border_width_pt:.2f}pt | "
                f"{op.fen[:20]}{'...' if len(op.fen) > 20 else ''}"
            )
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, idx)
            self.ops_list.addItem(item)

            fen_item = QtWidgets.QListWidgetItem(
                f"{idx + 1:03d} | pag {op.page_num + 1} | {self._operation_full_fen(op)}"
            )
            fen_item.setData(QtCore.Qt.UserRole, idx)
            self.fen_ops_list.addItem(fen_item)

        if selected_idx is not None and 0 <= selected_idx < len(self.operations):
            self._loading_ui = True
            self.ops_list.setCurrentRow(selected_idx)
            self._loading_ui = False
            self._select_operation_in_fen_tab(selected_idx)
        self._update_lichess_link()
        self._refresh_changes_list()
        self._update_edit_context_state()

    def _refresh_changes_list(self, selected: Optional[tuple[str, int]] = None) -> None:
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
            item.setForeground(QtGui.QColor("#667085"))
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
            QtWidgets.QMessageBox.warning(self, "Sem selecao", "Selecione uma area para apagar.")
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
        self.statusBar().showMessage(f"Apagamento adicionado. Total: {len(self.erase_operations)}")

    def _refresh_erasers_list(self) -> None:
        self.erasers_list.clear()
        for idx, op in enumerate(self.erase_operations):
            x0, y0, x1, y1 = op.rect_pdf
            w = max(0.0, x1 - x0)
            h = max(0.0, y1 - y0)
            text = f"{idx + 1:03d} | pag {op.page_num + 1} | {w:.1f}x{h:.1f} pt"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, idx)
            self.erasers_list.addItem(item)
        self._refresh_changes_list()
        self._update_edit_context_state()

    def _remove_selected_eraser(self) -> None:
        item = self.erasers_list.currentItem()
        if not item:
            return
        idx = int(item.data(QtCore.Qt.UserRole))
        if 0 <= idx < len(self.erase_operations):
            del self.erase_operations[idx]
            self._refresh_erasers_list()
            self._refresh_page_overlays()
            self._update_edit_context_state()
            self.statusBar().showMessage(f"Apagamento removido. Total: {len(self.erase_operations)}")

    def _clear_erasers(self) -> None:
        if not self.erase_operations:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Limpar apagamentos",
            "Remover todos os apagamentos pendentes?",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.erase_operations.clear()
            self._refresh_erasers_list()
            self._refresh_page_overlays()
            self._update_edit_context_state()

    def _on_eraser_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        idx = int(item.data(QtCore.Qt.UserRole))
        self._focus_eraser(idx)

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
            self._loading_ui = True
            self.ops_list.setCurrentRow(next_idx)
            self._loading_ui = False
            self._focus_operation(next_idx)
            self._select_change("operation", next_idx)
        else:
            self.page_widget.clear_selection()
            self._update_lichess_link()

        self._refresh_page_overlays()
        self._update_edit_context_state()
        self.statusBar().showMessage(f"Substituicao removida. Total: {len(self.operations)}")

    def _remove_selected_operation(self) -> None:
        idx = self._selected_operation_index()
        if idx is None:
            return
        self._remove_operation_at_index(idx)

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
            "Limpar substituicoes",
            "Remover todas as substituicoes pendentes?",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.operations.clear()
            self._refresh_operations_list()
            self._refresh_page_overlays()
            self._update_edit_context_state()

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

    def _on_operation_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        idx = int(item.data(QtCore.Qt.UserRole))
        if not (0 <= idx < len(self.operations)):
            return
        self._focus_operation(idx)

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

        if auto_save_path:
            out_path = auto_save_path
        else:
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Salvar PDF de saida",
                str(Path(self.current_pdf_path).with_name(Path(self.current_pdf_path).stem + "_hq.pdf")),
                "PDF (*.pdf)",
            )
            if not out_path:
                return

        cursor_set = False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            cursor_set = True
            apply_operations_to_pdf(
                self.current_pdf_path,
                out_path,
                self.operations,
                erase_operations=self.erase_operations,
                whiteout=self.whiteout_check.isChecked(),
                include_lichess_link=self.include_lichess_link_check.isChecked(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao exportar", str(exc))
            return
        finally:
            if cursor_set:
                QtWidgets.QApplication.restoreOverrideCursor()

        QtWidgets.QMessageBox.information(self, "Concluido", f"PDF salvo em:\n{out_path}")

    def _save_project_dialog(self) -> None:
        if not self.current_pdf_path:
            QtWidgets.QMessageBox.warning(self, "Sem PDF", "Abra um PDF antes de salvar projeto.")
            return
        project_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salvar projeto", self.project_path or "project_state.json", "JSON (*.json)"
        )
        if not project_path:
            return

        state = ProjectState(
            source_pdf=self.current_pdf_path,
            source_pdf_fingerprint=fingerprint_file(self.current_pdf_path),
            operations=self.operations,
            erase_operations=self.erase_operations,
            study_positions=self.study_positions,
            current_page=self.current_page,
            include_lichess_link=self.include_lichess_link_check.isChecked(),
            ocr_full_next_page=self.ocr_full_next_page,
        )
        try:
            save_project_state(project_path, state)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao salvar projeto", str(exc))
            return
        self.project_path = project_path
        self._remember_last_project_path(project_path)
        self.statusBar().showMessage(f"Projeto salvo: {project_path}")

    def _load_project_from_path(self, project_path: str, show_dialogs: bool = True) -> bool:
        try:
            state = load_project_state(project_path)
        except Exception as exc:
            if show_dialogs:
                QtWidgets.QMessageBox.critical(self, "Erro ao carregar projeto", str(exc))
            return False

        if not Path(state.source_pdf).exists():
            if show_dialogs:
                QtWidgets.QMessageBox.warning(
                    self,
                    "PDF nao encontrado",
                    f"O PDF original nao existe:\n{state.source_pdf}",
                )
            return False

        self._open_pdf(state.source_pdf, clear_ops=False)
        self.operations = state.operations
        self.erase_operations = state.erase_operations
        self.study_positions = state.study_positions
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
        self._render_current_page()

        try:
            current_fp = fingerprint_file(state.source_pdf)
            if state.source_pdf_fingerprint and current_fp.get("sha256") != state.source_pdf_fingerprint.get("sha256"):
                if show_dialogs:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Aviso de integridade",
                        "O PDF atual difere do PDF usado quando o projeto foi salvo.",
                    )
        except Exception:
            pass

        self.statusBar().showMessage(f"Projeto carregado: {project_path}")
        return True

    def _load_project_dialog(self) -> None:
        project_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Carregar projeto", self.project_path or "", "JSON (*.json)"
        )
        if not project_path:
            return
        self._load_project_from_path(project_path, show_dialogs=True)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Chess PDF Editor")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
