"""
axonops_core.cli
~~~~~~~~~~~~~~~~
axonops CLI — start the engine, check status, tail logs.
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="axonops",
        description="AxonOps — Neural Infrastructure Engine",
    )
    sub = parser.add_subparsers(dest="command")

    # axonops start
    start_p = sub.add_parser("start", help="Start the AxonOps engine + API server")
    start_p.add_argument("--host", default="0.0.0.0")
    start_p.add_argument("--port", type=int, default=8000)
    start_p.add_argument("--reload", action="store_true")

    # axonops status
    sub.add_parser("status", help="Show engine status")

    # axonops logs
    logs_p = sub.add_parser("logs", help="Tail live decision log")
    logs_p.add_argument("--url", default="ws://localhost:8000/ws/events")

    args = parser.parse_args()

    if args.command == "start":
        import uvicorn
        uvicorn.run(
            "axonops_core.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )

    elif args.command == "status":
        import httpx
        try:
            r = httpx.get("http://localhost:8000/api/status", timeout=3)
            import json, pprint
            pprint.pprint(r.json())
        except Exception as e:
            print(f"Could not reach AxonOps API: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "logs":
        import asyncio, json
        import websockets

        async def tail(url: str) -> None:
            print(f"Connecting to {url} ...")
            async with websockets.connect(url) as ws:
                print("Connected. Listening for events (Ctrl+C to stop)\n")
                async for msg in ws:
                    event = json.loads(msg)
                    etype = event.get("type", "?")
                    ts    = event.get("ts", "")
                    if etype == "decision":
                        print(f"[{ts}] DECISION  {event.get('action_type')} → {event.get('target')} "
                              f"confidence={event.get('confidence', 0):.0%} tier={event.get('tier')}")
                    elif etype == "action_executed":
                        ok = "✓" if event.get("succeeded") else "✗"
                        print(f"[{ts}] EXECUTED {ok} {event.get('action_type')} → {event.get('target')} "
                              f"({event.get('message', '')})")
                    elif etype == "approval_required":
                        print(f"[{ts}] APPROVAL  {event.get('action_id')} — {event.get('reason')}")
                    elif etype == "metrics":
                        snap = event.get("snapshot", {})
                        cpu  = snap.get("cpu_percent", snap.get("aws_asg_cpu_percent", "?"))
                        mem  = snap.get("mem_percent", "?")
                        print(f"[{ts}] METRICS   cpu={cpu}% mem={mem}%")

        try:
            asyncio.run(tail(args.url))
        except KeyboardInterrupt:
            print("\nDisconnected.")

    else:
        parser.print_help()
