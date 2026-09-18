"""Only used by acceptance tests; no production fixture or fake-results mode."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import uvicorn

from product import api
from product.models import Listing, SearchResult, SourceResult

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=3182)
parser.add_argument("--data-dir", type=Path, required=True)
args = parser.parse_args()
job = Listing(
    id="fixture-role",
    company="Example Co",
    title="Support technician",
    url="https://example.test/jobs/one",
    provider="ashby",
    board="example",
    location="Austin, TX",
    description="Help people troubleshoot equipment.\nRequired: clear communication and Python.\nMust have five years of industry experience.",
)
api.scan_boards = lambda boards: SearchResult(
    jobs=[job],
    sources=[
        SourceResult(provider="ashby", slug="example", count=1),
        SourceResult(
            provider="lever",
            slug="offline",
            error="Board unavailable. Try again later.",
        ),
    ],
)
api.fetch_board = lambda board: [job]
uvicorn.run(
    api.create_app(args.data_dir), host="127.0.0.1", port=args.port, access_log=False
)
