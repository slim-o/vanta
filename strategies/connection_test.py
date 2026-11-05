import os

from mt5_helper.general_functions import retryable_initialize
from mt5_helper.dotenv import load_dotenv


def main() -> int:
    loaded = load_dotenv()
    if loaded:
        print(f"Loaded .env: {loaded}")

    terminal_path = os.environ.get("MT5_TERMINAL_PATH", "").strip()
    if not terminal_path:
        raise SystemExit(
            "Set MT5_TERMINAL_PATH to your terminal64.exe path (and MT5_LOGIN/MT5_PASSWORD/MT5_SERVER)."
        )
    retryable_initialize(max_retries=5, delay_seconds=1, terminal_path=terminal_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

