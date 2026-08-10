"""Render the design-only session-bubble source/profile marker proposal."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "designs" / "session-bubble-source-profile-proposal.png"

W, H = 1800, 1120

BG = "#0B1117"
SURFACE = "#10161D"
PANEL = "#0F1721"
CARD = "#141B24"
BORDER = "#263241"
TEXT = "#DCE7F2"
MUTED = "#8492A6"
FAINT = "#5E6A78"
ACCENT = "#F3D27A"
INFO = "#9CCBFF"
SUCCESS = "#8FE3A1"
CLI_BG = "#2A2417"
CLI_BORDER = "#8C7132"
PROFILE_BG = "#172332"
PROFILE_BORDER = "#415B79"


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        path = Path(r"C:\Windows\Fonts\CascadiaMono.ttf")
    elif bold:
        path = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
    else:
        path = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
    return ImageFont.truetype(str(path), size=size)


F_TITLE = font(32, bold=True)
F_SUBTITLE = font(16)
F_PANEL = font(20, bold=True)
F_PANEL_META = font(14)
F_HEADER = font(16, bold=True)
F_BODY = font(15)
F_FOOTER = font(14, bold=True)
F_SMALL = font(13)
F_TINY = font(12)
F_MONO = font(13, mono=True)
F_MONO_BOLD = font(13, bold=True, mono=True)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str, radius: int = 12, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, fill: str, f: ImageFont.FreeTypeFont, *, anchor: str | None = None) -> None:
    draw.text(xy, value, fill=fill, font=f, anchor=anchor)


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, *, fill: str, outline: str, ink: str, width: int, height: int = 26) -> None:
    rounded(draw, (x, y, x + width, y + height), fill, outline, radius=height // 2)
    text(draw, (x + width // 2, y + height // 2), label, ink, F_MONO_BOLD, anchor="mm")


def card(draw: ImageDraw.ImageDraw, x: int, y: int, *, cli: bool, long_profile: bool = False) -> None:
    width, height = 710, 244
    rounded(draw, (x, y, x + width, y + height), CARD, BORDER, radius=12)
    draw.line((x + 1, y + 47, x + width - 1, y + 47), fill="#273241", width=1)
    draw.line((x + 1, y + 1, x + 1, y + height - 1), fill=ACCENT if cli else "#3A485A", width=2)

    title = "21:07:03 | 已处理 48s | 修复 SaveCourse..."
    if not cli:
        title = "21:06:14 | 已处理 2m18s | 修复 SaveCourseEndpoint 调用错误"
    if long_profile:
        title = "21:08:22 | 已处理 12s | 执行测试"
    text(draw, (x + 18, y + 15), title, TEXT, F_HEADER)
    text(draw, (x + width - 22, y + 15), "×", MUTED, F_HEADER, anchor="ra")

    if cli:
        if long_profile:
            pill(draw, x + width - 290, y + 11, "CLI", fill=CLI_BG, outline=CLI_BORDER, ink=ACCENT, width=52)
            pill(draw, x + width - 230, y + 11, "profile: team...", fill=PROFILE_BG, outline=PROFILE_BORDER, ink=INFO, width=154)
        else:
            pill(draw, x + width - 290, y + 11, "CLI", fill=CLI_BG, outline=CLI_BORDER, ink=ACCENT, width=52)
            pill(draw, x + width - 230, y + 11, "profile: muyuan", fill=PROFILE_BG, outline=PROFILE_BORDER, ink=INFO, width=154)

    body_top = y + 70
    if cli:
        body = ["正在读取本地 JSONL 日志", "profile 只作为会话上下文显示，不改变计费通道"]
    else:
        body = ["正在读取本地 JSONL 日志", "Codex Desktop 会话：原有气泡内容保持不变"]
    text(draw, (x + 18, body_top), body[0], "#B8C6D8", F_BODY)
    text(draw, (x + 18, body_top + 29), body[1], "#B8C6D8", F_BODY)

    footer_y = y + height - 36
    text(draw, (x + 18, footer_y), "正在运行测试", ACCENT if cli else MUTED, F_FOOTER)
    text(draw, (x + width - 18, footer_y), "zjxq-admin", FAINT, F_SMALL, anchor="ra")


def callout(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, title: str, lines: list[str], *, accent: str = INFO) -> None:
    rounded(draw, (x, y, x + width, y + 160), SURFACE, BORDER, radius=12)
    draw.ellipse((x + 18, y + 20, x + 28, y + 30), fill=accent)
    text(draw, (x + 40, y + 16), title, accent, F_PANEL_META)
    for index, line in enumerate(lines):
        text(draw, (x + 18, y + 56 + index * 28), line, TEXT if index == 0 else MUTED, F_SMALL)


def main() -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    text(draw, (72, 54), "会话气泡标识设计 · v1", TEXT, F_TITLE)
    text(draw, (74, 101), "仅新增会话来源 / profile 上下文；气泡主体、尺寸、正文、动画和交互保持不变", MUTED, F_SUBTITLE)
    rounded(draw, (1370, 48, 1728, 96), "#10231D", "#285A42", radius=24)
    text(draw, (1549, 72), "DESIGN ONLY · 不改代码", SUCCESS, F_MONO_BOLD, anchor="mm")

    # Two side-by-side states keep the comparison immediate.
    rounded(draw, (72, 170, 858, 548), PANEL, BORDER, radius=16)
    rounded(draw, (942, 170, 1728, 548), PANEL, BORDER, radius=16)
    text(draw, (100, 199), "A  Codex Desktop · 现状保持", TEXT, F_PANEL)
    text(draw, (100, 233), "不显示 CLI 来源标签；没有 profile 元数据时不显示空位", MUTED, F_PANEL_META)
    text(draw, (970, 199), "B  Codex CLI · 增加两个最小标识", TEXT, F_PANEL)
    text(draw, (970, 233), "沿用现有标题行右侧的元信息位，不新增行、不改变卡片高度", MUTED, F_PANEL_META)
    card(draw, 112, 286, cli=False)
    card(draw, 982, 286, cli=True)

    # A small narrow-space example demonstrates the truncation rule.
    rounded(draw, (72, 584, 1110, 1048), PANEL, BORDER, radius=16)
    text(draw, (100, 613), "C  窄空间 / profile 过长", TEXT, F_PANEL)
    text(draw, (100, 647), "只截断 profile 标签内部，标题、状态和气泡外形不被挤压", MUTED, F_PANEL_META)
    card(draw, 112, 696, cli=True, long_profile=True)
    rounded(draw, (1188, 584, 1728, 1048), PANEL, BORDER, radius=16)
    text(draw, (1216, 613), "落位规则", TEXT, F_PANEL)
    rules = [
        (ACCENT, "CLI", "只给 CLI 来源显示，琥珀色，扫描优先级最高"),
        (INFO, "profile", "显示 profile 名称，蓝灰色，最长在标签内省略"),
        (SUCCESS, "Desktop", "Codex Desktop 卡片保持原样，不加来源标签"),
        (MUTED, "interaction", "不新增点击、悬停提示或独立动画"),
    ]
    for index, (color, label, detail) in enumerate(rules):
        yy = 690 + index * 76
        draw.ellipse((1218, yy + 5, 1232, yy + 19), fill=color)
        text(draw, (1250, yy), label, color, F_MONO_BOLD)
        text(draw, (1250, yy + 25), detail, TEXT if index < 2 else MUTED, F_SMALL)
        if index < len(rules) - 1:
            draw.line((1218, yy + 55, 1680, yy + 55), fill="#202B38", width=1)

    text(draw, (74, 1081), "拟议实现尺寸：标签文字约 7–8px；标签只占现有标题行尾部空间；profile 无值则整体隐藏。", FAINT, F_TINY)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
