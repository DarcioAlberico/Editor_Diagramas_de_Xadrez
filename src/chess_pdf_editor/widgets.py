from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import chess
from PySide6 import QtCore, QtGui, QtWidgets

from .fen import board_to_matrix, extract_piece_placement, matrix_to_piece_placement
from .study import StudyGame

PIECE_VALUES = [".", "P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"]
PIECE_LABELS = {
    ".": "",
    "P": "♙",
    "N": "♘",
    "B": "♗",
    "R": "♖",
    "Q": "♕",
    "K": "♔",
    "p": "♟",
    "n": "♞",
    "b": "♝",
    "r": "♜",
    "q": "♛",
    "k": "♚",
}
PIECE_IMAGE_FILES = {
    "P": "wP.png",
    "N": "wN.png",
    "B": "wB.png",
    "R": "wR.png",
    "Q": "wQ.png",
    "K": "wK.png",
    "p": "bP.png",
    "n": "bN.png",
    "b": "bB.png",
    "r": "bR.png",
    "q": "bQ.png",
    "k": "bK.png",
}
_PIECE_PIXMAPS: Optional[dict[str, QtGui.QPixmap]] = None


def _find_piece_image_dir() -> Optional[Path]:
    env_dir = os.getenv("CHESS_PIECE_IMAGE_DIR", "").strip()
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))

    root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            root / "Python-Easy-Chess-GUI-master" / "Images" / "60",
            root / "Images" / "60",
            Path.cwd() / "Python-Easy-Chess-GUI-master" / "Images" / "60",
            Path.cwd() / "Images" / "60",
        ]
    )

    for folder in candidates:
        if not folder.exists() or not folder.is_dir():
            continue
        # Precisa ter ao menos as duas peças básicas para considerar válido.
        if (folder / "wK.png").exists() and (folder / "bK.png").exists():
            return folder
    return None


def _get_piece_pixmaps() -> dict[str, QtGui.QPixmap]:
    global _PIECE_PIXMAPS
    if _PIECE_PIXMAPS is not None:
        return _PIECE_PIXMAPS

    _PIECE_PIXMAPS = {}
    folder = _find_piece_image_dir()
    if folder is None:
        return _PIECE_PIXMAPS

    for piece, filename in PIECE_IMAGE_FILES.items():
        fp = folder / filename
        if not fp.exists():
            continue
        pix = QtGui.QPixmap(str(fp))
        if not pix.isNull():
            _PIECE_PIXMAPS[piece] = pix
    return _PIECE_PIXMAPS


def _set_button_piece_visual(button: QtWidgets.QPushButton, piece: str, icon_size: int) -> None:
    pixmaps = _get_piece_pixmaps()
    pix = pixmaps.get(piece)
    if pix is not None:
        button.setText("")
        button.setIcon(QtGui.QIcon(pix))
        button.setIconSize(QtCore.QSize(max(8, icon_size), max(8, icon_size)))
        return
    button.setIcon(QtGui.QIcon())
    button.setText(PIECE_LABELS.get(piece, piece))


