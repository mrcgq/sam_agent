# -*- coding: utf-8 -*-
"""
SAM Agent MCP Server
让 DSH 通过 MCP 协议调用 SAM 的工具

支持传输方式：
  - stdio（DSH 本地启动 SAM 子进程）
  - streamable-http（SAM 作为 HTTP 服务，DSH 远程连接）

用法:
  stdio 模式:  python mcp_server.py
  HTTP 模式:   python mcp_server.py --http --port 8934
"""
from __future__ import annotations

import asyncio
import json
import sys
import argparse
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional

# 项目根目录
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.tool_executor import ToolExecutor
from core.knowledge_base import KnowledgeBase
from core.trajectory_store import TrajectoryStore


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server 核心
# ─────────────────────────────────────────────────────────────────────────────
class MCPServer:
    """MCP 协议服务器（JSON-RPC 2.0）"""

    SERVER_INFO = {
        "name": "sam-agent",
        "version": "1.0.0",
    }

    def __init__(self):
        self._executor = ToolExecutor()
        self._kb = self._executor._kb
        self._ts = self._executor._ts
        self._initialized = False

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """处理一个 JSON-RPC 请求"""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        # 路由
        if method == "initialize":
            result = self._handle_initialize(params)
        elif method == "tools/list":
            result = self._handle_tools_list(params)
        elif method == "tools/call":
            result = self._handle_tools_call(params)
        elif method == "resources/list":
            result = self._handle_resources_list(params)
        elif method == "resources/read":
            result = self._handle_resources_read(params)
        else:
            return self._error_response(req_id, -32601, f"Method not found: {method}")

        return self._success_response(req_id, result)

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._initialized = True
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": self.SERVER_INFO,
        }

    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tools = self._executor.get_tool_definitions()
        return {
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "inputSchema": t["function"].get("parameters", {}),
                }
                for t in tools
            ]
        }

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # 同步执行
        result = asyncio.run(self._executor.execute(tool_name, arguments))

        return {
            "content": [
                {
                    "type": "text",
                    "text": result.to_content(),
                }
            ],
            "isError": not result.success,
        }

    def _handle_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """知识库作为资源暴露"""
        self._kb.ensure_loaded()
        types = self._kb.get_all_types()
        resources = []
        for vt in types:
            resources.append({
                "uri": f"sam://knowledge/{vt}",
                "name": f"Knowledge: {vt.upper()}",
                "description": f"{vt.upper()} 漏洞知识库",
                "mimeType": "application/json",
            })
        # 轨迹统计
        resources.append({
            "uri": "sam://trajectory/stats",
            "name": "Trajectory Stats",
            "description": "轨迹统计信息",
            "mimeType": "application/json",
        })
        return {"resources": resources}

    def _handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = params.get("uri", "")
        if uri.startswith("sam://knowledge/"):
            vuln_type = uri.replace("sam://knowledge/", "")
            self._kb.ensure_loaded()
            result = self._kb.get_by_type(vuln_type)
            texts = []
            for r in result.rules:
                texts.append(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": "\n---\n".join(texts)}]}
        elif uri == "sam://trajectory/stats":
            stats = self._ts.stats()
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(stats, ensure_ascii=False, indent=2)}]}
        else:
            return {"contents": [], "error": {"code": -32602, "message": f"Unknown resource: {uri}"}}

    def _success_response(self, req_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error_response(self, req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ─────────────────────────────────────────────────────────────────────────────
# stdio 传输（DSH 本地启动 SAM 子进程）
# ─────────────────────────────────────────────────────────────────────────────
async def run_stdio(server: MCPServer) -> None:
    """从 stdin 读 JSON-RPC 请求，写 stdout 响应"""
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    while True:
        line = await reader.readline()
        if not line:
            break
        line_str = line.decode("utf-8").strip()
        if not line_str:
            continue
        try:
            request = json.loads(line_str)
        except json.JSONDecodeError:
            continue
        response = server.handle_request(request)
        resp_bytes = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
        writer.write(resp_bytes)
        await writer.drain()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 传输（SAM 作为 HTTP 服务，DSH 远程连接）
# ─────────────────────────────────────────────────────────────────────────────
def run_http(server: MCPServer, host: str, port: int) -> None:
    """Streamable HTTP MCP 服务"""
    import http.server
    import socketserver

    class MCPHandler(http.server.BaseHTTPRequestHandler):
        _server = server

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""
            try:
                request = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
                return

            response = self._server.handle_request(request)
            resp_body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

        def do_GET(self):
            # 健康检查
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "server": "sam-agent"}')
            else:
                self.send_error(404)

        def log_message(self, format, *args):
            pass  # 静默

    with socketserver.TCPServer((host, port), MCPHandler) as httpd:
        print(f"[MCP Server] HTTP 服务启动在 {host}:{port}")
        print(f"[MCP Server] 健康检查: http://{host}:{port}/health")
        print(f"[MCP Server] 按 Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[MCP Server] 已停止")
            httpd.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SAM Agent MCP Server")
    parser.add_argument("--http", action="store_true", help="使用 HTTP 传输（默认 stdio）")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8934, help="HTTP 端口（默认 8934）")
    args = parser.parse_args()

    server = MCPServer()

    if args.http:
        run_http(server, args.host, args.port)
    else:
        print("[MCP Server] stdio 模式启动，等待 DSH 连接...", file=sys.stderr)
        asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
