"""端到端测试：stdio client -> server.py -> vision.py -> 视觉 API。

用法：
    uv run --project . python test_client.py

默认使用两张示例图片（请替换为你自己的图片路径）：
    IMG_OCR     - 含文字的截图/扫描件（测试 OCR）
    IMG_ANALYZE - 任意图片（测试通用分析）
"""

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).parent / "server.py"
# TODO: 替换为你的本地图片路径
IMG_OCR = r"path/to/screenshot.png"
IMG_ANALYZE = r"path/to/photo.jpg"


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"[tools] {len(tools.tools)} registered:")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description.splitlines()[0][:60]}")
            print()

            print("[call] mimo_vision_ocr ...")
            t0 = asyncio.get_event_loop().time()
            r = await session.call_tool("mimo_vision_ocr", {"params": {"images": [IMG_OCR]}})
            print(f"[ocr elapsed {asyncio.get_event_loop().time()-t0:.0f}s]")
            text = r.content[0].text
            print(text[:600])
            print("...")

            print("[call] mimo_vision_analyze ...")
            t0 = asyncio.get_event_loop().time()
            r = await session.call_tool(
                "mimo_vision_analyze",
                {"params": {"images": [IMG_ANALYZE], "question": "这张图是什么？用一句话概括。"}},
            )
            print(f"[analyze elapsed {asyncio.get_event_loop().time()-t0:.0f}s]")
            print(r.content[0].text[:400])

            print("[call] mimo_vision_providers ...")
            r = await session.call_tool("mimo_vision_providers", {})
            print(r.content[0].text[:500])


if __name__ == "__main__":
    asyncio.run(main())
