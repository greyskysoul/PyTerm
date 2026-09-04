"""UI smoke tests using textual's Pilot (headless)."""

from __future__ import annotations

import time

import pytest

from pyterm.app import PyTermApp
from pyterm.keys import hex_bytes_per_line


async def test_app_starts_and_renders_device_output():
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        # simulate device output
        app.model.feed_bytes(b"\x1b[32mhello device\r\nworld")
        app._view().mark_dirty()
        await pilot.pause(0.3)
        rendered = str(app._view().render())
        assert "hello device" in rendered
        assert "world" in rendered


async def test_ctrl_a_prefix_opens_menu():
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1

        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause()
        assert len(app.screen_stack) == 2, "main menu should be pushed"
        assert app.screen_stack[-1].__class__.__name__ == "MainMenuScreen"

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_prefix_cancel_with_escape():
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app._prefix is False


async def test_quit_confirm_can_be_cancelled():
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("x")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        # Esc is bound to "否" — the app must keep running
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app._running is True


async def test_tab_in_modal_steps_one_widget_at_a_time():
    """Regression: Tab used to move focus by TWO widgets inside modals."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr.query_one("#echo").focus()
        await pilot.pause()

        expected = ["echo", "wrap", "rx_cr", "rx_lf", "ts", "vt"]

        visited = []
        for _ in range(len(expected) + 1):
            visited.append(app.focused.id)
            await pilot.press("tab")
            await pilot.pause(0.02)
        # every checkbox must be reachable in strict order, no skips
        assert visited[: len(expected)] == expected


async def test_arrows_navigate_between_fields_in_modal():
    """Arrow keys move the focus through the fields of a dialog.

    Previously the arrow keys did nothing inside modals (unless a widget like
    an Input or DataTable happened to own them).
    """
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr.query_one("#echo").focus()
        await pilot.pause()

        visited = []
        for _ in range(8):
            visited.append(app.focused.id)
            await pilot.press("down")
            await pilot.pause(0.02)
        # 7 checkboxes first, then the first text field of the row below
        assert visited == ["echo", "wrap", "rx_cr", "rx_lf", "ts", "vt", "hex", "enter"]


async def test_checkbox_toggles_with_left_right_arrows():
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        cb = scr.query_one("#echo")
        cb.focus()
        await pilot.pause()

        before = cb.value
        await pilot.press("right")
        await pilot.pause(0.02)
        assert cb.value is not before, "right arrow should toggle a focused checkbox"
        await pilot.press("left")
        await pilot.pause(0.02)
        assert cb.value is before, "left arrow should toggle it back"


async def test_arrows_inside_input_do_not_move_focus():
    """While typing in an Input the arrows edit the text, not the focus."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        field = scr.query_one("#timeout")
        field.focus()
        field.value = "abcd"
        await pilot.pause()

        await pilot.press("left")
        await pilot.pause(0.02)
        assert app.focused is field, "left arrow must stay inside the input"
        # up/down still navigate (they have no text-editing meaning)
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused is not field


async def test_dropdown_arrows_navigate_and_enter_selects_value():
    """选项页下拉框：折叠时方向键用于移动字段焦点（不打开菜单），
    Enter 打开菜单、方向键高亮、Enter 确认并自动关闭。"""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        sel = scr.query_one("#enter")
        sel.focus()
        await pilot.pause()
        assert sel.value == "cr"

        # ←/→：同一行两个下拉框之间移动焦点
        await pilot.press("right")
        await pilot.pause(0.02)
        assert app.focused.id == "back"
        await pilot.press("left")
        await pilot.pause(0.02)
        assert app.focused.id == "enter"

        # ↓/↑：折叠时只移动焦点，不打开菜单
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "back"
        assert sel.has_class("-expanded") is False
        await pilot.press("up")
        await pilot.pause(0.02)
        assert app.focused.id == "enter"

        # Enter：打开菜单；Escape：原样关闭、焦点回到下拉框
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert sel.has_class("-expanded") is True
        assert type(app.focused).__name__ == "SelectOverlay"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.focused is sel
        assert sel.has_class("-expanded") is False

        # 再次打开，方向键高亮 “CR+LF”，Enter 确认选中
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert sel.value == "crlf"
        assert app.focused is sel


async def test_help_menu_arrows_select_and_enter_runs():
    """Main menu rows are selectable: arrows move, Enter runs the item."""
    from pyterm.screens.help import MainMenuScreen
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        assert app.focused.id == "menu-z", "first menu item should be focused"

        # navigate down to the "选项设置" row and activate it with Enter
        while app.focused.id != "menu-o":
            await pilot.press("down")
            await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert len(app.screen_stack) == 2
        assert isinstance(app.screen_stack[-1], OptionsScreen)


