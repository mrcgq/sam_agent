# -*- coding: utf-8 -*-
"""
SAM Agent 工具执行层
集成知识库 + 轨迹持久化
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .task_types import OutputType, TaskSpec, TaskType, ToolResult
from .oracle import OracleVerdict, TaskOracle, RuleDimension
from .knowledge_base import KnowledgeBase
from .trajectory_store import TrajectoryStore


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
class ToolDefinition:
    __slots__ = ("name", "description", "parameters", "handler")

    def __init__(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler


class ToolExecutor:
    def __init__(
        self,
        oracle: Optional[TaskOracle] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
        trajectory_store: Optional[TrajectoryStore] = None,
    ):
        self._oracle = oracle or TaskOracle()
        self._kb = knowledge_base or KnowledgeBase()
        self._ts = trajectory_store or TrajectoryStore()
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_builtin_tools()

    # ---- 注册 ----
    def _register_builtin_tools(self) -> None:
        self.register_tool({
            "name": "verify_result",
            "description": "核验任务执行结果是否符合预期（AP-25 虚假连通空转检测）。返回 ACCEPT/DEGRADE/REJECT 及评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "任务描述"},
                    "result_output": {"type": "string", "description": "工具输出"},
                    "result_exit_code": {"type": "integer", "description": "退出码，默认 0"},
                    "target_path": {"type": "string", "description": "目标文件路径（可选）"}
                },
                "required": ["task_description", "result_output"]
            }
        }, self._handle_verify)

        self.register_tool({
            "name": "query_knowledge",
            "description": "查询漏洞知识库（SQL注入/XSS/RCE/LFI/SSRF/IDOR/OAST 等）。返回匹配规则、payload、oracle 模式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询关键词"},
                    "vuln_type": {"type": "string", "description": "漏洞类型（可选，如 sqli/xss/rce）"}
                },
                "required": ["query"]
            }
        }, self._handle_knowledge_query)

        self.register_tool({
            "name": "trajectory_stats",
            "description": "查看历史轨迹统计（总记录数、按核验结果分类）。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }, self._handle_trajectory_stats)

        self.register_tool({
            "name": "execute_command",
            "description": "在本地执行系统命令。禁止危险命令（rm -rf /、格式化、网络扫描等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 30"}
                },
                "required": ["command"]
            }
        }, self._handle_execute_command)

        self.register_tool({
            "name": "read_file",
            "description": "读取文件内容（最大 10MB）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        }, self._handle_read_file)

        self.register_tool({
            "name": "write_file",
            "description": "写入文件内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"}
                },
                "required": ["path", "content"]
            }
        }, self._handle_write_file)

        self.register_tool({
            "name": "list_files",
            "description": "列出目录下的文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认当前目录"}
                }
            }
        }, self._handle_list_files)

    def register_tool(self, definition: Dict[str, Any], handler: Callable) -> None:
        name = definition["name"]
        self._tools[name] = ToolDefinition(
            name=name,
            description=definition.get("description", ""),
            parameters=definition.get("parameters", {}),
            handler=handler,
        )

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
            }
            for t in self._tools.values()
        ]

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")
        try:
            if asyncio.iscoroutinefunction(tool.handler):
                result = await tool.handler(arguments)
            else:
                result = tool.handler(arguments)
            return result
        except Exception as e:
            return ToolResult(success=False, error=f"工具执行异常: {e}")

    # ---- 内置处理函数 ----

    def _handle_verify(self, args: Dict[str, Any]) -> ToolResult:
        spec = TaskSpec(description=args.get("task_description", ""), task_type=TaskType.VERIFY)
        result = ToolResult(
            output=args.get("result_output", ""),
            exit_code=args.get("result_exit_code", 0),
            output_type=OutputType.TEXT,
        )
        if args.get("target_path"):
            result.target_path = args["target_path"]
            result.output_type = OutputType.FILE

        verdict: OracleVerdict = self._oracle.evaluate_result(spec, result)
        return ToolResult(
            success=True,
            output={
                "status": verdict.status.value,
                "score": round(verdict.score, 3),
                "passed": verdict.passed,
                "violations": verdict.violations,
                "passed_rules": [d.value for d in verdict.passed_rules],
                "notes": verdict.audit_notes,
                "is_ap25": verdict.is_ap25_violation,
            },
        )

    def _handle_knowledge_query(self, args: Dict[str, Any]) -> ToolResult:
        query = args.get("query", "")
        vuln_type = args.get("vuln_type")
        self._kb.ensure_loaded()
        if vuln_type:
            result = self._kb.get_by_type(vuln_type)
        else:
            result = self._kb.lookup(query)
        return ToolResult(
            success=True,
            output={
                "summary": result.to_summary(),
                "payloads": result.payload_list()[:10],
                "oracle_patterns": result.oracle_patterns()[:10],
                "types_available": self._kb.get_all_types(),
            },
        )

    def _handle_trajectory_stats(self, args: Dict[str, Any]) -> ToolResult:
        stats = self._ts.stats()
        return ToolResult(success=True, output=stats)

    def _handle_execute_command(self, args: Dict[str, Any]) -> ToolResult:
        command = args.get("command", "")
        timeout = args.get("timeout", 30)

        dangerous = ["rm -rf /", ":(){:|:&};:", "mkfs", "dd if=", "wget http", "curl http"]
        for p in dangerous:
            if p in command:
                return ToolResult(success=False, error=f"命令包含危险模式: {p}")

        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()))
            return ToolResult(
                success=True,
                output={"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"命令超时 ({timeout}s)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _handle_read_file(self, args: Dict[str, Any]) -> ToolResult:
        path = Path(args.get("path", ""))
        if not path.exists():
            return ToolResult(success=False, error=f"文件不存在: {path}")
        if not path.is_file():
            return ToolResult(success=False, error=f"不是文件: {path}")
        if path.stat().st_size > 10 * 1024 * 1024:
            return ToolResult(success=False, error="文件过大（>10MB）")
        try:
            return ToolResult(success=True, output=path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _handle_write_file(self, args: Dict[str, Any]) -> ToolResult:
        path = Path(args.get("path", ""))
        content = args.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, output=f"已写入 {path} ({len(content)} 字节)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _handle_list_files(self, args: Dict[str, Any]) -> ToolResult:
        path = Path(args.get("path", "."))
        if not path.exists():
            return ToolResult(success=False, error=f"目录不存在: {path}")
        if not path.is_dir():
            return ToolResult(success=False, error=f"不是目录: {path}")
        try:
            items = []
            for item in sorted(path.iterdir()):
                items.append({"name": item.name, "type": "dir" if item.is_dir() else "file"})
            return ToolResult(success=True, output=items)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