class SelectablePageWidget(QtWidgets.QLabel):
    selection_changed = QtCore.Signal(object)
    point_clicked = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.setMouseTracking(True)
        self.setBackgroundRole(QtGui.QPalette.Base)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        self._selection_rect: Optional[QtCore.QRectF] = None
        self._drag_start: Optional[QtCore.QPointF] = None
        self._dragging = False
        self._operation_rects: list[QtCore.QRectF] = []
        self._eraser_rects: list[QtCore.QRectF] = []
        self._study_rects: list[QtCore.QRectF] = []

    def set_page_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.clear_selection()

    def clear_selection(self) -> None:
        self._selection_rect = None
        self._drag_start = None
        self._dragging = False
        self.update()
        self.selection_changed.emit(None)

    def set_operation_rects(self, rects: list[tuple[float, float, float, float]]) -> None:
        self._operation_rects = [
            QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
            for (x0, y0, x1, y1) in rects
        ]
        self.update()

    def set_eraser_rects(self, rects: list[tuple[float, float, float, float]]) -> None:
        self._eraser_rects = [
            QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
            for (x0, y0, x1, y1) in rects
        ]
        self.update()

    def set_study_rects(self, rects: list[tuple[float, float, float, float]]) -> None:
        self._study_rects = [
            QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
            for (x0, y0, x1, y1) in rects
        ]
        self.update()

    def selection_rect(self) -> Optional[tuple[float, float, float, float]]:
        if self._selection_rect is None:
            return None
        r = self._selection_rect.normalized()
        return (r.left(), r.top(), r.right(), r.bottom())

    def set_selection_rect(self, rect: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = rect
        self._selection_rect = QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
        self.update()
        self.selection_changed.emit(self.selection_rect())

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self.pixmap() is None:
            return super().mousePressEvent(event)
        p = self._clamp_point(event.position())
        self._drag_start = p
        self._selection_rect = QtCore.QRectF(p, p)
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._dragging or self._drag_start is None:
            return super().mouseMoveEvent(event)
        p = self._clamp_point(event.position())
        self._selection_rect = QtCore.QRectF(self._drag_start, p).normalized()
        self.update()
        self.selection_changed.emit(self.selection_rect())

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or not self._dragging:
            return super().mouseReleaseEvent(event)
        self._dragging = False
        if self._selection_rect and self._drag_start is not None:
            p = self._clamp_point(event.position())
            dx = p.x() - self._drag_start.x()
            dy = p.y() - self._drag_start.y()
            r = self._selection_rect.normalized()
            # Trata como clique mesmo com pequeno tremor/arrasto curto.
            is_short_drag = (r.width() <= 18.0 and r.height() <= 18.0)
            is_short_distance = (dx * dx + dy * dy) <= (10.0 * 10.0)
            if is_short_drag or is_short_distance:
                self.point_clicked.emit((p.x(), p.y()))
                self.clear_selection()
                return
        if self._selection_rect and (self._selection_rect.width() < 2 or self._selection_rect.height() < 2):
            self.clear_selection()
            return
        self.update()
        self.selection_changed.emit(self.selection_rect())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        if self._operation_rects:
            pen = QtGui.QPen(QtGui.QColor(20, 140, 255), 2)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(20, 140, 255, 28))
            for rect in self._operation_rects:
                painter.drawRect(rect)

        if self._eraser_rects:
            pen = QtGui.QPen(QtGui.QColor(255, 140, 0), 2, QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(255, 140, 0, 28))
            for rect in self._eraser_rects:
                painter.drawRect(rect)

        if self._study_rects:
            pen = QtGui.QPen(QtGui.QColor(60, 150, 90), 2, QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(60, 150, 90, 26))
            for rect in self._study_rects:
                painter.drawRect(rect)

        if self._selection_rect is None:
            return
        pen = QtGui.QPen(QtGui.QColor(230, 40, 40), 2)
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(255, 0, 0, 40))
        painter.drawRect(self._selection_rect)

    def _clamp_point(self, point: QtCore.QPointF) -> QtCore.QPointF:
        if self.pixmap() is None:
            return point
        width = max(0.0, float(self.pixmap().width() - 1))
        height = max(0.0, float(self.pixmap().height() - 1))
        x = min(max(0.0, point.x()), width)
        y = min(max(0.0, point.y()), height)
        return QtCore.QPointF(x, y)


class _CellButton(QtWidgets.QPushButton):
    right_clicked = QtCore.Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            self.right_clicked.emit()
            return
        super().mousePressEvent(event)


class BoardEditorWidget(QtWidgets.QWidget):
    board_changed = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._matrix = [["."] * 8 for _ in range(8)]
        self._cell_size = 42

        self.piece_combo = QtWidgets.QComboBox()
        for piece in PIECE_VALUES:
            label = "vazio" if piece == "." else piece
            self.piece_combo.addItem(label, piece)
        self.piece_combo.setCurrentIndex(1)

        self._board_container = QtWidgets.QWidget()
        self._board_container.setFixedSize(self._cell_size * 8, self._cell_size * 8)

        self.grid = QtWidgets.QGridLayout(self._board_container)
        self.grid.setSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self._buttons: list[list[_CellButton]] = []
        for r in range(8):
            row: list[_CellButton] = []
            for c in range(8):
                b = _CellButton()
                b.setFixedSize(self._cell_size, self._cell_size)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setStyleSheet(self._cell_style(r, c))
                b.clicked.connect(lambda checked=False, rr=r, cc=c: self._set_cell_from_palette(rr, cc))
                b.right_clicked.connect(lambda rr=r, cc=c: self._clear_cell(rr, cc))
                self.grid.addWidget(b, r, c)
                row.append(b)
            self._buttons.append(row)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Peca ativa:"))
        top.addWidget(self.piece_combo, 1)

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self._board_container, 0, QtCore.Qt.AlignLeft)
        root.addStretch(1)

        self.refresh_ui()

    def piece_placement(self) -> str:
        return matrix_to_piece_placement(self._matrix)

    def set_piece_placement(self, fen_like: str) -> None:
        piece_placement = extract_piece_placement(fen_like)
        self._matrix = board_to_matrix(piece_placement)
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    def clear_board(self) -> None:
        self._matrix = [["."] * 8 for _ in range(8)]
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    def rotate_clockwise(self) -> None:
        self._matrix = [list(row) for row in zip(*self._matrix[::-1])]
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    def flip_vertical(self) -> None:
        self._matrix = self._matrix[::-1]
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    def refresh_ui(self) -> None:
        for r in range(8):
            for c in range(8):
                piece = self._matrix[r][c]
                _set_button_piece_visual(self._buttons[r][c], piece, icon_size=34)

    def _set_cell_from_palette(self, row: int, col: int) -> None:
        piece = self.piece_combo.currentData()
        if piece is None:
            piece = "."
        self._matrix[row][col] = str(piece)
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    def _clear_cell(self, row: int, col: int) -> None:
        self._matrix[row][col] = "."
        self.refresh_ui()
        self.board_changed.emit(self.piece_placement())

    @staticmethod
    def _cell_style(row: int, col: int) -> str:
        light = "#f0d9b5"
        dark = "#b58863"
        bg = light if (row + col) % 2 == 0 else dark
        return (
            f"QPushButton {{ background-color: {bg}; border: none; "
            "font-size: 22px; font-weight: bold; } "
            "QPushButton:hover { border: none; }"
        )