async def test_help_menu_letter_key_still_runs():
    """Pressing the function letter on the main menu runs it immediately."""
    from pyterm.screens.help import MainMenuScreen
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        await pilot.press("o")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen_stack[-1], OptionsScreen)


async def test_confirm_dialog_arrows_move_between_buttons():
    from pyterm.screens.base import ConfirmDialog

    app = PyTermApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyTerm 吗？"))
        await pilot.pause(0.2)
        assert app.focused.id == "yes"
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "no"


async def test_datatable_arrows_step_one_row_at_a_time():
    """Regression: arrow keys used to skip every other DataTable row."""
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        table = scr.query_one("#ports")
        for i in range(4):
            table.add_row(f"COM{i}", "fake device")
        table.focus()
        await pilot.pause()

        rows = []
        for _ in range(4):
            rows.append(table.cursor_row)
            await pilot.press("down")
            await pilot.pause(0.02)
        assert rows == [0, 1, 2, 3]
        assert len(table.rows) >= 4


async def test_connect_success_refreshes_status_bar():
    """Regression: successful connect called the non-existent
    ``app.refresh_status`` and crashed with AttributeError."""
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()

        # fake a successful port open
        def _fake_open(settings) -> None:
            return None

        app.open_serial = _fake_open  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr._devices = [("COM9", "fake device")]  # type: ignore[attr-defined]
        scr._selected = "COM9"  # type: ignore[attr-defined]

        # exercises the same code path as pressing Enter on a DataTable row
        scr._connect()
        await pilot.pause(0.2)

        assert len(app.screen_stack) == 1, "dialog should have dismissed after connect"


async def test_options_autofocuses_first_checkbox():
    """Entering the options dialog must focus the first item so the arrow
    keys work immediately (no need to Tab first)."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        assert app.focused.id == "echo"

        # and arrows work right away
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "wrap"


async def test_options_checkboxes_use_circle_markers():
    """Options checkboxes render a hollow circle when off and a solid
    circle when on instead of the default X marker."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        echo = scr.query_one("#echo")  # default: off
        assert "○" in str(echo.render())
        assert "●" not in str(echo.render())

        vt = scr.query_one("#vt")  # default send_vt_sequences=True -> on
        assert "●" in str(vt.render())

        await pilot.press("right")  # toggle echo
        await pilot.pause(0.05)
        assert "●" in str(echo.render())
        assert "○" not in str(echo.render())


async def test_connection_page_compact_and_right_aligned_buttons():
    """Connection inputs are single-line; buttons sit on their own row at the
    right; the old 断开 button was replaced by 返回."""
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        for field_id in ("baud", "bytesize", "parity", "stopbits", "flow"):
            assert scr.query_one(f"#{field_id}").region.height == 1

        assert len(scr.query("#disconnect")) == 0
        ids = [b.id for b in scr.query("Button")]
        assert ids == ["refresh", "connect", "cancel"]

        row = scr.query_one("#conn-buttons")
        last = scr.query_one("#cancel")
        # buttons are flush against the right edge of their row
        assert last.region.x + last.region.width == row.region.x + row.region.width

        # 返回 closes the dialog
        last.focus()
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1


# --------------------------------------------------------------------------- new behaviour


async def test_enter_thrice_without_port_shows_reminder():
    """Pressing Enter 3 times with no port connected pops a reminder."""
    app = PyTermApp()
    notes: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.notify = lambda message, *a, **k: notes.append(str(message))  # type: ignore[method-assign]
        await pilot.pause()
        assert app.is_connected() is False

        await pilot.press("enter")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.05)

        assert any("未连接端口" in n for n in notes)


async def test_hex_menu_toggle_mounts_and_removes_bar():
    """HEX off -> no hex widgets in the DOM at all (so combos keep working);
    HEX on -> the bottom bar is mounted; toggling off removes it again."""
    app = PyTermApp()
    mapper_calls: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert len(app.query("#hex-bar")) == 0  # not mounted in normal mode

        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is True
        assert len(app.query("#hex-bar")) == 1
        assert app.query_one("#hex-input").can_focus is True
        assert app.query_one("#hex-send").can_focus is True
        # enabling via the shortcut auto-focuses the hex editor
        assert app.focused.id == "hex-input"

        # typing now goes into the hex editor, never through the byte mapper
        orig_map = app.mapper.map
        app.mapper.map = lambda key, char: (mapper_calls.append(key), orig_map(key, char))[1]  # type: ignore[method-assign]
        await pilot.press("a")
        await pilot.press("b")
        await pilot.pause(0.1)
        assert mapper_calls == []
        assert app.query_one("#hex-input").text == "AB"

        # Ctrl+A prefix still works while the hex editor exists
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        await pilot.pause(0.1)

        # toggling off removes the bar again -> normal DOM restored
        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is False
        assert len(app.query("#hex-bar")) == 0


