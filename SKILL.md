---
name: mimo-vision
description: Provide vision to non-vision models (e.g. deepseek) by calling the Xiaomi MiMo V2.5 multimodal chat-completions API. Use when the user provides or references an image, screenshot, photo, scan, chart, UI capture, or image URL and the current model cannot see it — including describing image contents, OCR/extracting visible text, reading error screenshots, understanding UI layout, comparing multiple images, interpreting charts/diagrams, and answering questions about visual content (图片、截图、照片、OCR、UI、图表、验证码). Invoke scripts/vision.py with local image paths or URLs; the script prints the vision model's text answer, or raw JSON with --json.
---

# MiMo Vision

当主模型没有视觉能力（如 DeepSeek）、用户提供图片或要求理解图片内容时，自动把图片交给视觉模型分析（默认 grok-4.5 优先，自动故障转移），并把视觉模型输出转述给用户。

## 何时使用

- 用户提供本地图片路径、截图、照片、扫描件、图片 URL，需要描述内容、提取文字（OCR）、阅读报错截图、理解 UI 布局、识别图表、对比多张图、看验证码等。
- 当前模型无法直接查看图片（本身无视觉，或图片没有作为附件进入上下文）。
- 需要把视觉结果继续用于后续任务（例如按截图里的文案改代码、按图表数据写分析）。

如果当前模型本身能直接看到图片，不要重复调用，直接基于图片回答。

## 自动路由（默认行为）

`config.json` 的 `providers` 数组定义了按优先级排列的视觉服务，脚本按 `priority` 升序依次尝试，**某个 provider 失败（网络错误 / HTTP 4xx/5xx / 空响应）自动切换到下一个**：

| 优先级 | 名称 | 模型 | 端点 | 说明 |
|---|---|---|---|---|
| 1 | grok | grok-4.5 | pcph.asia/v1（中转） | 高质量，key 临时，可能失效 |
| 2 | mimo-free | mimo-v2.5-free | opencode.ai/zen/v1 | 免费免 key，日常主力（grok 失效的首选落点） |
| 3 | agnes | agnes-2.5-flash | apihub.agnes-ai.com/v1 | 永久免费，原生多模态（4K 图像），兜底 |
| 4 | step | step-3.7-flash | api.stepfun.com/step_plan/v1（官方） | 原生多模态（图片+视频），最稳兜底 |

- 成功后 stderr 打印 `[via grok / grok-4.5]` 标识实际命中的服务，stdout 保持纯净文本。
- 全部失败时列出每个 provider 的失败原因并退出码非 0。
- 图片只编码一次，多个 provider 复用，无额外开销。
- 临时强制指定某个服务：命令行 `--model/--endpoint/--api-key` 或环境变量 `MIMO_VISION_MODEL/ENDPOINT/API_KEY`（此时不走路由）。

## 配置凭据

- 默认配置已写入 `config.json`，开箱即用（grok 带 key，mimo 免 key）。
- 新增/替换服务：在 `providers` 数组加一项 `{name, model, endpoint, api_key(可省略), priority}`。
- 保留旧格式兼容：顶层 `model` / `endpoint` / `api_key` 字段仍可用（无 providers 数组时生效）。

> 注意：`opencode.ai/zen` 走 Cloudflare，脚本已内置浏览器 UA；勿改成脚本自定义 UA（会被 403 拦截）。

## 调用方式

```bash
python "C:\Users\Administrator\.workbuddy\skills\mimo-vision\scripts\vision.py" <图片路径或URL> -q "问题"
```

常用参数：

- 多张图：`vision.py a.png b.png -q "对比这两张图"`
- 纯 OCR：`vision.py shot.png -q "把图中所有文字原样提取出来"`
- 输出原始 JSON：加 `--json`（含 usage token 统计、provider 标识）
- 强制指定服务：`--model grok-4.5 --api-key sk-xxx --endpoint https://...`
- 需要代理时：`--proxy http://127.0.0.1:7890`（或环境变量 `MIMO_VISION_PROXY`）

## 结果处理

- 脚本成功时把视觉模型的文本回答打印到 stdout（stderr 有 `[via xxx]` 来源标识），失败时退出码非 0、错误写到 stderr。
- 把输出当作图片内容的事实来源，结合上下文继续完成用户任务；若回答明显与图片不符，可在回复里注明。
- 一次可传多张图；本地图片会被自动 base64 编码，URL 直接透传。
- 图片上限 15MB；单张过大请先压缩。
- API 报错排查、配置字段、错误码见 `references/api.md`（只在需要时读取）。
