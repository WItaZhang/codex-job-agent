"""Generate bilingual product overviews with the Python standard library.

Run: uv run --locked python docs/assets/build_architecture.py
Wide and compact compositions share the same product relationships.
"""

from html import escape
from pathlib import Path

OUT = Path(__file__).parent
INK = "#242936"
MUTED = "#67707C"
RULE = "#D5D9DF"
ACCENT = "#575588"
PALE = "#F5F4FA"
FEEDBACK = "#8A919B"


class Figure:
    def __init__(self, width, height, zh):
        self.zh = zh
        title = self.tr("Codex Job Agent: how it works", "Codex Job Agent：如何工作")
        desc = self.tr(
            "Your experience and goals guide Codex to discover opportunities and tailor applications, "
            "with more attention to strong matches. Your submission rules independently determine "
            "automatic application or your review before submission. Track applications and next "
            "actions, then use your feedback to refine the next search.",
            "你的经历和目标指导 Codex 广泛发现机会、准备申请，高匹配岗位获得更多准备精力。"
            "你另行设定投递边界，决定自动投递或审核后投递。查看投递记录和待办，"
            "再用你的反馈调整下一轮求职。",
        )
        self.parts = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            ),
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(desc)}</desc>',
            "<defs>",
        ]
        for name, color in (("flow", INK), ("feedback", FEEDBACK)):
            self.parts.append(
                f'<marker id="{name}" markerWidth="8" markerHeight="8" refX="7" refY="4" '
                'orient="auto-start-reverse" markerUnits="userSpaceOnUse">'
                f'<path d="M0 0 L7 4 L0 8 Z" fill="{color}"/></marker>'
            )
        self.parts += [
            "</defs>",
            f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
            (
                '<style>text{font-family:Arial,"Microsoft YaHei","PingFang SC",sans-serif}'
                '.serif{font-family:Georgia,"Times New Roman",serif}</style>'
            ),
        ]

    def tr(self, en, zh):
        return zh if self.zh else en

    def text(self, x, y, label, size=22, color=INK, weight=400, anchor="middle", serif=False):
        family = ' class="serif"' if serif else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" fill="{color}"{family}>{escape(label)}</text>'
        )

    def rect(self, x, y, width, height, fill="#FFFFFF", stroke=RULE, radius=3, sw=1.3):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def path(self, d, color=INK, width=1.6, arrow=False, dashed=False):
        extra = ' stroke-dasharray="5 5"' if dashed else ""
        if arrow:
            marker = "feedback" if color == FEEDBACK else "flow"
            extra += f' marker-end="url(#{marker})"'
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linejoin="round" stroke-linecap="round"{extra}/>'
        )

    def circle(self, x, y, radius, fill, stroke="none"):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{fill}" stroke="{stroke}"/>')

    def profile(self, x, y):
        self.rect(x, y, 80, 94, "#FAFAFB", "#A7ADB8")
        self.circle(x + 40, y + 27, 10, "#FFFFFF", MUTED)
        self.path(f"M{x + 23} {y + 52} C{x + 24} {y + 34},{x + 56} {y + 34},{x + 57} {y + 52}", MUTED)
        self.path(f"M{x + 19} {y + 69} H{x + 61} M{x + 26} {y + 80} H{x + 54}", RULE, 2)

    def opportunities(self, x, y):
        # Accent marks relevant opportunities, not a measured ranking.
        for dx, dy, relevant in (
            (0, 0, False),
            (48, -10, True),
            (96, 2, False),
            (0, 43, True),
            (48, 33, False),
            (96, 45, True),
        ):
            stroke = "#8C89AF" if relevant else RULE
            self.rect(x + dx, y + dy, 37, 31, "#FFFFFF", stroke, radius=2)
            self.path(f"M{x + dx + 8} {y + dy + 11} H{x + dx + 28}", stroke, 1.6)
            self.path(f"M{x + dx + 8} {y + dy + 20} H{x + dx + 21}", stroke, 1.2)

    def application(self, x, y):
        self.rect(x + 9, y - 7, 62, 82, "#E9E7F3", "#C8C4DB", radius=2)
        self.rect(x, y, 62, 82, "#FFFFFF", ACCENT, radius=2, sw=1.5)
        self.path(f"M{x + 12} {y + 17} H{x + 40}", ACCENT, 3)
        for dy, end in ((32, 49), (43, 45), (57, 49), (68, 39)):
            self.path(f"M{x + 12} {y + dy} H{x + end}", "#ADA8C5", 1.6)

    def progress(self, x, y):
        self.rect(x, y, 86, 83, "#FFFFFF", "#A7ADB8")
        for dy, end in ((21, 65), (42, 59), (63, 68)):
            self.circle(x + 17, y + dy, 3, MUTED)
            self.path(f"M{x + 29} {y + dy} H{x + end}", "#B2B7C0", 2)

    def agent(self, x, y):
        self.rect(x, y, 480, 242, PALE, "#B7B3CE", radius=5)
        self.text(x + 240, y + 35, "Codex Job Agent", 27, ACCENT, 600, serif=True)
        self.opportunities(x + 61, y + 81)
        self.application(x + 321, y + 76)
        self.path(f"M{x + 215} {y + 119} H{x + 280}", arrow=True)
        self.text(x + 128, y + 190, self.tr("Discover broadly", "广泛发现机会"), 22, weight=600)
        self.text(x + 354, y + 190, self.tr("Tailor applications", "重点准备申请"), 22, weight=600)
        self.text(x + 354, y + 219, self.tr("Extra care for strong matches", "高匹配机会，投入更多精力"), 18, ACCENT)

    def write(self, name):
        (OUT / name).write_text("\n".join(self.parts + ["</svg>", ""]), encoding="utf-8", newline="\n")