async def test_hex_editor_autoformats_and_rejects_invalid():
    """The hex editor adds a space after every byte and strips non-hex input."""
    app = PyTermApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        field = app.query_one("#hex-input")
        assert field.text == ""

        # mixed/illegal input is filtered, uppercase, spaced per byte
        field.text = "aabbGG 0d"
        await pilot.pause(0.1)
        assert field.text == "AA BB 0D"

        # a long input is wrapped into lines of bytes that fit the width
        field.text = "AA" * 30
        await pilot.pause(0.1)
        lines = field.text.split("\n")
        assert all(line == " ".join("AA" for _ in range(16)) for line in lines[:-1])
        assert len(lines) == 2  # 30 bytes -> 16 + 14


async def test_hex_receive_displays_hex_text():
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.cfg.hex_mode = True
        app._rx_to_terminal(b"\x41\x42\x0d")
        await pilot.pause(0.3)
        assert "41 42 0D" in str(app._view().render())


async def test_hex_receive_separates_rx_chunks():
    """Two separately received chunks must not merge their boundary bytes
    (last byte of chunk 1 and first byte of chunk 2 keep a space)."""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.cfg.hex_mode = True
        app._rx_to_terminal(b"\x41\x42")  # chunk 1 ends with 42
        await pilot.pause(0.05)
        app._rx_to_terminal(b"\x0d\x0a")  # chunk 2 starts with 0D
        await pilot.pause(0.3)
        text = str(app._view().render())
        assert "41 42 0D 0A" in text
        assert "420D" not in text


async def test_hex_receive_multiline_wraps_to_line_start():
    """RX chunks longer than one hex line wrap cleanly: the bytes-per-line
    adapts to the display width and each wrapped line must start at column 0
    (format_hex's LF separator is sent as CR+LF, otherwise a bare LF makes every
    subsequent line drift right like a staircase)."""
    from pyterm.config import AppConfig

    app = PyTermApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        per_line = hex_bytes_per_line(max(1, app.model.columns), max_bytes=32)
        app._rx_to_terminal(bytes(range(per_line + 1)))  # 一整行 + 1 字节
        await pilot.pause(0.3)
        rows = [
            "".join(c.data for c in row).rstrip() for row in app.model.screen_rows()
        ]
        nonempty = [r for r in rows if r]
        assert len(nonempty) >= 2
        assert nonempty[0] == " ".join(f"{b:02X}" for b in range(per_line))
        # 第二行必须从行首开始，前面不能有缩进（回归 \n -> \r\n 修复）
        assert nonempty[1].startswith(f"{per_line:02X}")
        assert nonempty[1] == nonempty[1].lstrip()


async def test_hex_receive_does_not_break_on_cr_lf_bytes():
    """HEX 接收时真实的 0A/0D 字节只是普通数据，显示为 "0A"/"0D"，
    不应像文本模式那样在换行字节处断行——只按字节数分组换行。"""
    from pyterm.config import AppConfig

    app = PyTermApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app._rx_to_terminal(b"AB\r\nCD")  # 含真实 CR/LF（0D 0A）
        await pilot.pause(0.3)
        rows = [
            "".join(c.data for c in row).rstrip() for row in app.model.screen_rows()
        ]
        nonempty = [r for r in rows if r]
        # 全部留在同一显示行内顺序显示，0D/0A 处没有产生额外断行
        assert nonempty == ["41 42 0D 0A 43 44"]


