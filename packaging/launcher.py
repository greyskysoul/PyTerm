"""PyInstaller entry point: ``pyterm`` console binary."""

from pyterm.app import main

if __name__ == "__main__":
    raise SystemExit(main())
