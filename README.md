# DeepSeek Vision Skill — 视觉识别技能

为无视觉能力的 AI 主模型（如 DeepSeek）提供图像识别能力：把图片交给多模态视觉模型分析，自动故障转移，保证识别链路永不中断。

## 特性

- **自动路由**：按优先级依次尝试多个视觉服务，失败自动切换到下一个（网络错误 / HTTP 4xx/5xx / 空响应）。
- **多服务支持**：已内置 mimo-v2.5-free（免费）、step-3.7-flash（官方，原生多模态）、agnes-2.5-flash（永久免费多模态）三个 provider，可自由增删。
- **完整识别能力**：图像描述、OCR 文字提取、UI/截图理解、图表解读、多图对比、验证码识别。
- **OpenAI 兼容**：所有 provider 均为 `/chat/completions` 协议，脚本零依赖（仅标准库）。
- **零冗余编码**：多张图片只 base64 编码一次，多 provider 复用。

## 目录结构

```
deepseek-vision-skill/
├── SKILL.md          # 技能触发与使用说明
├── config.json       # provider 路由配置（不含任何密钥）
├── scripts/
│   └── vision.py     # 核心脚本（自动路由 + 视觉调用，含 analyze() 库接口）
├── mcp/              # MCP server（把视觉管线暴露为 typed tools）
│   ├── server.py     # FastMCP server：mimo_vision_analyze / ocr / providers
│   ├── test_client.py
│   └── pyproject.toml
└── references/
    └── api.md        # API 说明与排错
```

## MCP Server（可选）

把视觉识别作为 MCP 工具暴露给任意 AI 编程工具（Claude Code / opencode / Cursor 等），直接工具调用即可看图，无需走技能指令流。

```bash
cd mcp
uv sync          # 安装 fastmcp + pillow
uv run python server.py        # 启动 server（stdio 协议）
uv run python test_client.py   # 端到端测试
```

注册 3 个工具：

| 工具 | 功能 |
|---|---|
| `mimo_vision_analyze` | 分析图片（描述 / 问答 / 多图对比），自动故障转移 |
| `mimo_vision_ocr` | 逐行提取图片中的全部文字（纯 OCR） |
| `mimo_vision_providers` | 查看当前配置的视觉服务优先级与凭据状态 |

MCP server 直接复用 `scripts/vision.py` 的 `analyze()` 库接口（单一事实来源），不派生子进程，长任务在线程中执行不阻塞事件循环。图片长边 >1536px 自动压缩。

配置示例（opencode / Claude Code 的 MCP 配置）：

```json
{
  "mcpServers": {
    "mimo-vision": {
      "command": "uv",
      "args": ["run", "--project", "绝对路径/mimo-vision/mcp", "python", "server.py"]
    }
  }
}
```

## 使用

### 1. 配置密钥（可选）

免费端点 `opencode.ai/zen` 无需 key；其他 provider 通过环境变量注入（**不要**写入 config.json）：

```bash
export MIMO_VISION_API_KEY="sk-xxx"        # 作用于所有需要 key 的 provider
export MIMO_VISION_ENDPOINT="https://..."  # 可选：覆盖端点
export MIMO_VISION_MODEL="step-3.7-flash"     # 可选：覆盖模型
```

### 2. 调用

```bash
python scripts/vision.py <图片路径或URL> -q "描述这张图片"

# 多图对比
python scripts/vision.py a.png b.png -q "对比这两张图"

# 纯 OCR
python scripts/vision.py shot.png -q "把图中所有文字原样提取出来"

# 原始 JSON（含 token 统计、命中的 provider）
python scripts/vision.py shot.png --json

# 强制指定某个服务（绕过路由）
python scripts/vision.py shot.png --model step-3.7-flash --api-key sk-xxx --endpoint https://...
```

### 3. 路由配置

`config.json` 的 `providers` 数组按 `priority` 升序尝试：

| priority | name | model | endpoint | 说明 |
|---|---|---|---|---|
| 1 | mimo-free | mimo-v2.5-free | opencode.ai/zen/v1 | 免费免 key，开箱即用 |
| 2 | step | step-3.7-flash | api.stepfun.com/step_plan/v1 | 官方稳定，需自配 key |
| 3 | agnes | agnes-2.5-flash | apihub.agnes-ai.com/v1 | 永久免费多模态，需自配 key |

默认路由全部使用免费/官方服务，无任何中转依赖。需要 key 的服务通过环境变量 `MIMO_VISION_API_KEY` 注入。

成功后 stderr 打印 `[via xxx / model]` 标识命中的服务，stdout 保持纯净文本。

## 安全说明

- 仓库**不含任何 API 密钥**。密钥请通过环境变量提供，或在使用前自行填入本地 `config.json`。
- 请勿将含密钥的 `config.json` 提交到仓库。

## 注意事项

- `opencode.ai/zen` 走 Cloudflare，脚本已内置浏览器 UA；请勿改成自定义 UA（会被 403 拦截）。
- 图片上限 15MB，过大请先压缩。
- 部分模型是推理模型：若响应 content 为空，通常是 `max_tokens` 太小只生成了思考过程，调大重试。
