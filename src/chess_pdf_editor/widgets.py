from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import chess
from PySide6 import QtCore, QtGui, QtWidgets

from .fen import board_to_matrix, extract_piece_placement, matrix_to_piece_placement
from .resources import asset_candidates
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
    "P": ("wp.png", "wP.png"),
    "N": ("wn.png", "wN.png"),
    "B": ("wb.png", "wB.png"),
    "R": ("wr.png", "wR.png"),
    "Q": ("wq.png", "wQ.png"),
    "K": ("wk.png", "wK.png"),
    "p": ("bp.png", "bP.png"),
    "n": ("bn.png", "bN.png"),
    "b": ("bb.png", "bB.png"),
    "r": ("br.png", "bR.png"),
    "q": ("bq.png", "bQ.png"),
    "k": ("bk.png", "bK.png"),
}
_PIECE_PIXMAPS: Optional[dict[str, QtGui.QPixmap]] = None


def _find_piece_image_dir() -> Optional[Path]:
    env_dir = os.getenv("CHESS_PIECE_IMAGE_DIR", "").strip()
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))

    # `asset_candidates` cobre repositório, pasta de trabalho e — no executável —
    # o diretório extraído pelo PyInstaller e a pasta do próprio `.exe`.
    for parts in (
        ("assets", "piece_images"),
        ("Python-Easy-Chess-GUI-master", "Images", "60"),
        ("Images", "60"),
    ):
        candidates.extend(asset_candidates(*parts))

    for folder in candidates:
        if not folder.exists() or not folder.is_dir():
            continue
        # Precisa ter ao menos os dois reis para considerar válido.
        has_white_king = any((folder / filename).exists() for filename in PIECE_IMAGE_FILES["K"])
        has_black_king = any((folder / filename).exists() for filename in PIECE_IMAGE_FILES["k"])
        if has_white_king and has_black_king:
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

    for piece, filenames in PIECE_IMAGE_FILES.items():
        fp = next((folder / filename for filename in filenames if (folder / filename).exists()), None)
        if fp is None:
            continue
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

    #: Alças desenhadas em volta da seleção (Sprint 6.1).
    _HANDLE_KEYS = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    _HANDLE_SIZE = 8.0
    #: Tolerância de clique — maior que o desenho, senão acertar a alça vira sorte.
    _HANDLE_HIT = 11.0
    #: Abaixo disso só as alças de canto cabem sem se sobrepor.
    _MIN_EDGE_HANDLES_PX = 34.0
    _CURSORS = {
        "nw": QtCore.Qt.SizeFDiagCursor,
        "se": QtCore.Qt.SizeFDiagCursor,
        "ne": QtCore.Qt.SizeBDiagCursor,
        "sw": QtCore.Qt.SizeBDiagCursor,
        "n": QtCore.Qt.SizeVerCursor,
        "s": QtCore.Qt.SizeVerCursor,
        "e": QtCore.Qt.SizeHorCursor,
        "w": QtCore.Qt.SizeHorCursor,
    }
    #: Passo das setas do teclado, em pontos PDF (Shift = passo fino).
    STEP_PT = 1.0
    FINE_STEP_PT = 0.25

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.setMouseTracking(True)
        self.setBackgroundRole(QtGui.QPalette.Base)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        # Sem foco de teclado as setas nunca chegariam aqui.
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._selection_rect: Optional[QtCore.QRectF] = None
        self._drag_start: Optional[QtCore.QPointF] = None
        self._dragging = False
        # Modo do arrasto em curso: "new" | "move" | "resize".
        self._drag_mode: Optional[str] = None
        self._active_handle: Optional[str] = None
        self._rect_at_press: Optional[QtCore.QRectF] = None
        # Pixels de tela por ponto PDF: converte o passo do teclado (em pt) para
        # o espaço em que este widget trabalha.
        self._px_per_pt = 2.0
        self._operation_rects: list[QtCore.QRectF] = []
        self._eraser_rects: list[QtCore.QRectF] = []
        self._study_rects: list[QtCore.QRectF] = []
        self._candidate_rects: list[QtCore.QRectF] = []

    def set_page_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.clear_selection()

    def set_points_scale(self, px_per_pt: float) -> None:
        """Zoom em vigor, para o passo do teclado ser em pontos PDF de verdade."""
        try:
            value = float(px_per_pt)
        except (TypeError, ValueError):
            return
        if value > 0:
            self._px_per_pt = value

    def clear_selection(self) -> None:
        self._selection_rect = None
        self._drag_start = None
        self._dragging = False
        self._drag_mode = None
        self._active_handle = None
        self._rect_at_press = None
        self.unsetCursor()
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

    def set_candidate_rects(self, rects: list[tuple[float, float, float, float]]) -> None:
        self._candidate_rects = [
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

    # ------------------------------------------------------------------
    # Ajuste fino da seleção (Sprint 6.1)
    # ------------------------------------------------------------------
    #
    # Antes só existia "arrastar do zero": corrigir um recorte 2 pt torto exigia
    # apagar e redesenhar a seleção inteira. Agora o retângulo é um objeto vivo —
    # alças para redimensionar, corpo para deslocar, setas para o ajuste fino.

    def _handle_centers(self) -> dict[str, QtCore.QPointF]:
        if self._selection_rect is None:
            return {}
        r = self._selection_rect.normalized()
        cx = (r.left() + r.right()) / 2.0
        cy = (r.top() + r.bottom()) / 2.0
        centers = {
            "nw": QtCore.QPointF(r.left(), r.top()),
            "ne": QtCore.QPointF(r.right(), r.top()),
            "se": QtCore.QPointF(r.right(), r.bottom()),
            "sw": QtCore.QPointF(r.left(), r.bottom()),
        }
        # Numa seleção minúscula as alças de borda cobririam as de canto.
        if r.width() >= self._MIN_EDGE_HANDLES_PX:
            centers["n"] = QtCore.QPointF(cx, r.top())
            centers["s"] = QtCore.QPointF(cx, r.bottom())
        if r.height() >= self._MIN_EDGE_HANDLES_PX:
            centers["w"] = QtCore.QPointF(r.left(), cy)
            centers["e"] = QtCore.QPointF(r.right(), cy)
        return centers

    def _handle_at(self, point: QtCore.QPointF) -> Optional[str]:
        tolerance = self._HANDLE_HIT
        best: Optional[str] = None
        best_distance = tolerance
        for key, center in self._handle_centers().items():
            distance = max(abs(point.x() - center.x()), abs(point.y() - center.y()))
            if distance <= best_distance:
                best = key
                best_distance = distance
        return best

    def _resized_rect(self, handle: str, base: QtCore.QRectF, point: QtCore.QPointF) -> QtCore.QRectF:
        left, top, right, bottom = base.left(), base.top(), base.right(), base.bottom()
        if "w" in handle:
            left = point.x()
        if "e" in handle:
            right = point.x()
        if "n" in handle:
            top = point.y()
        if "s" in handle:
            bottom = point.y()
        return QtCore.QRectF(QtCore.QPointF(left, top), QtCore.QPointF(right, bottom)).normalized()

    def _moved_rect(self, base: QtCore.QRectF, dx: float, dy: float) -> QtCore.QRectF:
        """Desloca mantendo o tamanho: encostar na borda não encolhe a seleção."""
        moved = QtCore.QRectF(base)
        moved.translate(dx, dy)
        if self.pixmap() is not None:
            max_x = max(0.0, float(self.pixmap().width() - 1) - moved.width())
            max_y = max(0.0, float(self.pixmap().height() - 1) - moved.height())
            moved.moveLeft(min(max(0.0, moved.left()), max_x))
            moved.moveTop(min(max(0.0, moved.top()), max_y))
        return moved

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self.pixmap() is None:
            return super().mousePressEvent(event)
        self.setFocus(QtCore.Qt.MouseFocusReason)
        p = self._clamp_point(event.position())
        self._drag_start = p
        self._dragging = True

        if self._selection_rect is not None:
            handle = self._handle_at(p)
            if handle is not None:
                self._drag_mode = "resize"
                self._active_handle = handle
                self._rect_at_press = QtCore.QRectF(self._selection_rect)
                self.update()
                return
            if self._selection_rect.normalized().contains(p):
                self._drag_mode = "move"
                self._active_handle = None
                self._rect_at_press = QtCore.QRectF(self._selection_rect)
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                self.update()
                return

        self._drag_mode = "new"
        self._active_handle = None
        self._rect_at_press = None
        self._selection_rect = QtCore.QRectF(p, p)
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._dragging or self._drag_start is None:
            self._update_hover_cursor(event.position())
            return super().mouseMoveEvent(event)

        p = self._clamp_point(event.position())
        if self._drag_mode == "resize" and self._rect_at_press is not None and self._active_handle:
            self._selection_rect = self._resized_rect(self._active_handle, self._rect_at_press, p)
        elif self._drag_mode == "move" and self._rect_at_press is not None:
            self._selection_rect = self._moved_rect(
                self._rect_at_press,
                p.x() - self._drag_start.x(),
                p.y() - self._drag_start.y(),
            )
        else:
            self._selection_rect = QtCore.QRectF(self._drag_start, p).normalized()
        self.update()
        self.selection_changed.emit(self.selection_rect())

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or not self._dragging:
            return super().mouseReleaseEvent(event)
        mode = self._drag_mode
        self._dragging = False
        self._drag_mode = None
        self._active_handle = None
        self._rect_at_press = None
        self.unsetCursor()

        if mode in ("new", "move") and self._selection_rect and self._drag_start is not None:
            p = self._clamp_point(event.position())
            dx = p.x() - self._drag_start.x()
            dy = p.y() - self._drag_start.y()
            is_short_distance = (dx * dx + dy * dy) <= (10.0 * 10.0)
            # Arrastar dentro de uma seleção existente é deslocamento; só o
            # clique parado continua valendo como "focar o que está aqui".
            if mode == "move":
                if is_short_distance:
                    self.point_clicked.emit((p.x(), p.y()))
                    self.clear_selection()
                    return
                self.update()
                self.selection_changed.emit(self.selection_rect())
                return
            r = self._selection_rect.normalized()
            # Trata como clique mesmo com pequeno tremor/arrasto curto.
            is_short_drag = (r.width() <= 18.0 and r.height() <= 18.0)
            if is_short_drag or is_short_distance:
                self.point_clicked.emit((p.x(), p.y()))
                self.clear_selection()
                return
        if self._selection_rect and (self._selection_rect.width() < 2 or self._selection_rect.height() < 2):
            self.clear_selection()
            return
        self.update()
        self.selection_changed.emit(self.selection_rect())

    def _update_hover_cursor(self, position: QtCore.QPointF) -> None:
        if self._selection_rect is None:
            self.unsetCursor()
            return
        handle = self._handle_at(position)
        if handle is not None:
            self.setCursor(self._CURSORS[handle])
        elif self._selection_rect.normalized().contains(position):
            self.setCursor(QtCore.Qt.OpenHandCursor)
        else:
            self.unsetCursor()

    # -- teclado -------------------------------------------------------

    _ARROW_DELTAS = {
        QtCore.Qt.Key_Left: (-1.0, 0.0),
        QtCore.Qt.Key_Right: (1.0, 0.0),
        QtCore.Qt.Key_Up: (0.0, -1.0),
        QtCore.Qt.Key_Down: (0.0, 1.0),
    }

    def _handles_key(self, event: QtGui.QKeyEvent) -> bool:
        return self._selection_rect is not None and event.key() in self._ARROW_DELTAS

    def event(self, event: QtCore.QEvent) -> bool:
        # `←`/`→` são atalhos de janela (navegar página). Sem aceitar o
        # ShortcutOverride, o atalho dispararia antes do keyPressEvent e as setas
        # nunca chegariam à seleção. Só interceptamos quando há o que mover.
        if event.type() == QtCore.QEvent.ShortcutOverride and self._handles_key(event):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if not self._handles_key(event):
            return super().keyPressEvent(event)

        modifiers = event.modifiers()
        step_pt = self.FINE_STEP_PT if modifiers & QtCore.Qt.ShiftModifier else self.STEP_PT
        step_px = max(0.05, step_pt * self._px_per_pt)
        dx, dy = self._ARROW_DELTAS[event.key()]
        base = QtCore.QRectF(self._selection_rect).normalized()

        if modifiers & QtCore.Qt.ControlModifier:
            # Ctrl redimensiona pela borda inferior-direita; mover e redimensionar
            # com o mesmo passo mantém o ajuste previsível.
            grown = QtCore.QRectF(
                base.left(),
                base.top(),
                max(2.0, base.width() + dx * step_px),
                max(2.0, base.height() + dy * step_px),
            )
            self._selection_rect = self._clamp_rect(grown)
        else:
            self._selection_rect = self._moved_rect(base, dx * step_px, dy * step_px)

        event.accept()
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

        if self._candidate_rects:
            # Deteccoes aguardando conferencia: roxo pontilhado.
            pen = QtGui.QPen(QtGui.QColor(150, 70, 200), 2, QtCore.Qt.DotLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(150, 70, 200, 30))
            for rect in self._candidate_rects:
                painter.drawRect(rect)

        if self._selection_rect is None:
            return
        pen = QtGui.QPen(QtGui.QColor(230, 40, 40), 2)
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(255, 0, 0, 40))
        painter.drawRect(self._selection_rect)

        # Alças: branco com contorno escuro para aparecer sobre página clara ou
        # sobre o próprio diagrama.
        half = self._HANDLE_SIZE / 2.0
        painter.setPen(QtGui.QPen(QtGui.QColor(120, 20, 20), 1))
        painter.setBrush(QtGui.QColor(255, 255, 255, 235))
        for center in self._handle_centers().values():
            painter.drawRect(
                QtCore.QRectF(
                    center.x() - half,
                    center.y() - half,
                    self._HANDLE_SIZE,
                    self._HANDLE_SIZE,
                )
            )

    def _clamp_point(self, point: QtCore.QPointF) -> QtCore.QPointF:
        if self.pixmap() is None:
            return point
        width = max(0.0, float(self.pixmap().width() - 1))
        height = max(0.0, float(self.pixmap().height() - 1))
        x = min(max(0.0, point.x()), width)
        y = min(max(0.0, point.y()), height)
        return QtCore.QPointF(x, y)

    def _clamp_rect(self, rect: QtCore.QRectF) -> QtCore.QRectF:
        r = rect.normalized()
        if self.pixmap() is None:
            return r
        return QtCore.QRectF(
            self._clamp_point(r.topLeft()),
            self._clamp_point(r.bottomRight()),
        ).normalized()


