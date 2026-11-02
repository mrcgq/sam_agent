#!/usr/bin/env python3
"""
SAM Agent CLI 入口（完全体）
集成知识库 + 轨迹持久化 + MCP Server
"""
import sys
import os
import argparse
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from config import SAMConfig
from core.model_adapter import ModelAdapter
from core.tool_executor import ToolExecutor
from core.agent_loop import AgentLoop


def main():
    parser = argparse.ArgumentParser(description="SAM Agent - 让模型变聪明的 Agent 系统（完全体）")
    parser.add_argument("task", nargs="?", help="任务描述")
    parser.add_argument("--config", "-c", help="配置文件路径")
    parser.add_argument("--base-url", help="模型 API Base URL")
    parser.add_argument("--api-key", help="模型 API Key")
    parser.add_argument("--model", "-m", help="模型名称")
    parser.add_argument("--provider", "-p", choices=["openai", "anthropic", "deepseek"], default="openai")
    parser.add_argument("--max-iterations", type=int, help="最大迭代次数")
    parser.add_argument("--log", help="日志文件路径")
    parser.add_argument("--setup", action="store_true", help="交互式配置")
    parser.add_argument("--mcp", action="store_true", help="启动 MCP Server（HTTP 模式）")
    parser.add_argument("--mcp-port", type=int, default=8934, help="MCP Server 端口")
    args = parser.parse_args()

    # MCP Server 模式
    if args.mcp:
        from mcp_server import MCPServer
        import asyncio
        server = MCPServer()
        print(f"[MCP] SAM Agent MCP Server 启动在 127.0.0.1:{args.mcp_port}")
        print(f"[MCP] DSH 配置地址: http://127.0.0.1:{args.mcp_port}")
        print(f"[MCP] 工具列表: {', '.join(t['function']['name'] for t in server._executor.get_tool_definitions())}")
        print("[MCP] 按 Ctrl+C 停止")
        asyncio.run(_run_mcp_http(server, args.mcp_port))
        return

    # 普通 CLI 模式
    config = SAMConfig(args.config)

    if args.setup:
        _interactive_setup(config)
        return

    if args.base_url:
        config.set("model.base_url", args.base_url)
    if args.api_key:
        config.set("model.api_key", args.api_key)
    if args.model:
        config.set("model.model_name", args.model)
    if args.provider:
        config.set("model.provider", args.provider)
    if args.max_iterations:
        config.set("agent.max_iterations", args.max_iterations)

    if not config.get("model.base_url"):
        print("错误: 未配置模型 Base URL。使用 --setup 或 --base-url。")
        _print_config(config)
        sys.exit(1)
    if not config.get("model.api_key"):
        print("错误: 未配置模型 API Key。使用 --setup 或 --api-key。")
        sys.exit(1)

    task = args.task
    if not task:
        print("请输入任务描述 (Ctrl+D 结束):")
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass
        task = "\n".join(lines).strip()
    if not task:
        print("错误: 未提供任务描述。")
        sys.exit(1)

    model_adapter = ModelAdapter(
        base_url=config.get("model.base_url"),
        api_key=config.get("model.api_key"),
        model_name=config.get("model.model_name"),
        provider=config.get("model.provider", "openai"),
    )

    tool_executor = ToolExecutor()

    print(f"\n[工具] {', '.join(t['function']['name'] for t in tool_executor.get_tool_definitions())}")
    print(f"[模型] {config.get('model.model_name')} @ {config.get('model.base_url')}")
    print(f"[知识库] {tool_executor._kb.status()['total_rules']} 条规则, {len(tool_executor._kb.get_all_types())} 类漏洞")
    print(f"[轨迹] {tool_executor._ts.stats()['total_records']} 条历史记录")
    print(f"[最大迭代] {config.get('agent.max_iterations', 50)}")
    print("-" * 60)

    log_file = args.log or str(project_root / "agent.log")
    agent = AgentLoop(
        model_adapter=model_adapter,
        tool_executor=tool_executor,
        max_iterations=config.get("agent.max_iterations", 50),
        log_file=log_file,
    )

    print(f"\n>>> 任务: {task}")
    print("-" * 60)

    start = time.time()
    result = agent.run(task)
    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print("最终结果")
    print("=" * 60)
    print(result.answer)
    print("-" * 60)
    print(f"任务ID: {result.task_id}")
    print(f"迭代: {result.total_iterations} | 步骤: {len(result.steps)} | 成功: {result.success} | 耗时: {elapsed:.1f}s")
    if result.error:
        print(f"错误: {result.error}")
    print("=" * 60)

    if not result.success:
        sys.exit(1)


async def _run_mcp_http(server, port: int):
    import http.server, socketserver
    class H(http.server.BaseHTTPRequestHandler):
        _s = server
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            try:
                req = __import__("json").loads(body) if body else {}
            except __import__("json").JSONDecodeError:
                self.send_error(400, "Invalid JSON"); return
            resp = self._s.handle_request(req)
            out = __import__("json").dumps(resp, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","server":"sam-agent"}')
            else:
                self.send_error(404)
        def log_message(self, *a): pass
    with socketserver.TCPServer(("127.0.0.1", port), H) as httpd:
        try: httpd.serve_forever()
        except KeyboardInterrupt: httpd.shutdown()


def _interactive_setup(config: SAMConfig):
    print("=" * 60)
    print("SAM Agent 配置")
    print("=" * 60)
    base_url = input(f"Base URL [{config.get('model.base_url', '')}]: ").strip()
    if base_url: config.set("model.base_url", base_url)
    api_key = input("API Key (本次使用，不保存): ").strip()
    if api_key:
        os.environ["SAM_API_KEY"] = api_key
        config.set("model.api_key", "[已设置]")
    model_name = input(f"模型名 [{config.get('model.model_name', 'gpt-5.6')}]: ").strip()
    if model_name: config.set("model.model_name", model_name)
    provider = input(f"提供商 (openai/anthropic/deepseek) [{config.get('model.provider', 'openai')}]: ").strip()
    if provider: config.set("model.provider", provider)
    print("\n[√] 配置已保存")
    _print_config(config)


def _print_config(config: SAMConfig):
    print("\n当前配置:")
    print(f"  Base URL: {config.get('model.base_url', '(未设置)')}")
    print(f"  API Key: {config.get('model.api_key', '(未设置)')}")
    print(f"  模型: {config.get('model.model_name', '(未设置)')}")
    print(f"  提供商: {config.get('model.provider', 'openai')}")


if __name__ == "__main__":
    main()
