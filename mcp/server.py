#!/usr/bin/env python3
"""
MCP server wrapping the mimo-vision skill's vision pipeline.

The skill's `scripts/vision.py` sends images to a vision API with automatic
provider failover (priority order from config.json: agnes-2.5-flash →
step-3.7-flash → mimo-v2.5-free → grok-4.5). This server exposes that
pipeline as typed MCP tools so any AI coding tool (Claude Code, opencode,
Cursor, ...) can do vision recognition with a direct tool call instead of
skill instruction-following — faster and cheaper.

The vision script is imported as a library (`vision.analyze`), keeping a
single source of truth for the failover logic. Long edges >1536px are
auto-resized with Pillow before sending. No subprocess is spawned, so the
MCP server stays responsive while the (network-bound) call runs in a
worker thread.

Run:
    uv run --project <this-dir> python server.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import vision  # noqa: E402  (the skill's vision.py, imported as a library)

CONFIG_PATH = SKILL_ROOT / "config.json"

DEFAULT_QUESTION = "请详细描述这张图片的内容，包括所有可见文字、布局和关键细节。"
OCR_QUESTION = "把图中所有文字原样提取出来，逐行输出。不要描述图片，只输出文字。"
# Per-provider request timeout. Measured on real workloads: agnes ~85s for a
# full OCR (long output). Must stay above the slowest reliable provider.
DEFAULT_TIMEOUT = 100

mcp = FastMCP("mimo_vision_mcp")


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class VisionImagesInput(BaseModel):
    """Common input for image analysis tools."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    images: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="本地图片路径或 http(s) 图片 URL，最多 8 张。本地图片会被自动 base64 编码，超过 15MB 会报错，长边超过 1536px 自动压缩。",
    )
    question: Optional[str] = Field(
        default=None,
        description='对图片的问题（中英文均可）。例如 "图里是什么错误"、"对比这两张图"。不填则默认详细描述图片内容。',
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=64, le=8192,
        description="回答最大 token 数，默认读 config.json（2048）。回答被截断时可调大。",
    )
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0,
        description="采样温度，默认 0.7。OCR/提取任务可调低（如 0.1），创意描述可调高。",
    )
    proxy: Optional[str] = Field(
        default=None,
        description='代理 URL（如 http://127.0.0.1:7890）。本地网络直连可达时不需要。',
    )


