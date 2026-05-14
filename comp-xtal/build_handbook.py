#!/usr/bin/env python3
"""Build static handbook from Markdown in content/. Optional one-time PDF import."""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import markdown
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
MANIFEST = CONTENT / "manifest.txt"
OUT_HTML = ROOT / "index.html"
PDF = ROOT.parent / "material" / "comp-xtal_stable_handbook.pdf"

# PDF-import only: anchors used by `import-pdf` / `import-pdf-images` to split the
# original PDF. The `build` command does NOT read this — it derives sections from the
# manifest and per-file frontmatter, so hand-authored chapters need only a content file
# (listed in content/manifest.txt) and an entry in PART_GROUPS below.
SECTIONS: list[tuple[str, str, str | None]] = [
    ("intro", "About this handbook", None),
    ("references", "Helpful references", "Helpful references"),
    (
        "preparation-remote-gui",
        "Preparation: Remote access to GUI",
        "✅Preparation: Remote access to GUI",
    ),
    (
        "basic-initial-gui",
        "Basic: Initial GUI configuration for an experiment",
        "✅Basic: Initial GUI configuration for an experiment",
    ),
    (
        "basic-programs-tasks",
        "Basic: CCTBX.XFEL Programs and Tasks",
        "✅ Basic: CCTBX.XFEL Programs and Tasks",
    ),
    (
        "intermediate-realtime-s3df",
        "Intermediate: Prepare for real-time usage during beamtime (S3DF)",
        "Intermediate: Prepare for real-time usage during \nbeamtime (S3DF) \nBefore the beamtime",
    ),
    (
        "intermediate-geometry-refinement",
        "Intermediate: Geometry Refinement",
        "✅ Intermediate: Geometry Refinement \ncctbx/dials understands",
    ),
    (
        "intermediate-mask",
        "Intermediate: How to make a mask",
        "✅ Intermediate: How to make a mask \nThis chapter assumes",
    ),
    (
        "intermediate-indexing",
        "Intermediate: Indexing result improvement",
        "Intermediate: Indexing result improvement \nDiagnostics",
    ),
    (
        "intermediate-photon-energy",
        "Intermediate: Photon energy calibration",
        "Intermediate: Photon energy calibration \nCorrect energy",
    ),
    (
        "advanced-event-code",
        "Advanced: Event code handling",
        "✅ Advanced: Event code handling \nHelpful past experiments",
    ),
    (
        "appendix-programs",
        "Appendix: relevant programs and parameters",
        "Appendix: relevant programs and parameters \nWhen tweaking PHIL",
    ),
    ("debugging-tips", "Debugging tips", "Debugging tips \nReview all the red warning"),
]

ORDERED_SLUGS = [s[0] for s in SECTIONS]
SLUG_TO_TITLE = {s[0]: s[1] for s in SECTIONS}

# TOC + on-page grouping: (part_slug, part_label, [section ids]) — each id appears exactly once.
PART_GROUPS: list[tuple[str, str, list[str]]] = [
    ("about", "About this handbook", ["intro"]),
    ("references", "Helpful references", ["references"]),
    ("preparation", "Preparation", ["preparation-remote-gui"]),
    (
        "basic",
        "Basic",
        ["basic-initial-gui", "basic-programs-tasks"],
    ),
    (
        "intermediate",
        "Intermediate",
        [
            "intermediate-realtime-s3df",
            "intermediate-geometry-refinement",
            "intermediate-mask",
            "intermediate-indexing",
            "intermediate-photon-energy",
        ],
    ),
    ("advanced", "Advanced", ["advanced-event-code"]),
    ("appendix", "Appendix", ["appendix-programs"]),
    ("debugging", "Debugging Tips", ["debugging-tips"]),
    ("abismal", "Merging with ABISMAL", ["running-abismal"]),
]

IMAGES_DIR = ROOT / "images"
FIG_BEGIN = "<!-- handbook-pdf-figures-begin -->"
FIG_END = "<!-- handbook-pdf-figures-end -->"
MIN_IMAGE_SIDE = 48


def soften_linebreaks(text: str) -> str:
    text = re.sub(r"-[ \t]*\n[ \t]*(?=[a-z])", "", text)
    text = re.sub(r"(?<=[a-z,)\]])[ \t]*\n[ \t]*(?=[a-z])", " ", text)
    text = re.sub(r"(?<=[,;])[ \t]*\n[ \t]*(?=\S)", " ", text)
    return text


