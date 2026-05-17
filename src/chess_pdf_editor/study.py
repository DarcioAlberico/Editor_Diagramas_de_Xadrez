from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Iterable, Optional

import chess
import chess.pgn


@dataclass
class StudyState:
    start_fen: str
    current_fen: str
    move_count: int
    can_undo: bool
    can_redo: bool


class StudyGame:
    def __init__(self, start_fen: str = chess.STARTING_FEN) -> None:
        self._start_fen = ""
        self._board = chess.Board()
        self._history: list[chess.Move] = []
        self._cursor = 0
        self.set_start_fen(start_fen)

    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def start_fen(self) -> str:
        return self._start_fen

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def history_length(self) -> int:
        return len(self._history)

    def set_start_fen(self, fen: str) -> None:
        board = chess.Board(fen)
        self._start_fen = board.fen()
        self._history = []
        self._cursor = 0
        self._board = board.copy(stack=False)

    def state(self) -> StudyState:
        return StudyState(
            start_fen=self._start_fen,
            current_fen=self._board.fen(),
            move_count=self._cursor,
            can_undo=self._cursor > 0,
            can_redo=self._cursor < len(self._history),
        )

    def legal_moves_from(self, square: int) -> list[chess.Move]:
        return [mv for mv in self._board.legal_moves if mv.from_square == square]

    def push_move(self, move: chess.Move) -> str:
        if move not in self._board.legal_moves:
            raise ValueError(f"Movimento ilegal: {move.uci()}")
        san = self._board.san(move)
        if self._cursor < len(self._history):
            self._history = self._history[: self._cursor]
        self._history.append(move)
        self._cursor += 1
        self._board.push(move)
        return san

    def undo(self) -> bool:
        if self._cursor <= 0:
            return False
        self._cursor -= 1
        self._rebuild()
        return True

    def redo(self) -> bool:
        if self._cursor >= len(self._history):
            return False
        self._cursor += 1
        self._rebuild()
        return True

    def clear_moves(self) -> None:
        self._history = []
        self._cursor = 0
        self._rebuild()

    def goto_ply(self, ply: int) -> bool:
        target = max(0, min(int(ply), len(self._history)))
        if target == self._cursor:
            return False
        self._cursor = target
        self._rebuild()
        return True

    def last_move(self) -> Optional[chess.Move]:
        if self._cursor <= 0:
            return None
        return self._history[self._cursor - 1]

    def moves(self) -> list[chess.Move]:
        return list(self._history[: self._cursor])

    def all_moves(self) -> list[chess.Move]:
        return list(self._history)

    def san_line(self) -> list[str]:
        board = chess.Board(self._start_fen)
        out: list[str] = []
        for move in self._history:
            out.append(board.san(move))
            board.push(move)
        return out

    def load_pgn(self, pgn_text: str) -> dict[int, dict[str, str]]:
        stream = StringIO(pgn_text or "")
        game = chess.pgn.read_game(stream)
        if game is None:
            raise ValueError("PGN invalido ou vazio.")
        start_board = game.board()
        self.set_start_fen(start_board.fen())
        comments: dict[int, dict[str, str]] = {}
        root_comment = game.comment.strip()
        if root_comment:
            comments[0] = {"before": root_comment, "after": ""}
        node = game
        ply = 0
        while node.variations:
            node = node.variation(0)
            ply += 1
            self.push_move(node.move)
            comment = node.comment.strip()
            if comment:
                comments[ply] = {"before": "", "after": comment}
        return comments

    def to_pgn(
        self,
        event: str = "Study Session",
        site: str = "Chess PDF Editor",
        white: str = "White",
        black: str = "Black",
        date: Optional[datetime] = None,
        comment_before: str = "",
        comment_after: str = "",
        comment_ply: Optional[int] = None,
        move_comments: Optional[dict[int, dict[str, str]]] = None,
        include_all: bool = False,
    ) -> str:
        game = chess.pgn.Game()
        now = date or datetime.now()
        game.headers["Event"] = event
        game.headers["Site"] = site
        game.headers["Date"] = now.strftime("%Y.%m.%d")
        game.headers["Round"] = "-"
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["Result"] = self._board.result(claim_draw=True) if self._board.is_game_over(claim_draw=True) else "*"

        if self._start_fen != chess.STARTING_FEN:
            game.setup(chess.Board(self._start_fen))
            game.headers["SetUp"] = "1"
            game.headers["FEN"] = self._start_fen

        comments: dict[int, dict[str, str]] = {}
        if move_comments:
            for ply, values in move_comments.items():
                comments[int(ply)] = {
                    "before": str(values.get("before", "")).strip(),
                    "after": str(values.get("after", "")).strip(),
                }

        moves_to_export = self._history if include_all else self._history[: self._cursor]
        max_ply = len(moves_to_export)

        if comment_before.strip() or comment_after.strip():
            target_ply = self._cursor if comment_ply is None else max(0, min(int(comment_ply), max_ply))
            comments[target_ply] = {
                "before": comment_before.strip(),
                "after": comment_after.strip(),
            }

        def append_comment(target: chess.pgn.ChildNode | chess.pgn.Game, text: str) -> None:
            text = text.strip()
            if not text:
                return
            target.comment = text if not target.comment else f"{target.comment} {text}"

        node = game
        root_comments = comments.get(0, {})
        append_comment(game, root_comments.get("before", ""))

        for ply_idx, move in enumerate(moves_to_export, start=1):
            ply_comments = comments.get(ply_idx, {})
            append_comment(node, ply_comments.get("before", ""))
            node = node.add_variation(move)
            append_comment(node, ply_comments.get("after", ""))
        append_comment(game, root_comments.get("after", ""))
        return str(game)

    def load_moves(self, moves: Iterable[chess.Move]) -> None:
        self._history = []
        self._cursor = 0
        self._rebuild()
        for move in moves:
            self.push_move(move)

    def _rebuild(self) -> None:
        board = chess.Board(self._start_fen)
        for move in self._history[: self._cursor]:
            board.push(move)
        self._board = board
