from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


WIDTH = 1600
HEIGHT = 1000
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "background-usage-transparency-concept.png"

FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
FONT_MONO = Path(r"C:\Windows\Fonts\consola.ttf")

C = {
    "canvas": "#0b1016",
    "stage": "#10151b",
    "surface": "#0f171f",
    "header": "#202938",
    "panel": "#131c25",
    "panel_2": "#101820",
    "border": "#2a3a4b",
    "divider": "#233242",
    "text": "#eef4fa",
    "muted": "#8fa0b3",
    "faint": "#65778b",
    "blue": "#8dbdff",
    "blue_strong": "#6f91f2",
    "amber": "#ffc35c",
    "amber_strong": "#e59a2f",
    "green": "#89d59a",
    "red": "#ff7b7b",
}


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO if mono else FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), size=size)


F = {
    "title": font(30, bold=True),
    "subtitle": font(15),
    "eyebrow": font(13, bold=True),
    "h2": font(16, bold=True),
    "h3": font(14, bold=True),
    "body": font(12),
    "body_bold": font(12, bold=True),
    "small": font(11),
    "small_bold": font(11, bold=True),
    "tiny": font(10),
    "tiny_bold": font(10, bold=True),
    "metric": font(23, bold=True),
    "mono": font(10, mono=True),
}


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    *,
    fill: str = C["text"],
    style: str = "body",
    anchor: str | None = None,
) -> None:
    draw.text(xy, value, fill=fill, font=F[style], anchor=anchor)


def panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=7, fill=fill or C["panel"], outline=C["border"], width=1)


