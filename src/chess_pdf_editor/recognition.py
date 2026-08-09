"""Escolha do motor de reconhecimento (Sprint 7.2).

Até aqui só existia um caminho: mandar a imagem para `helpman.komtera.lt`. Com o motor
local pronto, passam a existir três, e o app inteiro continua falando com **um**
contrato — `predict(image_png, filename) -> OcrPrediction` — porque foi ele que já
existia. Trocar o motor não toca em GUI, worker de lote nem fila de candidatos.

    local     só a máquina. Sem rede, sem enviar página nenhuma.
    remote    só o serviço externo. O comportamento de antes, intacto.
    hybrid    local primeiro; o remoto entra como reforço onde o local ficou inseguro.

**Por que o híbrido é o padrão.** Num livro de 898 páginas o modo remoto são 898
requisições HTTP; o local resolve a maioria delas em ~55 ms cada, sem sair da máquina.
Mas o classificador local foi treinado num conjunto de estilos de diagrama, e um livro
com figurina que ele nunca viu degrada — é exatamente aí que a confiança mínima cai e
vale gastar a requisição. O híbrido é o único modo em que o custo de rede é
proporcional à dificuldade real do livro.

**Reforço é por tabuleiro, não por página.** Quando o remoto é chamado, o que ele
substitui são só as leituras inseguras; a geometria continua vindo do detector local,
que trabalha na resolução do render e é reprodutível ao pixel, enquanto o remoto
devolve a caixa em coordenadas normalizadas e arredondadas.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional, Protocol

from . import local_ocr
from .logging_config import get_logger
from .ocr_api import OcrApiClient, OcrApiError
from .types import OcrBoardResult, OcrPrediction

logger = get_logger("recognition")

ENGINE_LOCAL = "local"
ENGINE_REMOTE = "remote"
ENGINE_HYBRID = "hybrid"

ENGINE_MODES = (ENGINE_HYBRID, ENGINE_LOCAL, ENGINE_REMOTE)
DEFAULT_ENGINE_MODE = ENGINE_HYBRID


def default_engine_mode() -> str:
    """Modo a usar quando o usuário ainda não escolheu.

    O padrão é o híbrido, que reconhece na máquina e só recorre ao serviço externo
    onde a confiança fica baixa. Numa distribuição **light** (§44) esse padrão seria
    uma promessa que o executável não pode cumprir: ele saiu sem o motor local de
    propósito. Ali o padrão é o remoto — o único que funciona naquele pacote.

    Vale só para o padrão: escolha salva do usuário continua ganhando.
    """
    from .resources import is_light_build

    return ENGINE_REMOTE if is_light_build() else DEFAULT_ENGINE_MODE

ENGINE_LABELS = {
    ENGINE_HYBRID: "Local primeiro, remoto como reforço",
    ENGINE_LOCAL: "Somente local (offline)",
    ENGINE_REMOTE: "Somente remoto (serviço externo)",
}

#: Abaixo desta confiança mínima por casa o híbrido pede uma segunda opinião. É o mesmo
#: valor do portão de exportação do projeto de origem (`ACCEPT_MIN_CONFIDENCE`), medido
#: no split de teste: a confiança mínima fica >= 0,90 em quase todo tabuleiro exato e a
#: média nas casas erradas é ~0,75.
REINFORCE_BELOW_CONFIDENCE = 0.80

#: Acima disso duas detecções são o mesmo tabuleiro.
_MATCH_IOU = 0.50


class RecognitionError(RuntimeError):
    """Falha de reconhecimento, qualquer que seja o motor."""


class RecognitionEngine(Protocol):
    def predict(
        self,
        image_png: bytes,
        filename: str = "board.png",
        assume_whole_image: bool = False,
    ) -> OcrPrediction:
        """`assume_whole_image` = a imagem *já é* o tabuleiro (Reconhecer seleção).

        O motor remoto ignora a dica: ele sempre detectou por conta própria. O local
        usa para não devolver "nada encontrado" numa seleção sem moldura nem margem,
        onde o detector por contorno não tem em que se agarrar mas a área desenhada
        pelo usuário é a resposta.
        """

    def uses_network(self) -> bool: ...

    @property
    def name(self) -> str: ...


def _rect_from_result(result: OcrBoardResult) -> tuple[float, float, float, float]:
    return (
        result.xc - result.width / 2.0,
        result.yc - result.height / 2.0,
        result.xc + result.width / 2.0,
        result.yc + result.height / 2.0,
    )


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class RemoteEngine:
    """O serviço externo, exatamente como antes."""

    name = ENGINE_REMOTE

    def __init__(self, endpoint: Optional[str] = None, client: Optional[OcrApiClient] = None) -> None:
        self._client = client or OcrApiClient(endpoint=endpoint)

    def uses_network(self) -> bool:
        return True

    def predict(
        self,
        image_png: bytes,
        filename: str = "board.png",
        assume_whole_image: bool = False,
    ) -> OcrPrediction:
        del assume_whole_image  # o serviço remoto sempre detecta por conta própria
        try:
            return self._client.predict(image_png, filename=filename)
        except OcrApiError as exc:
            raise RecognitionError(str(exc)) from exc


class LocalEngine:
    """Detector + classificador na máquina, sem rede."""

    name = ENGINE_LOCAL

    def __init__(self, recognizer=None, model_path: Optional[str] = None) -> None:
        if recognizer is not None:
            self._recognizer = recognizer
            return
        try:
            self._recognizer = local_ocr.get_recognizer(model_path)
        except Exception as exc:
            raise RecognitionError(str(exc)) from exc

    def uses_network(self) -> bool:
        return False

    def warm_up(self) -> None:
        warm = getattr(self._recognizer, "warm_up", None)
        if callable(warm):
            warm()

    def predict(
        self,
        image_png: bytes,
        filename: str = "board.png",
        assume_whole_image: bool = False,
    ) -> OcrPrediction:
        try:
            return self._recognizer.predict(
                image_png,
                filename=filename,
                assume_whole_image=assume_whole_image,
            )
        except Exception as exc:
            raise RecognitionError(str(exc)) from exc


class HybridEngine:
    """Local primeiro; o remoto reforça só as leituras inseguras."""

    name = ENGINE_HYBRID

    def __init__(
        self,
        local: RecognitionEngine,
        remote: RecognitionEngine,
        min_confidence: float = REINFORCE_BELOW_CONFIDENCE,
    ) -> None:
        self._local = local
        self._remote = remote
        self._min_confidence = float(min_confidence)

    def uses_network(self) -> bool:
        # Verdadeiro mesmo quando na prática a rede quase nunca é usada: o aviso de
        # privacidade tem de descrever o que *pode* acontecer, não a média.
        return True

    def warm_up(self) -> None:
        warm = getattr(self._local, "warm_up", None)
        if callable(warm):
            warm()

    def _needs_reinforcement(self, prediction: OcrPrediction) -> bool:
        if not prediction.results:
            return True
        for result in prediction.results:
            if result.confidence is None or result.confidence < self._min_confidence:
                return True
        return False

    def predict(
        self,
        image_png: bytes,
        filename: str = "board.png",
        assume_whole_image: bool = False,
    ) -> OcrPrediction:
        try:
            local_prediction = self._local.predict(
                image_png,
                filename=filename,
                assume_whole_image=assume_whole_image,
            )
        except RecognitionError as exc:
            logger.warning("Motor local falhou em %s (%s); usando o remoto", filename, exc)
            return self._remote.predict(image_png, filename=filename)

        if not self._needs_reinforcement(local_prediction):
            return local_prediction

        try:
            remote_prediction = self._remote.predict(image_png, filename=filename)
        except RecognitionError as exc:
            # Sem rede o local continua sendo a melhor resposta que existe. Devolver
            # erro aqui jogaria fora leitura boa por causa de uma casa insegura.
            logger.info("Reforço remoto indisponível em %s (%s); mantendo o local", filename, exc)
            return local_prediction

        if not local_prediction.results:
            return remote_prediction
        return self._merge(local_prediction, remote_prediction)

    def _merge(self, local_prediction: OcrPrediction, remote_prediction: OcrPrediction) -> OcrPrediction:
        """Troca a leitura das casas inseguras, preservando a geometria local."""
        remote_boxes = [(_rect_from_result(r), r) for r in remote_prediction.results]
        merged: list[OcrBoardResult] = []
        reinforced = 0
        for result in local_prediction.results:
            if result.confidence is not None and result.confidence >= self._min_confidence:
                merged.append(result)
                continue
            rect = _rect_from_result(result)
            best = max(
                ((_iou(rect, other_rect), other) for other_rect, other in remote_boxes),
                key=lambda item: item[0],
                default=(0.0, None),
            )
            if best[1] is None or best[0] < _MATCH_IOU:
                merged.append(result)
                continue
            reinforced += 1
            merged.append(replace(result, fen=best[1].fen, confidence=best[1].confidence))

        # Tabuleiro que só o remoto viu entra também: o local pode ter perdido um.
        for other_rect, other in remote_boxes:
            if all(_iou(other_rect, _rect_from_result(r)) < _MATCH_IOU for r in merged):
                merged.append(other)

        logger.info(
            "Híbrido: %d tabuleiro(s) local, %d reforçado(s) pelo remoto, %d total",
            len(local_prediction.results),
            reinforced,
            len(merged),
        )
        return OcrPrediction(
            request_id=f"hybrid:{remote_prediction.request_id or '-'}",
            status=200 if merged else 204,
            message=remote_prediction.message,
            results=merged,
        )


def normalize_mode(mode: Optional[str]) -> str:
    value = (mode or "").strip().lower()
    return value if value in ENGINE_MODES else DEFAULT_ENGINE_MODE


def make_engine(
    mode: str,
    endpoint: Optional[str] = None,
    model_path: Optional[str] = None,
    min_confidence: float = REINFORCE_BELOW_CONFIDENCE,
) -> RecognitionEngine:
    """Motor pedido, com queda para o remoto quando o local não está disponível.

    A queda é silenciosa de propósito no `hybrid` (é o padrão de fábrica e o usuário
    não escolheu nada), mas **ruidosa** no `local`: ali ele pediu explicitamente para
    não usar rede, e trocar isso por baixo do pano seria uma quebra de contrato.
    """
    mode = normalize_mode(mode)

    if mode == ENGINE_REMOTE:
        return RemoteEngine(endpoint=endpoint)

    if mode == ENGINE_LOCAL:
        return LocalEngine(model_path=model_path)

    try:
        local = LocalEngine(model_path=model_path)
    except RecognitionError as exc:
        logger.info("Híbrido sem motor local (%s); seguindo só com o remoto", exc)
        return RemoteEngine(endpoint=endpoint)
    return HybridEngine(local, RemoteEngine(endpoint=endpoint), min_confidence=min_confidence)


def mode_uses_network(mode: str) -> bool:
    """O modo pode enviar páginas para fora? Alimenta o aviso de privacidade."""
    return normalize_mode(mode) != ENGINE_LOCAL
