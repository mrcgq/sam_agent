# -*- coding: utf-8 -*-
"""
SAM Agent 主循环（完全体）
集成知识库 + 轨迹持久化
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model_adapter import ModelAdapter, ModelResponse
from .tool_executor import ToolExecutor, ToolResult
from .oracle import TaskOracle
from .knowledge_base import KnowledgeBase
from .trajectory_store import TrajectoryStore


SYSTEM_PROMPT = """你是 SAM-∞（SAM Agent），一个由 SAM 增强的 AI Agent。

你的核心能力：
1. 调用外部工具完成任务
2. 核验每一步的结果（物理事实，拒绝口头承诺）
3. 查询漏洞知识库（SQL注入/XSS/RCE/LFI/SSRF/IDOR/OAST 等）
4. 记录执行轨迹供回溯审计

工作流程：
- 收到任务后，规划步骤
- 每步调用合适的工具
- 用 verify_result 核验工具输出是否可靠
- 需要安全测试知识时，用 query_knowledge 查询
- 完成任务后给出最终答案

注意：
- 只执行授权范围内的操作
- 不执行危险命令（rm -rf /、格式化、网络扫描等）
- 核验结果要客观，空输出不等于成功
- 遇到不确定的情况，先查询知识库"""


@dataclass
class AgentStep:
    step: int
    action: str
    input_summary: str
    output_summary: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    task: str
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    total_iterations: int = 0
    success: bool = True
    error: Optional[str] = None
    task_id: str = ""


class AgentLoop:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_executor: ToolExecutor,
        max_iterations: int = 50,
        log_file: Optional[str] = None,
    ):
        self.model = model_adapter
        self.tools = tool_executor
        self.max_iterations = max_iterations
        self.log_file = Path(log_file) if log_file else None
        self._steps: List[AgentStep] = []
        self._messages: List[Dict[str, Any]] = []
        self._task_id: str = ""

    def run(self, task: str) -> AgentResult:
        self._steps = []
        self._task_id = f"sam-{uuid.uuid4().hex[:8]}"
        self._messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        iteration = 0
        final_answer = None

        self._log(f"=== 任务 {self._task_id}: {task[:100]} ===")

        while iteration < self.max_iterations:
            iteration += 1
            self._log(f"--- 迭代 {iteration}/{self.max_iterations} ---")

            try:
                response = self._call_model()
            except Exception as e:
                return AgentResult(task=task, answer="", steps=self._steps,
                                   total_iterations=iteration, success=False,
                                   error=f"模型调用失败: {e}", task_id=self._task_id)

            self._log(f"模型响应: finish={response.finish_reason}, content_len={len(response.content or '')}, tools={len(response.tool_calls)}")

            # 最终答案
            if response.content and not response.tool_calls:
                final_answer = response.content
                self._log(f"最终答案: {final_answer[:200]}")
                break

            # 工具调用
            if response.tool_calls:
                for tc in response.tool_calls:
                    self._log(f"  工具: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:200]})")

                    result = self._execute_sync(tc.name, tc.arguments)

                    # 记录轨迹
                    verdict = None
                    verdict_score = None
                    if result.success and isinstance(result.output, dict):
                        verdict = result.output.get("status")
                        verdict_score = result.output.get("score")

                    self.tools._ts.record(
                        task_id=self._task_id,
                        step=iteration,
                        action="tool_execute",
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                        tool_output=result.output if result.success else None,
                        verdict=verdict,
                        verdict_score=verdict_score,
                    )

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.to_content(),
                    }
                    self._messages.append(tool_msg)

                    self._steps.append(AgentStep(
                        step=iteration,
                        action="tool_execute",
                        input_summary=f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:100]})",
                        output_summary=result.to_content()[:200],
                    ))
                    self._log(f"  结果: success={result.success}, len={len(result.to_content())}")

            if not response.content and not response.tool_calls:
                final_answer = "模型未返回任何内容"
                break

        if final_answer is None:
            final_answer = f"达到最大迭代次数 ({self.max_iterations})"

        # 记录最终轨迹
        self.tools._ts.record(
            task_id=self._task_id,
            step=iteration,
            action="task_complete",
            tool_output=final_answer,
            verdict="ACCEPT" if (iteration < self.max_iterations) else "RETRY",
        )

        result = AgentResult(
            task=task,
            answer=final_answer,
            steps=self._steps,
            total_iterations=iteration,
            success=iteration < self.max_iterations,
            task_id=self._task_id,
        )
        self._log(f"=== 任务完成: iterations={iteration}, success={result.success} ===")
        return result

    def _call_model(self) -> ModelResponse:
        tools_def = self.tools.get_tool_definitions()
        return self.model.chat(messages=self._messages, tools=tools_def if tools_def else None)

    def _execute_sync(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, self.tools.execute(tool_name, arguments)).result()
            else:
                return asyncio.run(self.tools.execute(tool_name, arguments))
        except RuntimeError:
            return asyncio.run(self.tools.execute(tool_name, arguments))

    def _log(self, msg: str):
        print(msg)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    def get_history(self) -> List[Dict[str, Any]]:
        return self._messages.copy()