class BeforeAfterWidget(QtWidgets.QWidget):
    """Miniaturas lado a lado do diagrama: como esta hoje x como vai ficar."""

    def __init__(self, thumb_height: int = 150) -> None:
        super().__init__()
        self._thumb_height = max(80, int(thumb_height))
        self._before_png: Optional[bytes] = None
        self._after_png: Optional[bytes] = None

        self.before_label = self._make_thumb_label()
        self.after_label = self._make_thumb_label()
        self.message_label = QtWidgets.QLabel("Selecione um diagrama para comparar.")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(QtCore.Qt.AlignCenter)
        self.message_label.setStyleSheet("QLabel { color: palette(mid); padding: 12px; }")

        self.thumbs = QtWidgets.QWidget()
        thumbs_layout = QtWidgets.QHBoxLayout(self.thumbs)
        thumbs_layout.setContentsMargins(0, 0, 0, 0)
        thumbs_layout.setSpacing(10)
        thumbs_layout.addLayout(self._make_column("Antes", self.before_label), 1)
        thumbs_layout.addLayout(self._make_column("Depois", self.after_label), 1)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.message_label)
        root.addWidget(self.thumbs)
        self.thumbs.setVisible(False)

    @staticmethod
    def _make_column(title: str, label: QtWidgets.QLabel) -> QtWidgets.QVBoxLayout:
        caption = QtWidgets.QLabel(title)
        caption.setAlignment(QtCore.Qt.AlignCenter)
        caption.setStyleSheet("QLabel { font-weight: 600; }")
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(3)
        column.addWidget(caption)
        column.addWidget(label, 1)
        return column

    def _make_thumb_label(self) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setMinimumHeight(self._thumb_height)
        label.setStyleSheet(
            "QLabel { background-color: palette(base); border: 1px solid palette(mid); "
            "border-radius: 4px; }"
        )
        label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        return label

    def set_message(self, message: str) -> None:
        self._before_png = None
        self._after_png = None
        self.before_label.clear()
        self.after_label.clear()
        self.message_label.setText(message)
        self.message_label.setVisible(True)
        self.thumbs.setVisible(False)

    def set_images(self, before_png: Optional[bytes], after_png: Optional[bytes]) -> None:
        self._before_png = before_png
        self._after_png = after_png
        self.message_label.setVisible(False)
        self.thumbs.setVisible(True)
        self._apply_pixmap(self.before_label, before_png)
        self._apply_pixmap(self.after_label, after_png)

    def _apply_pixmap(self, label: QtWidgets.QLabel, png_bytes: Optional[bytes]) -> None:
        if not png_bytes:
            label.clear()
            return
        pixmap = QtGui.QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            label.clear()
            return
        available_width = max(48, label.width() - 6)
        label.setPixmap(
            pixmap.scaled(
                available_width,
                self._thumb_height - 6,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._before_png or self._after_png:
            self._apply_pixmap(self.before_label, self._before_png)
            self._apply_pixmap(self.after_label, self._after_png)


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
        self._active_piece = "P"

        self._palette_group = QtWidgets.QButtonGroup(self)
        self._palette_group.setExclusive(True)
        self._palette_buttons: dict[str, QtWidgets.QPushButton] = {}

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

        palette = QtWidgets.QGridLayout()
        palette.setContentsMargins(0, 0, 0, 0)
        palette.setHorizontalSpacing(4)
        palette.setVerticalSpacing(4)
        for idx, piece in enumerate(PIECE_VALUES):
            button = QtWidgets.QPushButton()
            button.setCheckable(True)
            button.setFixedSize(30, 30)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setToolTip("Casa vazia" if piece == "." else f"Peça {piece}")
            if piece == ".":
                button.setText("×")
            else:
                _set_button_piece_visual(button, piece, icon_size=24)
            button.clicked.connect(lambda checked=False, value=piece: self._select_palette_piece(value))
            self._palette_group.addButton(button)
            self._palette_buttons[piece] = button
            palette.addWidget(button, idx // 7, idx % 7)

        self._palette_buttons[self._active_piece].setChecked(True)
        self._refresh_palette_styles()

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(palette)
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

    def _select_palette_piece(self, piece: str) -> None:
        self._active_piece = piece if piece in PIECE_VALUES else "."
        self._refresh_palette_styles()

    def _refresh_palette_styles(self) -> None:
        for piece, button in self._palette_buttons.items():
            selected = piece == self._active_piece
            bg = "#e8f1ff" if selected else "#ffffff"
            border = "#1f6feb" if selected else "#c8d0da"
            # O fundo do botao e sempre claro (para as pecas aparecerem), entao o
            # texto precisa de cor fixa escura: herdar do tema deixaria o "x" da
            # casa vazia branco no branco no modo escuro.
            button.setStyleSheet(
                f"QPushButton {{ background-color: {bg}; color: #1b1b1b; "
                f"border: 2px solid {border}; "
                "border-radius: 4px; font-size: 18px; font-weight: bold; }} "
                "QPushButton:hover { border-color: #1f6feb; }"
            )

    def _set_cell_from_palette(self, row: int, col: int) -> None:
        self._matrix[row][col] = self._active_piece
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
        # Cor de texto fixa: as casas tem cor de madeira em qualquer tema.
        return (
            f"QPushButton {{ background-color: {bg}; color: #101010; border: none; "
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
        self._emit_state_changed("Posição de estudo carregada.")

    def load_pgn_text(self, pgn_text: str) -> dict[int, dict[str, str]]:
        move_comments = self._game.load_pgn(pgn_text)
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed("PGN carregado no tabuleiro de estudo.")
        return move_comments

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

    def start_fen(self) -> str:
        return self._game.start_fen

    def start_turn(self) -> str:
        return "b" if self._game.start_fen.split()[1] == "b" else "w"

    def start_fullmove_number(self) -> int:
        try:
            return max(1, int(self._game.start_fen.split()[5]))
        except Exception:
            return 1

    def current_pgn(
        self,
        comment_before: str = "",
        comment_after: str = "",
        comment_ply: Optional[int] = None,
        move_comments: Optional[dict[object, dict[str, str]]] = None,
        include_all: bool = False,
    ) -> str:
        return self._game.to_pgn(
            comment_before=comment_before,
            comment_after=comment_after,
            comment_ply=comment_ply,
            move_comments=move_comments,
            include_all=include_all,
        )

    def current_ply(self) -> int:
        return self._game.cursor

    def san_line(self) -> list[str]:
        return self._game.san_line()

    def current_path_key(self) -> str:
        return self._game.current_path_key()

    def move_tree(self) -> list[dict[str, object]]:
        return self._game.move_tree()

    def goto_path(self, path_key: str) -> bool:
        ok = self._game.goto_path(path_key)
        if not ok:
            return False
        self._selected_square = None
        self._legal_targets.clear()
        self._refresh()
        self._emit_state_changed(f"Navegando para {self._game.current_path_key()}.")
        return True

    def current_variation_info(self) -> tuple[int, int]:
        return self._game.current_variation_info()

    def select_sibling_variation(self, offset: int) -> bool:
        ok = self._game.select_sibling_variation(offset)
        if ok:
            self._selected_square = None
            self._legal_targets.clear()
            self._refresh()
            index, count = self._game.current_variation_info()
            self._emit_state_changed(f"Variante {index}/{count}.")
        return ok

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
        # Idem: fundo de tabuleiro nao acompanha o tema, o texto tambem nao pode.
        return (
            f"QPushButton {{ background-color: {bg}; color: #101010; border: none; "
            "font-size: 24px; font-weight: bold; } "
            "QPushButton:hover { border: none; }"
        )
