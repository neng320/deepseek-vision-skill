# MiMo Vision API Notes

## 端点与凭据

- **默认端点（免费，无需 key）**：`https://opencode.ai/zen/v1/chat/completions`，模型 `mimo-v2.5-free`（200k 上下文，原生全模态，OpenAI 兼容）。
- **可选官方端点（付费，需 key）**：`https://api.xiaomimimo.com/v1/chat/completions`，模型 `mimo-v2.5` / `mimo-v2.5-pro`。
- **临时高质量备选（用户提供的中转，key 可能随时失效）**：`https://pcph.asia/v1/chat/completions`，模型 `grok-4.5`（xAI，视觉细节识别更强，实测能读出 GUM logo 等小字；按量计费）。
  - 切换命令（key 用环境变量，不落盘）：
    ```bash
    MIMO_VISION_API_KEY="sk-xxx" MIMO_VISION_ENDPOINT="https://pcph.asia/v1/chat/completions" MIMO_VISION_MODEL="grok-4.5" python scripts/vision.py 图片 -q "问题"
    ```
  - 或命令行参数：`--api-key sk-xxx --endpoint https://pcph.asia/v1/chat/completions --model grok-4.5`
- **永久免费多模态（agnes）**：`https://apihub.agnes-ai.com/v1/chat/completions`，模型 `agnes-2.5-flash`（官方承诺全模态永久免费：文本/4K 图像/短视频，API 无 Token 计费、无每日上限；文本默认 RPM 30）。
  - ⚠️ 注意区分：`agnes-2.5-pro-alpha` 是**付费**模型（输入 $0.45/M、输出 $0.90/M），勿误用；`agnes-image-2.1-flash` 走 `/v1/images/generations` 生图端点，非视觉理解。
  - agnes 响应含 `reasoning_content`（思考过程）+ `content`（最终答案），与 deepseek 推理格式一致，脚本只打印 content。
  - 用户提供的第二个 key（sk-2Yjg...）实测 401 无效，勿用。
- 读取顺序：命令行参数 > 环境变量 > `config.json`。
- 环境变量：`MIMO_VISION_API_KEY`、`MIMO_VISION_ENDPOINT`、`MIMO_VISION_MODEL`、`MIMO_VISION_TIMEOUT`、`MIMO_VISION_MAX_TOKENS`、`MIMO_VISION_TEMPERATURE`、`MIMO_VISION_PROXY`。

## ⚠️ Cloudflare 拦截（重要）

`opencode.ai/zen` 走 Cloudflare，脚本默认 UA（如 `mimo-vision-skill/1.0`）会被 403 拦截（`error code: 1010`）。
脚本已内置浏览器 UA（`BROWSER_UA`），**不要**在 headers 里改回自定义 UA。若自己写请求，务必带浏览器 UA。

## Payload

```json
{
  "model": "mimo-v2.5-free",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        {"type": "text", "text": "问题"}
      ]
    }
  ],
  "max_tokens": 2048,
  "temperature": 0.7
}
```

本地图片会被 `scripts/vision.py` 转成 data URL；http(s) 图片 URL 直接透传。图片上限 15MB。

## 响应特点

- `mimo-v2.5-free` 是推理模型：响应含 `message.reasoning` / `reasoning_details` 字段（思考过程），`message.content` 为最终回答，脚本只打印 content。
- 若 content 为空但请求正常，通常是 max_tokens 过小只生成了思考过程，增大 `--max-tokens` 重试。

## 常见错误

- `403 / error code 1010`：Cloudflare 拦截，确认使用了浏览器 UA。
- `401`：api_key 无效或已过期（收费端点），换 key 重试。
- `404 / model not found`：模型名错误，检查 `mimo-v2.5-free` 是否可用。
- `429`：限流/额度不足，降低频率或稍后重试。
- `URLError`：网络不通或超时，检查网络/代理；需要代理时用 `--proxy` 或 `MIMO_VISION_PROXY`。