class _StudyArrowOverlay(QtWidgets.QWidget):
    def __init__(self, owner: "StudyBoardWidget", parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        if not self._owner.show_last_move_arrow():
            return
        move = self._owner.last_move()
        if move is None:
            return

        p0 = self._owner.square_center(move.from_square)
        p1 = self._owner.square_center(move.to_square)
        dx = p1.x() - p0.x()
        dy = p1.y() - p0.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            return

        start_pad = self._owner.cell_size() * 0.24
        end_pad = self._owner.cell_size() * 0.28
        sx = p0.x() + dx * (start_pad / length)
        sy = p0.y() + dy * (start_pad / length)
        ex = p1.x() - dx * (end_pad / length)
        ey = p1.y() - dy * (end_pad / length)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(46, 118, 187, 220), 4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(ex, ey))

        head_len = self._owner.cell_size() * 0.22
        ux = dx / length
        uy = dy / length
        px = -uy
        py = ux
        tip = QtCore.QPointF(ex, ey)
        left = QtCore.QPointF(ex - ux * head_len + px * head_len * 0.55, ey - uy * head_len + py * head_len * 0.55)
        right = QtCore.QPointF(ex - ux * head_len - px * head_len * 0.55, ey - uy * head_len - py * head_len * 0.55)
        painter.setBrush(QtGui.QColor(46, 118, 187, 220))
        painter.drawPolygon(QtGui.QPolygonF([tip, left, right]))


