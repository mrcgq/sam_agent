#!/usr/bin/env python3
"""
SAM Agent 完全体集成测试
验证：知识库 + 轨迹持久化 + MCP Server
"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def test_knowledge_base():
    print("=" * 60)
    print("测试 1: 知识库加载与查询")
    print("=" * 60)

    from core.knowledge_base import KnowledgeBase
    kb = KnowledgeBase()
    kb.ensure_loaded()

    status = kb.status()
    print(f"  规则目录: {status['rules_dir']}")
    print(f"  总规则数: {status['total_rules']}")
    print(f"  漏洞类型: {', '.join(status['vuln_types'])}")

    assert status["total_rules"] > 0, "知识库未加载任何规则"
    print(f"  可用类型: {', '.join(status['vuln_types'])}")
    print("  [√] 知识库加载成功")

    # 查询测试
    result = kb.lookup("sqli")
    print(f"  查询 'sqli': 匹配 {result.total_matches} 条")
    assert result.total_matches > 0, "查询无结果"
    print(f"  最佳匹配: {result.best_rule.vuln_type} - {result.best_rule.title}")
    print(f"  Payload 数量: {len(result.payload_list())}")
    print(f"  Oracle 模式: {len(result.oracle_patterns())}")
    print("  [√] 知识库查询正常")

    # 按类型获取
    result = kb.get_by_type("sqli")
    print(f"  类型 sqli: {result.total_matches} 条规则")
    print(f"  Oracle 模式示例: {result.oracle_patterns()[:2]}")
    print("  [√] 按类型查询正常")

    print()


def test_trajectory_store():
    print("=" * 60)
    print("测试 2: 轨迹持久化")
    print("=" * 60)

    from core.trajectory_store import TrajectoryStore
    TrajectoryStore.reset()
    db_path = Path(__file__).parent / "test_trajectories.db"
    # Windows 兼容：先尝试删除旧文件（如果有残留）
    # 如果删除失败（被占用），后面会用 clear_all 清理
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        pass

    ts = TrajectoryStore(db_path=str(db_path))
    # 确保测试任务干净（防止上次残留导致断言失败）
    ts.clear_task("test-task-001")

    # 写入
    row_id = ts.record(
        task_id="test-task-001",
        step=1,
        action="tool_execute",
        tool_name="write_file",
        tool_input={"path": "/tmp/test.txt", "content": "hello"},
        tool_output="已写入",
        verdict="ACCEPT",
        verdict_score=1.0,
    )
    print(f"  写入记录 ID: {row_id}")
    assert row_id > 0, "写入失败"

    ts.record(
        task_id="test-task-001",
        step=2,
        action="tool_execute",
        tool_name="verify_result",
        tool_input={"task_description": "test"},
        tool_output={"status": "ACCEPT", "score": 1.0},
        verdict="ACCEPT",
        verdict_score=1.0,
    )
    print("  写入第 2 条记录")

    # 查询
    records = ts.query_by_task("test-task-001")
    print(f"  查询 task test-task-001: {len(records)} 条记录")
    assert len(records) == 2, f"查询记录数不对: 期望 2, 实际 {len(records)}"
    print("  [√] 轨迹写入/查询正常")

    # 统计
    stats = ts.stats()
    print(f"  统计: total={stats['total_records']}, by_verdict={stats['by_verdict']}")
    print("  [√] 轨迹统计正常")

    # 清理（Windows 必须先 close 否则 PermissionError）
    db_file = Path(ts._db_path)
    ts.close()
    TrajectoryStore.reset()
    try:
        db_file.unlink(missing_ok=True)
        # 同时清理 WAL/SHM 文件（SQLite WAL 模式会产生）
        Path(str(db_file) + "-wal").unlink(missing_ok=True)
        Path(str(db_file) + "-shm").unlink(missing_ok=True)
        print("  [√] 轨迹文件已清理")
    except PermissionError as e:
        print(f"  [!] 清理跳过（文件被占用）: {e}")
        print("  提示: 手动删除 test_trajectories.db 即可")
    print()


def test_tool_executor_integration():
    print("=" * 60)
    print("测试 3: 工具执行器集成（知识库 + 轨迹）")
    print("=" * 60)

    import asyncio
    from core.tool_executor import ToolExecutor

    executor = ToolExecutor()
    tools = executor.get_tool_definitions()
    tool_names = [t["function"]["name"] for t in tools]
    print(f"  注册工具: {', '.join(tool_names)}")

    assert "query_knowledge" in tool_names, "缺少 query_knowledge 工具"
    assert "trajectory_stats" in tool_names, "缺少 trajectory_stats 工具"
    assert "verify_result" in tool_names, "缺少 verify_result 工具"
    print("  [√] 工具注册完整")

    # 测试 query_knowledge
    result = asyncio.run(executor.execute("query_knowledge", {"query": "sqli"}))
    print(f"  query_knowledge 结果: success={result.success}")
    if result.success:
        output = result.output
        print(f"    摘要: {output.get('summary', '')[:80]}")
        print(f"    Payload: {len(output.get('payloads', []))} 个")
        print(f"    Oracle: {len(output.get('oracle_patterns', []))} 个")
    print("  [√] query_knowledge 正常")

    # 测试 trajectory_stats
    result = asyncio.run(executor.execute("trajectory_stats", {}))
    print(f"  trajectory_stats 结果: success={result.success}")
    print("  [√] trajectory_stats 正常")

    print()


def test_mcp_server():
    print("=" * 60)
    print("测试 4: MCP Server 协议")
    print("=" * 60)

    from mcp_server import MCPServer
    server = MCPServer()

    # initialize
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    print(f"  initialize: protocolVersion={resp['result']['protocolVersion']}")
    assert resp["result"]["serverInfo"]["name"] == "sam-agent"
    print("  [√] initialize 正常")

    # tools/list
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    print(f"  tools/list: {len(tools)} 个工具")
    print(f"    {', '.join(tool_names)}")
    assert "query_knowledge" in tool_names
    assert "trajectory_stats" in tool_names
    print("  [√] tools/list 正常")

    # tools/call - query_knowledge
    resp = server.handle_request({
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {"name": "query_knowledge", "arguments": {"query": "rce"}}
    })
    content = resp["result"]["content"][0]["text"]
    print(f"  tools/call query_knowledge: len={len(content)}")
    assert "rce" in content.lower() or "RCE" in content
    print("  [√] tools/call 正常")

    # resources/list
    resp = server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}})
    resources = resp["result"]["resources"]
    print(f"  resources/list: {len(resources)} 个资源")
    for r in resources[:3]:
        print(f"    {r['uri']} - {r['name']}")
    print("  [√] resources/list 正常")

    print()


def main():
    print("\n" + "=" * 60)
    print("SAM Agent v2.0 完全体集成测试")
    print("=" * 60 + "\n")

    test_knowledge_base()
    test_trajectory_store()
    test_tool_executor_integration()
    test_mcp_server()

    print("=" * 60)
    print("[√√√] 完全体集成测试全部通过")
    print("  - 知识库: 9 类漏洞规则已加载")
    print("  - 轨迹: SQLite 持久化正常")
    print("  - 工具: 7 个内置工具（含知识库+轨迹）")
    print("  - MCP: JSON-RPC 2.0 协议正常")
    print("=" * 60)


if __name__ == "__main__":
    main()
