from __future__ import annotations

import argparse
from pathlib import Path

from forge.desktop.diagnostics import log_event, log_exception
from forge.desktop.runtime import run_prompt
from forge.desktop.server import launch_desktop


def main() -> int:
    parser = argparse.ArgumentParser(description="FORGE Desktop")
    parser.add_argument("--headless-prompt", help="Run one prompt without opening the GUI")
    parser.add_argument("--operator", action="store_true", help="Use the operator brain in headless mode")
    parser.add_argument("--workspace", help="Workspace root for file and shell execution")
    parser.add_argument("--confirm", action="store_true", help="Allow real execution for high-risk or mutable steps")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    parser.add_argument("--output-path", help="Write the response text to a file")
    parser.add_argument("--host", default="127.0.0.1", help="Host address for desktop web app")
    parser.add_argument("--port", type=int, default=0, help="Port for desktop web app")
    parser.add_argument("--no-browser", action="store_true", help="Start the local server without opening a browser")
    args = parser.parse_args()
    log_event(
        "Desktop entrypoint args="
        f"headless={bool(args.headless_prompt)} operator={args.operator} "
        f"host={args.host} port={args.port} no_browser={args.no_browser}"
    )

    if args.headless_prompt:
        result = run_prompt(
            args.headless_prompt,
            use_operator=args.operator,
            workspace_root=args.workspace,
            confirmed=args.confirm,
            dry_run=args.dry_run,
        )
        if args.output_path:
            output_path = Path(args.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result, encoding="utf-8")
            log_event(f"Headless result written to {output_path}")
        else:
            print(result)
            log_event("Headless result printed to stdout")
        return 0

    launch_desktop(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log_exception("Desktop entrypoint crashed", exc)
        raise
