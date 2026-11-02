"""
SAM Agent 模型统一适配层
支持 OpenAI / Anthropic / DeepSeek 等主流 API
"""
import json
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ModelResponse:
    """模型响应"""
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class ModelAdapter:
    """模型统一适配器"""

    def __init__(self, base_url: str, api_key: str, model_name: str, provider: str = "openai"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.provider = provider
        self._session = None

    def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.Client(timeout=60)
        return self._session

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None) -> ModelResponse:
        """统一调用接口"""
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, tools)
        else:
            # OpenAI / DeepSeek 兼容接口
            return self._chat_openai(messages, tools)

    def _chat_openai(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None) -> ModelResponse:
        """OpenAI 兼容接口调用"""
        session = self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = session.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"模型调用失败: {resp.status_code} {resp.text}")

        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = []
        for tc in message.get("tool_calls", []):
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=args
            ))

        return ModelResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data
        )

    def _chat_anthropic(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None) -> ModelResponse:
        """Anthropic Messages API 调用"""
        session = self._get_session()
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        # Anthropic 消息格式转换
        anthropic_messages = []
        system_prompt = None
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            elif m["role"] == "tool":
                # Anthropic 工具结果格式
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", "")
                        }
                    ]
                })
            else:
                anthropic_messages.append({
                    "role": "user" if m["role"] == "user" else "assistant",
                    "content": m.get("content", "")
                })

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": anthropic_messages,
            "max_tokens": 4096
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {})
                }
                for t in tools
            ]

        resp = session.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic 调用失败: {resp.status_code} {resp.text}")

        data = resp.json()

        tool_calls = []
        content_parts = data.get("content", [])
        for part in content_parts:
            if part.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=part["id"],
                    name=part["name"],
                    arguments=part.get("input", {})
                ))

        # 提取文本内容
        text_content = None
        for part in content_parts:
            if part.get("type") == "text":
                text_content = part["text"]
                break

        stop_reason = data.get("stop_reason", "end_turn")
        finish_reason = "tool_calls" if tool_calls else ("stop" if stop_reason == "end_turn" else stop_reason)

        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw=data
        )

    def list_tools_for_api(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将工具定义转换为 API 需要的格式"""
        if self.provider == "anthropic":
            return [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {})
                }
                for t in tools
            ]
        return tools
