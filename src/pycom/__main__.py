"""Allow `python -m pycom`."""

from pycom.app import main

if __name__ == "__main__":
    raise SystemExit(main())
