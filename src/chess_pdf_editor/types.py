from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

Rect = tuple[float, float, float, float]


@dataclass
class OcrBoardResult:
    fen: str
    xc: float
    yc: float
    width: float
    height: float
    confidence: Optional[float] = None


@dataclass
class OcrPrediction:
    request_id: str
    status: int
    message: Optional[str]
    results: list[OcrBoardResult]


@dataclass
class OverlayOperation:
    page_num: int
    rect_pdf: Rect
    fen: str
    side_to_move: str = "w"
    fullmove_number: int = 1
    source: str = "manual"
    confidence: Optional[float] = None
    whiteout_padding_pt: float = 0.5
    whiteout_padding_left_pt: float = 0.5
    whiteout_padding_top_pt: float = 0.5
    whiteout_padding_right_pt: float = 0.5
    whiteout_padding_bottom_pt: float = 0.5
    border_width_pt: float = 0.0


@dataclass
class EraseOperation:
    page_num: int
    rect_pdf: Rect


@dataclass
class StudyPosition:
    page_num: int
    rect_pdf: Rect
    fen: str
    side_to_move: str = "w"
    fullmove_number: int = 1
    pgn: str = ""
    comment_before: str = ""
    comment_after: str = ""
    move_comments: dict[str, dict[str, str]] = field(default_factory=dict)
    note: str = ""
