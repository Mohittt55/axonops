"""axonops CLI — start the engine and API server."""
from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="axonops",
        description="AxonOps — Neural Infrastructure Engine",
    )
    parser.add_argument("--host",  default="0.0.0.0", help="API host (default: 0.0.0.0)")
    parser.add_argument("--port",  default=8000, type=int, help="API port (default: 8000)")
    parser.add_argument("--tick",  default=10,   type=int, help="Collection interval in seconds")
    parser.add_argument("--env",   default="default", help="Environment name")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG","INFO","WARNING","ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(name)-24s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        import uvicorn
        from .api.server import create_app
        from .engine.loop import EngineConfig

        cfg             = EngineConfig()
        cfg.tick_interval = args.tick
        cfg.environment   = args.env

        app = create_app(cfg)
        print("\n  AxonOps v1.0.0  —  Neural Infrastructure Engine")
        print(f"  API  →  http://{args.host}:{args.port}")
        print(f"  Docs →  http://{args.host}:{args.port}/docs")
        print(f"  WS   →  ws://{args.host}:{args.port}/ws\n")

        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    except KeyboardInterrupt:
        print("\nAxonOps stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
