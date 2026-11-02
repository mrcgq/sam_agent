# -*- coding: utf-8 -*-
"""
SAM Agent 任务类型定义（独立版本，不依赖 sam_infinity）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class TaskType(str, Enum):
    SHELL_EXEC = "shell"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    VERIFY = "verify"
    KNOWLEDGE_QUERY = "knowledge_query"
    TRAJECTORY_RECORD = "trajectory_record"
    OTHER = "other"


class OutputType(str, Enum):
    TEXT = "text"
    FILE = "file"
    JSON = "json"
    BINARY = "binary"


@dataclass
class TaskSpec:
    """任务规格"""
    description: str
    task_type: TaskType = TaskType.OTHER
    target_path: Optional[str] = None
    expected_output: Optional[str] = None
    command: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool = True
    output: Any = None
    exit_code: int = 0
    output_type: OutputType = OutputType.TEXT
    target_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def output_str(self) -> str:
        if isinstance(self.output, str):
            return self.output
        if isinstance(self.output, bytes):
            return self.output.decode("utf-8", errors="replace")
        import json
        try:
            return json.dumps(self.output, ensure_ascii=False)
        except Exception:
            return str(self.output)

    def to_content(self) -> str:
        """转为文本内容供模型消费"""
        if self.success:
            if isinstance(self.output, str):
                return self.output
            import json
            try:
                return json.dumps(self.output, ensure_ascii=False, indent=2)
            except Exception:
                return str(self.output)
        return f"[ERROR] {self.error}"
