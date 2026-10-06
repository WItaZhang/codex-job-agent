"""Build the bilingual SVG system figures with only the Python standard library.

Run from any directory: python docs/assets/build_architecture.py
The generated SVGs are the checked-in documentation assets.
"""

from html import escape
from pathlib import Path

OUT = Path(__file__).parent
INK = "#20252C"
MUTED = "#626A73"
RULE = "#C5C9CC"
PURPLE = "#625781"
TEAL = "#326B68"


class Figure:
    def __init__(self, width, height, zh, desc):
        self.zh = zh
        self.parts = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            ),
            '<title id="title">Codex Job Agent — system architecture</title>',
            f'<desc id="desc">{escape(desc)}</desc>',
            "<defs>",
            (
                '<marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" '
                'orient="auto-start-reverse" markerUnits="userSpaceOnUse">'
                f'<path d="M0 0 L7 4 L0 8 Z" fill="{INK}"/></marker>'
            ),
            (
                '<marker id="audit" markerWidth="8" markerHeight="8" refX="7" refY="4" '
                'orient="auto-start-reverse" markerUnits="userSpaceOnUse">'
                f'<path d="M0 0 L7 4 L0 8 Z" fill="{TEAL}"/></marker>'
            ),
            "</defs>",
            f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
            (
                '<style>text{font-family:Arial,"Microsoft YaHei","PingFang SC",sans-serif;'
                f"fill:{INK}"
                '} .serif{font-family:Georgia,"Noto Serif CJK SC",SimSun,serif}'
                '.mono{font-family:"Cascadia Code",Consolas,"Microsoft YaHei",monospace}</style>'
            ),
        ]

    def text(self, x, y, label, size=20, color=INK, weight=400, anchor="start", family="", italic=False):
        attributes = f' class="{family}"' if family else ""
        if italic:
            attributes += ' font-style="italic"'
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" style="fill:{color}"{attributes}>{escape(label)}</text>'
        )

    def rect(self, x, y, width, height, fill="#FFFFFF", stroke=RULE, sw=1.2):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def path(self, d, color=INK, width=1.5, arrow=False, both=False, dashed=False):
        extra = ' stroke-dasharray="5 4"' if dashed else ""
        marker = "audit" if color == TEAL else "arrow"
        if arrow or both:
            extra += f' marker-end="url(#{marker})"'
        if both:
            extra += f' marker-start="url(#{marker})"'
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{extra}/>')

    def panel(self, x, y, letter, label, size=20):
        self.text(x, y, f"({letter})", size, weight=700, family="serif")
        self.text(x + size * 2 + 4, y, label, size, weight=600)

    def storage(self, x, y, width, height, title, subtitle, size=20):
        mid = x + width / 2
        self.parts.append(
            f'<path d="M{x} {y + 12} C{x} {y - 4},{x + width} {y - 4},{x + width} {y + 12} '
            f'V{y + height - 12} C{x + width} {y + height + 4},{x} {y + height + 4},{x} {y + height - 12} Z" '
            f'fill="#FAFAF8" stroke="{RULE}" stroke-width="1.2"/>'
        )
        self.path(f"M{x} {y + 12} C{x} {y + 28},{x + width} {y + 28},{x + width} {y + 12}", RULE, 1.2)
        self.text(mid, y + 49, title, size, anchor="middle", weight=600)
        self.text(mid, y + 76, subtitle, size - 4, MUTED, anchor="middle")

    def write(self, name):
        (OUT / name).write_text("\n".join(self.parts + ["</svg>", ""]), encoding="utf-8", newline="\n")


