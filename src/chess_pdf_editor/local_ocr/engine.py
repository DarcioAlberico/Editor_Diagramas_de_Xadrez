"""Adaptador entre o pipeline vendorizado e o contrato de OCR do editor.

O resto do app (GUI, `BatchOcrWorker`, fila de candidatos) fala com **um** contrato:

    predict(image_png: bytes, filename: str) -> OcrPrediction

com os retângulos em coordenadas normalizadas 0–1 da imagem enviada. Esse contrato
nasceu do serviço remoto; mantê-lo aqui é o que permite trocar o motor sem tocar em
nada acima — o motor híbrido depende exatamente disso.

Três decisões que valem explicação:

* **A confiança reportada é `min_confidence`, não a média.** ~77% das casas de um
  diagrama são vazias e triviais, então a média fica ~0,97 mesmo num tabuleiro com
  erro. A casa mais insegura é o que separa acerto de erro — e é ela que decide se o
  híbrido chama o servidor remoto.
* **Orientação é decidida por diagrama** (`predict_with_orientation`), não por um
  ajuste global: o livro pode ter diagramas nos dois sentidos.
* **Recorte sem tabuleiro detectável vira o tabuleiro inteiro.** Quando o usuário já
  selecionou a área na mão e ela não tem moldura nem margem, o detector por contorno
  não tem em que se agarrar — mas a seleção *é* a resposta, e insistir em detectar
  devolveria "nada encontrado" para uma seleção perfeita.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger
from ..types import OcrBoardResult, OcrPrediction, Rect

logger = get_logger("local_ocr")


class LocalOcrError(RuntimeError):
    """Mesma família de erro do cliente remoto, para a UI tratar igual."""


# Um modelo carregado é ~8,8 MB de pesos e ~1 s de carga. O worker de lote e a UI
# pedem o mesmo arquivo; carregar duas vezes seria desperdício puro.
_MODEL_CACHE: dict[tuple[str, str], tuple[object, str]] = {}
_MODEL_LOCK = threading.Lock()


def _load_shared_model(model_path: Path, device: str):
    key = (str(model_path.resolve()), device)
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached

    from ._vendor.inference import load_model

    model, resolved_device = load_model(model_path, device=device)
    with _MODEL_LOCK:
        _MODEL_CACHE.setdefault(key, (model, resolved_device))
        return _MODEL_CACHE[key]


def clear_model_cache() -> None:
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()


def _png_to_rgb(image_png: bytes):
    import numpy as np
    from PIL import Image

    if not image_png:
        raise LocalOcrError("Imagem vazia para reconhecimento local.")
    image = Image.open(io.BytesIO(image_png)).convert("RGB")
    return np.asarray(image)


def refine_rect(
    image_png: bytes,
    rect_img: Rect,
    margin_ratio: float = 0.30,
) -> Optional[Rect]:
    """Encosta um retângulo aproximado nas bordas reais do tabuleiro (§6.2).

    Função de módulo, e não método: o ajuste usa **só o detector por contorno**, que
    não precisa de modelo nenhum carregado. Assim `Ajustar seleção à borda` funciona
    numa instalação que tem OpenCV mas ainda não baixou o classificador.

    A margem existe porque a seleção do usuário costuma cortar *dentro* do diagrama:
    sem folga não haveria como crescer até a borda verdadeira.

    Medido em 7 diagramas de dois livros reais, partindo de seleções deslocadas e
    encolhidas de 4 a 10%: o retângulo devolvido pelo recorte é **idêntico**, ao pixel,
    ao que a detecção da página inteira encontra para o mesmo diagrama. Ou seja, o
    resultado do ajuste é a borda do tabuleiro e não a seleção que o originou — que é
    a propriedade que o usuário percebe ao clicar duas vezes e nada se mexer.

    Devolve `None` quando não encontra tabuleiro — mexer na seleção nesse caso seria
    pior que não fazer nada.
    """
    from ._vendor.board_detection import detect_boards

    image_rgb = _png_to_rgb(image_png)
    height, width = image_rgb.shape[:2]
    x0, y0, x1, y1 = (float(v) for v in rect_img)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if (x1 - x0) < 16 or (y1 - y0) < 16:
        return None

    margin_x = (x1 - x0) * margin_ratio
    margin_y = (y1 - y0) * margin_ratio
    cx0 = max(0, int(round(x0 - margin_x)))
    cy0 = max(0, int(round(y0 - margin_y)))
    cx1 = min(width, int(round(x1 + margin_x)))
    cy1 = min(height, int(round(y1 + margin_y)))
    crop = image_rgb[cy0:cy1, cx0:cx1]
    if crop.size == 0:
        return None

    found = detect_boards(crop, max_boards=1, warn_on_cap=False)
    if not found:
        return None
    quad = found[0][1]
    if quad is None:
        return None
    return (
        cx0 + float(quad[:, 0].min()),
        cy0 + float(quad[:, 1].min()),
        cx0 + float(quad[:, 0].max()),
        cy0 + float(quad[:, 1].max()),
    )


#: Tolerância do clique que cai *fora* da moldura, como fração do lado do
#: tabuleiro. Acertar por dentro de uma borda de 2 px seria exigir precisão de
#: cirurgião; 8% do lado é a folga que perdoa a mão sem pegar o diagrama vizinho.
CLICK_TOLERANCE_RATIO = 0.08


def _rect_from_quad(quad, offset_x: float = 0.0, offset_y: float = 0.0) -> Rect:
    return (
        offset_x + float(quad[:, 0].min()),
        offset_y + float(quad[:, 1].min()),
        offset_x + float(quad[:, 0].max()),
        offset_y + float(quad[:, 1].max()),
    )


def detect_board_rects(image_png: bytes, max_boards: int = 12) -> list[Rect]:
    """Retângulos dos tabuleiros da imagem, em pixels dela.

    Só o detector por contorno, como o `refine_rect`: nenhum modelo é carregado,
    então isto funciona numa instalação com OpenCV e sem o classificador.

    Medido numa página A4 a zoom 2.0 (1190×1684): **~40 ms**. É o que permite que um
    clique dispare a detecção da página inteira sem worker e sem lag perceptível.
    """
    from ._vendor.board_detection import detect_boards

    image_rgb = _png_to_rgb(image_png)
    found = detect_boards(image_rgb, max_boards=max_boards, warn_on_cap=False)
    return [_rect_from_quad(quad) for _crop, quad in found if quad is not None]


def _point_distance_to_rect(rect: Rect, x: float, y: float) -> float:
    x0, y0, x1, y1 = rect
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return (dx * dx + dy * dy) ** 0.5


def _rect_area(rect: Rect) -> float:
    x0, y0, x1, y1 = rect
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def board_rect_at(
    image_png: bytes,
    point_img: tuple[float, float],
    max_boards: int = 12,
    tolerance_ratio: float = CLICK_TOLERANCE_RATIO,
) -> Optional[Rect]:
    """Tabuleiro sob o ponto clicado, ou `None` se não houver nenhum ali.

    Quem contém o ponto ganha; havendo mais de um (moldura dentro de moldura), ganha
    o **menor**, que é a borda mais justa do tabuleiro. Nada contendo o ponto, aceita
    o mais próximo dentro de `tolerance_ratio` do seu próprio lado — o clique que
    raspou a borda por fora vale, o clique no meio do texto não.
    """
    x, y = (float(point_img[0]), float(point_img[1]))
    rects = detect_board_rects(image_png, max_boards=max_boards)
    if not rects:
        return None

    containing = [rect for rect in rects if _point_distance_to_rect(rect, x, y) == 0.0]
    if containing:
        return min(containing, key=_rect_area)

    best: Optional[Rect] = None
    best_distance = float("inf")
    for rect in rects:
        x0, y0, x1, y1 = rect
        tolerance = max(x1 - x0, y1 - y0) * float(tolerance_ratio)
        distance = _point_distance_to_rect(rect, x, y)
        if distance <= tolerance and distance < best_distance:
            best, best_distance = rect, distance
    return best


class LocalRecognizer:
    """Detecta e reconhece diagramas sem sair da máquina."""

    def __init__(
        self,
        model_path: Optional[Path] = None,
        device: str = "cpu",
        max_boards: Optional[int] = None,
    ) -> None:
        from . import default_model_path, dependencies_available, unavailable_reason

        if not dependencies_available():
            raise LocalOcrError(unavailable_reason())
        resolved = Path(model_path) if model_path else default_model_path()
        if resolved is None or not resolved.is_file():
            raise LocalOcrError(unavailable_reason())

        from ._vendor.config import DEFAULT_MAX_BOARDS

        self.model_path = resolved
        self.device = device
        self.max_boards = int(max_boards or DEFAULT_MAX_BOARDS)
        self._model = None
        self._resolved_device = device

    # -- carga preguiçosa ---------------------------------------------

    def _ensure_model(self):
        if self._model is None:
            self._model, self._resolved_device = _load_shared_model(self.model_path, self.device)
        return self._model, self._resolved_device

    def warm_up(self) -> None:
        """Paga o custo da carga antes do lote, para a barra de progresso não mentir."""
        self._ensure_model()

    # -- contrato de OCR ----------------------------------------------

    def predict(
        self,
        image_png: bytes,
        filename: str = "board.png",
        assume_whole_image: bool = False,
    ) -> OcrPrediction:
        """Mesma forma da resposta do serviço remoto, sem rede.

        `assume_whole_image` vale para `Reconhecer seleção`: ali a área já foi
        escolhida pelo usuário e não achar contorno não significa não haver tabuleiro.
        """
        from ._vendor.board_detection import detect_boards
        from ._vendor.config import BOARD_SIZE
        from ._vendor.inference import predict_with_orientation

        import cv2

        image_rgb = _png_to_rgb(image_png)
        height, width = image_rgb.shape[:2]
        model, device = self._ensure_model()

        boards = detect_boards(image_rgb, max_boards=self.max_boards, warn_on_cap=False)
        if not boards and assume_whole_image:
            whole = cv2.resize(image_rgb, (BOARD_SIZE, BOARD_SIZE), interpolation=cv2.INTER_AREA)
            boards = [(whole, None)]

        results: list[OcrBoardResult] = []
        for warped, quad in boards:
            oriented = predict_with_orientation(warped, model, device)
            prediction = oriented.prediction
            if quad is None:
                x0, y0, x1, y1 = 0.0, 0.0, float(width), float(height)
            else:
                x0 = float(quad[:, 0].min())
                y0 = float(quad[:, 1].min())
                x1 = float(quad[:, 0].max())
                y1 = float(quad[:, 1].max())
            results.append(
                OcrBoardResult(
                    fen=prediction.fen_board,
                    xc=((x0 + x1) / 2.0) / max(1.0, width),
                    yc=((y0 + y1) / 2.0) / max(1.0, height),
                    width=(x1 - x0) / max(1.0, width),
                    height=(y1 - y0) / max(1.0, height),
                    confidence=float(prediction.min_confidence),
                )
            )

        if len(boards) >= self.max_boards:
            # O detector avisa sozinho quando o teto corta candidato bom, mas o texto
            # dele manda "aumente 'Max diagramas'" — uma opção que este app não expõe
            # (§59.17.3). Daí o aviso próprio, com o número que permite reconhecer o
            # caso num log de suporte. Sem ele o corte é silencioso, e o corte é **por
            # score**: numa grade 3x3 o que fica de fora pode ser o do canto superior
            # direito, e nada na tela diz que faltou um.
            logger.warning(
                "Página %s bateu o teto de %d diagramas do detector local; se ela tem "
                "mais que isso, algum ficou de fora",
                filename,
                self.max_boards,
            )
        logger.info(
            "Reconhecimento local: %d tabuleiro(s) em %s (%dx%d px)",
            len(results),
            filename,
            width,
            height,
        )
        return OcrPrediction(
            request_id="local",
            status=200 if results else 204,
            message=None if results else "Nenhum tabuleiro detectado localmente.",
            results=results,
        )

    # -- snap da seleção (§6.2) ---------------------------------------

    def refine_rect(
        self,
        image_png: bytes,
        rect_img: Rect,
        margin_ratio: float = 0.30,
    ) -> Optional[Rect]:
        """Ver `refine_rect` no módulo — aqui só para quem já tem o reconhecedor."""
        return refine_rect(image_png, rect_img, margin_ratio=margin_ratio)
