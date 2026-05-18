from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import Iterable, Optional, Union

import chess
import chess.pgn


@dataclass
class StudyState:
    start_fen: str
    current_fen: str
    move_count: int
    can_undo: bool
    can_redo: bool


@dataclass
class StudyMoveEntry:
    label: str
    san: str
    ply: int
    path: str
    current: bool
    children: list["StudyMoveEntry"] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "san": self.san,
            "ply": self.ply,
            "path": self.path,
            "current": self.current,
            "children": [child.as_dict() for child in self.children],
        }


class StudyGame:
    """Study board state backed by python-chess PGN nodes.

    The PGN tree is the source of truth for the move list and variations. The
    board is always synchronized from the current PGN node, matching the model
    used by the reference chessboard template.
    """

    def __init__(self, start_fen: str = chess.STARTING_FEN) -> None:
        self._start_fen = ""
        self._board = chess.Board()
        self._game = chess.pgn.Game()
        self._current_node: chess.pgn.GameNode = self._game
        self.set_start_fen(start_fen)

    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def start_fen(self) -> str:
        return self._start_fen

    @property
    def cursor(self) -> int:
        return len(self._path_moves())

    @property
    def history_length(self) -> int:
        return len(self._path_moves())

    def set_start_fen(self, fen: str) -> None:
        board = chess.Board(fen)
        self._start_fen = board.fen()
        self._game = chess.pgn.Game()
        if self._start_fen != chess.STARTING_FEN:
            self._game.setup(board)
            self._game.headers["SetUp"] = "1"
            self._game.headers["FEN"] = self._start_fen
        self._current_node = self._game
        self._board = board.copy(stack=False)

    def state(self) -> StudyState:
        return StudyState(
            start_fen=self._start_fen,
            current_fen=self._board.fen(),
            move_count=self.cursor,
            can_undo=self._current_node.parent is not None,
            can_redo=bool(self._current_node.variations),
        )

    def legal_moves_from(self, square: int) -> list[chess.Move]:
        return [mv for mv in self._board.legal_moves if mv.from_square == square]

    def push_move(self, move: chess.Move) -> str:
        if move not in self._board.legal_moves:
            raise ValueError(f"Movimento ilegal: {move.uci()}")
        san = self._board.san(move)
        child = next((variation for variation in self._current_node.variations if variation.move == move), None)
        if child is None:
            child = self._current_node.add_variation(move)
        self._current_node = child
        self._sync_board_to_current_node()
        return san

    def undo(self) -> bool:
        if self._current_node.parent is None:
            return False
        self._current_node = self._current_node.parent
        self._sync_board_to_current_node()
        return True

    def redo(self) -> bool:
        if not self._current_node.variations:
            return False
        self._current_node = self._current_node.variations[0]
        self._sync_board_to_current_node()
        return True

    def clear_moves(self) -> None:
        self._game.variations.clear()
        self._current_node = self._game
        self._sync_board_to_current_node()

    def goto_ply(self, ply: int) -> bool:
        path = self._path_nodes()
        target = int(ply)
        if target >= len(path):
            path = self._mainline_nodes()
        target = max(0, min(target, len(path) - 1))
        if path[target] is self._current_node:
            return False
        self._current_node = path[target]
        self._sync_board_to_current_node()
        return True

    def last_move(self) -> Optional[chess.Move]:
        if self._current_node.move is None:
            return None
        return self._current_node.move

    def moves(self) -> list[chess.Move]:
        return self._path_moves()

    def all_moves(self) -> list[chess.Move]:
        return self._mainline_moves()

    def san_line(self) -> list[str]:
        board = chess.Board(self._start_fen)
        out: list[str] = []
        for move in self._path_moves():
            out.append(board.san(move))
            board.push(move)
        return out

    def current_path_key(self) -> str:
        moves = self._path_moves()
        return "|".join(move.uci() for move in moves) if moves else "0"

    def move_entries(self) -> list[StudyMoveEntry]:
        entries: list[StudyMoveEntry] = []
        self._append_position_entries(self._game, self._game.board(), [], entries, None)
        return entries

    def move_tree(self) -> list[dict[str, object]]:
        return [entry.as_dict() for entry in self.move_entries()]

    def goto_path(self, path_key: str) -> bool:
        target = self._find_node_by_path(self._game, path_key)
        if target is None or target is self._current_node:
            return False
        self._current_node = target
        self._sync_board_to_current_node()
        return True

    def current_variation_info(self) -> tuple[int, int]:
        parent = self._current_node.parent
        if parent is None:
            return (0, 0)
        count = len(parent.variations)
        index = parent.variations.index(self._current_node) if self._current_node in parent.variations else 0
        return (index + 1, count)

    def select_sibling_variation(self, offset: int) -> bool:
        parent = self._current_node.parent
        if parent is None or len(parent.variations) <= 1:
            return False
        current_idx = parent.variations.index(self._current_node)
        next_idx = (current_idx + int(offset)) % len(parent.variations)
        self._current_node = parent.variations[next_idx]
        self._sync_board_to_current_node()
        return True

    def load_pgn(self, pgn_text: str) -> dict[int, dict[str, str]]:
        stream = StringIO(pgn_text or "")
        game = chess.pgn.read_game(stream)
        if game is None:
            raise ValueError("PGN invalido ou vazio.")
        start_board = game.board()
        self._start_fen = start_board.fen()
        self._game = game
        self._current_node = self._deepest_mainline_node()
        self._sync_board_to_current_node()
        comments: dict[int, dict[str, str]] = {}
        root_comment = game.comment.strip()
        if root_comment:
            comments[0] = {"before": root_comment, "after": ""}
        node = game
        ply = 0
        while node.variations:
            node = node.variation(0)
            ply += 1
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
        move_comments: Optional[dict[object, dict[str, str]]] = None,
        include_all: bool = False,
    ) -> str:
        game = self._clone_game()
        now = date or datetime.now()
        game.headers["Event"] = event
        game.headers["Site"] = site
        game.headers["Date"] = now.strftime("%Y.%m.%d")
        game.headers["Round"] = "-"
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["Result"] = self._board.result(claim_draw=True) if self._board.is_game_over(claim_draw=True) else "*"

        comments: dict[Union[int, str], dict[str, str]] = {}
        if move_comments:
            for ply, values in move_comments.items():
                key: Union[int, str]
                try:
                    key = int(ply)
                except Exception:
                    key = str(ply)
                comments[key] = {
                    "before": str(values.get("before", "")).strip(),
                    "after": str(values.get("after", "")).strip(),
                }

        moves_to_export = self._mainline_moves() if include_all else self._path_moves()
        max_ply = len(moves_to_export)

        if comment_before.strip() or comment_after.strip():
            target_ply = self.cursor if comment_ply is None else max(0, min(int(comment_ply), max_ply))
            comments[target_ply] = {
                "before": comment_before.strip(),
                "after": comment_after.strip(),
            }

        def append_comment(target: chess.pgn.ChildNode | chess.pgn.Game, text: str) -> None:
            text = text.strip()
            if not text:
                return
            target.comment = text if not target.comment else f"{target.comment} {text}"

        def append_starting_comment(target: chess.pgn.ChildNode | chess.pgn.Game, text: str) -> None:
            text = text.strip()
            if not text:
                return
            if isinstance(target, chess.pgn.ChildNode):
                target.starting_comment = (
                    text if not target.starting_comment else f"{target.starting_comment} {text}"
                )
            else:
                append_comment(target, text)

        node: chess.pgn.GameNode = game
        root_comments = comments.get(0, {})
        append_comment(game, root_comments.get("before", ""))

        for ply_idx, move in enumerate(moves_to_export, start=1):
            ply_comments = comments.get(ply_idx, {})
            append_comment(node, ply_comments.get("before", ""))
            child = next((variation for variation in node.variations if variation.move == move), None)
            if child is None:
                child = node.add_variation(move)
            node = child
            append_comment(node, ply_comments.get("after", ""))

        for key, values in comments.items():
            if not isinstance(key, str) or key == "0":
                continue
            target = self._find_node_by_path(game, key)
            if target is None:
                continue
            append_starting_comment(target, values.get("before", ""))
            append_comment(target, values.get("after", ""))
        append_comment(game, root_comments.get("after", ""))
        return str(game)

    def load_moves(self, moves: Iterable[chess.Move]) -> None:
        self.clear_moves()
        for move in moves:
            self.push_move(move)

    def _path_nodes(self) -> list[chess.pgn.GameNode]:
        nodes: list[chess.pgn.GameNode] = []
        node: Optional[chess.pgn.GameNode] = self._current_node
        while node is not None:
            nodes.append(node)
            node = node.parent
        nodes.reverse()
        return nodes

    def _path_moves(self) -> list[chess.Move]:
        return [node.move for node in self._path_nodes()[1:] if node.move is not None]

    def _mainline_moves(self) -> list[chess.Move]:
        return list(self._game.mainline_moves())

    def _mainline_nodes(self) -> list[chess.pgn.GameNode]:
        nodes: list[chess.pgn.GameNode] = [self._game]
        node: chess.pgn.GameNode = self._game
        while node.variations:
            node = node.variation(0)
            nodes.append(node)
        return nodes

    def _deepest_mainline_node(self) -> chess.pgn.GameNode:
        node: chess.pgn.GameNode = self._game
        while node.variations:
            node = node.variation(0)
        return node

    def _clone_game(self) -> chess.pgn.Game:
        return chess.pgn.read_game(StringIO(str(self._game))) or chess.pgn.Game()

    def _sync_board_to_current_node(self) -> None:
        self._board = self._current_node.board()

    def _make_move_entry(
        self,
        node: chess.pgn.GameNode,
        board_before_move: chess.Board,
        parent_path: list[str],
    ) -> StudyMoveEntry:
        if node.move is None:
            raise ValueError("No move on root PGN node.")
        label = (
            f"{board_before_move.fullmove_number}."
            if board_before_move.turn == chess.WHITE
            else f"{board_before_move.fullmove_number}..."
        )
        path = [*parent_path, node.move.uci()]
        return StudyMoveEntry(
            label=label,
            san=board_before_move.san(node.move),
            ply=len(path),
            path="|".join(path),
            current=node is self._current_node,
        )

    def _append_move_line(
        self,
        node: chess.pgn.GameNode,
        board_before_move: chess.Board,
        parent_path: list[str],
        container: list[StudyMoveEntry],
        flatten_continuation: bool,
    ) -> StudyMoveEntry:
        if node.move is None:
            raise ValueError("No move on root PGN node.")
        entry = self._make_move_entry(node, board_before_move, parent_path)
        container.append(entry)

        board_after_move = board_before_move.copy(stack=False)
        board_after_move.push(node.move)
        path = [*parent_path, node.move.uci()]
        continuation_container = container if flatten_continuation else entry.children
        self._append_position_entries(node, board_after_move, path, continuation_container, entry)
        return entry

    def _append_position_entries(
        self,
        node: chess.pgn.GameNode,
        board: chess.Board,
        path: list[str],
        container: list[StudyMoveEntry],
        anchor: Optional[StudyMoveEntry],
    ) -> None:
        if not node.variations:
            return

        if anchor is None:
            self._append_move_line(node.variation(0), board, path, container, True)
            for variation in node.variations[1:]:
                self._append_move_line(variation, board, path, container, False)
            return

        for variation in node.variations[1:]:
            self._append_move_line(variation, board, path, anchor.children, False)
        self._append_move_line(node.variation(0), board, path, container, True)

    @staticmethod
    def _find_node_by_path(root: chess.pgn.GameNode, path_key: str) -> Optional[chess.pgn.GameNode]:
        if path_key == "0":
            return root
        node = root
        for uci in path_key.split("|"):
            child = next((variation for variation in node.variations if variation.move.uci() == uci), None)
            if child is None:
                return None
            node = child
        return node