def wide(zh=False):
    def tr(en, cn):
        return cn if zh else en

    desc = tr(
        "Personal context conditions the Codex planner. Repository skills guide one reasoning host. "
        "Structured actions and observations connect Codex to local tools and persistent state. "
        "Reading job sites is separate from submission, which checks authorization, packet version and budget. "
        "Observed acknowledgement or an unknown result returns to the tools. Packets are frozen before "
        "submission; confirmed submissions can be sampled for independent reviews and user feedback. "
        "Development validation and reviewed changes are separate from runtime activity.",
        "个性化上下文输入 Codex；仓库技能指导同一个推理宿主。动作与观察连接 Codex、本地工具和持久状态。"
        "岗位读取与提交分开，提交前检查授权、申请包版本和预算。网页确认或未知结果返回工具。"
        "提交前冻结申请包，确认投递后可抽样进行独立评审并反馈给用户。开发验证及经审核的修改与运行期分开。",
    )
    f = Figure(1180, 792, zh, desc)
    f.text(40, 37, "CODEX JOB AGENT", 16, weight=700)
    f.text(1140, 37, tr("SYSTEM ARCHITECTURE / 01", "系统架构 / 01"), 14, MUTED, anchor="end", family="mono")
    f.path("M40 56 H1140", INK, 1.2)
    f.panel(40, 103, "a", tr("Personal context", "个性化上下文"))
    f.panel(330, 103, "b", tr("Agent–tool interaction", "Agent 与工具交互"))
    f.path("M921 95 H953", INK, 1.4, arrow=True)
    f.text(964, 101, tr("Runtime", "运行期"), 14, MUTED)
    f.path("M921 119 H953", TEAL, 1.4, arrow=True, dashed=True)
    f.text(964, 125, tr("Evidence / review", "证据与评审"), 14, MUTED)

    f.rect(40, 148, 206, 204, "#FAFAF8")
    context = [
        (180, tr("Confirmed facts", "已确认事实"), tr("experience · sources", "经历与来源")),
        (248, tr("Preferences", "偏好与约束"), tr("roles · constraints", "目标岗位与硬性条件")),
        (316, tr("Authorization", "投递授权"), tr("automatic / review", "自动投递 / 用户审核")),
    ]
    for y, label, detail in context:
        f.text(58, y, label, 20, weight=600)
        f.text(58, y + 24, detail, 16, MUTED)
    f.path("M58 219 H228 M58 287 H228", RULE, 0.8)
    f.path("M246 213 H323", arrow=True)
    f.path("M246 322 H289 V397 H323", arrow=True)
    f.text(299, 357, tr("policy", "授权策略"), 15, MUTED)

    f.rect(330, 148, 440, 144, "#F5F3F8", PURPLE, 1.35)
    f.text(352, 187, "Codex", 30, family="serif", weight=700)
    f.text(748, 184, tr("REASONING HOST", "推理宿主"), 13, PURPLE, anchor="end", family="mono")
    f.text(352, 221, tr("Dynamic planning & language reasoning", "动态规划与语言推理"), 20)
    f.path("M352 239 H748", "#D8D2E3", 0.8)
    f.text(
        352,
        271,
        tr("4 skills: adapt · discover · prepare · coordinate", "四个技能：适配 · 发现 · 准备 · 协调"),
        16,
        PURPLE,
    )

    f.path("M451 299 V369", arrow=True)
    f.text(438, 340, tr("actions", "结构化动作"), 16, MUTED, anchor="end", family="mono")
    f.path("M655 369 V299", arrow=True)
    f.text(668, 340, tr("observations", "工具观察"), 16, MUTED, family="mono")

    f.rect(330, 376, 440, 172)
    f.text(352, 409, tr("Local tools", "本地工具"), 23, weight=600)
    f.text(748, 407, "PYTHON", 13, MUTED, anchor="end", family="mono")
    f.text(352, 440, tr("discover · inspect · prepare", "发现岗位 · 观察表单 · 准备材料"), 18, MUTED)
    f.rect(348, 461, 404, 69, "#F0F6F4", TEAL, 1.15)
    f.text(364, 487, tr("Submission gate", "提交前检查"), 20, TEAL, weight=600)
    f.text(364, 514, tr("authorization · packet version · budget", "授权 · 申请包版本 · 预算"), 17, TEAL)

    f.storage(
        40, 443, 206, 91, tr("Persistent state", "持久状态"), tr("jobs · packets · events", "岗位 · 申请包 · 事件")
    )
    f.text(143, 559, "SQLite", 14, MUTED, anchor="middle", family="mono")
    f.path("M253 488 H323", both=True)

    f.rect(935, 166, 205, 221, "#FAFAF8")
    f.text(1037.5, 201, tr("Job sites / ATS", "招聘网站 / ATS"), 22, weight=600, anchor="middle")
    f.path("M953 218 H1122", RULE, 0.8)
    f.text(1037.5, 249, tr("Public openings", "公开岗位"), 19, anchor="middle")
    f.text(1037.5, 313, tr("Application forms", "申请表单"), 19, anchor="middle")
    f.path("M965 340 H1110 M965 353 H1075 M965 366 H1095", "#D7DBDD", 1)

    f.path("M777 426 H825 V248 H928", both=True)
    f.text(837, 235, tr("read / inspect", "读取 / 观察"), 15, MUTED)
    f.path("M777 505 H875 V314 H928", arrow=True)
    f.text(890, 452, tr("authorized", "获准后"), 16, TEAL)
    f.text(890, 475, tr("submission", "提交"), 16, TEAL)
    f.path("M1037 394 V572 H731 V555", arrow=True)
    f.text(1127, 596, tr("acknowledgement / unknown", "网页确认 / 结果未知"), 16, MUTED, anchor="end")

    f.path("M354 555 V640 H153 V648", TEAL, 1.35, arrow=True, dashed=True)
    f.text(368, 591, tr("packet + trace", "申请包与执行记录"), 15, TEAL)
    f.path("M40 607 H1140", RULE, 1)
    f.panel(40, 633, "c", tr("Evidence & evaluation", "证据与评测"))
    f.text(
        1140, 633, tr("runtime audit  /  development validation", "运行期抽检  /  开发期验证"), 14, MUTED, anchor="end"
    )

    f.rect(40, 655, 226, 77, "#FBF9F5", "#C7BAA5")
    f.text(57, 686, tr("Frozen packet", "冻结的申请包"), 20, weight=600)
    f.text(57, 715, tr("before submission", "提交前保留快照"), 16, MUTED)
    f.path("M273 693 H309", TEAL, 1.35, arrow=True, dashed=True)
    f.rect(316, 655, 288, 77, "#F4F8F6", "#AABFBA")
    f.text(333, 686, tr("Independent review", "独立评审"), 20, weight=600)
    f.text(333, 715, tr("confirmed sample → user inbox", "确认后抽样 → 用户待办"), 16, MUTED)
    f.path("M611 693 H719", TEAL, 1.35, arrow=True, dashed=True)
    f.text(665, 680, tr("findings", "问题证据"), 14, TEAL, anchor="middle")
    f.path("M680 649 V741", RULE, 1, dashed=True)
    f.rect(726, 655, 414, 77, "#FFFFFF", RULE)
    f.text(743, 686, tr("Development validation", "开发期验证"), 20, weight=600)
    f.text(743, 715, tr("diagnose → regress → reviewed changes", "根因分析 → 回归比较 → 审核修改"), 16, MUTED)
    f.path("M40 753 H1140", INK, 1)
    f.text(
        40,
        776,
        tr(
            "Model judgment guides work; explicit contracts govern external actions.",
            "模型判断指导任务；显式契约约束外部操作。",
        ),
        15,
        MUTED,
        italic=not zh,
    )
    f.text(1140, 776, tr("No runtime self-modification", "运行期不自行改写实现"), 14, MUTED, anchor="end")
    f.write(f"architecture.{'zh-CN' if zh else 'en'}.svg")


