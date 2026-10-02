#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对外视图过滤 · 内部痕迹扫描器

用途：定位“只该内部看、却出现在对外成品里”的内容。
只做定位，不改文件；删不删由人按四条判据决定。

用法：
    python scan_internal_traces.py <文件或目录>
    python scan_internal_traces.py <文件或目录> --format json

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

# cat=命中类别, pat=正则, exts=适用扩展名集合, note=提示
RULES = [
    dict(cat="稿件版本史", pat=r"v\d{1,2}\s*稿|v\d+\.\d+\s*稿|相对\s*v\d|上一版|前一版|此前的版本|改版说明|修订记录",
         exts=DOC_EXTS, note="改稿过程记录，对外只留结论"),
    dict(cat="评审编号", pat=r"对应\s*(?:R|C|工程|反方|审稿)\s*[#\d]|[（(]\s*(?:R|C)\d{1,3}\s*[)）]|反方\s*[#第]\s*\d|工程\s*[#第]\s*\d",
         exts=DOC_EXTS, note="内部评审编号，成品里全删"),
    dict(cat="自指元叙述", pat=r"本(?:稿|文|节|章|页|方案|技能|文档)(?:说明|介绍|旨在|不|将|拟)|本文档|如上所述|下文将",
         exts=DOC_EXTS | HTML_EXTS, note="主语是文档自身，按读者测试审"),
    dict(cat="章末要点", pat=r"^\s*(?:本章|本节|全文|以上)?要点[:：]|^本章小结|^本节小结",
         exts=DOC_EXTS, note="与摘要重复的要点复述"),
    dict(cat="元叙述文案", pat=r"本页(?:面)?(?:用于|支持|提供|展示|旨在|可)|本页支持以下功能|这个页面",
         exts=HTML_EXTS, note="页面用内容说明自己，不用文案说明"),
    dict(cat="markdown 残留", pat=r"\*\*[^*\n]+\*\*|^\s{0,3}[·◦]\s|^\s{0,3}[-*]\s+\S",
         exts={".docx", ".html", ".htm", ".xhtml"}, note="交付物里的标记残留"),
    dict(cat="未完成功能", pat=r"待开发|敬请期待|coming\s*soon|功能开发中|暂未开放|功能待开发",
         exts=DOC_EXTS | HTML_EXTS, note="没做完的功能不进交付物"),
    dict(cat="调试残留", pat=r"\bTODO\b|\bFIXME\b|\bXXX\b\s*[:：]|console\.log|console\.debug|debugger\s*;|alert\(",
         exts=DOC_EXTS | HTML_EXTS, note="调试代码与标记"),
    dict(cat="调试面板", pat=r"调试面板|开发模式|dev\s*panel|仅开发时使用|仅供开发",
         exts=DOC_EXTS | HTML_EXTS, note="开发用组件，交付前删"),
    dict(cat="演示占位声明", pat=r"演示版本|示例数据|模拟数据|占位|placeholder|Lorem ipsum|mock\s*数据",
         exts=DOC_EXTS | HTML_EXTS, note="占位或免责式措辞"),
    dict(cat="页脚元信息", pat=r"内容可能随时调整|如有变更恕不另行通知|测试版|beta\s*版",
         exts=DOC_EXTS | HTML_EXTS, note="页脚元信息，一般不面向读者"),
    dict(cat="对内注释", pat=r"<!--[^>]{0,200}(?:删除|internal|自己看|暂时|备注|以后|后续)[^>]{0,200}-->|//\s*(?:TODO|FIXME|注意|备注)|/\*\s*(?:TODO|注意|备注)",
         exts=HTML_EXTS, note="实现说明泄漏到交付页"),
    dict(cat="HTML 注释", pat=r"<!--(?!\s*\[if)",
         exts=HTML_EXTS, note="交付页里所有注释都需过目"),
    dict(cat="给创作者看的说明", pat=r"给创作者|面向作者|供作者|说明[:：]\s*$",
         exts=DOC_EXTS | HTML_EXTS, note="写给过程读者而非结果读者"),
]

COMPILED = [(r, re.compile(r["pat"], re.MULTILINE)) for r in RULES]


def collect_files(target):
    if os.path.isfile(target):
        return [target]
    out = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   {"node_modules", "__pycache__", "dist", "build", "_archive"}]
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext in DOC_EXTS | HTML_EXTS:
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


def scan_file(path):
    ext = os.path.splitext(path)[1].lower()
    hits = []
    for i, line in enumerate(read_lines(path), 1):
        if not line.strip():
            continue
        for rule, rx in COMPILED:
            if ext not in rule["exts"]:
                continue
            m = rx.search(line)
            if not m:
                continue
            frag = line.strip()
            if len(frag) > 90:
                s = max(0, m.start() - 20)
                frag = ("…" if s else "") + frag[s:s + 90] + "…"
            hits.append(dict(line=i, cat=rule["cat"], note=rule["note"], text=frag))
            break
    return hits


def main():
    ap = argparse.ArgumentParser(description="对外视图过滤 · 内部痕迹扫描器")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print("路径不存在：%s" % args.target, file=sys.stderr)
        return 2

    files = collect_files(args.target)
    results = []
    cat_count = {}
    total = 0
    for p in files:
        hits = scan_file(p)
        if not hits:
            continue
        total += len(hits)
        for h in hits:
            cat_count[h["cat"]] = cat_count.get(h["cat"], 0) + 1
        results.append(dict(file=p, hits=hits))

    if args.format == "json":
        print(json.dumps(dict(scanned=len(files), hits=total,
                              by_category=cat_count, results=results),
                         ensure_ascii=False, indent=2))
        return 0

    print("扫描 %d 个文件，命中 %d 处" % (len(files), total))
    print("=" * 60)
    if not results:
        print("未发现内部痕迹。")
        return 0
    for r in results:
        print("\n▸ %s" % r["file"])
        for h in r["hits"]:
            print("  L%-5d [%s] %s" % (h["line"], h["cat"], h["text"]))
    print("\n" + "=" * 60)
    print("按类别统计：")
    for c, n in sorted(cat_count.items(), key=lambda kv: -kv[1]):
        print("  %-14s %d" % (c, n))
    print("\n提示：扫描只做定位。删不删按四条判据（读者 / 删除 / 自指 / 重复）决定，"
          "模板强制栏目与合规声明不动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
