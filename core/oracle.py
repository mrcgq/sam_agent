# -*- coding: utf-8 -*-
"""
SAM Agent 轻量级任务核验（独立版本）
基于《五者合一法典》AP-25 虚假连通空转检测
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .task_types import TaskSpec, ToolResult, OutputType, TaskType


class VerdictStatus(str, Enum):
    ACCEPT = "ACCEPT"
    DEGRADE = "DEGRADE"
    RETRY = "RETRY"
    REJECT = "REJECT"


class RuleDimension(str, Enum):
    EXIT_CODE = "EXIT_CODE"
    OUTPUT_NONEMPTY = "OUTPUT_NONEMPTY"
    FILE_EXISTS = "FILE_EXISTS"
    FILE_NONEMPTY = "FILE_NONEMPTY"
    CONTAINS_KEYWORD = "CONTAINS_KEYWORD"


@dataclass(frozen=True)
class VerificationRule:
    dimension: RuleDimension
    target_value: Any
    weight: float = 1.0
    mandatory: bool = True
    description: str = ""


@dataclass
class OracleVerdict:
    status: VerdictStatus
    score: float
    violations: List[str] = field(default_factory=list)
    passed_rules: List[RuleDimension] = field(default_factory=list)
    audit_notes: str = ""
    is_ap25_violation: bool = False

    @property
    def passed(self) -> bool:
        return self.status in (VerdictStatus.ACCEPT, VerdictStatus.DEGRADE)


class TaskOracle:
    """
    轻量级任务物理裁判庭
    拒绝口头承诺，唯一采信硬件与操作系统反馈。
    """

    def __init__(self, accept_threshold: float = 0.95):
        self.accept_threshold = accept_threshold

    def evaluate_result(
        self,
        spec: TaskSpec,
        result: ToolResult,
        rules: Optional[List[VerificationRule]] = None
    ) -> OracleVerdict:
        """端到端裁决评估核心"""
        active_rules = rules or self._synthesize_default_rules(spec, result)

        total_weight = sum(r.weight for r in active_rules)
        earned_weight = 0.0
        violations: List[str] = []
        passed_dims: List[RuleDimension] = []
        is_ap25 = False
        fatal_breach = False

        # AP-25 虚假连通空转检测
        if result.exit_code == 0:
            zero_yield = False
            if result.output is None:
                zero_yield = True
            elif isinstance(result.output, (str, bytes)) and len(result.output) == 0:
                zero_yield = True
            elif isinstance(result.output, (dict, list)) and len(result.output) == 0:
                zero_yield = True
            elif result.output_type == OutputType.FILE and result.target_path:
                tp = Path(result.target_path)
                if not tp.exists() or tp.stat().st_size == 0:
                    zero_yield = True

            # 波斯泰尔半透膜豁免：静默命令
            if zero_yield and spec.task_type == TaskType.SHELL_EXEC and spec.command:
                first_cmd = spec.command.strip().split()[0].lower()
                if first_cmd in ("mkdir", "touch", "cp", "mv", "rm", "rmdir", "chmod", "git"):
                    zero_yield = False

            if zero_yield:
                is_ap25 = True
                violations.append("【AP-25 虚假连通空转】退出码为 0，但真实业务吞吐/产出物理为 0 字节！")
                return OracleVerdict(
                    status=VerdictStatus.REJECT,
                    score=0.0,
                    violations=violations,
                    passed_rules=[],
                    audit_notes="触发 AP-25 致命反模式，定性为假活死代码，当场处决。",
                    is_ap25_violation=True
                )

        # 逐维物理规则对账
        for rule in active_rules:
            passed, err_msg = self._check_dimension(rule, spec, result)
            if passed:
                earned_weight += rule.weight
                passed_dims.append(rule.dimension)
            else:
                violations.append(f"[{rule.dimension.name}] 违背: {err_msg}")
                if rule.mandatory:
                    fatal_breach = True

        score = (earned_weight / total_weight) if total_weight > 0 else 0.0

        if fatal_breach or score < 0.5:
            status = VerdictStatus.REJECT
        elif score >= self.accept_threshold:
            status = VerdictStatus.ACCEPT
        else:
            status = VerdictStatus.DEGRADE

        return OracleVerdict(
            status=status,
            score=score,
            violations=violations,
            passed_rules=passed_dims,
            audit_notes=f"物理评测得分: {score:.3f} | 违例数: {len(violations)}",
            is_ap25_violation=is_ap25
        )

    def _check_dimension(
        self,
        rule: VerificationRule,
        spec: TaskSpec,
        result: ToolResult
    ) -> tuple:
        """检查单个维度"""
        dim = rule.dimension
        target = rule.target_value

        if dim == RuleDimension.EXIT_CODE:
            expected = target if isinstance(target, int) else 0
            if result.exit_code == expected:
                return True, ""
            return False, f"退出码 {result.exit_code} != {expected}"

        elif dim == RuleDimension.OUTPUT_NONEMPTY:
            if result.output is not None:
                if isinstance(result.output, (str, bytes)) and len(result.output) > 0:
                    return True, ""
                if isinstance(result.output, (dict, list)) and len(result.output) > 0:
                    return True, ""
            return False, "输出为空"

        elif dim == RuleDimension.FILE_EXISTS:
            if target and Path(str(target)).exists():
                return True, ""
            return False, f"文件不存在: {target}"

        elif dim == RuleDimension.FILE_NONEMPTY:
            if target:
                p = Path(str(target))
                if p.exists() and p.stat().st_size > 0:
                    return True, ""
            return False, f"文件为空或不存在: {target}"

        elif dim == RuleDimension.CONTAINS_KEYWORD:
            output_str = result.output_str if result.output else ""
            if target and target in output_str:
                return True, ""
            return False, f"输出不包含关键字: {target}"

        return False, f"未知维度: {dim}"

    def _synthesize_default_rules(
        self,
        spec: TaskSpec,
        result: ToolResult
    ) -> List[VerificationRule]:
        """根据任务类型自动生成默认规则"""
        rules: List[VerificationRule] = []

        # 基础规则：退出码
        rules.append(VerificationRule(
            RuleDimension.EXIT_CODE,
            target_value=0,
            weight=2.0,
            mandatory=True,
            description="退出码必须为 0"
        ))

        # 基础规则：输出非空
        rules.append(VerificationRule(
            RuleDimension.OUTPUT_NONEMPTY,
            target_value=True,
            weight=1.5,
            mandatory=True,
            description="输出不能为空"
        ))

        # 文件任务：检查文件
        if result.output_type == OutputType.FILE or result.target_path or spec.target_path:
            target = result.target_path or spec.target_path
            if target:
                rules.append(VerificationRule(
                    RuleDimension.FILE_EXISTS,
                    target_value=target,
                    weight=2.0,
                    mandatory=True,
                    description=f"文件必须存在: {target}"
                ))
                rules.append(VerificationRule(
                    RuleDimension.FILE_NONEMPTY,
                    target_value=target,
                    weight=1.5,
                    mandatory=True,
                    description=f"文件不能为空: {target}"
                ))

        # 如果任务描述包含预期关键词
        if spec.expected_output:
            rules.append(VerificationRule(
                RuleDimension.CONTAINS_KEYWORD,
                target_value=spec.expected_output,
                weight=1.0,
                mandatory=False,
                description=f"输出应包含: {spec.expected_output}"
            ))

        return rules