def pill(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    value: str,
    *,
    fill: str,
    outline: str,
    color: str,
    style: str = "small_bold",
) -> None:
    draw.rounded_rectangle(box, radius=5, fill=fill, outline=outline, width=1)
    x1, y1, x2, y2 = box
    text(draw, ((x1 + x2) // 2, (y1 + y2) // 2), value, fill=color, style=style, anchor="mm")


def dots(draw: ImageDraw.ImageDraw, x: int, y: int, *, color: str = C["muted"]) -> None:
    for column in range(3):
        draw.ellipse((x + column * 5, y, x + column * 5 + 2, y + 2), fill=color)


def check_button(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.ellipse((x, y, x + 28, y + 28), fill="#17241c", outline="#416f4d", width=1)
    draw.line((x + 8, y + 14, x + 12, y + 18), fill=C["green"], width=2)
    draw.line((x + 12, y + 18, x + 21, y + 9), fill=C["green"], width=2)


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((*start, *end), fill="#8d692a", width=2)
    ex, ey = end
    draw.polygon(((ex, ey), (ex - 10, ey - 5), (ex - 10, ey + 5)), fill="#8d692a")


def metric(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    value: str,
    detail: str,
    *,
    color: str,
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=6, fill=C["panel_2"], outline=C["divider"])
    text(draw, (x1 + 12, y1 + 9), label, fill=C["muted"], style="tiny")
    text(draw, (x1 + 12, y1 + 27), value, fill=color, style="metric")
    text(draw, (x1 + 12, y2 - 17), detail, fill=C["faint"], style="tiny")


def draw_background_bubble(draw: ImageDraw.ImageDraw) -> None:
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((104, 294, 494, 465), radius=9, fill=(0, 0, 0, 175))
    shadow = shadow.filter(ImageFilter.GaussianBlur(17))
    draw._image.paste(shadow, (0, 10), shadow)

    draw.rounded_rectangle((104, 294, 494, 465), radius=9, fill="#111b24", outline="#72501d", width=1)
    draw.rounded_rectangle((104, 294, 494, 331), radius=9, fill="#202938")
    draw.rectangle((104, 323, 494, 331), fill="#202938")
    draw.ellipse((121, 307, 131, 317), fill=C["amber"])
    text(draw, (140, 303), "Codex App 后台任务使用了额度", style="body_bold")
    text(draw, (140, 319), "今天 09:07 - 09:10", fill=C["faint"], style="tiny")
    check_button(draw, 451, 299)

    text(draw, (121, 347), "Memory consolidation", style="h3")
    pill(
        draw,
        (340, 343, 474, 366),
        "gpt-5.6-terra",
        fill="#17243a",
        outline="#2e4c71",
        color=C["blue"],
        style="tiny_bold",
    )
    text(draw, (121, 377), "9 次请求", fill=C["muted"], style="small")
    text(draw, (196, 377), "612.8k tokens", fill=C["text"], style="body_bold")
    text(draw, (326, 377), "估算 $0.742", fill=C["amber"], style="body_bold")
    draw.line((121, 405, 477, 405), fill=C["divider"])

    draw.ellipse((122, 420, 142, 440), outline="#536b83", width=1)
    draw.line((128, 430, 136, 430), fill=C["blue"], width=1)
    draw.line((133, 426, 137, 430), fill=C["blue"], width=1)
    draw.line((133, 434, 137, 430), fill=C["blue"], width=1)
    text(draw, (151, 417), "查看后台用量记录", fill=C["blue"], style="small_bold")
    text(draw, (477, 418), "本机日志已归因", fill=C["green"], style="tiny", anchor="ra")


def draw_settings(draw: ImageDraw.ImageDraw) -> None:
    x1, y1, x2, y2 = 548, 181, 1518, 922
    draw.rounded_rectangle((x1, y1, x2, y2), radius=8, fill="#0d151d", outline="#33465a")
    draw.rounded_rectangle((x1, y1, x2, y1 + 53), radius=8, fill="#182231")
    draw.rectangle((x1, y1 + 45, x2, y1 + 53), fill="#182231")
    text(draw, (568, 199), "codex-usage-hud v1.x.x", style="h3")
    text(draw, (568, 218), "Codex App 非会话用量审计", fill=C["muted"], style="tiny")
    draw.rounded_rectangle((1470, 193, 1498, 221), radius=5, fill="#2b3746")
    text(draw, (1484, 207), "×", fill=C["muted"], style="body_bold", anchor="mm")

    draw.rectangle((x1, 234, x2, 279), fill="#10161d")
    draw.line((x1, 279, x2, 279), fill=C["divider"])
    tabs = (
        (568, 615, "设置", False),
        (623, 670, "存储", False),
        (678, 805, "后台用量  2", True),
        (813, 932, "请作者喝咖啡", False),
        (940, 1026, "版本更新", False),
    )
    for tx1, tx2, label, active in tabs:
        if active:
            draw.rounded_rectangle((tx1, 243, tx2, 270), radius=5, fill=C["header"])
        text(
            draw,
            ((tx1 + tx2) // 2, 256),
            label,
            fill=C["amber"] if active else C["muted"],
            style="small_bold" if active else "small",
            anchor="mm",
        )

    metric(draw, (568, 298, 760, 374), "今天后台费用", "$0.961", "Provider 账单差额", color=C["amber"])
    metric(draw, (770, 298, 962, 374), "今天后台 Tokens", "1.05M", "本机日志已确认", color=C["blue"])
    metric(draw, (972, 298, 1164, 374), "后台任务", "2", "14 次 API 请求", color=C["text"])
    metric(draw, (1174, 298, 1498, 374), "使用模型", "terra + luna", "2 个模型 · 2 项功能", color=C["green"])

    draw.rounded_rectangle((568, 387, 1498, 426), radius=6, fill=C["panel_2"], outline=C["divider"])
    pill(draw, (580, 395, 630, 418), "今天", fill=C["header"], outline="#3b4b5e", color=C["amber"], style="tiny_bold")
    text(draw, (646, 401), "近 7 天", fill=C["muted"], style="tiny")
    text(draw, (696, 401), "近 30 天", fill=C["muted"], style="tiny")
    draw.line((758, 394, 758, 419), fill=C["divider"])
    pill(draw, (774, 395, 907, 418), "全部功能", fill=C["panel"], outline=C["border"], color=C["muted"], style="tiny")
    draw.polygon(((893, 404), (899, 404), (896, 408)), fill=C["muted"])
    pill(draw, (916, 395, 1040, 418), "全部模型", fill=C["panel"], outline=C["border"], color=C["muted"], style="tiny")
    draw.polygon(((1026, 404), (1032, 404), (1029, 408)), fill=C["muted"])
    pill(draw, (1358, 395, 1486, 418), "导出审计记录", fill=C["header"], outline="#3b4b5e", color=C["text"], style="tiny_bold")

    # History list
    panel(draw, (568, 440, 923, 874), fill="#111922")
    text(draw, (586, 457), "后台任务历史", style="body_bold")
    text(draw, (905, 458), "今天 2 项", fill=C["muted"], style="tiny", anchor="ra")
    draw.line((568, 480, 923, 480), fill=C["divider"])

    draw.rounded_rectangle((579, 493, 912, 603), radius=6, fill="#1b211f", outline="#8a6122", width=1)
    draw.ellipse((593, 508, 601, 516), fill=C["amber"])
    text(draw, (610, 502), "Memory consolidation", style="body_bold")
    pill(draw, (784, 500, 898, 522), "gpt-5.6-terra", fill="#17243a", outline="#2e4c71", color=C["blue"], style="tiny")
    text(draw, (593, 534), "09:07 - 09:10 · 9 次请求", fill=C["muted"], style="tiny")
    text(draw, (593, 554), "612.8k tokens", style="body_bold")
    text(draw, (714, 554), "估算 $0.742", fill=C["amber"], style="body_bold")
    pill(draw, (819, 546, 898, 568), "已确认", fill="#11231c", outline="#315947", color=C["green"], style="tiny_bold")
    text(draw, (593, 580), "Memory Writing Agent · Phase 2", fill=C["faint"], style="tiny")

    draw.rounded_rectangle((579, 615, 912, 715), radius=6, fill=C["panel"], outline=C["divider"])
    draw.ellipse((593, 630, 601, 638), fill=C["blue_strong"])
    text(draw, (610, 624), "Context-aware suggestions", style="body_bold")
    pill(draw, (784, 622, 898, 644), "gpt-5.6-luna", fill="#17243a", outline="#2e4c71", color=C["blue"], style="tiny")
    text(draw, (593, 656), "09:01 - 09:02 · 5 次请求", fill=C["muted"], style="tiny")
    text(draw, (593, 676), "441.7k tokens", style="body_bold")
    text(draw, (714, 676), "估算 $0.219", fill=C["amber"], style="body_bold")
    text(draw, (593, 697), "Generate hyperpersonalized suggestions", fill=C["faint"], style="tiny")

    text(draw, (586, 745), "更早记录", fill=C["muted"], style="small_bold")
    text(draw, (586, 768), "昨天及更早的后台任务只保留在此处，不生成桌面气泡。", fill=C["faint"], style="tiny")
    draw.rounded_rectangle((579, 795, 912, 849), radius=6, fill="#10171e", outline="#202c38")
    text(draw, (593, 807), "2026-07-19  ·  Memory consolidation", fill=C["muted"], style="tiny")
    text(draw, (593, 827), "gpt-5.6-terra  ·  188.2k  ·  估算 $0.218", fill=C["faint"], style="tiny")

    # Request detail
    panel(draw, (935, 440, 1498, 874), fill="#111922")
    text(draw, (953, 457), "任务详情", style="body_bold")
    pill(draw, (1399, 451, 1482, 473), "本机已归因", fill="#11231c", outline="#315947", color=C["green"], style="tiny_bold")
    draw.line((935, 480, 1498, 480), fill=C["divider"])
    text(draw, (953, 493), "Memory consolidation", style="h3")
    text(draw, (953, 518), "Memory Writing Agent: Phase 2 (Consolidation)", fill=C["blue"], style="mono")

    metadata = (
        (953, 545, "模型", "gpt-5.6-terra"),
        (1128, 545, "线程", "019f7d10…4a10"),
        (953, 579, "进程", "Codex App · PID 6956"),
        (1128, 579, "时段", "09:07:18 - 09:10:10"),
    )
    for mx, my, label, value in metadata:
        text(draw, (mx, my), label, fill=C["faint"], style="tiny")
        text(draw, (mx, my + 15), value, style="small_bold")
    text(draw, (953, 621), "工作目录", fill=C["faint"], style="tiny")
    text(draw, (953, 637), r"C:\Users\zjxqm\.codex\memories", fill=C["muted"], style="mono")

    draw.line((953, 660, 1480, 660), fill=C["divider"])
    text(draw, (953, 675), "请求明细", style="body_bold")
    pill(draw, (1030, 670, 1067, 692), "9", fill=C["header"], outline=C["border"], color=C["amber"], style="tiny_bold")
    request_rows = (
        (704, "09:07:18", "POST /responses", "71.4k", "$0.086"),
        (733, "09:07:34", "POST /responses", "68.9k", "$0.082"),
        (762, "09:08:02", "POST /responses", "74.1k", "$0.090"),
    )
    for index, (ry, when, endpoint, tokens, cost) in enumerate(request_rows):
        fill = "#18222d" if index == 0 else C["panel_2"]
        outline = "#3a5068" if index == 0 else C["divider"]
        draw.rounded_rectangle((953, ry, 1480, ry + 25), radius=4, fill=fill, outline=outline)
        text(draw, (965, ry + 6), when, fill=C["muted"], style="tiny")
        text(draw, (1030, ry + 6), endpoint, fill=C["blue"], style="mono")
        text(draw, (1338, ry + 6), tokens, style="tiny_bold")
        text(draw, (1433, ry + 6), cost, fill=C["amber"], style="tiny_bold")

    text(draw, (953, 803), "请求内容", style="body_bold")
    text(draw, (1479, 805), "已截断 · 可展开", fill=C["muted"], style="tiny", anchor="ra")
    draw.rounded_rectangle((953, 824, 1480, 859), radius=5, fill="#0c131a", outline=C["divider"])
    text(draw, (966, 835), "Memory Writing Agent: Phase 2 (Consolidation)…", fill="#b6c5d4", style="mono")

    draw.line((x1, 887, x2, 887), fill=C["divider"])
    text(draw, (568, 895), "费用标记为“估算”时来自 HUD 价格表；完成 Provider 对账后显示“账单”。", fill=C["muted"], style="tiny")
    pill(draw, (1395, 894, 1498, 916), "关闭", fill=C["header"], outline="#3b4b5e", color=C["text"], style="tiny_bold")


def build() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), C["canvas"])
    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        shade = int(16 - min(5, y / HEIGHT * 5))
        draw.line((0, y, WIDTH, y), fill=(shade, shade + 5, shade + 11))

    text(draw, (68, 49), "APP-LEVEL USAGE · TWO-SURFACE DESIGN", fill=C["amber"], style="eyebrow")
    text(draw, (68, 78), "Codex App 非会话用量透明化 · 修订方案", style="title")
    text(
        draw,
        (68, 122),
        "后台任务不属于任何会话：当天新事件由 PySide6 气泡提醒，完整历史与逐请求内容进入设置页独立 Tab。",
        fill=C["muted"],
        style="subtitle",
    )

    draw.rounded_rectangle((54, 156, 1546, 952), radius=8, fill=C["stage"], outline="#202a35")

    text(draw, (82, 181), "1  当天提醒 · PySide6 气泡", style="h2")
    text(draw, (82, 208), "独立于会话气泡栈，不计入任何会话 HUD。", fill=C["muted"], style="small")
    pill(draw, (361, 178, 511, 203), "仅当天 · 未确认事件", fill="#251c0f", outline="#68471a", color=C["amber"], style="tiny_bold")

    draw.rounded_rectangle((82, 226, 516, 498), radius=8, fill="#14191f", outline="#252f3a")
    draw.rounded_rectangle((82, 226, 516, 266), radius=8, fill="#1e1e1f")
    draw.rectangle((82, 258, 516, 266), fill="#1e1e1f")
    text(draw, (100, 240), "Codex App", fill="#d8dee6", style="small_bold")
    dots(draw, 481, 246, color="#707982")
    draw.rounded_rectangle((103, 280, 435, 288), radius=3, fill="#252a30")
    draw.rounded_rectangle((103, 478, 379, 486), radius=3, fill="#252a30")
    draw_background_bubble(draw)

    arrow(draw, (487, 429), (544, 429))
    text(draw, (501, 442), "点击", fill=C["amber"], style="tiny")

    panel(draw, (82, 516, 516, 714), fill="#111922")
    text(draw, (100, 535), "气泡规则", style="h3")
    rules = (
        (568, "同一后台线程的多次请求聚合为一个事件"),
        (599, "对勾关闭后记录 event_id，不再重复弹出"),
        (630, "昨天及更早记录只进历史，不生成气泡"),
        (661, "气泡链接复用工作目录热点，跳转并选中记录"),
        (692, "查看详情不自动确认；只有点击对勾才关闭"),
    )
    for ry, value in rules:
        draw.ellipse((101, ry + 1, 108, ry + 8), fill=C["green"] if ry == 599 else C["blue"])
        text(draw, (119, ry - 4), value, fill=C["muted"], style="small")

    panel(draw, (82, 731, 516, 912), fill="#111922")
    text(draw, (100, 750), "状态边界", style="h3")
    pill(draw, (100, 780, 190, 805), "未确认", fill="#251c0f", outline="#68471a", color=C["amber"], style="tiny_bold")
    text(draw, (205, 785), "今天的新后台任务，可显示气泡", fill=C["muted"], style="small")
    pill(draw, (100, 818, 190, 843), "已关闭", fill="#17241c", outline="#315947", color=C["green"], style="tiny_bold")
    text(draw, (205, 823), "仍留在历史，不再显示气泡", fill=C["muted"], style="small")
    pill(draw, (100, 856, 190, 881), "历史", fill="#17202a", outline="#344557", color=C["blue"], style="tiny_bold")
    text(draw, (205, 861), "早于今天，仅在设置页查询", fill=C["muted"], style="small")

    text(draw, (548, 154), "2  历史审计 · 设置 / 后台用量", style="h2")
    text(draw, (873, 160), "气泡跳转后自动高亮对应任务", fill=C["amber"], style="small_bold")
    arrow(draw, (544, 429), (678, 255))
    draw_settings(draw)

    return image


if __name__ == "__main__":
    build().save(OUTPUT, format="PNG", optimize=True)
    print(OUTPUT)
