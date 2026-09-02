"""File / directory picker built on textual's DirectoryTree."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Static

from pyterm.screens.base import ModalBase


class PathPicker(ModalBase):
    """Navigate the filesystem; dismisses with the selected path (str).

    * ``pick_files=True``  -> Enter on a file selects it
    * ``pick_files=False`` -> "选择当前目录" button selects the shown folder
    """

    def __init__(self, start: str = "", pick_files: bool = True) -> None:
        super().__init__()
        self._start = start or os.getcwd()
        self._pick_files = pick_files
        self._cur: str = self._start

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("选择文件 / 目录", id="picker-title")
            yield Static("", id="picker-path")
            with Vertical(id="picker-tree"):
                pass
            with Horizontal(id="picker-buttons"):
                yield Button("上一级", id="up")
                yield Button("选择当前目录", id="choose")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        if not self._pick_files:
            self.query_one("#choose").display = True
        else:
            self.query_one("#choose").display = False
        self._load(self._cur)

    # -- helpers ----------------------------------------------------------------------
    def _load(self, path: str) -> None:
        if not os.path.isdir(path):
            path = os.path.dirname(path) or path
        self._cur = os.path.abspath(path)
        self.query_one("#picker-path", Static).update(self._cur)
        box = self.query_one("#picker-tree", Vertical)
        box.remove_children()
        tree = DirectoryTree(self._cur)
        box.mount(tree)
        tree.focus()

    # -- events --------------------------------------------------------------------------
    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        if self._pick_files:
            self.dismiss(str(event.path))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._load(str(event.path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "up":
            parent = os.path.dirname(self._cur)
            if parent and parent != self._cur:
                self._load(parent)
        elif bid == "choose" and not self._pick_files:
            self.dismiss(self._cur)
        elif bid == "cancel":
            self.dismiss(None)
