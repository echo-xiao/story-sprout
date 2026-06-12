"""MongoDB MCP client.

Integrates MongoDB's official MCP server (`mongodb-mcp-server`) over stdio,
satisfying the hackathon requirement to integrate a Partner Entity's MCP
server. The pipeline reads preprocess data through the Model Context
Protocol instead of talking to the database driver directly.

Every call is best-effort: on any failure (Node/npx missing, MCP error,
unparseable payload) the caller falls back to the pymongo layer or local
files, so the demo never hard-fails on the MCP path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# The MongoDB MCP server wraps returned documents in guard tags to defend
# against prompt injection. NOTE: the server's WARNING preamble ALSO names the
# same tags ("between the <tag> and </tag> tags"), so a naive first-match picks
# up a decoy whose body is just " and ". We scan all tag pairs and take the
# first whose body actually parses as JSON.
_TAG_RE = re.compile(
    r"<untrusted-user-data-[0-9a-fA-F-]+>\s*(.*?)\s*</untrusted-user-data-[0-9a-fA-F-]+>",
    re.DOTALL,
)


def _server_params(read_only: bool = True):
    """Build stdio launch params for the official MongoDB MCP server.

    read_only=True (default) launches with --readOnly for the read path;
    pass read_only=False for the write path (consistency-hub updates).
    """
    from mcp import StdioServerParameters
    from src.config import MONGODB_URI

    env = os.environ.copy()
    env["MDB_MCP_CONNECTION_STRING"] = MONGODB_URI
    # No "@latest" — use the globally pre-installed version (Dockerfile) and
    # avoid an npm-registry version check on every launch.
    args = ["-y", "mongodb-mcp-server"]
    if read_only:
        args.append("--readOnly")
    return StdioServerParameters(command="npx", args=args, env=env)


def _parse_find_result(text: str) -> list[dict]:
    """Extract the document array from the MCP `find` tool's text payload."""
    for body in _TAG_RE.findall(text or ""):
        body = body.strip()
        if not body or body[0] not in "[{":
            continue
        try:
            docs = json.loads(body)
        except Exception:
            continue
        if isinstance(docs, list):
            return docs
        if isinstance(docs, dict):
            return [docs]
    return []


async def _load_files_async(book_id: str, names: list[str]) -> dict[str, Any]:
    """One MCP session: fetch each preprocess_files document for `book_id`."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from src.config import MONGODB_DB

    out: dict[str, Any] = {}
    try:
        async with stdio_client(_server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for name in names:
                    res = await session.call_tool(
                        "find",
                        {
                            "database": MONGODB_DB,
                            "collection": "preprocess_files",
                            "filter": {"book_id": book_id, "filename": f"{name}.json"},
                            "limit": 1,
                        },
                    )
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    docs = _parse_find_result(text)
                    if docs and isinstance(docs[0], dict) and "data" in docs[0]:
                        out[name] = docs[0]["data"]
    except Exception as e:
        # The stdio transport can raise non-fatal cleanup errors on exit
        # AFTER the data was already fetched — only propagate if we got nothing.
        if not out:
            raise
        logger.debug("MCP stdio cleanup noise (data already fetched): %s", e)
    return out


def load_preprocess_files_via_mcp(book_id: str, names: list[str]) -> dict[str, Any]:
    """Read preprocess documents through the MongoDB MCP server.

    Returns a dict of {name: data} for the documents found. Returns an empty
    dict on any failure so the caller can fall back gracefully.
    """
    try:
        return asyncio.run(_load_files_async(book_id, names))
    except Exception as e:
        logger.warning("MCP load_preprocess_files failed for %s: %s", book_id, e)
        return {}


async def _update_characters_async(book_id: str, items: list) -> int:
    """One MCP session: write consistency data into the characters collection."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from src.config import MONGODB_DB

    count = {"n": 0}
    try:
        async with stdio_client(_server_params(read_only=False)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for canonical_name, updates in items:
                    result = await session.call_tool(
                        "update-many",
                        {
                            "database": MONGODB_DB,
                            "collection": "characters",
                            "filter": {"book_id": book_id, "canonical_name": canonical_name},
                            "update": {"$set": updates},
                        },
                    )
                    # The MCP SDK reports tool failures IN-BAND (result.isError),
                    # not as exceptions — counting every call as a success made
                    # the sync claim writes that never happened.
                    if getattr(result, "isError", False):
                        err_text = "".join(
                            getattr(c, "text", "") for c in (result.content or [])
                        )
                        logger.warning(
                            "MCP update-many failed for %s/%s: %s",
                            book_id, canonical_name, err_text[:300] or "unknown tool error",
                        )
                        continue
                    count["n"] += 1
    except Exception as e:
        if count["n"] == 0:
            raise
        logger.debug("MCP stdio cleanup noise after %d writes: %s", count["n"], e)
    return count["n"]


def update_characters_via_mcp(book_id: str, items: list) -> int:
    """Persist character consistency data to MongoDB via the MCP server.

    items: list of (canonical_name, updates_dict). One MCP session for all.
    Returns number of characters written; 0 on failure (best-effort).
    """
    if not items:
        return 0
    try:
        return asyncio.run(_update_characters_async(book_id, items))
    except Exception as e:
        logger.warning("MCP update_characters failed for %s: %s", book_id, e)
        return 0
