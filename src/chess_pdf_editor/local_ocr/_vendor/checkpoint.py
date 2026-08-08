"""Leitura e escrita do checkpoint, com os metadados que a S-27 pede.

O checkpoint antigo era `{"model_state": ...}` e mais nada. Três consequências que
apareceram na prática, não em revisão de código:

1. **Retomar zerava o controle de melhor época.** `best_val_loss` recomeçava em infinito
   e a primeira época da retomada sobrescrevia o arquivo mesmo se fosse pior. Foi o que
   aconteceu ao treinar o baseline (ver BASELINE.md); a Fase 1 registrou a pendência.
2. **Trocar a arquitetura descartava pesos em silêncio.** `strict=False` no
   `load_state_dict` faz uma CNN de entrada 48×48 aceitar os pesos de uma de 64×64,
   ignorando as camadas que não batem -- e treinar a partir de meio modelo aleatório.
3. **Não dava para saber que dados produziram um checkpoint.** Qual semente, qual split,
   quantas amostras: nada disso estava gravado, então "reproduza este número" não era uma
   instrução executável.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch

from .config import PIECE_CLASSES

logger = logging.getLogger(__name__)

CHECKPOINT_FORMAT = 2
"""1 = `{"model_state": ...}` cru (pré-Fase 5). 2 = com metadados."""


@dataclass(frozen=True)
class Checkpoint:
    """Um checkpoint carregado, com os metadados separados dos pesos."""

    state: dict[str, Any]
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    temperature: float = 1.0
    """Temperatura da calibração (S-28). 1,0 significa não calibrado."""

    @property
    def is_legacy(self) -> bool:
        """Checkpoint sem metadados: gravado antes da Fase 5.

        Continua carregando para inferência de propósito -- `piece_classifier_baseline.pt`
        é a única forma de reproduzir os números do BASELINE.md, e recusá-lo tornaria o
        baseline do projeto inverificável. O que ele **não** pode fazer é ser retomado
        para treino, porque aí a arquitetura precisa ser conhecida.
        """
        return int(self.metadata.get("checkpoint_format", 1)) < CHECKPOINT_FORMAT

    @property
    def arch_version(self) -> str:
        return str(self.metadata.get("arch_version", ""))

    @property
    def class_names(self) -> list[str]:
        names = self.metadata.get("class_names")
        return list(names) if names else []

    @property
    def best_metric(self) -> float | None:
        value = self.metadata.get("best_metric")
        return float(value) if value is not None else None


def _load_raw(path: Path, *, map_location: str) -> Any:
    try:
        return torch.load(path, map_location=map_location, weights_only=True)
    except TypeError:
        logger.debug("torch.load sem weights_only nesta versao; usando fallback.")
        return torch.load(path, map_location=map_location)


def load_checkpoint(path: Path, *, map_location: str = "cpu") -> Checkpoint:
    """Le um checkpoint .pt nos dois formatos em uso."""
    path = Path(path)
    raw = _load_raw(path, map_location=map_location)

    if isinstance(raw, dict) and "model_state" in raw:
        state = raw["model_state"]
        metadata = dict(raw.get("metadata") or {})
        stored = raw.get("temperature", metadata.get("temperature"))
        temperature = 1.0 if stored is None else float(stored)
    else:
        state = raw
        metadata = {}
        temperature = 1.0

    if not isinstance(state, dict):
        raise ValueError(f"Checkpoint invalido em {path}: esperado dict de pesos, obtido {type(state).__name__}.")

    if temperature <= 0:
        raise ValueError(f"Temperatura inválida em {path}: {temperature}. Deve ser positiva.")

    return Checkpoint(state=state, path=path, metadata=metadata, temperature=temperature)


def load_state_dict(path: Path, *, map_location: str) -> dict[str, Any]:
    """Compatibilidade: só os pesos. Prefira `load_checkpoint`."""
    return load_checkpoint(Path(path), map_location=map_location).state


def save_checkpoint(
    path: Path,
    state: dict[str, Any],
    *,
    metadata: dict[str, Any],
    temperature: float = 1.0,
) -> None:
    payload = {
        "model_state": state,
        "metadata": {**metadata, "checkpoint_format": CHECKPOINT_FORMAT},
        "temperature": float(temperature),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)


def check_compatible(checkpoint: Checkpoint, arch_version: str, *, class_names: list[str] | None = None) -> None:
    """Levanta se os metadados do checkpoint contradizem a arquitetura pedida.

    Isto é uma **mensagem melhor**, não a garantia. Quem garante é o
    `load_state_dict(strict=True)` que vem em seguida: nomes e formatos de todos os
    tensores têm de bater, e no espaço de `ArchConfig` cada fator muda algum deles --
    `channels` muda a primeira convolução, `image_size` muda a entrada da cabeça linear
    (2048 / 4608 / 8192), `head` e `backbone` mudam as próprias chaves. O que esta função
    acrescenta é dizer "este checkpoint é de `cnn-gray-64-linear`, você pediu
    `cnn-gray-32-linear`" em vez de despejar uma lista de tensores incompatíveis.

    **Checkpoint sem metadados não é recusado.** A primeira versão disto recusava, com o
    argumento de que retomar poderia continuar a partir de metade de um modelo aleatório.
    O argumento estava errado: era exatamente isso que o `strict=False` de antes permitia,
    e é exatamente isso que o `strict=True` impede. Recusar só bloqueava quem tem um
    checkpoint anterior à Fase 5 -- que é todo mundo que usava o projeto -- sem comprar
    segurança nenhuma. O que de fato não dá para saber de um checkpoint antigo é qual
    métrica ele atingiu; ver `training._resolve_best_metric`, que mede em vez de supor.
    """
    expected_classes = class_names if class_names is not None else PIECE_CLASSES

    if checkpoint.arch_version and checkpoint.arch_version != arch_version:
        raise ValueError(
            f"{checkpoint.path.name} é da arquitetura '{checkpoint.arch_version}', "
            f"mas o treino pediu '{arch_version}'. Treine do zero (--fresh, ou a caixa "
            f"'Treinar do zero' na interface) ou aponte outro checkpoint."
        )

    if checkpoint.class_names and checkpoint.class_names != expected_classes:
        raise ValueError(
            f"{checkpoint.path.name} foi treinado com outras classes "
            f"({len(checkpoint.class_names)}: {', '.join(checkpoint.class_names[:5])}...). "
            f"Esperadas {len(expected_classes)}."
        )


def git_commit() -> str:
    """Commit atual, para o checkpoint dizer de que código ele saiu. Vazio se não der."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