def compact(zh=False):
    def tr(en, cn):
        return cn if zh else en

    f = Figure(
        680,
        1128,
        zh,
        tr(
            "Compact system diagram. User context conditions a Codex action–observation loop. Local tools "
            "separate discovery and inspection from authorized submission, persist jobs and evidence, "
            "and observe outcomes. Runtime sampled reviews are distinct from development validation.",
            "窄屏系统图：用户上下文指导 Codex 的动作与观察循环。本地工具区分读取与获准后的提交，"
            "持久保存岗位和证据并观察结果。运行期抽样评审与开发期验证分别展示。",
        ),
    )
    f.text(28, 36, "CODEX JOB AGENT", 21, weight=700)
    f.text(652, 36, "01", 19, MUTED, anchor="end", family="mono")
    f.path("M28 57 H652", INK, 1.2)
    f.panel(28, 102, "a", tr("Personal context", "个性化上下文"), 24)
    f.rect(28, 126, 624, 98, "#FAFAF8")
    f.path("M236 142 V182 M444 142 V182", RULE, 0.8)
    for x, en, cn in [
        (132, "Confirmed facts", "确认事实"),
        (340, "Preferences", "偏好与约束"),
        (548, "Authorization", "投递授权"),
    ]:
        f.text(x, 164, tr(en, cn), 23, weight=600, anchor="middle")
    f.text(
        340,
        205,
        tr("user-confirmed profile & submission policy", "用户确认的资料与提交策略"),
        20,
        MUTED,
        anchor="middle",
    )
    f.path("M340 231 V286", arrow=True)

    f.panel(28, 268, "b", tr("Agent & tools", "Agent 与工具"), 24)
    f.rect(28, 294, 624, 155, "#F5F3F8", PURPLE, 1.3)
    f.text(50, 337, "Codex", 33, weight=700, family="serif")
    f.text(628, 333, tr("REASONING HOST", "推理宿主"), 18, PURPLE, anchor="end", family="mono")
    f.text(50, 376, tr("plan · research · compose · revise", "动态规划 · 研究岗位 · 写作 · 重新判断"), 24)
    f.path("M50 397 H630", "#D8D2E3", 0.8)
    f.text(50, 428, tr("guided by four repository skills", "四个仓库技能提供领域方法"), 22, PURPLE)
    f.path("M215 456 V526", arrow=True)
    f.text(198, 498, tr("actions", "动作"), 22, MUTED, anchor="end", family="mono")
    f.path("M370 526 V456", arrow=True)
    f.text(388, 498, tr("observations", "观察"), 22, MUTED, family="mono")

    f.rect(28, 534, 404, 230)
    f.text(50, 572, tr("Local tools", "本地工具"), 27, weight=600)
    f.text(50, 609, tr("discover · inspect · prepare", "发现 · 观察 · 材料准备"), 22, MUTED)
    f.rect(48, 632, 364, 111, "#F0F6F4", TEAL, 1.15)
    f.text(66, 667, tr("Submission gate", "提交前检查"), 25, TEAL, weight=600)
    f.text(66, 703, tr("authorization · version", "授权 · 申请包版本"), 23, TEAL)
    f.text(66, 730, tr("budget · submit intent", "预算 · 提交意图"), 23, TEAL)
    f.rect(500, 555, 152, 188, "#FAFAF8")
    f.text(576, 599, tr("Job sites", "招聘网站"), 24, weight=600, anchor="middle")
    f.text(576, 633, "ATS", 24, anchor="middle")
    f.path("M518 649 H634", RULE, 1)
    f.text(576, 683, tr("receipt", "网页回执"), 21, MUTED, anchor="middle")
    f.text(576, 716, tr("or unknown", "或结果未知"), 21, MUTED, anchor="middle")
    f.path("M439 587 H493", both=True)
    f.text(466, 569, tr("read", "读取"), 17, MUTED, anchor="middle")
    f.path("M439 703 H493", arrow=True)
    f.text(466, 685, tr("apply", "提交"), 17, TEAL, anchor="middle")
    f.path("M110 771 V788 H340", RULE, 1.3)
    f.text(
        340,
        818,
        tr("Persistent state · jobs, packets, events", "持久状态：岗位、申请包与事件"),
        23,
        MUTED,
        anchor="middle",
    )

    f.path("M28 850 H652", RULE, 1)
    f.panel(28, 891, "c", tr("Evidence & evaluation", "证据与评测"), 24)
    f.path("M340 920 V1030", RULE, 1, dashed=True)
    f.text(28, 942, tr("Runtime audit", "运行期抽检"), 24, TEAL, weight=600)
    f.text(28, 978, tr("Frozen packet → sample", "冻结申请包 → 确认后抽样"), 22)
    f.text(28, 1014, tr("Review → user inbox", "独立评审 → 用户待办"), 22)
    f.text(370, 942, tr("Development", "开发期验证"), 24, weight=600)
    f.text(370, 978, tr("RCA · regression", "根因分析 · 回归比较"), 22)
    f.text(370, 1014, tr("Reviewed changes", "审核后的实现修改"), 22)
    f.text(
        28,
        1066,
        tr("Freeze before submit; sample after confirmation.", "提交前冻结证据，确认投递后抽样。"),
        21,
        MUTED,
        italic=not zh,
    )
    f.path("M28 1090 H652", INK, 1)
    f.text(28, 1116, tr("No runtime self-modification", "运行期不自行改写实现"), 19, MUTED)
    f.write(f"architecture.{'zh-CN' if zh else 'en'}.compact.svg")


if __name__ == "__main__":
    for language in (False, True):
        wide(language)
        compact(language)