async def test_hex_receive_wraps_across_small_chunks():
    """连续到达的多个小块也要严格按“每行 N 字节”换行（与发送区一致的连续
    自动换行），而不是每个块各自排版、长期堆在同一行不换行。"""
    from pyterm.config import AppConfig

    app = PyTermApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        per_line = hex_bytes_per_line(max(1, app.model.columns), max_bytes=32)
        # 每块只发 2 字节，但累计超过 per_line 字节后必须发生换行
        chunk = b"\xaa\xbb"
        for _ in range(per_line // 2 + 1):
            app._rx_to_terminal(chunk)
        await pilot.pause(0.3)
        rows = [
            "".join(c.data for c in row).rstrip() for row in app.model.screen_rows()
        ]
        nonempty = [r for r in rows if r]
        assert nonempty[0] == " ".join("AA BB" for _ in range(per_line // 2))
        # 溢出部分从新一行第 0 列开始
        assert nonempty[1] == "AA BB"
        assert nonempty[1] == nonempty[1].lstrip()


async def test_hex_send_button_transmits_bytes():
    app = PyTermApp()
    sent: list[bytes] = []
    notes: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.notify = lambda message, *a, **k: notes.append(str(message))  # type: ignore[method-assign]
        app.is_connected = lambda: True  # type: ignore[method-assign]
        app.serial.write = lambda data: sent.append(bytes(data))  # type: ignore[method-assign]
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.1)
        assert app.cfg.hex_mode is True

        field = app.query_one("#hex-input")
        field.text = "AA 0D 7F"
        await pilot.pause(0.05)
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f"]
        # 发送后发送区内容被保留，可直接再次发送同一批数据
        assert field.text == "AA 0D 7F"
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f", b"\xaa\x0d\x7f"]

        # non-hex input is stripped by the editor -> empty warning, nothing sent
        field.text = "GG"
        await pilot.pause(0.05)
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f", b"\xaa\x0d\x7f"]
        assert any("输入字节" in n for n in notes)


async def test_idle_exit_when_no_bytes_received():
    app = PyTermApp(exit_idle=0.2)
    exited: list = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.exit = lambda *a, **k: exited.append(a)  # type: ignore[method-assign]
        await pilot.pause()
        app._last_rx = time.monotonic() - 5.0
        app._tick()
        assert exited, "idle watchdog should have triggered exit"

        # fresh data keeps the app alive
        app._last_rx = time.monotonic()
        app._tick()
        assert len(exited) == 1


# --------------------------------------------------------------------------- CLI arguments


def test_cli_short_params_removed_long_kept():
    from pyterm.app import _parse_args

    args = _parse_args(
        [
            "--port",
            "COM1",
            "--data-bits",
            "7",
            "--parity",
            "E",
            "--stop-bits",
            "1.5",
            "--flow",
            "rtscts",
        ]
    )
    assert args.data_bits == 7
    assert args.parity == "E"
    assert args.stop_bits == 1.5
    assert args.flow == "rtscts"

    with pytest.raises(SystemExit):
        _parse_args(["-d", "8"])  # short alias removed
    with pytest.raises(SystemExit):
        _parse_args(["-f", "rtscts"])  # short alias removed (needs -p now)


def test_cli_send_and_script_require_port():
    from pyterm.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["-s", "AT\r"])
    with pytest.raises(SystemExit):
        _parse_args(["-f", "boot.txt"])

    args = _parse_args(["-p", "COM3", "-s", "AT\r"])
    assert args.port == "COM3"
    assert args.send == "AT\r"

    args = _parse_args(["-p", "COM3", "-f", "boot.txt", "-b", "115200"])
    assert args.script == "boot.txt"


# --------------------------------------------------------------------------- virtual loopback


async def test_connection_page_lists_virtual_loopback():
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert any(dev == "LOOPBACK" for dev, _ in scr._devices)  # type: ignore[attr-defined]
        assert scr._devices[-1][0] == "LOOPBACK"  # type: ignore[attr-defined]


async def test_connect_virtual_loopback_routes_to_open_loopback():
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    calls: list = []

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.open_loopback = lambda: (calls.append(1), None)[1]  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr._selected = "LOOPBACK"  # type: ignore[attr-defined]
        scr._connect()  # type: ignore[attr-defined]
        await pilot.pause(0.2)
        assert calls == [1]
        assert len(app.screen_stack) == 1, "loopback connect should dismiss the dialog"


async def test_loopback_echoes_sent_bytes():
    """Virtual loopback: sent bytes come straight back as received text."""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        assert app.is_connected() is True
        assert "回环" in app._status_text()

        app.send_bytes(b"abc")
        await pilot.pause(0.3)
        assert "abc" in str(app._view().render())
        assert app._tx == 3
        assert app._rx == 3

        app.close_serial()
        await pilot.pause(0.05)
        assert app.is_connected() is False


async def test_clear_screen_resets_tx_rx_counters():
    """清屏 (Ctrl+A C) 同时复位状态栏的 TX/RX 字节计数器与显示内容。"""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None

        app.send_bytes(b"hello")
        await pilot.pause(0.3)
        assert app._tx == 5
        assert app._rx == 5
        assert "hello" in str(app._view().render())

        # Ctrl+A C 走清屏动作
        await pilot.press("ctrl+a")
        await pilot.press("c")
        await pilot.pause(0.3)

        assert app._tx == 0, "清屏后 TX 计数应归零"
        assert app._rx == 0, "清屏后 RX 计数应归零"
        assert not "".join(c.data for r in app.model.screen_rows() for c in r).strip()
        assert "TX 0" in app._status_text()
        assert "RX 0" in app._status_text()


async def test_loopback_echoes_cr_as_crlf():
    """A lone \r sent into the loopback comes back as \r\n (like a real
    terminal), so Enter starts a new line instead of overwriting it."""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)

        # 程序一启动就打印菜单快捷键提示（本地打印，不计入 RX 计数）
        rows0 = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert "按 Ctrl+A Z 打开功能菜单" in rows0[0]
        assert app._tx == 0 and app._rx == 0

        assert app.open_loopback() is None
        await pilot.pause(0.2)
        # 连接成功后打印“已连接”提示
        rows1 = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert "已连接" in rows1[1] and "虚拟回环" in rows1[1]

        # "a\rb" echoes as "a\r\nb" -> a then b on consecutive lines (no blank row)
        app.send_bytes(b"a\rb")
        await pilot.pause(0.3)
        rows = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert rows[2] == "a"
        assert rows[3] == "b"
        assert app._tx == 3
        assert app._rx == 4  # the lone \r is echoed as two bytes (\r\n)

        # an already-formed \r\n must not double up into two line breaks
        app.send_bytes(b"\r\n")
        await pilot.pause(0.3)
        assert app._tx == 5
        assert app._rx == 6  # \r\n echoed unchanged (2 bytes)


