#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对外视图过滤 · 口语痕迹扫描器（识别层 · 正则那一层）

用途：定位“像说话、不像写文章”的表达。
只做标记，不做替换；改不改、改到哪一档，由人按场合定。

为什么不做“一对一替换表”：口语感大多数藏在句式里，不在词上。
所以本脚本只认“特征”——语气残留、直呼读者、口语句式、经验断言等，
命中不等于要改，只等于“这里值得你看一眼”。

两层分工：
  1) 本脚本（正则层）—— 抓有固定形态的特征，出清单。快、可复现、零误改。
  2) LLM 层 —— 加载本技能的 agent 读本清单，逐段判正式档位，出改写方向。
     判定标准与提示词见 references/口语识别.md。

用法：
    python scan_colloquial.py <文件或目录>
    python scan_colloquial.py <文件或目录> --format json
    python scan_colloquial.py <文件或目录> --register ppt    # 讲伴型：中档也当问题
    python scan_colloquial.py <文件或目录> --register doc    # 独读型（默认）
    python scan_colloquial.py <文件或目录> --min-level mid   # 只看中档以上

支持 .md / .txt / .html / .htm / .xhtml / .docx（自动解包读正文）与目录递归。
"""

import argparse
import json
import os
import re
import sys
import zipfile

DOC_EXTS = {".md", ".markdown", ".txt", ".docx"}
HTML_EXTS = {".html", ".htm", ".xhtml"}
ALL_EXTS = DOC_EXTS | HTML_EXTS

LEVEL_ORDER = {"high": 0, "mid": 1, "low": 2}
LEVEL_LABEL = {"high": "高", "mid": "中", "low": "低"}

# cat=特征类别, pat=正则, level=置信档, note=为什么算口语, hint=书面方向(参考，不是定案)
# level 口径：
#   high —— 形态本身即口语，书面语基本不出现
#   mid  —— 书面也能用，但用在这里偏口语，要看场合
#   low  —— 只是提示，可能是正当用法（引语、比喻、步骤说明）
RULES = [
    # ── A 语气残留 ──────────────────────────────────────────────
    dict(cat="句尾语气词", level="high",
         pat=r"[呗啵咯]|啦(?=[，。！？；：、\s]|$)",
         note="叹词/语气词，无实义",
         hint="直接删"),
    dict(cat="句尾语气词", level="mid",
         pat=r"[吧嘛啊呢哦](?=[，。！？；：、\s]|$)",
         note="句末语气词，书面语不出现（引语内除外）",
         hint="直接删"),
    dict(cat="感叹叠用", level="high",
         pat=r"[！!]{2,}|[？?]{2,}",
         note="连用标点属口语强调",
         hint="收成单个"),
    dict(cat="口语叠词", level="mid",
         pat=r"(?:好好|快快|早早|多多|慢慢)(?:地|的)?(?=[一二三四五六七八九十看想做去来走说学读写练])|慢悠悠|轻轻松松",
         note="口语叠词，书面用中性表述",
         hint="换成中性副词或删"),

    # ── B 直呼读者 ──────────────────────────────────────────────
    dict(cat="直呼读者", level="mid",
         pat=r"(?:^|[\s，。；：、“”])(?:你|您)(?:们)?(?:会|要|可以|应该|得|想|看|把|来|自己)|咱们|大伙儿|大家(?:都|一起来)?",
         note="书面语里读者作主语时不出现人称代词",
         hint="改无人称陈述，或整句删"),

    # ── C 解释性插入（元叙述·态度型）────────────────────────────
    dict(cat="纠偏口吻", level="mid",
         pat=r"关键(?:不是|在于|是)|真正(?:要|该|需要)(?:做|练|学|注意)的|说白了|说穿了|一句话[,，]?记住",
         note="说话人在纠正读者，是课堂口吻",
         hint="改成对事实的直接陈述"),
    dict(cat="解释性插入", level="mid",
         pat=r"其实(?:呢)?|也就是说[,，]|换句话说[,，]",
         note="解释是讲者的活，正文只给事实",
         hint="删，或改成正文自身表述"),

    # ── D 口语强度词 ────────────────────────────────────────────
    dict(cat="口语强度词", level="high",
         pat=r"贼(?:好|快|多|大|难)|超级(?:好|快|多|大|难)|巨(?:好|大|多)|爆(?:好|强)|老(?:是|爱|想)",
         note="口语程度副词，公文与学术文体不用",
         hint="换“尤为/较为/频繁”等中性词"),
    dict(cat="口语强度词", level="mid",
         pat=r"挺(?:好|不错|重要|明显|大|多|快|慢|难|容易|有意思|合适)|特别(?:好|重要|明显|大|多|快|难)|非常(?:之)?(?:好|棒)|还不错|蛮(?:好|大|多)",
         note="口语程度表达，书面倾向中性副词",
         hint="换“较为/颇为/尤为”"),

    # ── E 口语句式模板 ──────────────────────────────────────────
    dict(cat="口语句式", level="high",
         pat=r"别看|别说|甭管|甭|要不然|要不然呢|怎么着",
         note="纯口语句式",
         hint="换成“尽管/不论/否则”等"),
    dict(cat="口语句式", level="mid",
         pat=r"把[^，。！？；]{1,10}给[^，。！？；]{1,10}(?:了|掉|完)|(?:要是|万一)(?:你|他|她|它|我们)?|连[^，。！？；]{1,8}都",
         note="“把…给…”“要是”“连…都”是口语句式",
         hint="改主动陈述或规范句式"),
    dict(cat="口语句式", level="low",
         pat=r"一[^，。！？；]{1,6}就[^，。！？；]{1,10}|什么[^，。！？；]{0,6}都",
         note="“一…就…”“什么…都”书面亦可用，此处仅提示",
         hint="看语境，可保留"),

    # ── F 经验断言 ──────────────────────────────────────────────
    dict(cat="经验断言", level="mid",
         pat=r"你会发现|你会觉得|你会看到|试试(?:看|一下|试)|不得不说|你知道吗|感觉到(?:了)?",
         note="把读者的体验写进正文，是口播动作",
         hint="删，或改客观陈述"),

    # ── G 松散衔接 ──────────────────────────────────────────────
    dict(cat="松散衔接", level="high",
         pat=r"反正|话说回来|回过头来说|这么一来|那(?:就)?这么着",
         note="口语衔接词，无实义",
         hint="直接删"),
    dict(cat="松散衔接", level="mid",
         pat=r"(?:^|[，。；：])\s*然后(?=[，。；：]|$)|(?:^|[，。；：])就是(?:说)?(?=[，。；：])|(?:^|[，。；：])另外(?:呢)(?=[，。；：])|再(?:说|讲)了",
         note="口语衔接词，书面用“随后/此外/再者”或直接删",
         hint="换书面连接词或删"),

    # ── H 松散量词与口语动词 ────────────────────────────────────
    dict(cat="口语量词", level="high",
         pat=r"一堆|好些个|挺多|好多(?:人|事|东西)?|一(?:大)?堆|点儿|一点儿|一丢丢",
         note="口语量词，学术与公文用“大量/若干/略”",
         hint="换“大量/若干/多项”"),
    dict(cat="口语动词", level="high",
         pat=r"搞定|整明白|弄明白|搞明白|搞不清|弄不清|搞错|弄错|瞎(?:说|搞|弄)|折腾",
         note="口语动词，书面用“完成/厘清/处置”",
         hint="换“完成/厘清/处理”"),
    dict(cat="口语动词", level="mid",
         pat=r"搞(?:好|定|清楚|出来|回来)|弄(?:好|清楚|完|出|回来)|整(?:好|完|清楚)",
         note="口语动词“搞 / 弄 / 整”带补语的说法（裸单字不报：会误伤调整、完整、整理）",
         hint="换“完成/厘清/处理/整理”"),

    # ── I 比喻拟人 ──────────────────────────────────────────────
    dict(cat="比喻拟人", level="low",
         pat=r"(?:就)?像[^，。！？；]{1,12}(?:一样|似的)|仿佛|好似|就像是|犹如",
         note="说明文体裁里比喻常需删；文学体裁可留",
         hint="看文体，说明文倾向删"),
]

# 按置信档排序：同一行命中多条时，先把高置信的报到前面
COMPILED = sorted(
    [(r, re.compile(r["pat"], re.MULTILINE)) for r in RULES],
    key=lambda pair: LEVEL_ORDER[pair[0]["level"]],
)

REGISTER_HINT = {
    "doc": "独读型（报告 / 方案 / 申报材料）：高、中档都看；低档仅提示，多半可留。",
    "ppt": "讲伴型（授课 / 路演 / 答辩页）：三档都看，中档在这类载体上通常也该动。",
    "mixed": "混合型：讲的部分按讲伴型收紧，读者自读的部分按独读型放宽。",
}


def collect_files(target):
    if os.path.isfile(target):
        return [target]
    out = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   {"node_modules", "__pycache__", "dist", "build", "_archive"}]
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext in ALL_EXTS:
                out.append(os.path.join(root, name))
    return out


def read_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:
        return []
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&apos;", "'")):
        xml = xml.replace(a, b)
    return xml.split("\n")


def read_lines(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return read_docx(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().split("\n")


def scan_file(path, min_level, max_per_line=3):
    """一行最多报 max_per_line 条：同一行常同时命中多类特征，但报满就够看。"""
    hits = []
    for i, line in enumerate(read_lines(path), 1):
        if not line.strip():
            continue
        n = 0
        for rule, rx in COMPILED:
            if LEVEL_ORDER[rule["level"]] > LEVEL_ORDER[min_level]:
                continue
            m = rx.search(line)
            if not m:
                continue
            frag = line.strip()
            if len(frag) > 90:
                s = max(0, m.start() - 20)
                frag = ("…" if s else "") + frag[s:s + 90] + "…"
            hits.append(dict(line=i, cat=rule["cat"], level=rule["level"],
                             note=rule["note"], hint=rule["hint"], text=frag))
            n += 1
            if n >= max_per_line:
                break
    return hits


def main():
    ap = argparse.ArgumentParser(description="对外视图过滤 · 口语痕迹扫描器")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--register", choices=["doc", "ppt", "mixed"], default="doc",
                    help="载体场合：doc 独读型（默认）/ ppt 讲伴型 / mixed 混合型")
    ap.add_argument("--min-level", choices=["high", "mid", "low"], default="low",
                    help="只看该档及以上：high 只报高置信，默认 low 全报")
    ap.add_argument("--max-per-line", type=int, default=3,
                    help="同一行最多报几条，默认 3")
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print("路径不存在：%s" % args.target, file=sys.stderr)
        return 2

    files = collect_files(args.target)
    results = []
    cat_count, level_count = {}, {}
    total = 0
    for p in files:
        hits = scan_file(p, args.min_level, args.max_per_line)
        if not hits:
            continue
        total += len(hits)
        for h in hits:
            cat_count[h["cat"]] = cat_count.get(h["cat"], 0) + 1
            level_count[h["level"]] = level_count.get(h["level"], 0) + 1
        results.append(dict(file=p, hits=hits))

    if args.format == "json":
        print(json.dumps(dict(scanned=len(files), hits=total, register=args.register,
                              by_level=level_count, by_category=cat_count,
                              results=results), ensure_ascii=False, indent=2))
        return 0

    print("扫描 %d 个文件，命中 %d 处" % (len(files), total))
    print("载体场合：%s" % args.register)
    print("=" * 68)
    if not results:
        print("未发现口语痕迹。")
        return 0
    for r in results:
        print("\n▸ %s" % r["file"])
        for h in r["hits"]:
            print("  L%-5d [%s] %-8s %s" % (h["line"], LEVEL_LABEL[h["level"]],
                                            h["cat"], h["text"]))
    print("\n" + "=" * 68)
    print("按类别统计：")
    for c, n in sorted(cat_count.items(), key=lambda kv: -kv[1]):
        print("  %-12s %d" % (c, n))
    print("\n按置信档统计：高 %d ／ 中 %d ／ 低 %d"
          % (level_count.get("high", 0), level_count.get("mid", 0),
             level_count.get("low", 0)))
    print("\n场合口径：" + REGISTER_HINT[args.register])
    print("提示：扫描只做标记，不做替换。改不改、改到哪一档按场合定；")
    print("      正则只认已知模板，没见过的句式变体交给 LLM 层逐段判。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
