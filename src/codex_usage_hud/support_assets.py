"""Bundled support QR-code assets."""

from __future__ import annotations

import base64
from functools import lru_cache
from importlib import resources
from pathlib import Path

SUPPORT_QR_ASSETS: tuple[dict[str, str], ...] = (
    {
        "key": "alipay",
        "label": "支付宝",
        "hint": "打开支付宝扫一扫",
        "filename": "sponsor_alipay.jpg",
        "native_filename": "sponsor_alipay.png",
        "mime": "image/jpeg",
    },
    {
        "key": "wechat",
        "label": "微信赞赏",
        "hint": "打开微信扫一扫",
        "filename": "sponsor_wechat.jpg",
        "native_filename": "sponsor_wechat.png",
        "mime": "image/jpeg",
    },
)


@lru_cache(maxsize=1)
def support_qr_payload() -> list[dict[str, str]]:
    """Return support QR assets as renderer-safe data URIs."""
    payload: list[dict[str, str]] = []
    asset_root = resources.files("codex_usage_hud.assets")
    for item in SUPPORT_QR_ASSETS:
        data = asset_root.joinpath(item["filename"]).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        payload.append(
            {
                "key": item["key"],
                "label": item["label"],
                "hint": item["hint"],
                "src": f"data:{item['mime']};base64,{encoded}",
            }
        )
    return payload


def support_qr_asset_paths() -> list[dict[str, str]]:
    """Return local asset paths for native fallback UI helpers."""
    asset_root = resources.files("codex_usage_hud.assets")
    paths: list[dict[str, str]] = []
    for item in SUPPORT_QR_ASSETS:
        filename = item.get("native_filename") or item["filename"]
        try:
            path = Path(str(asset_root.joinpath(filename))).resolve()
        except OSError:
            path = Path(str(asset_root.joinpath(filename)))
        paths.append(
            {
                "key": item["key"],
                "label": item["label"],
                "hint": item["hint"],
                "path": str(path),
            }
        )
    return paths


__all__ = ["SUPPORT_QR_ASSETS", "support_qr_asset_paths", "support_qr_payload"]
