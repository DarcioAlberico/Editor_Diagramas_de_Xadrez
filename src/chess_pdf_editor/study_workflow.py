"""Posições de estudo do PDF: lista, comentários por lance e PGN (§22.3).

Mixin pelo mesmo motivo de `ocr_workflow.py`: estes métodos costuram a lista de
posições, o painel de estudo, o visor de PDF e o projeto salvo. Separá-los em
classe própria trocaria o acoplamento por uma indireção, sem reduzi-lo.

O que fica aqui é o **fluxo** — carregar uma posição no tabuleiro, sincronizar a
linha de lances, gravar comentário antes/depois do lance selecionado, estudar a
seleção da página. O painel em si (widgets e árvore SAN) é `study_panel.py`, que
não conhece a janela.

A parte delicada é a dos comentários: eles são gravados **por lance**, e o lance
ativo muda a cada navegação na árvore. `_flush_current_study_comment` é chamado
antes de qualquer troca de linha justamente para o texto em edição não vazar para
o lance errado.
"""
from __future__ import annotations

from typing import Optional

import chess
from PySide6 import QtCore, QtGui, QtWidgets

from .fen import extract_piece_placement, normalize_piece_placement, validate_piece_placement
from .logging_config import get_logger
from .study_panel import StudyPanel
from .types import StudyPosition

logger = get_logger("app")


class StudyWorkflowMixin:
    """Posições de estudo associadas ao PDF aberto."""

    def _touch_study_positions(self) -> None:
        """Há trabalho de estudo novo que ainda não foi para o disco (§59.4).

        O autosave só grava quando `_autosave_dirty` é verdadeiro, e quem levantava
        essa bandeira eram só o histórico de desfazer e o fim do lote de OCR — nenhum
        deles passa por aqui. O resultado era a promessa do Sprint 5.3 valendo para
        metade do produto: uma sessão inteira de estudo (que é a atividade mais
        demorada que o app tem, porque envolve ler a página e digitar) fechava sem
        gravar nada, porque o `closeEvent` também só salva se a bandeira estiver de pé.

        Um método só, e não `_mark_project_dirty()` espalhado por seis lugares: aqui é
        onde entra a entrada de histórico do modo Estudo no dia em que ela existir, e
        um chamador só é mais barato de mudar que seis.
        """
        self._mark_project_dirty()

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
        self._touch_study_positions()

    def _sync_study_position_start(self, pos: StudyPosition) -> None:
        """Traz a posição inicial do tabuleiro de volta para a entrada da lista.

        O `Vez de jogar:` do painel passou a mudar a FEN inicial (§59.9), e sem isto a
        troca não sobreviveria a sair da posição e voltar: `_load_study_position`
        recarrega de `pos`, que continuaria com o lado antigo. Um controle que
        funciona e depois se desfaz sozinho é pior que um inerte.

        É a mesma escrita que `_on_study_pgn_imported` já fazia para o caminho do PGN
        importado — agora num lugar só, por onde os dois passam.
        """
        parts = self.study_panel.study_board.start_fen().split()
        if len(parts) < 6:
            return
        pos.fen = parts[0]
        pos.side_to_move = "b" if parts[1] == "b" else "w"
        try:
            pos.fullmove_number = max(1, int(parts[5]))
        except ValueError:
            pos.fullmove_number = 1

    def _sync_study_position_line(self, pos: StudyPosition) -> None:
        self._sync_study_position_start(pos)
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
        self._touch_study_positions()

    @staticmethod
    def _set_study_comment_summary(pos: StudyPosition) -> None:
        pos.comment_before = ""
        pos.comment_after = ""
        pos.note = ""
        if not pos.move_comments:
            return
        try:
            # `staticmethod` chamado pelo nome da classe: era `MainWindow` porque o
            # método morava lá. Agora mora aqui, e apontar para a janela criaria um
            # import circular entre `app` e este módulo.
            first_key = sorted(pos.move_comments, key=StudyWorkflowMixin._study_comment_sort_key)[0]
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
        self._touch_study_positions()

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
        # A moldura verde da posição removida ficava na página até a próxima troca de
        # página (§59.8) — o que é pior que não sumir, porque ensina o usuário a não
        # confiar no que está vendo. Adicionar já redesenhava; só a remoção estava de
        # fora.
        self._refresh_page_overlays()
        self._touch_study_positions()
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
        self._touch_study_positions()
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
        self._touch_study_positions()
        self.statusBar().showMessage(f"Posição enviada para estudo. Total: {len(self.study_positions)}")
