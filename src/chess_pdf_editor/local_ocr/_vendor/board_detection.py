from __future__ import annotations

import logging
import math

import cv2
import numpy as np

from .config import BOARD_SIZE, CELL_SIZE, DEFAULT_MAX_BOARDS, DEFAULT_READING_ORDER, ReadingOrder

logger = logging.getLogger(__name__)


class NoBoardDetectedError(RuntimeError):
    pass


def order_quad_points(points: np.ndarray) -> np.ndarray:
    pts = np.array(points, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)

    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]  # top-left
    ordered[2] = pts[np.argmax(s)]  # bottom-right
    ordered[1] = pts[np.argmin(diff)]  # top-right
    ordered[3] = pts[np.argmax(diff)]  # bottom-left
    return ordered


def warp_from_quad(image_rgb: np.ndarray, quad: np.ndarray, target_size: int = BOARD_SIZE) -> np.ndarray:
    src = order_quad_points(quad)
    dst = np.array(
        [
            [0, 0],
            [target_size - 1, 0],
            [target_size - 1, target_size - 1],
            [0, target_size - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image_rgb, matrix, (target_size, target_size))


def _contour_geometry_score(quad: np.ndarray, image_area: float) -> float:
    area = cv2.contourArea(quad.astype(np.float32))
    if area <= 0:
        return 0.0
    # Keep small diagrams but reject tiny noisy contours.
    if area < image_area * 0.004:
        return 0.0

    x, y, w, h = cv2.boundingRect(quad.astype(np.int32))
    if h == 0:
        return 0.0
    ratio = w / float(h)
    if ratio < 0.62 or ratio > 1.62:
        return 0.0

    area_ratio = area / image_area
    # Saturates to avoid very large non-board boxes dominating by area only.
    area_component = min(area_ratio / 0.12, 1.0)
    squareness = max(0.0, 1.0 - (abs(math.log(ratio)) / math.log(1.62)))
    return area_component * (squareness**2.4)


def _bbox_from_quad(quad: np.ndarray) -> tuple[int, int, int, int]:
    xs = quad[:, 0]
    ys = quad[:, 1]
    x0, y0 = int(np.min(xs)), int(np.min(ys))
    x1, y1 = int(np.max(xs)), int(np.max(ys))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _bbox_iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / float(union) if union > 0 else 0.0


def _sort_selected_candidates(
    selected: list[tuple[np.ndarray, float, tuple[int, int, int, int]]],
    reading_order: ReadingOrder,
) -> None:
    if reading_order == "row":
        selected.sort(key=lambda item: (item[2][1], item[2][0]))
        return
    if reading_order != "column":
        raise ValueError("reading_order deve ser 'row' ou 'column'.")

    selected.sort(key=lambda item: item[2][0] + item[2][2] / 2.0)
    widths = [bbox[2] for _, _, bbox in selected]
    median_width = float(np.median(widths)) if widths else 0.0
    column_gap = max(8.0, median_width * 0.55)
    columns: list[list[tuple[np.ndarray, float, tuple[int, int, int, int]]]] = []

    for candidate in selected:
        _, _, bbox = candidate
        center_x = bbox[0] + bbox[2] / 2.0
        if not columns:
            columns.append([candidate])
            continue

        last_column = columns[-1]
        last_centers = [item[2][0] + item[2][2] / 2.0 for item in last_column]
        if abs(center_x - float(np.mean(last_centers))) <= column_gap:
            last_column.append(candidate)
        else:
            columns.append([candidate])

    ordered: list[tuple[np.ndarray, float, tuple[int, int, int, int]]] = []
    for column in columns:
        column.sort(key=lambda item: (item[2][1], item[2][0]))
        ordered.extend(column)
    selected[:] = ordered


def _bbox_visible_ratio(bbox: tuple[int, int, int, int], image_shape: tuple[int, int, int]) -> float:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return 0.0

    img_h, img_w = image_shape[:2]
    ix1 = max(0, x)
    iy1 = max(0, y)
    ix2 = min(img_w, x + w)
    iy2 = min(img_h, y + h)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    return (iw * ih) / float(w * h)


def _quad_point_inside_ratio(
    quad: np.ndarray,
    image_shape: tuple[int, int, int],
    margin_ratio: float = 0.015,
) -> float:
    img_h, img_w = image_shape[:2]
    margin_x = img_w * margin_ratio
    margin_y = img_h * margin_ratio
    inside = (
        (quad[:, 0] >= -margin_x)
        & (quad[:, 0] <= (img_w - 1) + margin_x)
        & (quad[:, 1] >= -margin_y)
        & (quad[:, 1] <= (img_h - 1) + margin_y)
    )
    return float(np.mean(inside))


def _periodic_peak_score(profile: np.ndarray, period: int) -> float:
    if profile.size <= period:
        return 0.0
    expected = [period * i for i in range(1, 8)]
    peaks: list[float] = []
    radius = max(2, period // 8)
    for center in expected:
        left = max(0, center - radius)
        right = min(profile.size, center + radius + 1)
        if right <= left:
            continue
        peaks.append(float(np.max(profile[left:right])))
    if not peaks:
        return 0.0

    baseline = float(np.percentile(profile, 55))
    spread = float(np.percentile(profile, 90) - baseline)
    if spread <= 1e-6:
        return 0.0
    return float(np.clip((np.mean(peaks) - baseline) / spread, 0.0, 1.0))


def _board_pattern_score(warped_rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(warped_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    # 20 px per cell: enough for line and checker texture cues.
    small = cv2.resize(gray, (160, 160), interpolation=cv2.INTER_AREA)

    cell_means = small.reshape(8, 20, 8, 20).mean(axis=(1, 3))
    parity = (np.indices((8, 8)).sum(axis=0) % 2) == 0
    even_cells = cell_means[parity]
    odd_cells = cell_means[~parity]
    contrast = abs(float(even_cells.mean()) - float(odd_cells.mean())) / 255.0
    within_var = (float(even_cells.std()) + float(odd_cells.std())) / (2.0 * 255.0)
    checker_score = float(np.clip(contrast * 2.4 - within_var * 0.9, 0.0, 1.0))

    gx = np.abs(np.diff(small, axis=1)).mean(axis=0)
    gy = np.abs(np.diff(small, axis=0)).mean(axis=1)
    grid_score = (_periodic_peak_score(gx, period=20) + _periodic_peak_score(gy, period=20)) / 2.0

    return float(np.clip(0.6 * checker_score + 0.4 * grid_score, 0.0, 1.0))


def _extract_candidate_quads(image_rgb: np.ndarray) -> list[tuple[np.ndarray, float, tuple[int, int, int, int]]]:
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_base = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        8,
    )
    image_area = float(image_rgb.shape[0] * image_rgb.shape[1])
    raw_candidates: list[tuple[np.ndarray, float, tuple[int, int, int, int], float]] = []
    kernel = np.ones((3, 3), np.uint8)
    threshold_passes = [thresh_base, cv2.morphologyEx(thresh_base, cv2.MORPH_CLOSE, kernel, iterations=1)]

    for thresh in threshold_passes:
        # RETR_LIST keeps inner contours too. This helps when board is inside a larger rectangle.
        contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if len(contour) < 4:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)

            if len(approx) == 4:
                quad = approx.reshape(4, 2).astype(np.float32)
            else:
                rect = cv2.minAreaRect(contour)
                quad = cv2.boxPoints(rect).astype(np.float32)

            geom_score = _contour_geometry_score(quad, image_area)
            if geom_score <= 0:
                continue

            bbox = _bbox_from_quad(quad)
            # Reject heavily out-of-frame quads; they tend to produce page-level false positives.
            if _bbox_visible_ratio(bbox, image_rgb.shape) < 0.65:
                continue
            if _quad_point_inside_ratio(quad, image_rgb.shape) < 0.5:
                continue

            warped = warp_from_quad(image_rgb, quad, target_size=320)
            pattern_score = _board_pattern_score(warped)
            score = geom_score * (0.55 + 0.45 * pattern_score)
            quad_area = float(cv2.contourArea(quad.astype(np.float32)))
            raw_candidates.append((quad, float(score), bbox, quad_area))

    if not raw_candidates:
        return []

    raw_candidates.sort(key=lambda item: item[1], reverse=True)
    deduped: list[tuple[np.ndarray, float, tuple[int, int, int, int], float]] = []
    for candidate in raw_candidates:
        _, _, bbox, _ = candidate
        if any(_bbox_iou(bbox, kept_bbox) > 0.9 for _, _, kept_bbox, _ in deduped):
            continue
        deduped.append(candidate)

    largest_area = max(item[3] for item in deduped)
    min_relative_area = largest_area * 0.02
    candidates = [item[:3] for item in deduped if item[3] >= min_relative_area]

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


def detect_boards(
    image_rgb: np.ndarray,
    target_size: int = BOARD_SIZE,
    max_boards: int = DEFAULT_MAX_BOARDS,
    iou_threshold: float = 0.25,
    reading_order: ReadingOrder = DEFAULT_READING_ORDER,
    warn_on_cap: bool = True,
) -> list[tuple[np.ndarray, np.ndarray | None]]:
    """Recorta os diagramas de uma página, numerados em `reading_order` (S-14).

    O padrão vem de `config.DEFAULT_READING_ORDER` para que GUI e exportação numerem os
    diagramas igual: o padrão daqui era `"row"` e a exportação passava `"column"`, então o
    `[Diagram "2"]` do PGN podia apontar para outra posição que a da tela.

    `warn_on_cap=False` para quem pede **um** tabuleiro de propósito -- refinar o recorte
    de um candidato já localizado, por exemplo. Ali o teto é o pedido, não um limite do
    usuário, e o aviso da Fase 5 mandava "aumente 'Max diagramas'" numa configuração que
    não tem efeito nenhum sobre essa chamada.
    """
    candidates = _extract_candidate_quads(image_rgb)
    top_score = candidates[0][1] if candidates else 0.0
    min_score = max(0.06, top_score * 0.25)
    selected: list[tuple[np.ndarray, float, tuple[int, int, int, int]]] = []
    dropped_by_cap: list[float] = []

    for candidate in candidates:
        _, score, bbox = candidate
        if score < min_score:
            continue
        if any(_bbox_iou(bbox, kept_bbox) > iou_threshold for _, _, kept_bbox in selected):
            continue
        if len(selected) >= max_boards:
            dropped_by_cap.append(score)
            continue
        selected.append(candidate)

    if dropped_by_cap and warn_on_cap:
        # O corte e por score, e o score nao ordena diagrama por posicao: numa grade 3x3 o
        # nono pode ser o do canto superior direito. Cortar em silencio fez exatamente isso
        # no "A Matter of Endgame Technique", e nada na tela dizia que faltava um.
        logger.warning(
            "max_boards=%d cortou %d candidato(s) que passaram no filtro de qualidade "
            "(scores %s contra %.4f do ultimo aceito). Se a pagina tem mais diagramas que "
            "isso, aumente 'Max diagramas'.",
            max_boards,
            len(dropped_by_cap),
            ", ".join(f"{score:.4f}" for score in dropped_by_cap[:4]),
            selected[-1][1] if selected else 0.0,
        )

    if not selected:
        return []

    _sort_selected_candidates(selected, reading_order)
    boards: list[tuple[np.ndarray, np.ndarray | None]] = []
    for quad, _, _ in selected:
        boards.append((warp_from_quad(image_rgb, quad, target_size=target_size), quad))
    return boards


def detect_board(image_rgb: np.ndarray, target_size: int = BOARD_SIZE) -> tuple[np.ndarray, np.ndarray | None]:
    boards = detect_boards(image_rgb=image_rgb, target_size=target_size, max_boards=1, warn_on_cap=False)
    if not boards:
        raise NoBoardDetectedError("Nenhum tabuleiro de xadrez foi detectado na imagem.")
    return boards[0]


def split_board_into_cells(board_rgb: np.ndarray) -> list[np.ndarray]:
    if board_rgb.shape[0] != BOARD_SIZE or board_rgb.shape[1] != BOARD_SIZE:
        board_rgb = cv2.resize(board_rgb, (BOARD_SIZE, BOARD_SIZE))

    cells: list[np.ndarray] = []
    for row in range(8):
        for col in range(8):
            y0 = row * CELL_SIZE
            y1 = (row + 1) * CELL_SIZE
            x0 = col * CELL_SIZE
            x1 = (col + 1) * CELL_SIZE
            cells.append(board_rgb[y0:y1, x0:x1])
    return cells
