# SAM Agent - 让模型变聪明

SAM Agent 是完全独立的 Agent 系统，**不需要 DSH**。
模型调用、工具执行、核验、轨迹全部由 SAM 自己控制。

## 目录结构

```
sam_agent/
├── cli.py              # CLI 入口
├── config.py           # 配置管理
├── core/
│   ├── __init__.py
│   ├── model_adapter.py   # 模型统一适配（OpenAI/Anthropic/DeepSeek）
│   ├── tool_executor.py   # 工具执行层
│   ├── agent_loop.py      # Agent 主循环
│   ├── tea_task_oracle.py # 任务核验（复用 V3）
│   ├── skill_trajectory_store.py  # 轨迹记录（复用 V3）
│   └── skill_kb.py        # 知识库（复用 V3）
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip3 install httpx
```

### 2. 配置模型

方式 A：命令行参数
```bash
python3 cli.py --base-url "https://api.deepseek.com" --api-key "你的key" --model "deepseek-chat" "你的任务"
```

方式 B：交互式配置
```bash
python3 cli.py --setup
```

方式 C：环境变量
```bash
export SAM_MODEL_BASE_URL="https://api.deepseek.com"
export SAM_API_KEY="你的key"
export SAM_MODEL_NAME="deepseek-chat"
python3 cli.py "你的任务"
```

### 3. 运行任务

```bash
python3 cli.py "创建一个文件 hello.txt，内容为 Hello World"
```

## 支持的工具

| 工具名 | 功能 |
|---|---|
| `verify_result` | 核验任务结果是否符合预期 |
| `query_knowledge` | 查询知识库 |
| `record_trajectory` | 记录执行轨迹 |
| `execute_command` | 执行系统命令（受限） |
| `read_file` | 读取文件 |
| `write_file` | 写入文件 |

## 支持的模型

| 提供商 | Base URL 示例 | 模型示例 |
|---|---|---|
| OpenAI | `https://api.openai.com` | `gpt-4o` |
| Anthropic | `https://api.anthropic.com` | `claude-sonnet-4-20250514` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 兼容 OpenAI 的中转 | `https://你的中转地址` | 取决于中转 |

## 工作流程

```
用户输入任务
    ↓
Agent 循环开始
    ↓
调用模型 → 模型决定调用哪个工具
    ↓
执行工具 → 得到结果
    ↓
SAM 核验结果（可选）
    ↓
把结果反馈给模型
    ↓
模型继续调用工具 或 给出最终答案
    ↓
循环结束 → 输出结果
```

## 注意事项

1. API Key 不会持久化保存到配置文件（--setup 模式除外）
2. 命令执行工具禁止危险命令（rm -rf / 等）
3. 文件操作限制大小（读取 ≤10MB）
4. 最大迭代次数默认 50，可通过 --max-iterations 调整
