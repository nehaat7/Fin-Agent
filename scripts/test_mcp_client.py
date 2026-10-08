"""Smoke-test an MCP server URL with the official MCP Python client: python test_mcp_client.py <url> [token]"""
import asyncio
import sys

from mcp import ClientSession
import httpx2
from mcp.client.streamable_http import streamable_http_client


async def main(url, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers, timeout=60) as http, \
            streamable_http_client(url, http_client=http, terminate_on_close=False) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", init.server_info.name, "protocol:", init.protocol_version)
            tools = (await s.list_tools()).tools
            print("tools:", [t.name for t in tools])
            res = await s.call_tool("get_quote", {"ticker": "AAPL"})
            print("get_quote AAPL ->", res.content[0].text[:200], "isError:", res.is_error)


asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
