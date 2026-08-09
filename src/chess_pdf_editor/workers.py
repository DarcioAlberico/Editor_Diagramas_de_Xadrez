"""Workers em segundo plano (Sprint 5.1).

O caso grave era `Detectar no PDF`: 898 requisicoes HTTP sequenciais na thread
da UI, seguradas por `processEvents()`. A janela ficava "nao respondendo" para o
sistema operacional e qualquer redraw dependia do proximo `processEvents`.

**Contrato de thread.** Cada worker abre o **seu proprio** `fitz.Document` a
partir do caminho do arquivo. Nada de `fitz.Page`, `fitz.Document` ou
`PdfService` cruza a fronteira de thread — o que atravessa por sinal sao apenas
tipos imutaveis/dataclasses proprias (`BoardDetection`, `str`, `int`). Assim o
documento aberto na UI (usado pela previa ao vivo) nunca e tocado por duas
threads.

O cache global de diagramas renderizados em `pdf_service` e compartilhado, e por
isso ganhou um lock la.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from PySide6 import QtCore

from .logging_config import get_logger
from .pdf_service import PdfService, apply_operations_to_pdf
from .recognition import DEFAULT_ENGINE_MODE, RecognitionError, make_engine
from .types import EraseOperation, OverlayOperation, Rect

logger = get_logger("workers")


@dataclass(frozen=True)
class BoardDetection:
    """Um tabuleiro detectado, ja em coordenadas PDF.

    A conversao acontece no worker porque ela precisa do documento — e o
    documento do worker e o unico que ele pode tocar com seguranca.
    """

    page_num: int
    rect_pdf: Rect
    fen: str
    confidence: Optional[float] = None
    # Fracao da area da pagina ocupada pelo diagrama. A heuristica
    # anti-falso-positivo (>50% da pagina) roda na UI, mas o dado vem daqui.
    area_ratio: float = 0.0


class BatchOcrWorker(QtCore.QThread):
    """Renderiza e reconhece um intervalo de paginas fora da thread da UI."""

    #: pagina sendo processada (0-based), total de paginas do intervalo
    progress = QtCore.Signal(int, int)
    #: pagina concluida, deteccoes daquela pagina
    page_done = QtCore.Signal(int, list)
    #: pagina que falhou, mensagem
    page_failed = QtCore.Signal(int, str)
    #: ultima pagina processada + 1 (retomada), se foi cancelado
    completed = QtCore.Signal(int, bool)

    def __init__(
        self,
        pdf_path: str,
        start_page: int,
        end_page: int,
        endpoint: Optional[str] = None,
        zoom: float = 2.0,
        parent: Optional[QtCore.QObject] = None,
        engine_mode: str = DEFAULT_ENGINE_MODE,
        model_path: Optional[str] = None,
        engine_factory: Optional[object] = None,
    ) -> None:
        super().__init__(parent)
        self._pdf_path = str(pdf_path)
        self._start_page = int(start_page)
        self._end_page = int(end_page)
        self._endpoint = endpoint
        self._zoom = float(zoom)
        self._cancel_requested = False
        self._next_page = int(start_page)
        self._engine_mode = str(engine_mode)
        self._model_path = model_path
        # O motor é construído dentro de `run()`, na thread do worker: um modelo
        # PyTorch carregado na thread da UI e usado aqui atravessaria a fronteira que
        # o contrato deste módulo existe para não atravessar. O `engine_factory` é a
        # porta de entrada dos testes, que injetam um dublê sem rede nem modelo.
        self._engine_factory = engine_factory

    def cancel(self) -> None:
        """Pede parada. O worker termina a pagina corrente e sai."""
        self._cancel_requested = True

    @property
    def next_page(self) -> int:
        """Pagina onde retomar (util depois de um cancelamento)."""
        return self._next_page

    def _build_engine(self):
        if self._engine_factory is not None:
            return self._engine_factory()
        engine = make_engine(
            self._engine_mode,
            endpoint=self._endpoint,
            model_path=self._model_path,
        )
        # Pagar a carga do modelo agora, e não na primeira página: senão a barra de
        # progresso fica parada em "página 1" por um segundo sem explicação.
        warm = getattr(engine, "warm_up", None)
        if callable(warm):
            warm()
        return engine

    def run(self) -> None:  # pragma: no cover - exercitado via teste de integracao
        client = self._build_engine()
        total = max(0, self._end_page - self._start_page)
        canceled = False
        service: Optional[PdfService] = None
        try:
            service = PdfService(self._pdf_path)
            for page_num in range(self._start_page, self._end_page):
                if self._cancel_requested:
                    canceled = True
                    break
                self.progress.emit(page_num, total)
                try:
                    detections = self._process_page(service, client, page_num)
                except Exception as exc:
                    logger.exception("Falha ao processar a pagina %d", page_num + 1)
                    self.page_failed.emit(page_num, str(exc))
                else:
                    self.page_done.emit(page_num, detections)
                finally:
                    self._next_page = page_num + 1
        except Exception as exc:
            logger.exception("OCR em lote abortado")
            self.page_failed.emit(self._start_page, str(exc))
        finally:
            if service is not None:
                service.close()
            self.completed.emit(self._next_page, canceled or self._cancel_requested)

    def _process_page(
        self,
        service: PdfService,
        client,
        page_num: int,
    ) -> list[BoardDetection]:
        rendered = service.render_page(page_num, zoom=self._zoom)
        try:
            prediction = client.predict(rendered.image_png, filename=f"page_{page_num + 1}.png")
        except RecognitionError as exc:
            raise RuntimeError(str(exc)) from exc

        if not prediction.results:
            return []

        page_rect = service.doc[page_num].rect
        page_area = max(1.0, page_rect.width * page_rect.height)
        width_px = max(1.0, float(rendered.width_px))
        height_px = max(1.0, float(rendered.height_px))

        detections: list[BoardDetection] = []
        for result in prediction.results:
            rect_img = (
                (result.xc - result.width / 2.0) * width_px,
                (result.yc - result.height / 2.0) * height_px,
                (result.xc + result.width / 2.0) * width_px,
                (result.yc + result.height / 2.0) * height_px,
            )
            rect_pdf = service.image_rect_to_pdf_rect(page_num, rect_img, rendered.matrix)
            x0, y0, x1, y1 = rect_pdf
            area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            detections.append(
                BoardDetection(
                    page_num=page_num,
                    rect_pdf=rect_pdf,
                    fen=result.fen,
                    confidence=result.confidence,
                    area_ratio=area / page_area,
                )
            )
        return detections


class ExportWorker(QtCore.QThread):
    """Grava o PDF de saida fora da thread da UI."""

    #: caminho gravado
    done = QtCore.Signal(str)
    #: mensagem de erro
    failed = QtCore.Signal(str)

    def __init__(
        self,
        input_pdf: str,
        output_pdf: str,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation] = (),
        whiteout: bool = True,
        include_lichess_link: bool = True,
        erase_coordinates: bool = False,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._input_pdf = str(input_pdf)
        self._output_pdf = str(output_pdf)
        # Copia defensiva: o usuario pode continuar editando a lista enquanto a
        # exportacao roda, e o PDF gravado tem de refletir o clique em Exportar.
        self._operations = [replace(op) for op in operations]
        self._erase_operations = [replace(op) for op in erase_operations]
        self._whiteout = bool(whiteout)
        self._include_lichess_link = bool(include_lichess_link)
        self._erase_coordinates = bool(erase_coordinates)

    def run(self) -> None:  # pragma: no cover - exercitado via teste de integracao
        try:
            apply_operations_to_pdf(
                self._input_pdf,
                self._output_pdf,
                self._operations,
                erase_operations=self._erase_operations,
                whiteout=self._whiteout,
                include_lichess_link=self._include_lichess_link,
                erase_coordinates=self._erase_coordinates,
            )
        except Exception as exc:
            logger.exception("Falha ao exportar %s", self._output_pdf)
            self.failed.emit(str(exc))
            return
        logger.info(
            "PDF exportado: %s (%d substituicoes, %d apagamentos)",
            self._output_pdf,
            len(self._operations),
            len(self._erase_operations),
        )
        self.done.emit(self._output_pdf)