class StudyBoardWidget(QtWidgets.QWidget):
    position_changed = QtCore.Signal(str)
    move_played = QtCore.Signal(str)
    state_changed = QtCore.Signal(str)
    line_changed = QtCore.Signal(object, int)

    def __init__(self, cell_size: int = 50) -> None:
        super().__init__()
        self._cell_size = max(28, int(cell_size))
        self._game = StudyGame()
        self._is_white_bottom = True
        self._selected_square: Optional[int] = None
        self._legal_targets: set[int] = set()
        self._show_last_move_arrow = True

        self._board_container = QtWidgets.QWidget()
        self._board_container.setFixedSize(self._cell_size * 8, self._cell_size * 8)

        self.grid = QtWidgets.QGridLayout(self._board_container)
        self.grid.setSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self._buttons: list[list[_CellButton]] = []
        for row in range(8):
            row_buttons: list[_CellButton] = []
            for col in range(8):
                b = _CellButton()
                b.setFixedSize(self._cell_size, self._cell_size)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(lambda checked=False, rr=row, cc=col: self._on_square_clicked(rr, cc))
                self.grid.addWidget(b, row, col)
                row_buttons.append(b)
            self._buttons.append(row_buttons)

        self._arrow_overlay = _StudyArrowOverlay(self, self._board_container)
        self._arrow_overlay.setGeometry(0, 0, self._cell_size * 8, self._cell_size * 8)
        self._arrow_overlay.raise_()

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self._board_container, 0, QtCore.Qt.AlignCenter)
        self._refresh()
        self._emit_line_changed()

    def cell_size(self) -> int:
        return self._cell_size

    def show_last_move_arrow(self) -> bool:
        return self._show_last_move_arrow

    def set_show_last_move_arrow(self, show: bool) -> None:
        self._show_last_move_arrow = bool(show)
        self._arrow_overlay.update()

    def set_start_fen(self, fen: str) -> None:
        self._game.set_start_fen(fen)
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed("Posicao de estudo carregada.")

    def load_pgn_text(self, pgn_text: str) -> None:
        self._game.load_pgn(pgn_text)
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed("PGN carregado no tabuleiro de estudo.")

    def goto_ply(self, ply: int) -> bool:
        ok = self._game.goto_ply(ply)
        if not ok:
            return False
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed(f"Navegando lance {self._game.cursor}.")
        return True

    def current_fen(self) -> str:
        return self._game.board.fen()

    def current_pgn(self, comment_before: str = "", comment_after: str = "") -> str:
        return self._game.to_pgn(comment_before=comment_before, comment_after=comment_after)

    def current_ply(self) -> int:
        return self._game.cursor

    def san_line(self) -> list[str]:
        return self._game.san_line()

    def last_move(self) -> Optional[chess.Move]:
        return self._game.last_move()

    def square_center(self, square: int) -> QtCore.QPointF:
        row, col = self._square_to_display(square)
        return QtCore.QPointF(
            col * self._cell_size + self._cell_size / 2.0,
            row * self._cell_size + self._cell_size / 2.0,
        )

    def undo_move(self) -> bool:
        ok = self._game.undo()
        if ok:
            self._selected_square = None
            self._legal_targets.clear()
            self._refresh()
            self._emit_state_changed("Movimento desfeito.")
        return ok

    def redo_move(self) -> bool:
        ok = self._game.redo()
        if ok:
            self._selected_square = None
            self._legal_targets.clear()
            self._refresh()
            self._emit_state_changed("Movimento refeito.")
        return ok

    def clear_moves(self) -> None:
        self._game.clear_moves()
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed("Linha de estudo reiniciada.")

    def flip_board(self) -> None:
        self._is_white_bottom = not self._is_white_bottom
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()

    def _on_square_clicked(self, row: int, col: int) -> None:
        sq = self._display_to_square(row, col)
        board = self._game.board
        piece = board.piece_at(sq)

        if self._selected_square is None:
            if piece and piece.color == board.turn:
                self._selected_square = sq
                self._legal_targets = {m.to_square for m in self._game.legal_moves_from(sq)}
                self._refresh()
            return

        if sq == self._selected_square:
            self._selected_square = None
            self._legal_targets.clear()
            self._refresh()
            return

        if sq in self._legal_targets:
            candidates = [
                m for m in self._game.legal_moves_from(self._selected_square) if m.to_square == sq
            ]
            if not candidates:
                self._selected_square = None
                self._legal_targets.clear()
                self._refresh()
                return
            move = next((m for m in candidates if m.promotion == chess.QUEEN), candidates[0])
            try:
                san = self._game.push_move(move)
            except Exception:
                self._selected_square = None
                self._legal_targets.clear()
                self._refresh()
                self.state_changed.emit("Movimento ilegal.")
                return
            self._selected_square = None
            self._legal_targets.clear()
            self._refresh()
            self.move_played.emit(san)
            self._emit_state_changed(f"Lance: {san}")
            return

        if piece and piece.color == board.turn:
            self._selected_square = sq
            self._legal_targets = {m.to_square for m in self._game.legal_moves_from(sq)}
            self._refresh()
            return

        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()

    def _refresh(self) -> None:
        for row in range(8):
            for col in range(8):
                sq = self._display_to_square(row, col)
                piece = self._game.board.piece_at(sq)
                piece_ch = piece.symbol() if piece else "."
                is_dark = (row + col) % 2 == 1
                is_selected = (sq == self._selected_square)
                is_target = (sq in self._legal_targets)
                _set_button_piece_visual(
                    self._buttons[row][col],
                    piece_ch,
                    icon_size=max(10, self._cell_size - 8),
                )
                self._buttons[row][col].setStyleSheet(
                    self._study_cell_style(
                        is_dark=is_dark,
                        is_selected=is_selected,
                        is_target=is_target,
                    )
                )
        self._arrow_overlay.update()

    def _emit_line_changed(self) -> None:
        self.line_changed.emit(self._game.san_line(), self._game.cursor)

    def _emit_state_changed(self, message: str) -> None:
        self.position_changed.emit(self._game.board.fen())
        self._emit_line_changed()
        self.state_changed.emit(message)

    def _display_to_square(self, row: int, col: int) -> int:
        if self._is_white_bottom:
            file_idx = col
            rank_idx = 7 - row
        else:
            file_idx = 7 - col
            rank_idx = row
        return chess.square(file_idx, rank_idx)

    def _square_to_display(self, square: int) -> tuple[int, int]:
        file_idx = chess.square_file(square)
        rank_idx = chess.square_rank(square)
        if self._is_white_bottom:
            row = 7 - rank_idx
            col = file_idx
        else:
            row = rank_idx
            col = 7 - file_idx
        return (row, col)

    @staticmethod
    def _study_cell_style(is_dark: bool, is_selected: bool, is_target: bool) -> str:
        if is_selected:
            bg = "#c97d6a" if is_dark else "#efb2a5"
        elif is_target:
            bg = "#7fae6d" if is_dark else "#b8d89f"
        else:
            bg = "#b58863" if is_dark else "#f0d9b5"
        return (
            f"QPushButton {{ background-color: {bg}; border: none; "
            "font-size: 24px; font-weight: bold; } "
            "QPushButton:hover { border: none; }"
        )
