"""UI smoke tests using textual's Pilot (headless)."""

from __future__ import annotations

from pyterm.app import PyTermApp


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
