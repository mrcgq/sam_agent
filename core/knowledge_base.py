# -*- coding: utf-8 -*-
"""
SAM Agent 知识库
加载 YAML 规则文件，支持关键词检索、漏洞类型查询
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RuleEntry:
    """单条规则"""
    vuln_type: str
    title: str
    description: str
    payloads: List[str] = field(default_factory=list)
    oracle_patterns: List[str] = field(default_factory=list)
    waf_bypass: List[str] = field(default_factory=list)
    oast_templates: List[str] = field(default_factory=list)
    db_fingerprints: Dict[str, List[str]] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def search_text(self) -> str:
        parts = [self.vuln_type, self.title, self.description]
        for p in self.payloads[:3]:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("payload", p.get("description", ""))))
        parts.extend(self.oracle_patterns[:3])
        parts.extend(self.tags)
        return " ".join(parts).lower()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vuln_type": self.vuln_type,
            "title": self.title,
            "description": self.description,
            "payloads": self.payloads[:5],
            "oracle_patterns": self.oracle_patterns[:5],
            "tags": self.tags,
        }


@dataclass
class LookupResult:
    """查询结果"""
    rules: List[RuleEntry] = field(default_factory=list)
    total_matches: int = 0

    @property
    def best_rule(self) -> Optional[RuleEntry]:
        return self.rules[0] if self.rules else None

    def payload_list(self) -> List[str]:
        payloads = []
        for r in self.rules:
            payloads.extend(r.payloads)
        return payloads[:20]

    def oracle_patterns(self) -> List[str]:
        patterns = []
        for r in self.rules:
            patterns.extend(r.oracle_patterns)
        return list(set(patterns))[:20]

    def to_summary(self) -> str:
        if not self.rules:
            return "知识库中未找到匹配的规则。"
        lines = [f"找到 {len(self.rules)} 条匹配规则："]
        for r in self.rules[:5]:
            lines.append(f"  [{r.vuln_type}] {r.title}")
            if r.description:
                lines.append(f"    {r.description[:80]}")
        return "\n".join(lines)


class KnowledgeBase:
    """SAM 知识库"""

    def __init__(self, rules_dir: Optional[str] = None):
        if rules_dir is None:
            rules_dir = str(Path(__file__).parent.parent / "knowledge" / "rules")
        self._rules_dir = Path(rules_dir)
        self._rules: List[RuleEntry] = []
        self._loaded = False
        self._lock = __import__("threading").Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_all_rules()
            self._loaded = True
            logger.info(f"知识库加载完成: {len(self._rules)} 条规则")

    def _load_all_rules(self) -> None:
        self._rules = []
        if not self._rules_dir.exists():
            logger.warning(f"规则目录不存在: {self._rules_dir}")
            return

        try:
            import yaml
        except ImportError:
            logger.error("缺少 pyyaml 依赖，请执行: pip install pyyaml")
            return

        for yaml_file in sorted(self._rules_dir.glob("*.yaml")):
            if yaml_file.name == "__init__.py":
                continue
            try:
                rules = self._load_rule_file(yaml_file)
                self._rules.extend(rules)
            except Exception as e:
                logger.warning(f"加载规则文件失败 {yaml_file.name}: {e}")

    def _load_rule_file(self, yaml_file: Path) -> List[RuleEntry]:
        import yaml
        entries = []
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                raw = f.read()
            # 去除首行可能的 "yaml" 标记（代码围栏残留）
            lines = raw.splitlines()
            if lines and lines[0].strip().lower() == "yaml":
                raw = "\n".join(lines[1:])
            data = yaml.safe_load(raw)
        except Exception as e:
            logger.warning(f"规则文件解析失败 {yaml_file.name}: {e}")
            return entries
        if not isinstance(data, dict):
            return entries
        if not isinstance(data, dict):
            return entries

        vuln_type = data.get("meta", {}).get("vuln_type", yaml_file.stem.replace("_rules", ""))
        description = data.get("meta", {}).get("description", "")

        # oracle_patterns
        oracle_patterns = []
        op_data = data.get("oracle_patterns", {})
        if isinstance(op_data, dict):
            for db_name, patterns in op_data.items():
                if isinstance(patterns, list):
                    oracle_patterns.extend(patterns)
        elif isinstance(op_data, list):
            oracle_patterns = op_data

        # payloads
        payloads = []
        p_data = data.get("payloads", {})
        if isinstance(p_data, list):
            payloads = p_data
        elif isinstance(p_data, dict):
            for cat, items in p_data.items():
                if isinstance(items, list):
                    payloads.extend(items[:5])

        # waf_bypass
        waf_bypass = []
        wb_data = data.get("waf_bypass", {})
        if isinstance(wb_data, list):
            waf_bypass = wb_data
        elif isinstance(wb_data, dict):
            for cat, items in wb_data.items():
                if isinstance(items, list):
                    waf_bypass.extend(items[:3])

        # oast_templates
        oast_templates = data.get("oast_templates", [])
        if isinstance(oast_templates, dict):
            oast_templates = [v for v in oast_templates.values() if isinstance(v, str)][:5]

        # db_fingerprints
        db_fingerprints = {}
        fp_data = data.get("db_fingerprints", {})
        if isinstance(fp_data, dict):
            for db_name, fps in fp_data.items():
                if isinstance(fps, list):
                    db_fingerprints[db_name] = fps[:5]

        entry = RuleEntry(
            vuln_type=vuln_type,
            title=f"{vuln_type.upper()} 检测规则",
            description=description,
            payloads=payloads,
            oracle_patterns=oracle_patterns,
            waf_bypass=waf_bypass,
            oast_templates=oast_templates,
            db_fingerprints=db_fingerprints,
            tags=[vuln_type],
        )
        entries.append(entry)
        return entries

    def lookup(self, query: str, vuln_type: Optional[str] = None, top_k: int = 5) -> LookupResult:
        """关键词检索"""
        self.ensure_loaded()
        query_lower = query.lower()
        results: List[Tuple[RuleEntry, float]] = []

        for rule in self._rules:
            if vuln_type and rule.vuln_type.lower() != vuln_type.lower():
                continue

            score = 0.0
            search_text = rule.search_text()

            # vuln_type 精确匹配
            if rule.vuln_type.lower() in query_lower:
                score += 10.0
            if query_lower in rule.vuln_type.lower():
                score += 8.0

            # 关键词出现在 payload 中
            for p in rule.payloads[:10]:
                if isinstance(p, str) and query_lower in p.lower():
                    score += 3.0
                elif isinstance(p, dict):
                    p_str = str(p.get("payload", p.get("description", ""))).lower()
                    if query_lower in p_str:
                        score += 3.0

            # 关键词出现在 oracle_patterns 中
            for op in rule.oracle_patterns[:10]:
                if isinstance(op, str) and query_lower in op.lower():
                    score += 2.0

            # 标题/描述匹配
            if query_lower in rule.title.lower():
                score += 5.0
            if query_lower in rule.description.lower():
                score += 2.0

            if score > 0:
                results.append((rule, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return LookupResult(
            rules=[r for r, _ in results[:top_k]],
            total_matches=len(results),
        )

    def get_by_type(self, vuln_type: str) -> LookupResult:
        """按漏洞类型获取"""
        self.ensure_loaded()
        rules = [r for r in self._rules if r.vuln_type.lower() == vuln_type.lower()]
        return LookupResult(rules=rules, total_matches=len(rules))

    def get_payloads(self, vuln_type: str, limit: int = 10) -> List[str]:
        """获取指定类型的 payload"""
        result = self.get_by_type(vuln_type)
        return result.payload_list()[:limit]

    def get_oracle_patterns(self, vuln_type: str) -> List[str]:
        """获取指定类型的 oracle 模式"""
        result = self.get_by_type(vuln_type)
        return result.oracle_patterns()

    def get_all_types(self) -> List[str]:
        self.ensure_loaded()
        return list(set(r.vuln_type for r in self._rules))

    def status(self) -> Dict[str, Any]:
        self.ensure_loaded()
        types = self.get_all_types()
        return {
            "rules_dir": str(self._rules_dir),
            "loaded": self._loaded,
            "total_rules": len(self._rules),
            "vuln_types": types,
        }

    def __repr__(self) -> str:
        return f"KnowledgeBase(rules={len(self._rules)}, dir={self._rules_dir})"