class _FakeSerial:
    """SerialManager 替身：记录打开状态与写入内容（供“切换连接”测试用）。"""

    def __init__(self) -> None:
        self._open = False
        self.written: list[bytes] = []

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, settings) -> str | None:
        self.settings = settings
        self._open = True
        return None

    def close(self) -> None:
        self._open = False

    def write(self, data: bytes) -> bool:
        if not self._open:
            return False
        self.written.append(data)
        return True

    def set_dtr(self, _value: bool) -> None:
        pass

    def set_rts(self, _value: bool) -> None:
        pass


async def test_switch_from_loopback_to_real_port(monkeypatch):
    """Regression: LOOPBACK → 真实串口 切换后必须退出回环模式。

    旧代码 open_serial() 不复位 _loopback：真实串口虽已打开，但发送仍被
    回环分支截走、状态栏仍显示“虚拟回环”，看起来就像“切换不成功”。
    """
    from pyterm.config import ConnectionSettings

    monkeypatch.setattr("pyterm.app.save_config", lambda cfg: None)

    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        fake = _FakeSerial()
        app.serial = fake  # type: ignore[assignment]

        assert app.open_loopback() is None
        assert app._loopback is True
        assert "虚拟回环" in app._status_text()

        # ConnectionScreen 对非 LOOPBACK 行调用的正是 open_serial
        settings = ConnectionSettings(port="COM42", baudrate=9600)
        assert app.open_serial(settings) is None

        assert app._loopback is False, "切到真实串口后应退出虚拟回环"
        assert fake.is_open
        assert "虚拟回环" not in app._status_text()
        assert "COM42" in app._status_text()

        # 发送必须到达真实串口，而不是被回环截走
        app.send_bytes(b"z")
        assert fake.written == [b"z"]


async def test_switch_real_to_real_keeps_sending_to_new_port(monkeypatch):
    """真实串口 → 另一真实串口：数据发往新端口。"""
    from pyterm.config import ConnectionSettings

    monkeypatch.setattr("pyterm.app.save_config", lambda cfg: None)

    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        fake = _FakeSerial()
        app.serial = fake  # type: ignore[assignment]

        assert app.open_serial(ConnectionSettings(port="COM1", baudrate=9600)) is None
        app.send_bytes(b"a")
        assert fake.written == [b"a"]

        assert app.open_serial(ConnectionSettings(port="COM2", baudrate=115200)) is None
        app.send_bytes(b"b")
        assert app._loopback is False
        assert fake.written == [b"a", b"b"]
        assert "COM2" in app._status_text()


def test_cli_exit_idle_accepts_float_and_rejects_nonpositive():
    from pyterm.app import _parse_args

    assert _parse_args(["-e", "0.5"]).exit_idle == 0.5
    assert _parse_args(["--exit-idle", "3"]).exit_idle == 3.0
    with pytest.raises(SystemExit):
        _parse_args(["-e", "0"])
    with pytest.raises(SystemExit):
        _parse_args(["-e", "-2"])


def test_cli_hex_flag_parsed():
    from pyterm.app import _parse_args

    assert _parse_args(["--hex"]).hex is True
    assert _parse_args([]).hex is False


async def test_hex_mode_enabled_at_startup_shows_bar():
    """A config with hex_mode=True (set by the --hex flag) starts in HEX mode."""
    from pyterm.config import AppConfig

    cfg = AppConfig(hex_mode=True)
    app = PyTermApp(cfg=cfg)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is True
        assert len(app.query("#hex-bar")) == 1
        assert app.query_one("#hex-input").can_focus is True

        # received bytes are rendered as hex right away
        app._rx_to_terminal(b"\x55\xaa")
        await pilot.pause(0.3)
        assert "55 AA" in str(app._view().render())


# --------------------------------------------------------------------------- small-window compact fallback


