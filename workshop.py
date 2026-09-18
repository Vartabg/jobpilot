"""Read-only workshop tools: python -m jobpilot.workshop inspect packet.json."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

if __package__:
    from .core.workshop_packet import WorkshopPacket
    from .core.workshop_review import inspect_packet, render_packet
else:
    from core.workshop_packet import WorkshopPacket
    from core.workshop_review import inspect_packet, render_packet

MAX_PACKET_BYTES = 262144


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a self-reported career packet without network access or tracker changes."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="Print the portable JSON schema.")
    inspect = sub.add_parser(
        "inspect", help="Validate a browser export and identify missing evidence."
    )
    inspect.add_argument("packet", type=Path)
    inspect.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(WorkshopPacket.model_json_schema(), indent=2))
        return 0
    try:
        with args.packet.open("rb") as handle:
            raw = handle.read(MAX_PACKET_BYTES + 1)
        if len(raw) > MAX_PACKET_BYTES:
            raise ValueError("Packet exceeds the size limit.")
        packet = WorkshopPacket.model_validate_json(raw)
    except (OSError, ValueError, ValidationError):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_workshop_packet",
                        "message": "The packet could not be read or does not match the workshop format.",
                        "action": "Export a fresh JobPilot workshop JSON packet under 256 KiB and try again. Use the schema command to inspect required fields.",
                    },
                }
            )
        )
        return 2
    result = inspect_packet(packet)
    if args.format == "markdown":
        print(render_packet(result["packet"]), end="")
    else:
        print(
            json.dumps(
                {"ok": True, "operation": "workshop.inspect", "data": result}, indent=2
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
