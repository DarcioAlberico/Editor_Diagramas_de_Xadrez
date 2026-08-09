"""Diff entre dois projetos salvos (§22.5 item 8).

O caso de uso é reprocessar um livro com um OCR melhor e querer saber **o que mudou**:
quais leituras o motor novo corrigiu, quais diagramas ele passou a achar, e — o que
importa mais — quais ele *deixou* de achar.

### Por que a chave não pode ser o retângulo

O jeito óbvio de casar as duas listas seria por `(página, retângulo)`. Ele não
funciona justamente no caso de uso: um detector melhor devolve a mesma moldura com
alguns pontos de diferença, então um diff por chave exata reportaria **todos** os
diagramas como removidos e readicionados. O relatório seria tecnicamente correto e
completamente inútil.

Então o casamento é geométrico: mesma página e **IoU ≥ 0,50** com o candidato mais
sobreposto. É o mesmo critério que a fila de candidatos usa para não duplicar
detecção (§29), com o limiar mais folgado — lá a pergunta é "isto já está aplicado?",
aqui é "isto é o mesmo diagrama, ainda que reenquadrado?".

Casada a dupla, o que interessa é **em que** ela difere: FEN, retângulo, confiança,
estilo. Uma FEN diferente é o motor lendo a posição de outra forma; só o retângulo
diferente é o mesmo diagrama reenquadrado.

### A checagem que vem antes de tudo

Diff entre projetos de **livros diferentes** não significa nada. Se os dois apontam
para PDFs de `sha256` diferente, o resultado sai com `same_source = False` e quem
mostra o diff tem de dizer isso antes de mostrar número nenhum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .logging_config import get_logger
from .project_state import ProjectState, load_project_state
from .types import EraseOperation, OverlayOperation, Rect

logger = get_logger("project_diff")

#: Sobreposição mínima para dizer "é o mesmo diagrama, reenquadrado".
MATCH_IOU = 0.50

#: Diferença de retângulo, em pontos PDF, abaixo da qual não vale reportar
#: "reenquadrado" — é ruído de arredondamento do detector, não mudança.
RECT_EPSILON_PT = 0.5

#: Idem para padding e borda.
STYLE_EPSILON_PT = 0.01

REASON_FEN = "fen"
REASON_RECT = "retangulo"
REASON_CONFIDENCE = "confianca"
REASON_STYLE = "estilo"
REASON_META = "lado_ou_lance"


def rect_iou(a: Rect, b: Rect) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _rect_moved(a: Rect, b: Rect, epsilon: float = RECT_EPSILON_PT) -> bool:
    return any(abs(float(a[i]) - float(b[i])) > epsilon for i in range(4))


def _style_of(op: OverlayOperation) -> tuple[float, ...]:
    return (
        float(op.whiteout_padding_left_pt),
        float(op.whiteout_padding_top_pt),
        float(op.whiteout_padding_right_pt),
        float(op.whiteout_padding_bottom_pt),
        float(op.border_width_pt),
    )


def _confidence_changed(before: Optional[float], after: Optional[float]) -> bool:
    if before is None and after is None:
        return False
    if before is None or after is None:
        return True
    return abs(float(before) - float(after)) > 0.005


@dataclass(frozen=True)
class ChangedDiagram:
    """Uma dupla casada que difere em algo."""

    page_num: int
    before: OverlayOperation
    after: OverlayOperation
    reasons: tuple[str, ...]

    @property
    def fen_changed(self) -> bool:
        return REASON_FEN in self.reasons


@dataclass
class ProjectDiff:
    same_source: bool = True
    source_before: str = ""
    source_after: str = ""
    added: list[OverlayOperation] = field(default_factory=list)
    removed: list[OverlayOperation] = field(default_factory=list)
    changed: list[ChangedDiagram] = field(default_factory=list)
    unchanged: int = 0
    erases_added: list[EraseOperation] = field(default_factory=list)
    erases_removed: list[EraseOperation] = field(default_factory=list)
    study_before: int = 0
    study_after: int = 0
    candidates_before: int = 0
    candidates_after: int = 0
    #: Ajustes que valem para o livro todo: `(nome, antes, depois)`.
    settings: list[tuple[str, object, object]] = field(default_factory=list)

    @property
    def fen_changes(self) -> list[ChangedDiagram]:
        """As que o motor passou a ler de outra forma — o que se quer conferir."""
        return [item for item in self.changed if item.fen_changed]

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.changed
            or self.erases_added
            or self.erases_removed
            or self.settings
        )


def _match(
    before: Sequence[OverlayOperation], after: Sequence[OverlayOperation]
) -> tuple[list[tuple[OverlayOperation, OverlayOperation]], list[OverlayOperation], list[OverlayOperation]]:
    """Casa as duas listas por página e sobreposição, o melhor par primeiro.

    Guloso pelo maior IoU: com dois diagramas próximos na mesma página, o par mais
    sobreposto se resolve primeiro e não sobra ambiguidade para o segundo.
    """
    pairs: list[tuple[float, int, int]] = []
    for i, op_before in enumerate(before):
        for j, op_after in enumerate(after):
            if int(op_before.page_num) != int(op_after.page_num):
                continue
            score = rect_iou(tuple(op_before.rect_pdf), tuple(op_after.rect_pdf))
            if score >= MATCH_IOU:
                pairs.append((score, i, j))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_before: set[int] = set()
    used_after: set[int] = set()
    matched: list[tuple[OverlayOperation, OverlayOperation]] = []
    for _score, i, j in pairs:
        if i in used_before or j in used_after:
            continue
        used_before.add(i)
        used_after.add(j)
        matched.append((before[i], after[j]))

    removed = [op for i, op in enumerate(before) if i not in used_before]
    added = [op for j, op in enumerate(after) if j not in used_after]
    return matched, added, removed


def _reasons(before: OverlayOperation, after: OverlayOperation) -> tuple[str, ...]:
    reasons: list[str] = []
    if str(before.fen) != str(after.fen):
        reasons.append(REASON_FEN)
    if _rect_moved(tuple(before.rect_pdf), tuple(after.rect_pdf)):
        reasons.append(REASON_RECT)
    if _confidence_changed(
        getattr(before, "confidence", None), getattr(after, "confidence", None)
    ):
        reasons.append(REASON_CONFIDENCE)
    if any(
        abs(a - b) > STYLE_EPSILON_PT for a, b in zip(_style_of(before), _style_of(after))
    ):
        reasons.append(REASON_STYLE)
    if (
        str(getattr(before, "side_to_move", "w")) != str(getattr(after, "side_to_move", "w"))
        or int(getattr(before, "fullmove_number", 1)) != int(getattr(after, "fullmove_number", 1))
    ):
        reasons.append(REASON_META)
    return tuple(reasons)


def _match_erases(
    before: Sequence[EraseOperation], after: Sequence[EraseOperation]
) -> tuple[list[EraseOperation], list[EraseOperation]]:
    used_after: set[int] = set()
    removed: list[EraseOperation] = []
    for op_before in before:
        best: Optional[int] = None
        best_score = MATCH_IOU
        for j, op_after in enumerate(after):
            if j in used_after or int(op_before.page_num) != int(op_after.page_num):
                continue
            score = rect_iou(tuple(op_before.rect_pdf), tuple(op_after.rect_pdf))
            if score >= best_score:
                best, best_score = j, score
        if best is None:
            removed.append(op_before)
        else:
            used_after.add(best)
    added = [op for j, op in enumerate(after) if j not in used_after]
    return added, removed


def _fingerprint_id(state: ProjectState) -> str:
    fingerprint = state.source_pdf_fingerprint or {}
    return str(fingerprint.get("sha256", "") or "")


def diff_states(before: ProjectState, after: ProjectState) -> ProjectDiff:
    """Compara dois projetos já carregados."""
    sha_before, sha_after = _fingerprint_id(before), _fingerprint_id(after)
    # Sem sha nos dois lados não há como afirmar que é outro livro; só o `sha`
    # presente e diferente é evidência de verdade.
    same_source = not (sha_before and sha_after and sha_before != sha_after)

    matched, added, removed = _match(before.operations, after.operations)
    diff = ProjectDiff(
        same_source=same_source,
        source_before=str(before.source_pdf),
        source_after=str(after.source_pdf),
        added=list(added),
        removed=list(removed),
        study_before=len(before.study_positions),
        study_after=len(after.study_positions),
        candidates_before=len(before.candidates),
        candidates_after=len(after.candidates),
    )
    for op_before, op_after in matched:
        reasons = _reasons(op_before, op_after)
        if not reasons:
            diff.unchanged += 1
            continue
        diff.changed.append(
            ChangedDiagram(
                page_num=int(op_after.page_num),
                before=op_before,
                after=op_after,
                reasons=reasons,
            )
        )
    diff.changed.sort(key=lambda item: (item.page_num, item.reasons))

    diff.erases_added, diff.erases_removed = _match_erases(
        before.erase_operations, after.erase_operations
    )

    for name, value_before, value_after in (
        ("include_lichess_link", before.include_lichess_link, after.include_lichess_link),
        ("erase_coordinates", before.erase_coordinates, after.erase_coordinates),
    ):
        if bool(value_before) != bool(value_after):
            diff.settings.append((name, bool(value_before), bool(value_after)))

    logger.info(
        "Diff de projeto: +%d -%d ~%d (=%d), mesma origem=%s",
        len(diff.added),
        len(diff.removed),
        len(diff.changed),
        diff.unchanged,
        diff.same_source,
    )
    return diff


def diff_files(path_before: str, path_after: str) -> ProjectDiff:
    """Carrega os dois projetos (migrando o formato, se preciso) e compara."""
    return diff_states(load_project_state(path_before), load_project_state(path_after))


def _fen_short(fen: str, limit: int = 26) -> str:
    text = str(fen)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def format_diff(diff: ProjectDiff, limit: int = 40) -> str:
    """Resumo legível. `limit` corta cada lista, dizendo quantas ficaram de fora."""
    lines: list[str] = []
    if not diff.same_source:
        lines.append(
            "ATENÇÃO: os dois projetos apontam para PDFs diferentes "
            f"({diff.source_before} x {diff.source_after}). O diff abaixo provavelmente "
            "não quer dizer nada."
        )
        lines.append("")

    lines.append(
        f"substituições: {len(diff.added)} adicionada(s), {len(diff.removed)} removida(s), "
        f"{len(diff.changed)} alterada(s), {diff.unchanged} igual(is)"
    )
    if diff.fen_changes:
        lines.append(f"  das alteradas, {len(diff.fen_changes)} mudaram de FEN")

    def _section(title: str, entries: Iterable[str]) -> None:
        items = list(entries)
        if not items:
            return
        lines.append("")
        lines.append(title)
        for text in items[:limit]:
            lines.append(f"  {text}")
        if len(items) > limit:
            lines.append(f"  ... e mais {len(items) - limit} (limite de exibição)")

    _section(
        "adicionadas:",
        (f"pág {op.page_num + 1} · {_fen_short(op.fen)}" for op in diff.added),
    )
    _section(
        "removidas:",
        (f"pág {op.page_num + 1} · {_fen_short(op.fen)}" for op in diff.removed),
    )
    _section(
        "alteradas:",
        (
            f"pág {item.page_num + 1} · {'+'.join(item.reasons)}"
            + (
                f" · {_fen_short(item.before.fen)} → {_fen_short(item.after.fen)}"
                if item.fen_changed
                else ""
            )
            for item in diff.changed
        ),
    )

    if diff.erases_added or diff.erases_removed:
        lines.append("")
        lines.append(
            f"apagamentos: {len(diff.erases_added)} adicionado(s), "
            f"{len(diff.erases_removed)} removido(s)"
        )
    if diff.study_before != diff.study_after:
        lines.append(f"posições de estudo: {diff.study_before} → {diff.study_after}")
    if diff.candidates_before != diff.candidates_after:
        lines.append(f"candidatos pendentes: {diff.candidates_before} → {diff.candidates_after}")
    for name, value_before, value_after in diff.settings:
        lines.append(f"ajuste {name}: {value_before} → {value_after}")

    if not diff.has_changes:
        lines.append("")
        lines.append("nada mudou entre os dois projetos.")
    return "\n".join(lines)