def load_full_pdf_text() -> str:
    r = PdfReader(str(PDF))
    return "\n\n".join(page.extract_text() or "" for page in r.pages)


def split_pdf_sections(full: str) -> list[tuple[str, str, str]]:
    intro_end = full.find("Helpful references")
    if intro_end < 0:
        raise SystemExit("Could not find start of references section in PDF.")
    out: list[tuple[str, str, str]] = []
    out.append(("intro", SECTIONS[0][1], full[:intro_end]))

    positions: list[tuple[str, str, int]] = []
    for sid, title, anchor in SECTIONS[1:]:
        if anchor is None:
            continue
        pos = full.find(anchor)
        if pos < 0:
            raise SystemExit(f"Missing anchor for section {sid!r} in PDF.")
        positions.append((sid, title, pos))

    for i, (sid, title, start) in enumerate(positions):
        end = positions[i + 1][2] if i + 1 < len(positions) else len(full)
        out.append((sid, title, full[start:end]))
    return out


def strip_leading_title(body: str, title: str) -> str:
    body = body.lstrip()
    if not body:
        return body
    first_line = body.split("\n", 1)[0].strip()
    first_norm = re.sub(r"^✅\s*", "", first_line)
    if first_norm == title or first_norm.replace("  ", " ") == title:
        rest = body.split("\n", 1)[1] if "\n" in body else ""
        return rest.lstrip("\n")
    return body


def section_body_to_markdown(body: str, title: str) -> str:
    body = strip_leading_title(body, title)
    body = soften_linebreaks(body).strip()
    if not body:
        return ""
    chunks = re.split(r"\n\s*\n+", body)
    return "\n\n".join(c.strip() for c in chunks if c.strip())


def slug_to_filename(slug: str) -> str:
    idx = ORDERED_SLUGS.index(slug) + 1
    return f"{idx:02d}-{slug}.md"


def char_offset_to_page(page_texts: list[str], idx: int) -> int:
    """Map a character index in ``"\\n\\n".join(page_texts)`` to a 0-based page index."""
    sep = 2
    pos = 0
    for i, t in enumerate(page_texts):
        if idx < pos + len(t):
            return i
        pos += len(t)
        if i < len(page_texts) - 1:
            pos += sep
    return len(page_texts) - 1


def section_start_pages(page_texts: list[str]) -> dict[str, int]:
    """First PDF page index where each section anchor appears (pypdf text, same as import-pdf)."""
    full = "\n\n".join(page_texts)
    starts: dict[str, int] = {"intro": 0}
    search_from = 0
    for sid, _title, anchor in SECTIONS[1:]:
        if anchor is None:
            continue
        found = full.find(anchor, search_from)
        if found < 0:
            found = full.find(anchor.replace("\n", " "), search_from)
        if found < 0:
            raise SystemExit(f"import-pdf-images: anchor not found for section {sid!r}")
        starts[sid] = char_offset_to_page(page_texts, found)
        search_from = found + 1
    return starts


def page_section_for_index(page_num: int, starts: dict[str, int]) -> str:
    best_sid = "intro"
    best_p = -1
    for sid in ORDERED_SLUGS:
        sp = starts.get(sid)
        if sp is None:
            continue
        if sp <= page_num and sp >= best_p:
            best_sid = sid
            best_p = sp
    return best_sid


def inject_figures_markdown(md_path: Path, image_md_lines: list[str]) -> None:
    raw = md_path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise SystemExit(f"{md_path}: expected YAML frontmatter")
    end_fm = raw.find("\n---\n", 4)
    if end_fm < 0:
        raise SystemExit(f"{md_path}: unterminated frontmatter")
    head = raw[: end_fm + 5]
    body = raw[end_fm + 5 :]
    body = re.sub(
        r"\n*<!-- handbook-pdf-figures-begin -->.*?<!-- handbook-pdf-figures-end -->\s*",
        "\n\n",
        body,
        flags=re.DOTALL,
    )
    body = body.rstrip()
    if image_md_lines:
        body = (
            body
            + f"\n\n{FIG_BEGIN}\n\n### Figures (from PDF)\n\n"
            + "\n\n".join(image_md_lines)
            + f"\n\n{FIG_END}\n"
        )
    md_path.write_text(head + "\n" + body.lstrip("\n") + "\n", encoding="utf-8")


