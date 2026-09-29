"""Allow `python -m tools.migrate <args>`."""
from tools.migrate.migrate import main

if __name__ == "__main__":
    raise SystemExit(main())
