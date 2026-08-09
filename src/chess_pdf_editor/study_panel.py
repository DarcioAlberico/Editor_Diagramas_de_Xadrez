"""Painel do modo Estudo: tabuleiro jogável, árvore de variantes e PGN.

Extraído de `app.py` (§22.3). O painel não conhece a `MainWindow` — conversa com
ela só por sinais (`about_to_change_line`, `pgn_imported`) e por um provedor de
PGN injetado (`set_pgn_provider`). Era o pedaço de `app.py` com menos amarras, e
por isso o primeiro a sair.

`StudyDialog` é o mesmo painel numa janela solta, para quem quer o tabuleiro de
estudo fora do painel lateral.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

from .fen import extract_piece_placement, normalize_piece_placement
from .theme import comment_highlight_colors
from .widgets import StudyBoardWidget


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
