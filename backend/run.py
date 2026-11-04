"""起動スクリプト。

    python run.py            # http://127.0.0.1:8765 で起動

127.0.0.1 以外にはバインドしない（LAN・外部からのアクセス禁止）。
"""

from __future__ import annotations

import os

import uvicorn

from app.main import create_app

HOST = "127.0.0.1"


def main() -> None:
    port = int(os.environ.get("TEACHER_AGENT_PORT", "8765"))
    print(f"Teacher Agent: http://{HOST}:{port}")
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