async def test_options_compact_on_small_window():
    """A too-small window switches to the simple full-width layout instead of
    the boxed multi-column form, and it stays keyboard-navigable."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(60, 16)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is True
        assert box.region.width == 60 and box.region.height == 16  # fills window

        # first field autofocused; arrows still move through the fields
        assert app.focused.id == "echo"
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "wrap"

        # every control of the dialog exists inside the simple layout
        for cid in (
            "echo", "wrap", "rx_cr", "rx_lf", "ts", "vt", "hex",
            "enter", "back", "decode", "timeout", "retries", "blocksize",
            "save", "cancel",
        ):
            assert len(scr.query(f"#{cid}")) == 1, f"missing #{cid} in compact mode"

        # 取消 closes the dialog
        scr.query_one("#cancel").focus()
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1


async def test_options_switches_layout_when_resized_and_keeps_edits():
    """Resizing across the threshold swaps rich <-> compact in place while
    keeping the values the user already typed/toggled."""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is False  # rich on a big window

        # make some edits before shrinking
        scr.query_one("#echo").focus()
        await pilot.press("right")
        await pilot.pause(0.05)
        scr.query_one("#timeout").value = "99"
        await pilot.pause(0.05)

        # shrink -> the compact layout keeps the values
        await pilot.resize_terminal(58, 15)
        await pilot.pause(0.4)
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is True
        assert scr.query_one("#echo").value is True
        assert scr.query_one("#timeout").value == "99"

        # grow back -> the rich layout keeps the values
        await pilot.resize_terminal(100, 32)
        await pilot.pause(0.4)
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is False
        assert scr.query_one("#echo").value is True
        assert scr.query_one("#timeout").value == "99"


async def test_connection_compact_on_small_window_and_connect():
    """Small-window connection page replaces the port table with a dropdown
    (LOOPBACK still offered) and connecting to it still works."""
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    calls: list = []

    async with app.run_test(size=(58, 15)) as pilot:
        await pilot.pause(0.2)
        app.open_loopback = lambda: (calls.append(1), None)[1]  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#conn-box")
        assert box.has_class("compact") is True
        assert len(scr.query("#ports")) == 0  # no DataTable in simple mode
        select = scr.query_one("#port-sel")
        assert app.focused is select
        assert any(dev == "LOOPBACK" for dev, _ in scr._devices)  # type: ignore[attr-defined]

        # the dropdown is filled with the detected ports (preselected)
        names = [dev for dev, _ in scr._devices]  # type: ignore[attr-defined]
        assert "LOOPBACK" in names
        assert str(select.value) in names

        # connect via the virtual loopback (same code path as rich mode)
        scr._selected = "LOOPBACK"  # type: ignore[attr-defined]
        scr._connect()  # type: ignore[attr-defined]
        await pilot.pause(0.2)
        assert calls == [1]
        assert len(app.screen_stack) == 1, "dialog dismissed after connect"


# --------------------------------------------------------------------------- 简洁模式自动切换阈值


async def test_connection_compact_threshold_is_30_rows():
    """连接页富布局在高度 <30 时自动切换为简洁模式（30 行及以上保持富布局）。"""
    from pyterm.screens.connection import ConnectionScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 29)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#conn-box").has_class("compact") is True
        await pilot.resize_terminal(100, 30)
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#conn-box").has_class("compact") is False


async def test_options_compact_threshold_is_27_rows():
    """选项页富布局在高度 <27 时自动切换为简洁模式（27 行及以上保持富布局）。"""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 26)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#options-box").has_class("compact") is True
        await pilot.resize_terminal(100, 27)
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#options-box").has_class("compact") is False


async def test_compact_controls_start_at_the_same_column():
    """简洁模式下输入框与下拉框从同一列开始且等宽，不再参差不齐。"""
    from pyterm.screens.options import OptionsScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        assert scr.query_one("#options-box").has_class("compact") is True
        xs = [scr.query_one(f"#{cid}").region.x for cid in ("enter", "back", "decode", "timeout", "retries", "blocksize")]
        widths = [scr.query_one(f"#{cid}").region.width for cid in ("enter", "back", "decode", "timeout", "retries", "blocksize")]
        assert len(set(xs)) == 1, f"控件左缘未对齐: {xs}"
        assert len(set(widths)) == 1, f"控件宽度不一致: {widths}"


async def test_main_menu_compact_threshold_is_30_rows():
    """功能菜单（含 about 两行与左下“返回”按钮）富布局在高度 <30 时自动切换
    为简洁模式（30 行及以上保持富布局）。"""
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 29)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.3)
        assert app.screen_stack[-1].query_one("#help-box").has_class("compact") is True
        await pilot.resize_terminal(100, 30)
        await pilot.pause(0.3)
        assert app.screen_stack[-1].query_one("#help-box").has_class("compact") is False


async def test_notifications_cleared_by_any_key():
    """未连接端口 / HEX 模式等 toast 提示放在左侧显示，按下任意键即关闭。"""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        app.notify("未连接端口：请按 Ctrl+A P 连接后再试", timeout=60)
        await pilot.pause(0.1)
        assert len(app._notifications) == 1
        await pilot.press("a")
        await pilot.pause(0.2)
        assert len(app._notifications) == 0, "任意键应关闭 toast 提示"


async def test_toast_renders_at_bottom_left():
    """toast（未连接端口 / HEX 模式等）在屏幕左下角显示（ToastHolder 左对齐）。"""
    app = PyTermApp()
    async with app.run_test(size=(100, 28), notifications=True) as pilot:
        await pilot.pause(0.3)
        app.notify("未连接端口：请按 Ctrl+A P 连接后再试", severity="warning", timeout=60)
        await pilot.pause(0.5)
        toast = next(w for w in app.screen.walk_children() if type(w).__name__ == "Toast")
        # 左对齐：toast 左缘靠近屏幕左缘；位于屏幕底部区域
        assert toast.region.x <= 2
        assert toast.region.y >= 20
        # 任意键关闭
        await pilot.press("a")
        await pilot.pause(0.2)
        assert len(app._notifications) == 0


# --------------------------------------------------------------------------- 左下角“菜单”按钮 + 启动/连接提示


async def test_main_screen_menu_button_bottom_left_opens_menu():
    """主界面左下角的“菜单”按钮；点击打开同一功能菜单，
    且按钮不抢占键盘焦点（can_focus=False）。"""
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        btn = app.query_one("#menu-btn")
        assert btn.can_focus is False  # 只响应鼠标，不让按键焦点离开终端
        assert "菜单" in str(btn.render())
        bottom = app.query_one("#bottom")
        # 位于最底行、紧贴左缘
        assert bottom.region.y + bottom.region.height == app.size.height
        assert btn.region.y == bottom.region.y and btn.region.height == 1
        assert btn.region.x == bottom.region.x
        # 状态栏不再重复显示“Ctrl+A Z”文字提示（交给按钮 + 启动首行提示）
        assert "Ctrl+A Z" not in app._status_text()

        # 点击打开主菜单（与 Ctrl+A Z 等价）
        await pilot.click("#menu-btn")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen_stack[-1], MainMenuScreen)


async def test_startup_hint_shown_without_connection():
    """程序一启动（无论是否连接端口）就在终端首行显示橙/粗体的菜单快捷键提示。"""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        assert app.is_connected() is False  # 没有连接端口
        row = app.model.screen_rows()[0]
        text = "".join(c.data for c in row).rstrip()
        assert "按 Ctrl+A Z 打开功能菜单" in text
        c = next(x for x in row if x.data.strip())
        assert c.bold is True and c.fg == "ffa500"  # 橙色加粗
        assert app._rx == 0 and app._tx == 0  # 本地提示不是串口收发


async def test_connect_hint_shown_in_orange_bold():
    """连接上端口后在屏幕上打印“已连接”提示（橙/粗体），每次连接都会打印。"""
    app = PyTermApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)
        assert app.open_loopback() is None
        await pilot.pause(0.2)

        rows = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        # 第 0 行是启动菜单提示，第 1 行是“已连接”提示
        assert "按 Ctrl+A Z 打开功能菜单" in rows[0]
        assert rows[1].startswith("已连接 虚拟回环")
        assert "直接键入即发送" in rows[1]
        line = app.model.screen_rows()[1]
        c = next(x for x in line if x.data.strip())
        assert c.bold is True and c.fg == "ffa500"
        assert app._rx == 0 and app._tx == 0

        # 清屏后再次连接仍会打印“已连接”提示
        app.model.clear()
        assert app.open_loopback() is None
        await pilot.pause(0.2)
        text = "".join(c.data for r in app.model.screen_rows() for c in r)
        assert "已连接" in text


# --------------------------------------------------------------------------- 功能菜单底部 about / 文案


async def test_main_menu_shows_about_and_clean_copy():
    """菜单底部显示版本/作者/GitHub，条目去掉冗余括号说明（仅保留 YMODEM）。"""
    from pyterm import PROJECT_AUTHOR, PROJECT_URL, __version__
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert str(scr.query_one("#help-repo").render()) == PROJECT_URL
        about = str(scr.query_one("#help-about").render())
        assert __version__ in about and PROJECT_AUTHOR in about
        # 不再出现冗余括号说明；(YMODEM) 仅在 发送/接收 两项保留
        plain = ("z", "c", "l", "h", "p", "o", "x")
        for key in plain:
            label = str(scr.query_one(f"#menu-{key}").render())
            assert "（" not in label and "(" not in label and ")" not in label
        for key in ("s", "r"):
            label = str(scr.query_one(f"#menu-{key}").render())
            assert "（" not in label and "YMODEM" in label


async def test_main_menu_back_button_bottom_left_closes():
    """菜单页面左下角有“返回”按钮，点击后回到主界面。"""
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        back = scr.query_one("#menu-back")
        assert "返回" in str(back.render())
        box = scr.query_one("#help-box")
        repo = scr.query_one("#help-repo")
        # 位于菜单框底部、与底部文案同一左缘（左下角）
        assert back.region.y >= repo.region.y
        assert back.region.x == repo.region.x
        assert back.region.right <= box.region.right

        await pilot.click("#menu-back")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 1, "返回应关闭功能菜单"


# --------------------------------------------------------------------------- --bare CLI


def test_cli_bare_requires_port():
    """--bare must specify a port: it is a headless bridge, not a UI."""
    from pyterm.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--bare"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-b", "115200"])


def test_cli_bare_rejects_interactive_startup_options():
    """--bare has no UI, so -s/-f/-e/--hex (interactive startup) are refused."""
    from pyterm.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "--hex"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "-s", "AT\r"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "-e", "5"])


def test_cli_bare_accepts_port_and_baud():
    from pyterm.app import _parse_args

    args = _parse_args(["--bare", "-p", "COM3", "-b", "115200"])
    assert args.bare is True
    assert args.port == "COM3"
    assert args.baud == 115200


# --------------------------------------------------------------------------- main menu / exit dialog small-window


async def test_help_menu_stays_boxed_on_large_window():
    """The normal Ctrl+A Z menu keeps its centred boxed layout on a big-enough
    terminal (no `compact` class)."""
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert not scr.query_one("#help-box").has_class("compact")


async def test_help_menu_compact_on_small_window_stays_usable():
    """Regression: the Ctrl+A Z menu overflowed tiny terminals.  Below the
    threshold the root toggles `compact`: it fills the screen, fits inside it,
    and every item is still reachable with the arrow keys."""
    from pyterm.screens.help import MainMenuScreen

    app = PyTermApp()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        root = scr.query_one("#help-box")
        assert root.has_class("compact")
        # the box must lie fully inside the terminal
        assert root.region.x >= 0 and root.region.y >= 0
        assert root.region.right <= scr.size.width
        assert root.region.bottom <= scr.size.height
        # first item focused; arrows reach the last one (menu scrolls into view)
        assert app.focused.id == "menu-z"
        for _ in range(8):
            await pilot.press("down")
            await pilot.pause(0.01)
        assert app.focused.id == "menu-x"

        # 再往下可聚焦到左下角“返回”按钮，Enter 关闭菜单
        await pilot.press("down")
        await pilot.pause(0.01)
        assert app.focused.id == "menu-back"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1


async def test_confirm_dialog_stays_boxed_on_large_window():
    """The exit confirmation keeps its boxed layout when the terminal is big."""
    from pyterm.screens.base import ConfirmDialog

    app = PyTermApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyTerm 吗？"))
        await pilot.pause(0.2)
        assert not app.screen_stack[-1].query_one("#confirm").has_class("compact")


async def test_confirm_dialog_compact_on_small_window():
    """Regression: the exit dialog (fixed width 54) overflowed narrow windows.
    On a small terminal it toggles `compact`, stays inside the screen, and both
    buttons remain usable."""
    from pyterm.screens.base import ConfirmDialog

    app = PyTermApp()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyTerm 吗？"))
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        root = scr.query_one("#confirm")
        assert root.has_class("compact")
        assert root.region.x >= 0 and root.region.right <= scr.size.width
        assert root.region.y >= 0 and root.region.bottom <= scr.size.height
        assert app.focused.id == "yes"
        await pilot.press("right")
        await pilot.pause(0.02)
        assert app.focused.id == "no"


# --------------------------------------------------------------------------- minimum terminal size


async def test_app_does_not_exit_on_usable_window():
    """A normal-sized terminal never trips the too-small guard."""
    app = PyTermApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        assert app._too_small is False
        assert app._running is True


async def test_app_exits_when_terminal_too_small():
    """When the terminal is far too small to be usable, the app stops instead
    of rendering a broken interface."""
    from pyterm.app import MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS

    app = PyTermApp()
    async with app.run_test(
        size=(MIN_TERMINAL_COLS - 5, MIN_TERMINAL_ROWS - 1)
    ) as pilot:
        await pilot.pause(0.3)
        assert app._too_small is True
        assert app._running is False


def test_too_small_message_lists_required_size():
    from pyterm.app import MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS, _too_small_message

    msg = _too_small_message(10, 3)
    assert "10" in msg and "3" in msg
    assert str(MIN_TERMINAL_COLS) in msg
    assert str(MIN_TERMINAL_ROWS) in msg
    assert "太小" in msg


def test_cli_prints_hint_and_returns_1_when_terminal_too_small(monkeypatch, capsys):
    """main() checks the terminal before launching the TUI: too small -> a hint
    on stderr and exit code 1, without entering the (unusable) interface."""
    import os

    import pyterm.app as appmod

    monkeypatch.setattr(
        appmod.shutil, "get_terminal_size", lambda *a, **k: os.terminal_size((10, 3))
    )
    rc = appmod.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "太小" in err