class OcrInput(BaseModel):
    """Input for the pure-OCR tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    images: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="本地图片路径或 http(s) 图片 URL，最多 8 张（适合截图、扫描件、报错图）。",
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=64, le=8192,
        description="回答最大 token 数，默认读 config.json（2048）。",
    )
    proxy: Optional[str] = Field(
        default=None,
        description='代理 URL（如 http://127.0.0.1:7890）。本地网络直连可达时不需要。',
    )


# ---------------------------------------------------------------------------
# Core runner: vision.analyze in a worker thread (network-bound, no subprocess)
# ---------------------------------------------------------------------------

def _analyze_sync(images: list[str], question: str,
                  max_tokens: Optional[int], temperature: Optional[float],
                  proxy: Optional[str]) -> tuple[str | None, str | None]:
    """Run the failover chain synchronously. Returns (markdown, error); one is None."""
    try:
        result = vision.analyze(
            images, question,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=DEFAULT_TIMEOUT,
            proxy=proxy,
        )
    except vision.VisionError as exc:
        return None, str(exc)
    except Exception as exc:  # defensive: library bugs must not kill the server
        return None, f"unexpected error: {type(exc).__name__}: {exc}"
    return (
        f"*识别来源：{result['provider']} / {result['model']}*\n\n{result['text']}",
        None,
    )


async def _analyze(images: list[str], question: str,
                   max_tokens: Optional[int], temperature: Optional[float],
                   proxy: Optional[str]) -> tuple[str | None, str | None]:
    """Async wrapper: run the blocking chain off the event loop."""
    return await asyncio.to_thread(_analyze_sync, images, question, max_tokens, temperature, proxy)


def _format_error(msg: str) -> str:
    """Consistent, actionable error text for agents."""
    return (
        f"视觉识别失败。{msg}\n\n"
        "排查建议：确认图片路径/URL 有效；如需代理请传 proxy；"
        "也可用 mimo_vision_providers 查看可用服务。"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="mimo_vision_analyze",
    annotations={
        "title": "分析图片（描述 / OCR / 问答 / 多图对比）",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mimo_vision_analyze(params: VisionImagesInput) -> str:
    """分析一张或多张图片，返回视觉模型（agnes / step / mimo / grok 自动故障转移）的文本回答。

    适合：图片内容描述、OCR 提取文字、读报错截图、理解 UI 布局、多图对比、
    识别图表/验证码、按图片内容回答问题。本地图片自动编码，URL 直接透传。

    Args:
        params (VisionImagesInput):
            - images (list[str], 必填): 本地图片路径或 http(s) URL，最多 8 张
            - question (Optional[str]): 对图片的问题；不填默认详细描述
            - max_tokens (Optional[int]): 回答长度上限，默认 2048
            - temperature (Optional[float]): 采样温度，默认 0.7
            - proxy (Optional[str]): 代理 URL，仅直连失败时需要

    Returns:
        str: Markdown 文本。成功格式:
            *识别来源：{provider}*
            {视觉模型回答}
        失败格式: "视觉识别失败。{原因} 排查建议: ..."
    """
    question = params.question or DEFAULT_QUESTION
    result, error = await _analyze(
        params.images, question, params.max_tokens, params.temperature, params.proxy,
    )
    if error:
        return _format_error(error)
    return result


@mcp.tool(
    name="mimo_vision_ocr",
    annotations={
        "title": "提取图片中的全部文字（OCR）",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mimo_vision_ocr(params: OcrInput) -> str:
    """从一张或多张图片中逐行提取所有可见文字（纯 OCR，不描述图片）。

    适合：截图、扫描件、报错弹窗、界面文案、表格照片。文字按图片中出现
    顺序输出；多张图会合并输出。本地图片自动编码，URL 直接透传。

    Args:
        params (OcrInput):
            - images (list[str], 必填): 本地图片路径或 http(s) URL，最多 8 张
            - max_tokens (Optional[int]): 输出长度上限，默认 2048
            - proxy (Optional[str]): 代理 URL，仅直连失败时需要

    Returns:
        str: Markdown 文本。成功格式:
            *识别来源：{provider}*
            {提取出的全部文字}
        失败格式: "视觉识别失败。{原因} 排查建议: ..."
    """
    result, error = await _analyze(
        params.images, OCR_QUESTION, params.max_tokens, None, params.proxy,
    )
    if error:
        return _format_error(error)
    return result


@mcp.tool(
    name="mimo_vision_providers",
    annotations={
        "title": "查看当前配置的视觉服务列表",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def mimo_vision_providers() -> str:
    """列出 mimo-vision 当前配置的视觉服务（优先级、模型、端点、是否带 key），不发网络请求。

    用于排查：为什么某次识别走了某个服务、某个服务是否可用。

    Returns:
        str: Markdown 表格: 优先级 | 名称 | 模型 | 端点 | 凭据
    """
    if not CONFIG_PATH.exists():
        return "未找到 config.json。服务不可用，请先确认 skill 目录完整。"
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"config.json 解析失败: {exc}。请修复配置文件。"

    providers = cfg.get("providers") or []
    if not providers:
        return "config.json 中未配置 providers 数组。"
    providers = sorted(providers, key=lambda p: int(p.get("priority", 99)))

    lines = ["| 优先级 | 名称 | 模型 | 端点 | 凭据 |", "|---|---|---|---|---|"]
    for p in providers:
        lines.append(
            f"| {p.get('priority', '?')} | {p.get('name', '?')} | {p.get('model', '?')} "
            f"| {p.get('endpoint', '?')} | {'有 key' if p.get('api_key') else '免 key'} |"
        )
    lines.append("")
    lines.append("调用时按优先级依次尝试，失败自动切换到下一个；成功时返回中会标注实际命中的服务。")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
