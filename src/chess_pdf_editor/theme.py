"""Cores e estilos que não podem sair da paleta do Qt (§24.2).

A regra que a auditoria de interface fixou:

| Situação | Solução |
|---|---|
| A cor pode vir do tema | `palette(...)` no QSS, e nada aqui |
| O fundo é fixo por design (casas do tabuleiro, paleta de peças) | cor de texto também fixa |
| A cor é semântica (aviso, marcador de comentário) | escolhida por `is_dark_theme()` |

Este módulo existe para a terceira linha. Ele estava dentro de `app.py`, onde
qualquer widget novo tinha de importar a janela inteira para pintar um aviso.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


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


# --- QSS reutilizado pela janela principal --------------------------------
#
# Ficavam como atributos de classe da `MainWindow`. Estão aqui porque são decisão
# de aparência, não de comportamento — e porque um painel extraído para outro
# módulo precisa deles sem importar a janela. A `MainWindow` mantém os nomes
# antigos como alias, então `self._CONTEXT_STYLE` continua funcionando.
#
# Azul de destaque com texto branco funciona em tema claro e escuro; o resto sai
# da paleta do sistema para não sumir quando o tema muda.

PRIMARY_BUTTON_STYLE = (
    "QPushButton { background-color: #1f6feb; color: #ffffff; font-weight: 600; "
    "padding: 6px 10px; border-radius: 4px; } "
    "QPushButton:hover { background-color: #3b82f6; } "
    "QPushButton:disabled { background-color: palette(button); color: palette(mid); }"
)
SECONDARY_BUTTON_STYLE = ""
CONTEXT_STYLE = (
    "QLabel { background-color: palette(alternate-base); color: palette(window-text); "
    "border: 1px solid palette(mid); border-radius: 5px; padding: 8px; }"
)
SECTION_STYLE = "QLabel { font-weight: 600; margin-top: 6px; }"
