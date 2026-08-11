"""Reconhecimento e fila de conferência, extraídos de `app.py` (§22.3).

### Por que um mixin, e não uma classe com o seu próprio estado

Estes métodos mexem em quase tudo que a janela tem: `self.operations`,
`self.candidates`, o visor de PDF, a prévia ao vivo, o histórico de desfazer, a
barra de status e uma dúzia de widgets. Transformá-los numa classe separada
exigiria passar a janela inteira como colaborador — o mesmo acoplamento, com uma
indireção a mais — ou reescrever o fluxo, o que é uma mudança de comportamento
disfarçada de organização.

O mixin move o código para um arquivo próprio sem mover **nada** de lugar
semanticamente: `MainWindow` continua sendo uma classe só em tempo de execução, e
o diff da extração é puro recorte-e-cola, verificável pela suíte.

O que o mixin espera da janela está listado em `_JANELA_REQUER` abaixo. Não é
contrato executável — é o que um leitor precisa saber antes de mexer aqui.

### O que vive aqui

* `Reconhecer seleção` / `Reconhecer página` — síncronos, na thread da UI
* `Detectar no PDF` — via `BatchOcrWorker`, com progresso e cancelamento
* a fila de candidatos (§23) e a fila de revisão por confiança (§29)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets

from . import legality
from .fen import extract_piece_placement, normalize_piece_placement, validate_piece_placement
from .logging_config import get_logger
from .pdf_service import crop_from_rendered_page
from .recognition import RecognitionError
from .types import OverlayOperation
from .workers import BatchOcrWorker

logger = get_logger("app")

#: Atributos e métodos que o mixin assume existir na janela que o usa.
_JANELA_REQUER = (
    # estado
    "pdf_service", "current_pdf_path", "current_render", "current_page",
    "operations", "candidates", "ocr_full_next_page",
    # widgets
    "page_widget", "board_editor", "fen_edit", "candidates_list", "candidates_label",
    "candidates_section", "auto_apply_check", "op_border_spin",
    "candidates_only_uncertain", "candidates_threshold_spin", "candidates_worst_first",
    "btn_apply_candidate", "btn_discard_candidate",
    "btn_apply_all_candidates", "btn_discard_all_candidates",
    # colaboração
    "_commit_history", "_refresh_page_overlays", "_schedule_preview_refresh",
    "_update_edit_context_state", "_current_whiteout_padding", "_current_fen_defaults",
    "_sync_candidates_tab",
    "_make_engine", "_confirm_remote_upload", "_engine_mode", "_local_model_path",
    "_ocr_endpoint", "_draft_operation", "_anchor_from_selection", "_set_position_anchor",
)


class RecognitionMixin:
    """Reconhecimento de diagramas e conferência das detecções."""

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

    # ------------------------------------------------------------------
    # Fila de revisão (Sprint 9.1)
    # ------------------------------------------------------------------

    def _uncertainty_threshold(self) -> float:
        return float(self.candidates_threshold_spin.value())

    def _is_uncertain(self, candidate: OverlayOperation) -> bool:
        """Confiança desconhecida conta como incerta.

        Mesma regra do motor híbrido: não saber não é o mesmo que estar confiante,
        e um candidato sem confiança é exatamente o que ninguém deveria aplicar às
        cegas.

        Posição impossível entra na fila **mesmo com confiança alta** (§37): o
        motor pode estar seguríssimo de uma leitura que não pode existir, e essa é
        justamente a que ninguém deve aplicar sem olhar.
        """
        if self._is_impossible_candidate(candidate):
            return True
        confidence = getattr(candidate, "confidence", None)
        return confidence is None or float(confidence) < self._uncertainty_threshold()

    @staticmethod
    def _is_impossible_candidate(candidate: OverlayOperation) -> bool:
        return legality.is_impossible(
            candidate.fen, getattr(candidate, "side_to_move", "w")
        )

    def _visible_candidate_indexes(self) -> list[int]:
        """Índices reais dos candidatos exibidos, na ordem em que aparecem."""
        indexes = list(range(len(self.candidates)))
        if self.candidates_only_uncertain.isChecked():
            indexes = [i for i in indexes if self._is_uncertain(self.candidates[i])]
        if self.candidates_worst_first.isChecked():
            # `None` primeiro (-1.0): é a leitura sobre a qual menos se sabe.
            indexes.sort(
                key=lambda i: (
                    -1.0
                    if getattr(self.candidates[i], "confidence", None) is None
                    else float(self.candidates[i].confidence)
                )
            )
        return indexes

    def _on_candidate_filter_changed(self) -> None:
        self.settings.setValue(
            "candidates_only_uncertain", bool(self.candidates_only_uncertain.isChecked())
        )
        self.settings.setValue("candidates_worst_first", bool(self.candidates_worst_first.isChecked()))
        self.settings.setValue("candidates_threshold", self._uncertainty_threshold())
        self.candidates_threshold_spin.setEnabled(self.candidates_only_uncertain.isChecked())
        self._refresh_candidates_list()

    @staticmethod
    def _candidate_label(position: int, index: int, candidate: OverlayOperation) -> str:
        confidence = getattr(candidate, "confidence", None)
        shown = "  ?  " if confidence is None else f"{float(confidence):.2f}"
        fen = candidate.fen
        # Sem o marcador, um candidato impossível com confiança 0,99 apareceria na
        # fila sem nada explicando por que está ali.
        flag = (
            " | ⚠ impossível"
            if legality.is_impossible(fen, getattr(candidate, "side_to_move", "w"))
            else ""
        )
        return (
            f"{position:03d} | pág {candidate.page_num + 1} | conf {shown}{flag} | "
            f"{fen[:24]}{'...' if len(fen) > 24 else ''}"
        )

    def _refresh_candidates_list(self, keep_row: Optional[int] = None) -> None:
        previous = self._selected_candidate_index() if keep_row is None else keep_row
        visible = self._visible_candidate_indexes()
        self._loading_ui = True
        try:
            self.candidates_list.clear()
            total = len(self.candidates)
            # Sem o "2 ·": a conferencia saiu do fluxo numerado para a sua propria
            # aba (§51.2), e numerar uma etapa que nao esta na sequencia era
            # prometer uma ordem que nao existe.
            if self.candidates_only_uncertain.isChecked() and total:
                self.candidates_label.setText(f"Conferir ({len(visible)} incertos de {total})")
            else:
                self.candidates_label.setText(f"Conferir ({total})")
            # Fila vazia e o estado normal: esconder a secao inteira devolve
            # espaco vertical para o que importa. O filtro nao esconde a secao —
            # senao nao haveria como desligar o filtro.
            self.candidates_section.setVisible(bool(self.candidates))
            # A aba tem a sua propria visibilidade, e e ela que aparece e some
            # com a fila. A secao acima continua respondendo pelo contrato do
            # filtro, que e outro.
            self._sync_candidates_tab()
            self.candidates_threshold_spin.setEnabled(self.candidates_only_uncertain.isChecked())
            for position, idx in enumerate(visible, start=1):
                item = QtWidgets.QListWidgetItem(
                    self._candidate_label(position, idx, self.candidates[idx])
                )
                item.setData(QtCore.Qt.UserRole, idx)
                self.candidates_list.addItem(item)
            if visible:
                # `previous` é um índice real, não uma linha: procurar por ele
                # mantém o mesmo candidato selecionado quando o filtro muda.
                row = visible.index(previous) if previous in visible else 0
                self.candidates_list.setCurrentRow(row)
        finally:
            self._loading_ui = False
        self._update_candidate_buttons()
        self._refresh_page_overlays()

    def _update_candidate_buttons(self) -> None:
        has_selection = self._selected_candidate_index() is not None
        has_visible = self.candidates_list.count() > 0
        self.btn_apply_candidate.setEnabled(has_selection)
        self.btn_discard_candidate.setEnabled(has_selection)
        self.btn_apply_all_candidates.setEnabled(has_visible)
        self.btn_discard_all_candidates.setEnabled(has_visible)
        # Com filtro ligado, "todos" quer dizer "todos os que você está vendo" —
        # e o rótulo tem de dizer isso, senão o botão promete demais.
        filtered = self.candidates_only_uncertain.isChecked() and len(
            self._visible_candidate_indexes()
        ) != len(self.candidates)
        self.btn_apply_all_candidates.setText("Aplicar visíveis" if filtered else "Aplicar todos")
        self.btn_discard_all_candidates.setText(
            "Descartar visíveis" if filtered else "Descartar todos"
        )

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

    def _take_visible_candidates(self) -> tuple[list[OverlayOperation], str]:
        """Retira da fila os candidatos exibidos. Devolve (retirados, descrição).

        Age só sobre o que está visível: com o filtro ligado, "todos" significa
        "todos os que você está vendo". Aplicar em massa o que está escondido
        seria exatamente o contrário do que a fila de conferência existe para
        evitar.
        """
        visible = set(self._visible_candidate_indexes())
        taken = [candidate for idx, candidate in enumerate(self.candidates) if idx in visible]
        self.candidates = [
            candidate for idx, candidate in enumerate(self.candidates) if idx not in visible
        ]
        scope = "visível(is)" if self.candidates else "candidato(s)"
        return taken, scope

    def _apply_all_candidates(self) -> None:
        visible = self._visible_candidate_indexes()
        if not visible:
            return
        hidden = len(self.candidates) - len(visible)
        question = f"Aplicar {len(visible)} candidato(s) sem conferir um a um?"
        if hidden:
            question += f"\n\n{hidden} candidato(s) fora do filtro ficam na fila."
        answer = QtWidgets.QMessageBox.question(self, "Aplicar candidatos", question)
        if answer != QtWidgets.QMessageBox.Yes:
            return

        taken, scope = self._take_visible_candidates()
        self.operations.extend(taken)
        self._refresh_operations_list()
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        self._commit_history(f"aplicar {len(taken)} candidato(s)")
        self.statusBar().showMessage(
            f"{len(taken)} {scope} aplicado(s). Total: {len(self.operations)}"
        )

    def _discard_all_candidates(self) -> None:
        visible = self._visible_candidate_indexes()
        if not visible:
            return
        hidden = len(self.candidates) - len(visible)
        question = f"Descartar {len(visible)} candidato(s)?"
        if hidden:
            question += f"\n\n{hidden} candidato(s) fora do filtro ficam na fila."
        answer = QtWidgets.QMessageBox.question(self, "Descartar candidatos", question)
        if answer != QtWidgets.QMessageBox.Yes:
            return

        taken, scope = self._take_visible_candidates()
        self._refresh_candidates_list()
        self._refresh_page_overlays()
        self._schedule_preview_refresh(immediate=True)
        self._commit_history(f"descartar {len(taken)} candidato(s)")
        self.statusBar().showMessage(f"{len(taken)} {scope} descartado(s).")