def wide(zh=False):
    f = Figure(1240, 448, zh)
    # Submission policy is independent of match quality.
    f.path("M120 151 V61 H906 V122", FEEDBACK, 1.3, arrow=True, dashed=True)
    f.text(906, 150, f.tr("Your submission rules", "你的投递边界"), 19, MUTED)
    f.profile(80, 151)
    f.text(120, 286, f.tr("Your goals", "你的求职目标"), 23, weight=600)
    f.text(120, 315, f.tr("Experience · preferences", "经历 · 偏好"), 17, MUTED)
    f.path("M178 222 H247", arrow=True)

    f.agent(255, 105)
    f.path("M743 222 H770")
    f.path("M770 222 V196 H800", arrow=True)
    f.path("M770 222 V282 H800", arrow=True)
    f.rect(809, 172, 196, 48, stroke="#A7ADB8")
    f.rect(809, 258, 196, 48, stroke="#A7ADB8")
    f.text(907, 203, f.tr("Auto-apply", "自动投递"), 22)
    f.text(907, 289, f.tr("Review → apply", "审核后投递"), 22)
    f.path("M1005 196 H1040 V239")
    f.path("M1005 282 H1040 V239 H1082", arrow=True)
    f.progress(1092, 195)
    f.text(1135, 316, f.tr("Track progress", "查看进展"), 22, weight=600)

    f.path("M1135 334 V390 H120 V333", FEEDBACK, 1.3, arrow=True, dashed=True)
    f.text(628, 423, f.tr("Your feedback shapes the next search", "你的反馈，调整下一轮求职"), 19, MUTED)
    f.write(f"architecture.{'zh-CN' if zh else 'en'}.svg")


def compact(zh=False):
    f = Figure(620, 802, zh)
    f.text(310, 56, f.tr("Your goals", "你的求职目标"), 28, weight=600)
    f.text(310, 88, f.tr("Experience · preferences", "经历 · 偏好"), 21, MUTED)
    f.path("M310 104 V125", arrow=True)
    f.agent(70, 134)
    f.path("M310 376 V405", arrow=True)
    f.text(310, 440, f.tr("Your rules", "你的投递边界"), 23, MUTED)
    f.path("M432 47 H591 V432 H407", FEEDBACK, 1.3, arrow=True, dashed=True)
    f.path("M310 456 V472 H179 V488", arrow=True)
    f.path("M310 472 H441 V488", arrow=True)

    f.rect(70, 496, 218, 62, stroke="#A7ADB8")
    f.rect(332, 496, 218, 62, stroke="#A7ADB8")
    f.text(179, 535, f.tr("Auto-apply", "自动投递"), 26)
    f.text(441, 535, f.tr("Review → apply", "审核后投递"), 26)
    f.path("M179 558 V600 H310 V641", arrow=True)
    f.path("M441 558 V600 H310")
    f.text(310, 679, f.tr("Track progress", "查看进展"), 28, weight=600)
    f.text(310, 712, f.tr("Applications · next actions", "投递记录 · 待办事项"), 21, MUTED)
    f.path("M310 730 V755 H29 V47 H204", FEEDBACK, 1.3, arrow=True, dashed=True)
    f.text(310, 790, f.tr("Your feedback shapes the next search", "你的反馈，调整下一轮求职"), 20, MUTED)
    f.write(f"architecture.{'zh-CN' if zh else 'en'}.compact.svg")


if __name__ == "__main__":
    for chinese in (False, True):
        wide(chinese)
        compact(chinese)
