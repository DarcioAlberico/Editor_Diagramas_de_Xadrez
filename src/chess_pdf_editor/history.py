"""Historico de alteracoes do modo edicao (undo/redo).

Ate aqui remover uma substituicao era definitivo — o modo Estudo tinha desfazer,
o modo Edicao nao (§22.3). Isso ficou mais grave com a fila de candidatos, onde
`Descartar todos` some com dezenas de deteccoes de uma vez.

**Por que snapshot e nao comando.** Um `Command` por acao (adicionar, remover,
aplicar candidato, mudar estilo em lote, limpar tudo) exigiria escrever e manter
o inverso de cada uma. As listas aqui sao pequenas — dataclasses rasas, algumas
centenas por livro — entao copiar o estado inteiro custa microssegundos e nao
tem inverso para errar. O limite de `limit` snapshots segura o uso de memoria.

Regra de uso: chamar `commit()` **depois** de mutar o estado. `reset()` define a
linha de base ao abrir um PDF ou carregar um projeto.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from .types import EraseOperation, OverlayOperation

DEFAULT_LIMIT = 60


@dataclass(frozen=True)
class ChangeSnapshot:
    """Estado completo das alteracoes num instante, com o rotulo da acao que o produziu."""

    label: str
    operations: tuple[OverlayOperation, ...] = field(default=())
    erase_operations: tuple[EraseOperation, ...] = field(default=())
    candidates: tuple[OverlayOperation, ...] = field(default=())

    @classmethod
    def capture(
        cls,
        label: str,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation],
        candidates: Sequence[OverlayOperation],
    ) -> "ChangeSnapshot":
        # `replace(op)` copia a dataclass: mutar a lista da UI depois nao pode
        # alterar o que ja foi gravado no historico.
        return cls(
            label=label,
            operations=tuple(replace(op) for op in operations),
            erase_operations=tuple(replace(op) for op in erase_operations),
            candidates=tuple(replace(op) for op in candidates),
        )

    def restore_operations(self) -> list[OverlayOperation]:
        return [replace(op) for op in self.operations]

    def restore_erase_operations(self) -> list[EraseOperation]:
        return [replace(op) for op in self.erase_operations]

    def restore_candidates(self) -> list[OverlayOperation]:
        return [replace(op) for op in self.candidates]

    def same_content(self, other: "ChangeSnapshot") -> bool:
        return (
            self.operations == other.operations
            and self.erase_operations == other.erase_operations
            and self.candidates == other.candidates
        )


class ChangeHistory:
    """Pilha linear de snapshots com um cursor.

    `_stack[_index]` e sempre o estado atual; desfazer anda para tras, refazer
    para frente, e um novo `commit` descarta o ramo a frente do cursor.
    """

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self._limit = max(2, int(limit))
        self._stack: list[ChangeSnapshot] = [ChangeSnapshot(label="inicio")]
        self._index = 0

    def reset(
        self,
        operations: Sequence[OverlayOperation] = (),
        erase_operations: Sequence[EraseOperation] = (),
        candidates: Sequence[OverlayOperation] = (),
        label: str = "inicio",
    ) -> None:
        self._stack = [ChangeSnapshot.capture(label, operations, erase_operations, candidates)]
        self._index = 0

    def commit(
        self,
        label: str,
        operations: Sequence[OverlayOperation],
        erase_operations: Sequence[EraseOperation],
        candidates: Sequence[OverlayOperation],
    ) -> bool:
        """Grava o estado atual. Devolve False se nada mudou de fato."""
        snapshot = ChangeSnapshot.capture(label, operations, erase_operations, candidates)
        if snapshot.same_content(self._stack[self._index]):
            return False

        del self._stack[self._index + 1 :]
        self._stack.append(snapshot)
        if len(self._stack) > self._limit:
            # Descarta o passado mais antigo, nunca o presente.
            del self._stack[0 : len(self._stack) - self._limit]
        self._index = len(self._stack) - 1
        return True

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._stack) - 1

    @property
    def undo_label(self) -> str:
        """Rotulo da acao que o proximo `undo` desfaz."""
        return self._stack[self._index].label if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        """Rotulo da acao que o proximo `redo` refaz."""
        return self._stack[self._index + 1].label if self.can_redo else ""

    def undo(self) -> Optional[ChangeSnapshot]:
        if not self.can_undo:
            return None
        self._index -= 1
        return self._stack[self._index]

    def redo(self) -> Optional[ChangeSnapshot]:
        if not self.can_redo:
            return None
        self._index += 1
        return self._stack[self._index]

    @property
    def current(self) -> ChangeSnapshot:
        return self._stack[self._index]

    def __len__(self) -> int:
        return len(self._stack)
