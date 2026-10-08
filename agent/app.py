"""Agent Lambda: serves the chat UI and runs a Claude tool-use loop over the MCP server's tools."""
import asyncio
import base64
import json
import logging
import os
from datetime import date
from pathlib import Path

from anthropic import AnthropicBedrock, AnthropicBedrockMantle
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger()
log.setLevel(logging.INFO)

MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-opus-4-6-v1")
EFFORT = os.environ.get("EFFORT", "medium")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "10"))
INDEX_HTML = (Path(__file__).parent / "index.html").read_text()

REGION = os.environ.get("BEDROCK_REGION") or os.environ["AWS_REGION"]
# Newer models (anthropic.claude-opus-5-5, ...) use Bedrock's Messages API endpoint (bedrock-mantle).
# Older models are invoked through inference profiles (us.anthropic.claude-opus-4-6-v1, ...) on bedrock-runtime.
if MODEL_ID.startswith("anthropic."):
    client = AnthropicBedrockMantle(aws_region=REGION)
    EXTRA_PARAMS = {"output_config": {"effort": EFFORT}}
else:
    client = AnthropicBedrock(aws_region=REGION, max_retries=6)  # low default RPM quotas on new accounts
    EXTRA_PARAMS = {}

SYSTEM_PROMPT = f"""You are a financial-data assistant. Today's date is {date.today().isoformat()}.
Answer questions about stocks, ETFs, indices, crypto and companies using the tools provided; they return
live Yahoo Finance data. Use tools for every number you report, and never invent figures. If the user names a company
rather than a ticker, look up the ticker first. Fetch data for several tickers in parallel when comparing.
Keep answers concise: lead with the direct answer, then a short markdown table or bullets with the key figures,
the as-of date and the currency. Give facts and analysis, not personalised investment advice."""


async def run_agent(history: list[dict]) -> dict:
    """history: [{"role": "user"|"assistant", "content": str}, ...] ending with a user turn."""
    headers = {"Authorization": f"Bearer {MCP_AUTH_TOKEN}"} if MCP_AUTH_TOKEN else {}
    trace = []
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=120)) as http, \
            streamable_http_client(MCP_SERVER_URL, http_client=http, terminate_on_close=False) as (read, write), \
            ClientSession(read, write) as session:
        await session.initialize()
        mcp_tools = (await session.list_tools()).tools
        tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                 for t in mcp_tools]

        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        for _ in range(MAX_TURNS):
            response = await asyncio.to_thread(
                client.messages.create,
                model=MODEL_ID,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                **EXTRA_PARAMS,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return {"answer": "I can't help with that request.", "trace": trace}
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n_(response truncated)_"
                return {"answer": text, "trace": trace}

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            results = await asyncio.gather(*(session.call_tool(b.name, b.input) for b in tool_uses))
            tool_results = []
            for block, result in zip(tool_uses, results):
                text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                trace.append({"tool": block.name, "input": block.input, "is_error": bool(result.is_error),
                              "output_preview": text[:300]})
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": text or "(empty)", "is_error": bool(result.is_error)})
            messages.append({"role": "user", "content": tool_results})

    return {"answer": "I hit the tool-call limit before finishing. Please try a narrower question.", "trace": trace}


def _json(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")

    if method == "GET" and path in ("/", "/index.html"):
        return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": INDEX_HTML}
    if method == "GET" and path == "/health":
        return _json(200, {"ok": True, "model": MODEL_ID})
    if method != "POST" or path != "/chat":
        return _json(404, {"error": "not found"})

    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        history = json.loads(raw).get("messages") or []
        history = [m for m in history if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
        if not history or history[-1]["role"] != "user":
            return _json(400, {"error": "messages must end with a user message"})
        history = history[-20:]
        if history[0]["role"] != "user":
            history = history[1:]
        return _json(200, asyncio.run(run_agent(history)))
    except Exception as exc:
        log.exception("agent failed")
        return _json(500, {"error": f"{type(exc).__name__}: {exc}"})