def cmd_import_pdf_images() -> None:
    """Extract embedded raster images from the PDF into images/ and inject Markdown figures."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise SystemExit("import-pdf-images requires pymupdf: pip install pymupdf") from e

    if not PDF.is_file():
        raise SystemExit(
            f"PDF not found: {PDF}\n"
            "Place comp-xtal_stable_handbook.pdf there, then run this command again."
        )

    r = PdfReader(str(PDF))
    page_texts = [(p.extract_text() or "") for p in r.pages]
    starts = section_start_pages(page_texts)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMAGES_DIR.iterdir():
        if f.is_file():
            f.unlink()

    doc = fitz.open(str(PDF))
    try:
        if doc.page_count != len(page_texts):
            raise SystemExit("Page count mismatch between PyMuPDF and pypdf.")

        figure_lines: dict[str, list[str]] = {sid: [] for sid in ORDERED_SLUGS}
        per_section_count: dict[str, int] = {sid: 0 for sid in ORDERED_SLUGS}
        seen_page_xref: set[tuple[int, int]] = set()
        total = 0

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            sid = page_section_for_index(page_index, starts)
            for img in page.get_images(full=True):
                xref = img[0]
                w, h = img[2], img[3]
                if w < MIN_IMAGE_SIDE or h < MIN_IMAGE_SIDE:
                    continue
                key = (page_index, xref)
                if key in seen_page_xref:
                    continue
                seen_page_xref.add(key)
                try:
                    info = doc.extract_image(xref)
                except (ValueError, RuntimeError):
                    continue
                raw_bytes = info["image"]
                ext = info.get("ext", "png")
                if ext == "jpeg":
                    ext = "jpg"
                per_section_count[sid] += 1
                n = per_section_count[sid]
                fname = f"{sid}_p{page_index + 1:02d}_{n:02d}.{ext}"
                (IMAGES_DIR / fname).write_bytes(raw_bytes)
                alt = f"PDF p.{page_index + 1} fig.{n}"
                figure_lines[sid].append(f"![{alt}](images/{fname})")
                total += 1
    finally:
        doc.close()

    for sid in ORDERED_SLUGS:
        inject_figures_markdown(CONTENT / slug_to_filename(sid), figure_lines[sid])

    print(f"Wrote {total} image(s) under {IMAGES_DIR}/ and updated Markdown figure blocks.")


def cmd_import_pdf() -> None:
    if not PDF.is_file():
        raise SystemExit(f"PDF not found: {PDF}")
    CONTENT.mkdir(parents=True, exist_ok=True)
    full = load_full_pdf_text()
    sections = split_pdf_sections(full)
    lines: list[str] = []
    for sid, title, body in sections:
        fn = slug_to_filename(sid)
        lines.append(fn)
        md_body = section_body_to_markdown(body, title)
        text = f"---\nid: {sid}\ntitle: {title}\n---\n\n{md_body}\n"
        (CONTENT / fn).write_text(text, encoding="utf-8")
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} files under {CONTENT}/")


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end < 0:
        return {}, raw
    block = raw[4:end]
    body = raw[end + 5 :]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def load_manifest_files() -> list[Path]:
    if not MANIFEST.is_file():
        raise SystemExit(f"Missing {MANIFEST}. Run: python build_handbook.py import-pdf (once) or create manifest.")
    names = [ln.strip() for ln in MANIFEST.read_text(encoding="utf-8").splitlines() if ln.strip()]
    paths: list[Path] = []
    for name in names:
        p = CONTENT / name
        if not p.is_file():
            raise SystemExit(f"Manifest lists missing file: {p}")
        paths.append(p)
    return paths


MD_EXTENSIONS = [
    "markdown.extensions.tables",
    "markdown.extensions.sane_lists",
    "markdown.extensions.smarty",
    "pymdownx.superfences",
    "pymdownx.magiclink",
]

MD_EXTENSION_CONFIGS: dict = {
    "pymdownx.superfences": {},
}


def _line_looks_like_code(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("```"):
        return False
    if re.match(r"^https?://", s, re.I):
        return False
    # Short label lines like "For psana2:" or "On S3DF"
    if s.endswith(":") and len(s) < 60 and "\t" not in s:
        if "/" not in s and "=" not in s and not re.match(r"^[a-z0-9_.-]+\.[a-z]{2,}\s", s, re.I):
            if not re.match(r"^[.#]?\s*\w+\s+.+[=./-]", s):
                return False
    if s.startswith(
        (
            "source ",
            "export ",
            "ssh ",
            "mysql ",
            "conda ",
            "cp ",
            "cd ",
            "rm ",
            "chmod ",
            "umask ",
            "mkdir ",
            "pip ",
            "python",
        )
    ):
        return True
    if s.startswith(("./", "../")):
        return True
    m_dials = re.match(r"^dials\.\w+\s+(\S)", s)
    if m_dials and m_dials.group(1) != "(":
        return True
    if re.match(r"^cctbx\.xfel\s", s):
        return True
    if re.match(r"^/[A-Za-z0-9_.~{}<>/-]+$", s) and s.count("/") >= 2:
        return True
    if re.match(r"^[\w.-]+\.(sh|phil|py|expt|txt|yml|yaml)\b", s, re.I):
        return True
    # Lowercase CLI only (avoid "CCTBX.XFEL4MFX - Stable" matching as flags)
    if re.match(r"^[a-z][a-z0-9_.-]*\s+-", s):
        return True
    if re.search(r"^\s*[\w.]+\s*=\s*\S", s) and (
        "reintegration" in s
        or "integration." in s
        or "merging." in s
        or "filter." in s
        or "scaling." in s
        or "stills." in s
        or "postrefinement" in s
        or "statistics." in s
    ):
        return True
    return False


def _wrap_inline_paths_in_segment(chunk: str) -> str:
    path_re = re.compile(
        r"(?<![`./\w)])(?:"
        r"/\.[\w./<>{}-]+"
        r"|~/[\w./<>{}-]+"
        r"|(?:/sdf/|/global/|/tmp/|/usr/)(?:[\w.-]+/)+[\w./<>{}-]+"
        r")"
    )
    parts = re.split(r"(`[^`\n]*`)", chunk)
    out: list[str] = []
    for i, p in enumerate(parts):
        if i % 2 == 1:
            out.append(p)
            continue
        pos = 0
        buf: list[str] = []
        for m in path_re.finditer(p):
            buf.append(p[pos : m.start()])
            tok = m.group(0)
            buf.append(f"`{tok}`")
            pos = m.end()
        buf.append(p[pos:])
        out.append("".join(buf))
    return "".join(out)


def _wrap_inline_paths_outside_fences(md: str) -> str:
    """Add backticks around filesystem paths in prose only (not inside ``` fences)."""
    lines = md.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
        else:
            out.append(_wrap_inline_paths_in_segment(line))
    return "\n".join(out)


def _promote_shell_lines_to_fences(md: str) -> str:
    """Group consecutive command/path-like lines into ```bash fences (outside existing fences)."""
    lines = md.split("\n")
    out: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        if out and out[-1].strip() and not out[-1].strip().startswith("```"):
            out.append("")
        out.append("```bash")
        out.extend(buf)
        out.append("```")
        out.append("")
        buf = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_buf()
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            flush_buf()
            out.append(line)
            continue
        if _line_looks_like_code(line):
            buf.append(line)
        else:
            flush_buf()
            out.append(line)
    flush_buf()
    return "\n".join(out).rstrip() + "\n"


def preprocess_markdown_body(md: str) -> str:
    # Promote command lines to fences first so paths inside those lines are not backticked.
    md = _promote_shell_lines_to_fences(md)
    md = _wrap_inline_paths_outside_fences(md)
    return md


def md_to_html_fragment(source: str) -> str:
    source = preprocess_markdown_body(source)
    return markdown.markdown(
        source,
        extensions=MD_EXTENSIONS,
        extension_configs=MD_EXTENSION_CONFIGS,
    )


def _validate_part_groups(section_ids: set[str]) -> None:
    """Cross-check PART_GROUPS against the section ids found in the manifest."""
    listed = [sid for _ps, _pt, ids in PART_GROUPS for sid in ids]
    dupes = sorted({sid for sid in listed if listed.count(sid) > 1})
    if dupes:
        raise SystemExit(f"PART_GROUPS lists section id(s) more than once: {dupes}")
    listed_set = set(listed)
    missing_from_parts = sorted(section_ids - listed_set)
    if missing_from_parts:
        raise SystemExit(
            f"Manifest sections not placed in PART_GROUPS: {missing_from_parts}"
        )
    orphan_parts = sorted(listed_set - section_ids)
    if orphan_parts:
        raise SystemExit(
            f"PART_GROUPS references sections with no manifest file: {orphan_parts}"
        )


# When a part has multiple chapters and already shows a part heading, drop this prefix from H2/TOC.
_MULTI_PART_TITLE_PREFIX: dict[str, str] = {
    "basic": "Basic: ",
    "intermediate": "Intermediate: ",
}


def _chapter_display_title(part_slug: str, multi_chapter: bool, full_title: str) -> str:
    if not multi_chapter:
        return full_title
    prefix = _MULTI_PART_TITLE_PREFIX.get(part_slug, "")
    if prefix and full_title.startswith(prefix):
        return full_title[len(prefix) :].strip() or full_title
    return full_title


def _article_html(sid: str, display_title: str, data_title: str, body_html: str) -> str:
    return (
        f'        <article id="{html.escape(sid, quote=True)}" class="section" '
        f'data-title="{html.escape(data_title, quote=True)}">\n'
        f"          <h2>{html.escape(display_title)}</h2>\n"
        f'          <div class="section-body">\n{body_html}\n          </div>\n'
        f"        </article>\n"
    )


def cmd_build() -> None:
    paths = load_manifest_files()
    by_id: dict[str, tuple[str, str]] = {}
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        sid = meta.get("id", "")
        title = meta.get("title", "")
        m = re.match(r"^(\d+)-(.+)\.md$", path.name)
        if not sid and m:
            sid = m.group(2)
        if not title and sid in SLUG_TO_TITLE:
            title = SLUG_TO_TITLE[sid]
        if not sid or not title:
            raise SystemExit(f"{path}: frontmatter must include id and title (or use known slug in filename).")
        if sid in by_id:
            raise SystemExit(f"{path}: duplicate section id {sid!r} in manifest.")
        body_html = md_to_html_fragment(body.strip())
        by_id[sid] = (title, body_html)

    _validate_part_groups(set(by_id))

    nav_parts: list[str] = []
    for part_slug, part_label, sids in PART_GROUPS:
        multi = len(sids) > 1
        if not multi:
            s = sids[0]
            full = by_id[s][0]
            nav_parts.append(
                f'        <li class="toc-leaf"><a href="#{html.escape(s, quote=True)}">'
                f"{html.escape(full)}</a></li>\n"
            )
            continue
        inner = "".join(
            f'            <li><a href="#{html.escape(s, quote=True)}">'
            f"{html.escape(_chapter_display_title(part_slug, True, by_id[s][0]))}</a></li>\n"
            for s in sids
        )
        nav_parts.append(
            f'        <li class="toc-part">\n'
            f'          <span class="toc-part-label">{html.escape(part_label)}</span>\n'
            f'          <ul class="toc-nested">\n{inner}          </ul>\n'
            f"        </li>\n"
        )
    nav_html = "".join(nav_parts)

    main_parts: list[str] = []
    for part_slug, part_label, sids in PART_GROUPS:
        blocks: list[str] = [
            f'    <section class="part" id="part-{html.escape(part_slug, quote=True)}" '
            f'data-part="{html.escape(part_slug, quote=True)}">\n'
        ]
        if len(sids) > 1:
            blocks.append(f'      <h2 class="part-heading">{html.escape(part_label)}</h2>\n')
        for sid in sids:
            full_title, body_html = by_id[sid]
            disp = _chapter_display_title(part_slug, len(sids) > 1, full_title)
            blocks.append(_article_html(sid, disp, full_title, body_html))
        blocks.append("    </section>\n")
        main_parts.append("".join(blocks))

    articles_html = "".join(main_parts)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CCTBX.XFEL handbook</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <a class="skip" href="#main">Skip to content</a>
  <header class="top">
    <div class="top-inner">
      <h1 class="site-title">CCTBX.XFEL handbook</h1>
    </div>
  </header>
  <div class="layout">
    <nav class="toc" aria-label="Table of contents">
      <div class="search-wrap toc-search">
        <label class="sr-only" for="search">Search handbook</label>
        <input type="search" id="search" placeholder="Search…" autocomplete="off" />
        <span class="search-meta" id="search-meta" aria-live="polite"></span>
      </div>
      <ul class="toc-root">
{nav_html}      </ul>
    </nav>
    <main id="main">
{articles_html}    </main>
  </div>
  <script src="search.js"></script>
</body>
</html>
"""
    OUT_HTML.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({len(by_id)} sections in {len(PART_GROUPS)} parts)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Handbook build (Markdown → HTML)")
    ap.add_argument(
        "command",
        nargs="?",
        default="build",
        choices=("build", "import-pdf", "import-pdf-images"),
        help="build | import-pdf (text→Markdown) | import-pdf-images (figures→images/ + MD)",
    )
    args = ap.parse_args()
    if args.command == "import-pdf":
        cmd_import_pdf()
    elif args.command == "import-pdf-images":
        cmd_import_pdf_images()
    else:
        cmd_build()


if __name__ == "__main__":
    main()
