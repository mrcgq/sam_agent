#!/usr/bin/env python3
"""
SAM Agent 冒烟测试
用本地假模型验证完整链路
"""
import sys
import json
import threading
import http.server
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ---------- 假模型服务器 ----------
CALL_COUNT = 0

class FakeModelHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        global CALL_COUNT
        CALL_COUNT += 1

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        payload = json.loads(body) if body else {}
        messages = payload.get("messages", [])
        tools = payload.get("tools", [])

        print(f"  [假模型] 收到请求 #{CALL_COUNT}, messages={len(messages)}, tools={len(tools)}")

        # 第一次：要求写文件
        if CALL_COUNT == 1:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "好的，我来创建文件。",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "/tmp/sam_test_hello.txt", "content": "Hello from SAM Agent!"})
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }]
            }
        # 第二次：写入成功后，核验结果
        elif CALL_COUNT == 2:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "好的，我来核验刚才的操作。",
                        "tool_calls": [{
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "verify_result",
                                "arguments": json.dumps({
                                    "task_description": "创建文件 hello.txt，内容为 Hello from SAM Agent",
                                    "result_output": "已写入 /tmp/sam_test_hello.txt (21 字节)",
                                    "result_exit_code": 0,
                                    "target_path": "/tmp/sam_test_hello.txt"
                                })
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }]
            }
        # 第三次：给出最终答案
        else:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "任务完成。已创建文件 /tmp/sam_test_hello.txt，内容为 'Hello from SAM Agent!'，核验通过。",
                        "tool_calls": []
                    },
                    "finish_reason": "stop"
                }]
            }

        resp_body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def log_message(self, format, *args):
        pass  # 静默


# ---------- 测试 ----------
def run_test():
    print("=" * 60)
    print("SAM Agent 冒烟测试")
    print("=" * 60)

    # 启动假模型服务器
    port = 18934
    server = http.server.HTTPServer(("127.0.0.1", port), FakeModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[√] 假模型服务器启动在 127.0.0.1:{port}")
    time.sleep(0.5)

    # 创建组件
    from config import SAMConfig
    from core.model_adapter import ModelAdapter
    from core.tool_executor import ToolExecutor
    from core.agent_loop import AgentLoop

    model = ModelAdapter(
        base_url=f"http://127.0.0.1:{port}",
        api_key="fake-key",
        model_name="fake-model",
        provider="openai"
    )

    executor = ToolExecutor()
    agent = AgentLoop(
        model_adapter=model,
        tool_executor=executor,
        max_iterations=10,
        log_file=str(Path(__file__).parent / "test_smoke.log")
    )

    # 执行任务
    task = "创建一个文件 hello.txt，内容为 Hello from SAM Agent"
    print(f"\n>>> 任务: {task}")
    print("-" * 60)

    result = agent.run(task)

    # 验证
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    print(f"答案: {result.answer}")
    print(f"迭代: {result.total_iterations}")
    print(f"步骤: {len(result.steps)}")
    print(f"成功: {result.success}")

    # 检查文件是否创建
    test_file = Path("/tmp/sam_test_hello.txt")
    if test_file.exists():
        content = test_file.read_text()
        print(f"[√] 文件已创建: {content}")
        test_file.unlink()
    else:
        print("[!] 文件未创建")

    if result.success and test_file.exists() is False:
        print("\n[√√√] 冒烟测试通过：完整链路 (模型→工具→核验→结果) 已打通")
    else:
        print("\n[!] 冒烟测试未完全通过")

    server.shutdown()


if __name__ == "__main__":
    run_test()
