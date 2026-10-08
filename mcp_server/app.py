"""Stateless MCP server (Streamable HTTP transport, JSON responses) running on AWS Lambda.

Lambda is request/response, so the server answers each JSON-RPC POST with a single
application/json body and keeps no session state. GET (server-initiated SSE stream)
is not offered, which the MCP spec allows by returning 405.
"""
import base64
import hmac
import json
import logging
import os
import traceback

from tools import TOOLS

log = logging.getLogger()
log.setLevel(logging.INFO)

SERVER_INFO = {"name": "fin-data-mcp", "version": "1.0.0"}
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


def _http(status, body=None, headers=None):
    resp = {"statusCode": status, "headers": {"content-type": "application/json", **(headers or {})}}
    resp["body"] = json.dumps(body) if body is not None else ""
    return resp


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_rpc(msg):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    method, req_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if req_id is None:  # notification (e.g. notifications/initialized)
        return None

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(req_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Financial market data tools (quotes, history, fundamentals, news) via Yahoo Finance.",
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [
            {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
            for name, t in TOOLS.items()
        ]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return _error(req_id, -32602, f"Unknown tool: {name}")
        try:
            data = tool["fn"](**args)
            return _result(req_id, {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False})
        except Exception as exc:  # tool errors are reported to the model, not as protocol errors
            log.warning("tool %s failed: %s\n%s", name, exc, traceback.format_exc())
            return _result(req_id, {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True})
    return _error(req_id, -32601, f"Method not found: {method}")


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "POST")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if AUTH_TOKEN:
        supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, AUTH_TOKEN):
            return _http(401, {"error": "unauthorized"})

    if method != "POST":
        return _http(405, {"error": "Only POST is supported (stateless server, no SSE stream)."},
                     {"allow": "POST"})

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _http(400, _error(None, -32700, "Parse error"))

    if isinstance(payload, list):  # JSON-RPC batch (older protocol versions)
        responses = [r for r in (handle_rpc(m) for m in payload) if r is not None]
        return _http(200, responses) if responses else _http(202)
    response = handle_rpc(payload)
    return _http(200, response) if response is not None else _http(202)
