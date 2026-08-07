#!/usr/bin/env python3
"""Send images to a vision API with automatic provider failover.

Supports a prioritized provider list (config.json "providers" array): the
script tries each provider in order of priority and falls back to the next
one on failure (network error / HTTP error / empty response). Local images
are base64-encoded ONCE and reused across providers.

Config precedence: CLI flag > env var > config.json.

Examples:
  python vision.py screenshot.png -q "提取图中所有文字"
  python vision.py a.png b.png -q "对比两张图的区别"
  python vision.py https://example.com/img.png --json
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = SKILL_ROOT / "config.json"

DEFAULT_ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"
DEFAULT_MODEL = "mimo-v2.5-free"
# 重要：opencode.ai/zen 走 Cloudflare，默认 UA（如 "mimo-vision-skill/1.0"）
# 会被 403 拦截（error code 1010），必须使用浏览器 UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB
DEFAULT_MAX_PIXEL = 1536  # 长边超过则自动压缩（mimo 等模型对大图会失明）
_TMP_FILES = []  # 压缩产生的临时文件，main 结束时清理


class VisionError(Exception):
    """Raised when a provider fails; message is human-readable."""


def load_config():
    config = {}
    if DEFAULT_CONFIG_PATH.exists():
        try:
            config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[mimo-vision] broken config.json: {exc}", file=sys.stderr)
            sys.exit(1)
    return config


def env_or_config(config, key, env_name, default=None):
    return os.environ.get(env_name) or config.get(key, default)


def maybe_resize(path_str, max_pixel):
    """长边超过 max_pixel 时用 Pillow 压缩到临时文件。

    Returns (file_to_read, tmp_path_to_cleanup)。无 Pillow 或压缩失败时
    返回原文件，仅 stderr 提示，不中断。
    """
    if not HAS_PIL:
        return path_str, None
    try:
        with Image.open(path_str) as im:
            w, h = im.size
            if max(w, h) <= max_pixel:
                return path_str, None
            im.thumbnail((max_pixel, max_pixel))
            fmt = (im.format or "JPEG").upper()
            if fmt == "PNG":
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                im.save(tmp, "PNG")
            else:
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
                rgb = im.convert("RGB") if im.mode != "RGB" else im
                rgb.save(tmp, "JPEG", quality=90)
            _TMP_FILES.append(tmp)
            print(
                f"[mimo-vision] image resized {w}x{h} -> {im.size[0]}x{im.size[1]}",
                file=sys.stderr,
            )
            return tmp, tmp
    except Exception as exc:
        print(f"[mimo-vision] resize failed, use original: {exc}", file=sys.stderr)
        return path_str, None


def image_to_url(value, max_pixel=None, no_resize=False):
    """Return an OpenAI-compatible content part for one image input."""
    if value.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": value}}
    path = Path(value)
    if not path.exists():
        print(f"[mimo-vision] image not found: {value}", file=sys.stderr)
        sys.exit(1)
    size = path.stat().st_size
    if size <= 0:
        print(f"[mimo-vision] empty image file: {value}", file=sys.stderr)
        sys.exit(1)
    if size > MAX_IMAGE_BYTES:
        print(
            f"[mimo-vision] image too large ({size} bytes, max {MAX_IMAGE_BYTES}): {value}",
            file=sys.stderr,
        )
        sys.exit(1)
    read_path = path
    if max_pixel and not no_resize:
        read_path, _ = maybe_resize(str(path), max_pixel)
    mime, _ = mimetypes.guess_type(str(read_path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(Path(read_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def image_to_content_parts(images, max_pixel=None, no_resize=False):
    """Encode all images once; the result is reused across providers."""
    return [image_to_url(img, max_pixel=max_pixel, no_resize=no_resize) for img in images]


def build_providers(config, args, env_override):
    """Return an ordered list of provider dicts.

    Priority: explicit CLI override > MIMO_VISION_* env vars > config
    "providers" array (sorted by priority) > legacy top-level config fields.
    """
    providers = []

    cli_forced = args.model is not None or args.endpoint is not None or args.api_key is not None
    env_forced = any(env_override.get(k) for k in ("model", "endpoint", "api_key"))

    if cli_forced or env_forced:
        p = {
            "name": "cli",
            "model": args.model or env_override.get("model") or env_or_config(
                config, "model", "MIMO_VISION_MODEL", DEFAULT_MODEL
            ),
            "endpoint": args.endpoint or env_override.get("endpoint") or env_or_config(
                config, "endpoint", "MIMO_VISION_ENDPOINT", DEFAULT_ENDPOINT
            ),
            "api_key": args.api_key or env_override.get("api_key") or env_or_config(
                config, "api_key", "MIMO_VISION_API_KEY"
            ),
        }
        providers.append(p)
        return providers

    listed = config.get("providers")
    if isinstance(listed, list) and listed:
        for p in listed:
            providers.append(
                {
                    "name": p.get("name") or p.get("model") or "provider",
                    "model": p.get("model", DEFAULT_MODEL),
                    "endpoint": p.get("endpoint", DEFAULT_ENDPOINT),
                    "api_key": p.get("api_key") or os.environ.get("MIMO_VISION_API_KEY"),
                    "priority": int(p.get("priority", 99)),
                }
            )
        providers.sort(key=lambda p: p["priority"])
        return providers

    providers.append(
        {
            "name": "default",
            "model": env_or_config(config, "model", "MIMO_VISION_MODEL", DEFAULT_MODEL),
            "endpoint": env_or_config(config, "endpoint", "MIMO_VISION_ENDPOINT", DEFAULT_ENDPOINT),
            "api_key": env_or_config(config, "api_key", "MIMO_VISION_API_KEY"),
        }
    )
    return providers


def build_payload(content_parts, question, model, max_tokens, temperature):
    content = list(content_parts)
    content.append({"type": "text", "text": question})
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def send_request(payload, endpoint, api_key, timeout, proxy):
    """POST to one endpoint. Returns parsed JSON or raises VisionError."""
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}" if api_key else None,
        "Content-Type": "application/json",
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
    }
    headers = {k: v for k, v in headers.items() if v is not None}
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("error", {}).get("message", "") or body.get("message", "")
        except Exception:
            pass
        raise VisionError(f"HTTP {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise VisionError(f"network error: {exc.reason}")
    except Exception as exc:
        raise VisionError(f"{type(exc).__name__}: {exc}")


def extract_text(data):
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        preview = json.dumps(data, ensure_ascii=False)[:500]
        raise VisionError(f"unexpected response shape: {preview}")
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
        return "\n".join(parts).strip()
    return str(content).strip() if content is not None else ""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze images with vision API (auto failover across providers)"
    )
    parser.add_argument("images", nargs="+", help="local image paths or http(s) image URLs")
    parser.add_argument(
        "-q",
        "--question",
        default="请详细描述这张图片的内容，包括所有可见文字、布局和关键细节。",
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON response")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--endpoint", default=None)
    parser.add_argument(
        "--proxy",
        default=None,
        help="explicit proxy URL (e.g. http://127.0.0.1:7890)",
    )
    parser.add_argument(
        "--no-resize",
        action="store_true",
        help="disable auto-resize of large images",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()

    env_override = {}
    for key, env_name in (
        ("model", "MIMO_VISION_MODEL"),
        ("endpoint", "MIMO_VISION_ENDPOINT"),
        ("api_key", "MIMO_VISION_API_KEY"),
    ):
        if os.environ.get(env_name):
            env_override[key] = os.environ[env_name]

    timeout = args.timeout or int(
        env_or_config(config, "timeout", "MIMO_VISION_TIMEOUT", 120)
    )
    max_tokens = args.max_tokens or int(
        env_or_config(config, "max_tokens", "MIMO_VISION_MAX_TOKENS", 2048)
    )
    if args.temperature is not None:
        temperature = args.temperature
    else:
        temperature = float(
            env_or_config(config, "temperature", "MIMO_VISION_TEMPERATURE", 0.7)
        )

    providers = build_providers(config, args, env_override)
    max_pixel = int(env_or_config(config, "max_pixel", "MIMO_VISION_MAX_PIXEL", DEFAULT_MAX_PIXEL))
    content_parts = image_to_content_parts(
        args.images, max_pixel=max_pixel, no_resize=args.no_resize
    )  # 编码一次，多 provider 复用
    proxy = args.proxy or env_or_config(config, "proxy", "MIMO_VISION_PROXY")

    failures = []
    try:
        for p in providers:
            payload = build_payload(content_parts, args.question, p["model"], max_tokens, temperature)
            try:
                data = send_request(payload, p["endpoint"], p["api_key"], timeout, proxy)
            except VisionError as exc:
                failures.append(f"{p['name']} ({p['model']}): {exc}")
                continue
            try:
                text = extract_text(data)
            except VisionError as exc:
                failures.append(f"{p['name']} ({p['model']}): {exc}")
                continue
            if not text:
                failures.append(
                    f"{p['name']} ({p['model']}): empty response (max_tokens 可能太小，只生成了思考过程)"
                )
                continue

            # 成功：provider 信息走 stderr，不污染 stdout 文本
            print(f"[via {p['name']} / {p['model']}]", file=sys.stderr)
            if args.json:
                out = dict(data)
                out["provider"] = p["name"]
                out["model"] = p["model"]
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print(text)
            return

        print("[mimo-vision] all providers failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    finally:
        for tmp in _TMP_FILES:
            try:
                os.unlink(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    main()
