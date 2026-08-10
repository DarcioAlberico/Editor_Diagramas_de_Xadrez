"""O classificador de peças e a descrição da sua arquitetura.

Até a Fase 5 a arquitetura era literal: uma CNN fixa, entrada 64×64 em tons de cinza,
cabeça `Linear(8192, 256)`. Nenhuma dessas três escolhas tinha sido medida contra
alternativa nenhuma (S-29), e o checkpoint não registrava qual delas produziu os pesos --
então trocar qualquer uma silenciosamente descartava metade dos pesos ao carregar, porque
`load_state_dict` rodava com `strict=False` (S-27).

`ArchConfig` resolve as duas coisas com o mesmo objeto: descreve a arquitetura para
`build_model` e vira a string `arch_version` gravada no checkpoint. Um checkpoint de
`cnn-gray-64-linear` carregado num `cnn-rgb-48-gap` passa a falhar alto, com o nome das
duas arquiteturas na mensagem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
import torch
import torch.nn as nn

from .config import MODEL_IMAGE_SIZE, PIECE_CLASSES

Channels = Literal["gray", "rgb"]
Head = Literal["linear", "gap"]
Backbone = Literal["cnn", "mobilenet_v3_small"]


@dataclass(frozen=True)
class ArchConfig:
    """Os fatores que a S-29 manda medir, um por vez.

    Os defaults são a arquitetura que produziu o baseline de 0,9906 -- para que
    `ArchConfig()` continue sendo exatamente o modelo que está em produção hoje.
    """

    backbone: Backbone = "cnn"
    channels: Channels = "gray"
    image_size: int = MODEL_IMAGE_SIZE
    head: Head = "linear"

    def __post_init__(self) -> None:
        if self.image_size % 8 != 0 or self.image_size <= 0:
            raise ValueError(f"image_size deve ser múltiplo positivo de 8 (três MaxPool2d); recebido {self.image_size}.")

    @property
    def in_channels(self) -> int:
        return 1 if self.channels == "gray" else 3

    @property
    def version(self) -> str:
        """Identidade da arquitetura, gravada no checkpoint como `arch_version`."""
        return f"{self.backbone}-{self.channels}-{self.image_size}-{self.head}"

    @classmethod
    def from_version(cls, version: str) -> ArchConfig:
        backbone, channels, size, head = version.rsplit("-", 3)
        return cls(backbone=backbone, channels=channels, image_size=int(size), head=head)  # type: ignore[arg-type]


DEFAULT_ARCH = ArchConfig()


class PieceClassifier(nn.Module):
    """CNN de três blocos convolucionais.

    `temperature` é um atributo simples, não um buffer: ela é gravada no checkpoint como
    chave própria e **fora** do `state_dict`, de propósito. Como buffer ela entraria no
    `state_dict` e todo checkpoint anterior à S-28 passaria a falhar sob `strict=True` --
    inclusive `piece_classifier_baseline.pt`, que é a única forma de reproduzir os números
    do BASELINE.md. Ver `calibration.py`.
    """

    def __init__(self, arch: ArchConfig = DEFAULT_ARCH, num_classes: int = len(PIECE_CLASSES)) -> None:
        super().__init__()
        self.arch = arch
        self.temperature: float = 1.0
        self.features = nn.Sequential(
            nn.Conv2d(arch.in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )

        if arch.head == "gap":
            # ~150 k parametros contra 2,1 M: a cabeca linear concentra 96% do modelo.
            self.classifier: nn.Module = nn.Sequential(
                nn.AdaptiveAvgPool2d(1),
                nn.Flatten(),
                nn.Dropout(p=0.25),
                nn.Linear(128, num_classes),
            )
        else:
            side = arch.image_size // 8
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(128 * side * side, 256),
                nn.ReLU(inplace=True),
                nn.Dropout(p=0.25),
                nn.Linear(256, num_classes),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Devolve logits crus. A temperatura é aplicada na inferência, não aqui --
        dividir no treino mudaria o gradiente da loss."""
        x = self.features(x)
        return self.classifier(x)


class MobileNetClassifier(nn.Module):
    """MobileNetV3-Small adaptada às 13 classes (fator "backbone" da S-29)."""

    def __init__(self, arch: ArchConfig, num_classes: int = len(PIECE_CLASSES), *, pretrained: bool = True) -> None:
        super().__init__()
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        self.arch = arch
        self.temperature: float = 1.0
        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        self.net = mobilenet_v3_small(weights=weights)
        if arch.in_channels == 1:
            # A primeira conv espera 3 canais. Somar os pesos ao longo do eixo de entrada
            # preserva a resposta do filtro para uma imagem cinza replicada em RGB, que e o
            # que a rede pre-treinada viu -- reinicializar jogaria fora o pre-treino.
            first = self.net.features[0][0]
            replacement = nn.Conv2d(1, first.out_channels, first.kernel_size, first.stride, first.padding, bias=False)
            with torch.no_grad():
                replacement.weight.copy_(first.weight.sum(dim=1, keepdim=True))
            self.net.features[0][0] = replacement
        in_features = self.net.classifier[-1].in_features
        self.net.classifier[-1] = nn.Linear(in_features, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def build_model(arch: ArchConfig = DEFAULT_ARCH, *, pretrained: bool = True) -> nn.Module:
    if arch.backbone == "mobilenet_v3_small":
        return MobileNetClassifier(arch, pretrained=pretrained)
    return PieceClassifier(arch)


def count_parameters(model: nn.Module) -> int:
    return sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)


def preprocess_cell_to_tensor(cell_rgb: np.ndarray, arch: ArchConfig = DEFAULT_ARCH) -> torch.Tensor:
    """Recorte de casa (RGB, uint8) → tensor (C, S, S) em [0, 1].

    O nome do parâmetro dizia `cell_bgr` e o corpo convertia de `COLOR_RGB2GRAY`: quem
    passasse BGR de fato teria os canais trocados. Todos os chamadores sempre passaram RGB.
    """
    if arch.channels == "gray":
        image = cv2.cvtColor(cell_rgb, cv2.COLOR_RGB2GRAY)
    else:
        image = cell_rgb

    resized = cv2.resize(image, (arch.image_size, arch.image_size), interpolation=cv2.INTER_AREA)
    normalized = resized.astype(np.float32) / 255.0
    if arch.channels == "gray":
        return torch.from_numpy(normalized).unsqueeze(0)
    return torch.from_numpy(np.ascontiguousarray(normalized.transpose(2, 0, 1)))
