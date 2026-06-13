#!/usr/bin/env python3
"""Generate the memsearch architecture diagrams in two Excalidraw dialects.

Single source of truth: each diagram is authored once as a compact spec
(zones / boxes / arrows / text). From that spec we emit:

  * shorthand JSON  -> .scratch/diagrams/NN-name.createview.json
        for mcp__excalidraw__create_view (live render; uses `label` + cameraUpdate)
  * full scene JSON -> docs/diagrams/NN-name.excalidraw
        committed deliverable (separate `text` elements, full font metadata,
        no `label`, no `cameraUpdate` -- the format excalidraw.com / the VS Code
        extension open directly)

The two dialects are NOT interchangeable: passing a `label` shortcut to the
full format silently drops the text. `lint_full()` enforces the invariant on
every committed file (JSON parses; zero `label`/`cameraUpdate`; every text
element carries fontFamily/width/height/textAlign/verticalAlign + dark stroke;
text-element count matches the spec so nothing was dropped).

Run:  python docs/diagrams/build_diagrams.py
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

# --- palette (from excalidraw read_me) ---------------------------------------
FILL2STROKE = {
    "#a5d8ff": "#1971c2",   # light blue   -> input/source
    "#b2f2bb": "#2f9e44",   # light green  -> output/success
    "#ffd8a8": "#e8590c",   # light orange -> external/host hook
    "#d0bfff": "#6741d9",   # light purple -> processing/core
    "#ffc9c9": "#e03131",   # light red    -> safety/critical
    "#fff3bf": "#f08c00",   # light yellow -> notes/decisions
    "#c3fae8": "#0c8599",   # light teal   -> storage/data
    "#eebefa": "#ae3ec9",   # light pink
    "#dbe4ff": "#4263eb",   # blue zone
    "#e5dbff": "#7048e8",   # purple zone
    "#d3f9d8": "#2f9e44",   # green zone
    "transparent": "#1e1e1e",
}
INK = "#1e1e1e"
_seed = itertools.count(1)


def _next_seed() -> int:
    return next(_seed)


def _reset_seed() -> None:
    """Reset the module-global seed/id counter so each diagram is numbered independently.

    `_seed` is shared by to_full/to_shorthand/_text_el/_shape_el; without a per-diagram
    reset, adding one element to an early diagram renumbers the seeds of every later
    diagram, producing noisy diffs across unrelated .excalidraw files for one change.
    """
    global _seed
    _seed = itertools.count(1)


# --- spec primitives ---------------------------------------------------------
def zone(x, y, w, h, fill, label):
    return {"k": "zone", "x": x, "y": y, "w": w, "h": h, "fill": fill, "label": label}


def box(id, x, y, w, h, text, fill, fs=16, dashed=False):
    return {"k": "box", "id": id, "x": x, "y": y, "w": w, "h": h,
            "text": text, "fill": fill, "fs": fs, "dashed": dashed}


def arrow(id, x1, y1, x2, y2, color=INK, label="", dashed=False):
    return {"k": "arrow", "id": id, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "color": color, "label": label, "dashed": dashed}


def text(id, x, y, s, fs=16, color=INK, anchor="left"):
    return {"k": "text", "id": id, "x": x, "y": y, "text": s, "fs": fs,
            "color": color, "anchor": anchor}


# --- geometry helpers --------------------------------------------------------
def _lines(s):
    return s.split("\n")


def _est_w(s, fs):
    return max((len(ln) for ln in _lines(s)), default=1) * fs * 0.6


def _est_h(s, fs):
    return len(_lines(s)) * fs * 1.25


# --- shorthand (create_view) -------------------------------------------------
def to_shorthand(spec):
    els = []
    xs, ys, xe, ye = [], [], [], []
    for e in spec:
        if e["k"] in ("zone", "box"):
            xs.append(e["x"]); ys.append(e["y"]); xe.append(e["x"] + e["w"]); ye.append(e["y"] + e["h"])
        elif e["k"] == "arrow":
            xs += [e["x1"], e["x2"]]; ys += [e["y1"], e["y2"]]; xe += [e["x1"], e["x2"]]; ye += [e["y1"], e["y2"]]
        else:
            xs.append(e["x"]); ys.append(e["y"]); xe.append(e["x"] + _est_w(e["text"], e["fs"])); ye.append(e["y"] + _est_h(e["text"], e["fs"]))
    minx, miny, maxx, maxy = min(xs), min(ys), max(xe), max(ye)
    cw, ch = maxx - minx, maxy - miny
    pad = 60
    width = max(cw + 2 * pad, (ch + 2 * pad) * 4 / 3)
    height = width * 3 / 4
    cam = {"type": "cameraUpdate", "width": round(width), "height": round(height),
           "x": round(minx - (width - cw) / 2), "y": round(miny - (height - ch) / 2)}
    els.append(cam)
    for e in spec:
        if e["k"] == "zone":
            els.append({"type": "rectangle", "id": e.get("id", "z" + str(_next_seed())),
                        "x": e["x"], "y": e["y"], "width": e["w"], "height": e["h"],
                        "roundness": {"type": 3}, "backgroundColor": e["fill"], "fillStyle": "solid",
                        "strokeColor": FILL2STROKE[e["fill"]], "strokeWidth": 1, "opacity": 30})
            els.append({"type": "text", "x": e["x"] + 14, "y": e["y"] + 10,
                        "text": e["label"], "fontSize": 18, "strokeColor": FILL2STROKE[e["fill"]]})
        elif e["k"] == "box":
            els.append({"type": "rectangle", "id": e["id"], "x": e["x"], "y": e["y"],
                        "width": e["w"], "height": e["h"], "roundness": {"type": 3},
                        "backgroundColor": e["fill"], "fillStyle": "solid",
                        "strokeColor": FILL2STROKE[e["fill"]], "strokeWidth": 2,
                        "strokeStyle": "dashed" if e["dashed"] else "solid",
                        "label": {"text": e["text"], "fontSize": e["fs"]}})
        elif e["k"] == "arrow":
            dx, dy = e["x2"] - e["x1"], e["y2"] - e["y1"]
            a = {"type": "arrow", "id": e["id"], "x": e["x1"], "y": e["y1"],
                 "width": abs(dx), "height": abs(dy), "points": [[0, 0], [dx, dy]],
                 "strokeColor": e["color"], "strokeWidth": 2, "endArrowhead": "arrow",
                 "strokeStyle": "dashed" if e["dashed"] else "solid"}
            if e["label"]:
                a["label"] = {"text": e["label"], "fontSize": 14}
            els.append(a)
        else:
            x = e["x"] - (_est_w(e["text"], e["fs"]) / 2 if e["anchor"] == "center" else 0)
            els.append({"type": "text", "x": round(x), "y": e["y"], "text": e["text"],
                        "fontSize": e["fs"], "strokeColor": e["color"]})
    return els


# --- full format (.excalidraw) -----------------------------------------------
def _text_el(x, y, s, fs, color):
    w, h = _est_w(s, fs), _est_h(s, fs)
    return {"type": "text", "id": "t" + str(_next_seed()), "x": round(x), "y": round(y),
            "width": round(w), "height": round(h), "text": s, "fontSize": fs,
            "fontFamily": 2, "textAlign": "center", "verticalAlign": "top",
            "strokeColor": color, "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
            "seed": _next_seed()}


def _shape_el(e):
    return {"type": "rectangle", "id": e["id"], "x": e["x"], "y": e["y"],
            "width": e["w"], "height": e["h"], "roundness": {"type": 3},
            "strokeColor": FILL2STROKE[e["fill"]], "backgroundColor": e["fill"],
            "fillStyle": "solid", "strokeWidth": 2,
            "strokeStyle": "dashed" if e["dashed"] else "solid", "roughness": 1,
            "opacity": 100, "seed": _next_seed()}


def to_full(spec):
    els = []
    for e in spec:
        if e["k"] == "zone":
            els.append({"type": "rectangle", "id": e.get("id", "z" + str(_next_seed())),
                        "x": e["x"], "y": e["y"], "width": e["w"], "height": e["h"],
                        "roundness": {"type": 3}, "strokeColor": FILL2STROKE[e["fill"]],
                        "backgroundColor": e["fill"], "fillStyle": "solid", "strokeWidth": 1,
                        "strokeStyle": "solid", "roughness": 1, "opacity": 30, "seed": _next_seed()})
            els.append(_text_el(e["x"] + 14, e["y"] + 10, e["label"], 18, FILL2STROKE[e["fill"]]))
        elif e["k"] == "box":
            els.append(_shape_el(e))
            tw, th = _est_w(e["text"], e["fs"]), _est_h(e["text"], e["fs"])
            els.append(_text_el(e["x"] + e["w"] / 2 - tw / 2, e["y"] + e["h"] / 2 - th / 2,
                                e["text"], e["fs"], INK))
        elif e["k"] == "arrow":
            dx, dy = e["x2"] - e["x1"], e["y2"] - e["y1"]
            els.append({"type": "arrow", "id": e["id"], "x": e["x1"], "y": e["y1"],
                        "width": abs(dx), "height": abs(dy), "points": [[0, 0], [dx, dy]],
                        "strokeColor": e["color"], "backgroundColor": "transparent",
                        "fillStyle": "solid", "strokeWidth": 2,
                        "strokeStyle": "dashed" if e["dashed"] else "solid", "roughness": 1,
                        "opacity": 100, "seed": _next_seed(), "endArrowhead": "arrow",
                        "startArrowhead": None})
            if e["label"]:
                mx, my = (e["x1"] + e["x2"]) / 2, (e["y1"] + e["y2"]) / 2
                tw = _est_w(e["label"], 14)
                els.append(_text_el(mx - tw / 2, my - 18, e["label"], 14, e["color"]))
        else:
            x = e["x"] - (_est_w(e["text"], e["fs"]) / 2 if e["anchor"] == "center" else 0)
            els.append(_text_el(x, e["y"], e["text"], e["fs"], e["color"]))
    return {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {}, "elements": els}


# --- lint (advisor #1) -------------------------------------------------------
def _expected_text_count(spec):
    n = 0
    for e in spec:
        if e["k"] == "zone":
            n += 1
        elif e["k"] == "box":
            n += 1
        elif e["k"] == "arrow":
            n += 1 if e["label"] else 0
        else:
            n += 1
    return n


class DiagramLintError(ValueError):
    """A generated full-format scene violated a lint invariant."""


def lint_full(scene, spec, name):
    # Enforce with explicit raises, NOT assert: `python -O` strips assert statements,
    # which would turn every invariant below into a no-op and silently write a broken scene.
    raw = json.dumps(scene)
    if not json.loads(raw):
        raise DiagramLintError(f"{name}: not valid JSON")
    # Structural checks (inspect each element, NOT the serialized string) so a text element
    # whose CONTENT mentions "label"/"cameraUpdate" does not false-positive.
    for el in scene["elements"]:
        if "label" in el:
            raise DiagramLintError(
                f"{name}: element {el.get('id', el.get('type', '?'))} carries a create_view-only `label` shortcut"
            )
        if el.get("type") == "cameraUpdate":
            raise DiagramLintError(f"{name}: contains a cameraUpdate pseudo-element")
    req = ("fontFamily", "textAlign", "verticalAlign", "width", "height", "fontSize")
    bad_color = {"transparent", "#ffffff", "#fff", "#b0b0b0", "#999", "#999999"}
    texts = [el for el in scene["elements"] if el["type"] == "text"]
    for t in texts:
        for f in req:
            if t.get(f) in (None, ""):  # present AND non-empty
                raise DiagramLintError(f"{name}: text {t.get('text', '?')!r} missing/empty {f}")
        if not str(t.get("text", "")).strip():
            raise DiagramLintError(f"{name}: a text element has blank text")
        if str(t.get("strokeColor", "")).lower() in bad_color:
            raise DiagramLintError(f"{name}: text {t['text']!r} has unreadable color")
    exp = _expected_text_count(spec)
    if len(texts) != exp:
        raise DiagramLintError(f"{name}: text count {len(texts)} != expected {exp} (text dropped?)")
    return len(texts)


# =============================================================================
#  DIAGRAM SPECS
# =============================================================================
C_BLUE, C_GREEN, C_ORANGE, C_PURPLE, C_RED = "#a5d8ff", "#b2f2bb", "#ffd8a8", "#d0bfff", "#ffc9c9"
C_YELLOW, C_TEAL = "#fff3bf", "#c3fae8"
Z_BLUE, Z_PURPLE, Z_GREEN = "#dbe4ff", "#e5dbff", "#d3f9d8"
ARR = "#495057"


def d1_architecture():
    s = [text("ti", 700, 18, "memsearch — Architecture & Components  (Windows + remote Milvus, ONNX)", 24, INK, "center")]
    # host
    s += [zone(40, 60, 1320, 130, Z_BLUE, "Claude Code host  —  hooks (shell) + skill")]
    s += [box("h_ss", 60, 100, 175, 70, "SessionStart\nhook", C_ORANGE),
          box("h_ups", 250, 100, 185, 70, "UserPromptSubmit\nhook", C_ORANGE),
          box("h_stop", 450, 100, 165, 70, "Stop\nhook (async)", C_ORANGE),
          box("h_se", 630, 100, 175, 70, "SessionEnd\nhook", C_ORANGE),
          box("h_sk", 880, 100, 230, 70, "memory-recall skill\n(context: fork, Bash)", C_PURPLE)]
    # package
    s += [zone(40, 220, 1320, 380, Z_PURPLE, "memsearch  —  Python package (ONNX provider, no API key)")]
    s += [box("common", 60, 260, 160, 60, "common.sh\nshared setup", C_YELLOW),
          box("cli", 250, 260, 160, 60, "memsearch CLI\n(click)", C_BLUE),
          box("core", 250, 360, 200, 80, "MemSearch\ncore orchestrator", C_PURPLE),
          box("scanner", 480, 260, 150, 60, "scanner\n.md/.markdown", C_BLUE),
          box("chunker", 650, 260, 150, 60, "chunker\nby heading", C_BLUE),
          box("embed", 820, 260, 190, 60, "embedder\nONNX bge-m3 (CPU)", C_GREEN),
          box("rerank", 1030, 260, 150, 60, "reranker\n(opt, off)", C_YELLOW, dashed=True),
          box("store", 480, 360, 180, 70, "MilvusStore\nhybrid search", C_TEAL),
          box("edge", 690, 360, 160, 70, "EdgeStore\ngraph edges", C_TEAL),
          box("watch", 880, 360, 150, 70, "watcher\ndebounce 1.5s", C_ORANGE),
          box("compact", 480, 470, 150, 60, "compact\n(LLM)", C_PURPLE),
          box("maint", 650, 470, 180, 60, "maintenance\n(opt-in, 24h)", C_PURPLE, dashed=True),
          box("config", 880, 470, 180, 60, "config\nTOML layering", C_YELLOW)]
    # stores
    s += [zone(40, 630, 1320, 300, Z_GREEN, "Data stores")]
    s += [box("milvus", 60, 670, 250, 95, "Milvus collection\nms_<proj>_<hash>\nremote podman :19530", C_TEAL),
          box("edgesdb", 330, 670, 230, 95, "~/.memsearch/edges.db\nSQLite (global)\nchunk_edges", C_TEAL),
          box("daily", 580, 670, 260, 95, ".memsearch/memory/\n<repo>/<branch>/\nYYYY-MM-DD.md", C_BLUE),
          box("cfg", 870, 670, 250, 95, "~/.memsearch/config.toml\n+ ./.memsearch.toml", C_YELLOW),
          box("curated", 60, 790, 250, 70, "CORRECTIONS / PROJECT /\nUSER.md  (opt-in)", C_ORANGE, dashed=True),
          box("locks", 330, 790, 250, 70, "~/.memsearch/locks/\nwatch|index .lock + .pid", C_RED),
          box("pending", 600, 790, 290, 70, ".pending/<token> sidecar\n.lock.d mutex  (transient)", C_RED, dashed=True)]
    # wiring
    s += [arrow("a1", 147, 170, 140, 260, ARR),
          arrow("a2", 532, 170, 330, 260, ARR),
          arrow("a3", 995, 170, 410, 290, ARR, "search / expand"),
          arrow("a4", 140, 320, 250, 365, ARR),
          arrow("a5", 410, 300, 350, 360, ARR),
          arrow("a6", 450, 400, 480, 395, ARR),
          arrow("a7", 450, 410, 690, 395, ARR),
          arrow("a8", 570, 430, 180, 670, ARR, "upsert / search"),
          arrow("a9", 770, 430, 440, 670, ARR),
          arrow("a10", 955, 430, 470, 290, ARR, "auto-index"),
          arrow("a11", 535, 170, 700, 670, ARR, "append summary")]
    return s


def d2_lifecycle():
    s = [text("ti", 660, 14, "memsearch plugin — Session Lifecycle  (chronological; server mode)", 24, INK, "center")]
    # P1
    s += [zone(40, 60, 1240, 300, Z_BLUE, "1.  SessionStart")]
    s += [box("s1", 70, 100, 270, 56, "detect memsearch / bootstrap uv", C_BLUE),
          box("s2", 70, 170, 270, 56, "read config (provider/uri/version)", C_BLUE),
          box("s3", 70, 240, 270, 56, "ensure_milvus_up (podman probe/start)", C_ORANGE),
          box("s4", 370, 100, 290, 56, "crash-recovery: re-home stale .pending", C_RED, dashed=True),
          box("s5", 370, 170, 290, 56, "start_watch -> spawn watcher (--replace)", C_PURPLE),
          box("s6", 370, 240, 290, 56, "build cold-start ctx (curated/daily)", C_GREEN),
          box("s7", 700, 170, 270, 56, "inject additionalContext + systemMessage", C_GREEN),
          box("s_note", 1000, 150, 260, 110, "reads: config.toml, daily logs\n(or CORRECTIONS/PROJECT/USER)\nwrites: .watch.pid, locks/watch-*.lock", C_YELLOW, 14, dashed=True)]
    # P2
    s += [zone(40, 390, 1240, 340, Z_PURPLE, "2.  Per-turn loop  (x N)")]
    s += [box("ups", 70, 430, 280, 56, "UserPromptSubmit -> '[memsearch] hint'", C_ORANGE),
          box("recall", 380, 430, 360, 56, "recall skill (fork): search -> expand -> transcript", C_PURPLE, dashed=True),
          box("stoph", 70, 510, 200, 46, "Stop (async):", C_ORANGE, 16),
          box("st1", 70, 570, 280, 56, "parse last turn (parse-transcript.sh)", C_BLUE),
          box("st2", 380, 570, 250, 56, "write .pending sidecar", C_RED, dashed=True),
          box("st3", 660, 570, 250, 56, "claude -p haiku summarize", C_GREEN),
          box("st4", 380, 650, 250, 56, "append daily .md (mkdir lock)", C_BLUE),
          box("st5", 660, 650, 200, 56, "drop sidecar", C_RED, dashed=True),
          box("st6", 70, 650, 280, 56, "index --replace -> Milvus upsert", C_TEAL),
          box("p2note", 960, 560, 300, 110, "reads: transcript .jsonl\nwrites: YYYY-MM-DD.md, Milvus upsert\nguard: stop_hook_active (no recursion)", C_YELLOW, 14, dashed=True)]
    # P3
    s += [zone(40, 760, 1240, 150, Z_BLUE, "3.  SessionEnd")]
    s += [box("e1", 70, 800, 300, 56, "run_maintenance (bg, 24h, opt-in)", C_PURPLE, dashed=True),
          box("e2", 410, 800, 200, 56, "stop_watch", C_ORANGE),
          box("e3", 650, 800, 320, 56, "kill orphaned milvus_lite (Lite only)", C_RED),
          box("e_wl", 1000, 790, 260, 80, "watchlock = single writer\nper (domain, collection)", C_YELLOW, 14, dashed=True)]
    # flow arrows
    s += [arrow("f1", 660, 360, 660, 390, ARR),
          arrow("f2", 660, 730, 660, 760, ARR),
          arrow("g1", 200, 566, 200, 656, ARR),
          arrow("g2", 350, 598, 380, 598, ARR),
          arrow("g3", 630, 598, 660, 598, ARR)]
    return s


def d3_index():
    s = [text("ti", 700, 16, "Index pipeline  —  markdown -> Milvus + edges.db", 24, INK, "center")]
    s += [box("md", 40, 110, 150, 70, "markdown\nfile", C_BLUE),
          box("scan", 230, 110, 175, 70, "scan_paths\n.md/.markdown\nskip hidden", C_BLUE, 14),
          box("read", 445, 110, 140, 70, "read_text\nutf-8", C_BLUE),
          box("chunk", 625, 105, 200, 80, "chunk_markdown\nby heading, <=1500c\nsplit large @ paras", C_PURPLE, 14),
          box("cid", 865, 100, 250, 90, "compute_chunk_id\nsha256(\"markdown:src:\nstart:end:chash:model\")[:16]", C_PURPLE, 14),
          box("diff", 1150, 110, 180, 70, "diff vs stored\nstale = old - new", C_YELLOW, 14)]
    s += [box("struct", 1130, 290, 200, 75, "structural edges\nsibling 1.0 /\nsame_section 0.6", C_TEAL, 14),
          box("clean", 855, 295, 200, 70, "clean_for_embedding\nstrip HTML comments", C_PURPLE, 14),
          box("embed", 640, 300, 180, 60, "embed (ONNX bge-m3)", C_GREEN, 14),
          box("upsert", 430, 300, 170, 60, "MilvusStore.upsert", C_TEAL, 14),
          box("sim", 200, 295, 180, 70, "similar edges\ndense_search >=0.7", C_TEAL, 14)]
    s += [box("delstale", 430, 470, 170, 70, "delete stale\n(after upsert)", C_RED, 14),
          box("gc", 640, 470, 200, 70, "deleted-file GC\nscoped is_relative_to", C_RED, 14)]
    s += [box("dbm", 960, 470, 180, 80, "Milvus collection\nupsert / delete", C_TEAL, 14),
          box("dbe", 1170, 470, 160, 80, "edges.db\nstructural+similar", C_TEAL, 14)]
    # callouts
    s += [box("c1", 40, 300, 130, 70, "force = re-embed all\ndefault = new IDs", C_YELLOW, 13, dashed=True),
          box("c2", 40, 470, 350, 70, "crash-safe order: upsert NEW chunks BEFORE deleting stale", C_YELLOW, 14, dashed=True)]
    # arrows: forward path row1
    s += [arrow("p1", 190, 145, 230, 145, ARR),
          arrow("p2", 405, 145, 445, 145, ARR),
          arrow("p3", 585, 145, 625, 145, ARR),
          arrow("p4", 825, 145, 865, 145, ARR),
          arrow("p5", 1115, 145, 1150, 145, ARR),
          arrow("p6", 1240, 180, 1230, 290, ARR),
          arrow("p7", 1130, 327, 1055, 330, ARR),
          arrow("p8", 855, 330, 820, 330, ARR),
          arrow("p9", 640, 330, 600, 330, ARR),
          arrow("p10", 515, 360, 515, 470, ARR),
          arrow("p11", 600, 505, 640, 505, ARR),
          arrow("e1", 1230, 365, 1250, 470, ARR, "edges"),
          arrow("e2", 290, 365, 1170, 510, "#0c8599", "similar"),
          arrow("m1", 515, 360, 960, 510, "#0c8599", "upsert"),
          arrow("m2", 600, 505, 960, 520, ARR)]
    return s


def d4_search():
    s = [text("ti", 700, 14, "Search & memory-recall  (hybrid + graph; 3-layer skill)", 24, INK, "center")]
    s += [zone(40, 60, 1320, 230, Z_PURPLE, "memory-recall skill  (context: fork, Bash only — runs in a subagent)")]
    s += [box("trig", 70, 110, 270, 70, "Claude judges question\nneeds history (or hint)", C_BLUE, 14),
          box("l1", 380, 105, 250, 80, "L1 search\nmemsearch search --top-k 5\n--consistency Strong", C_BLUE, 14),
          box("l2", 670, 110, 230, 70, "L2 expand\nmemsearch expand <hash>", C_GREEN, 14),
          box("l3", 940, 105, 250, 80, "L3 transcript\ntranscript.py --turn <uuid>", C_PURPLE, 14, dashed=True),
          box("ret", 670, 205, 250, 56, "curated summary -> main ctx", C_GREEN, 14)]
    s += [zone(40, 320, 1320, 540, Z_GREEN, "Hybrid search internals  (MemSearch.search)")]
    s += [box("q", 70, 360, 150, 60, "query text", C_BLUE, 14),
          box("emb", 250, 360, 190, 60, "embed query (ONNX)", C_GREEN, 14),
          box("guard", 470, 355, 200, 70, "_nonempty guard\n(BM25 NaN on empty)", C_YELLOW, 13, dashed=True),
          box("dense", 70, 480, 170, 70, "dense ANN\nCOSINE / FLAT", C_BLUE, 14),
          box("bm25", 270, 480, 170, 70, "BM25 sparse\nfull-text", C_ORANGE, 14),
          box("rrf", 480, 485, 150, 60, "RRF rank\nk = 60", C_PURPLE, 14),
          box("norm", 660, 480, 190, 70, "normalize -> [0,1]\nmax = R/(k+1)", C_PURPLE, 14),
          box("graph", 890, 470, 230, 90, "graph_expand\nseeds top10 -> edges.db\nfanout 5, RRF fuse w=0.5", C_TEAL, 14),
          box("rer", 1150, 480, 170, 70, "reranker (opt)\nfetch 3x -> top_k", C_YELLOW, 13, dashed=True),
          box("res", 890, 620, 230, 60, "results (top_k)", C_GREEN, 16),
          box("dbm", 250, 620, 180, 60, "Milvus collection", C_TEAL, 14),
          box("dbe", 500, 620, 160, 60, "edges.db", C_TEAL, 14),
          box("c1", 70, 720, 360, 70, "consistency Strong = read-after-write on remote Milvus", C_YELLOW, 14, dashed=True),
          box("c2", 470, 720, 360, 70, "graph default-ON (cli wires graph.enabled=True)", C_YELLOW, 14, dashed=True)]
    s += [arrow("k1", 340, 145, 380, 145, ARR),
          arrow("k2", 630, 145, 670, 145, ARR),
          arrow("k3", 900, 145, 940, 145, ARR),
          arrow("k4", 795, 185, 795, 205, ARR),
          arrow("s1", 220, 390, 250, 390, ARR),
          arrow("s2", 440, 390, 470, 390, ARR),
          arrow("d1", 155, 425, 155, 480, ARR),
          arrow("d2", 355, 425, 355, 480, ARR),
          arrow("r1", 240, 515, 480, 515, ARR),
          arrow("r2", 440, 515, 480, 515, ARR),
          arrow("r3", 630, 515, 660, 515, ARR),
          arrow("r4", 850, 515, 890, 515, ARR),
          arrow("r5", 1005, 560, 1005, 620, ARR),
          arrow("rd", 340, 550, 340, 620, "#0c8599", "read"),
          arrow("ge", 980, 560, 580, 620, "#0c8599", "neighbors")]
    return s


def d5_curation():
    s = [text("ti", 700, 14, "Background curation  —  self-maintaining memory", 24, INK, "center")]
    s += [zone(40, 60, 1320, 150, Z_PURPLE, "A.  Per-turn: Stop summarization")]
    s += [box("a1", 70, 110, 180, 60, "last turn", C_BLUE, 14),
          box("a2", 320, 105, 230, 70, "claude -p haiku\n3rd-person bullets", C_GREEN, 14),
          box("a3", 620, 110, 220, 60, "append daily .md", C_BLUE, 14)]
    s += [zone(40, 240, 1320, 150, Z_BLUE, "B.  Manual: memsearch compact")]
    s += [box("b1", 70, 290, 220, 60, "query chunks (Milvus)", C_TEAL, 14),
          box("b2", 330, 290, 180, 60, "LLM summarize", C_GREEN, 14),
          box("b3", 560, 285, 280, 70, "append memory/YYYY-MM-DD.md\n'## Memory Compact'", C_BLUE, 14),
          box("b4", 880, 290, 200, 60, "re-index -> Milvus", C_TEAL, 14)]
    s += [zone(40, 420, 1320, 440, Z_GREEN, "C.  SessionEnd: maintenance  (OPT-IN, default off; 24h-gated)")]
    s += [box("c1", 70, 465, 270, 60, "SessionEnd run_maintenance", C_ORANGE, 14),
          box("c2", 380, 460, 320, 70, "due? digest changed & age>=24h\n(.maintenance-state.json)", C_YELLOW, 13, dashed=True),
          box("c3", 70, 560, 270, 80, "read recent journals\n(12 files, 46K) + existing;\nscrub secrets", C_BLUE, 13),
          box("c4", 380, 555, 320, 90, "LLM: native claude -p sonnet\nOR api (openai/anthropic/gemini)\n+ run_memory_command (<=3)", C_PURPLE, 13),
          box("c5", 740, 560, 280, 60, "JSON {action: replace, content}", C_YELLOW, 14),
          box("c6", 740, 660, 280, 60, "atomic write (temp + os.replace)", C_GREEN, 14),
          box("c7", 740, 750, 320, 70, "CORRECTIONS.md / PROJECT.md /\nUSER.md", C_ORANGE, 14),
          box("c8", 360, 755, 320, 60, "SessionStart injects on next cold start", C_BLUE, 14),
          box("note", 1080, 560, 250, 120, "opt-in: enabled=False\nmarkdown stays source of\ntruth (compact re-indexed)\ntool calls path-validated", C_YELLOW, 13, dashed=True)]
    s += [arrow("x1", 250, 140, 320, 140, ARR),
          arrow("x2", 550, 140, 620, 140, ARR),
          arrow("y1", 290, 320, 330, 320, ARR),
          arrow("y2", 510, 320, 560, 320, ARR),
          arrow("y3", 840, 320, 880, 320, ARR),
          arrow("z1", 340, 495, 380, 495, ARR),
          arrow("z2", 205, 525, 205, 560, ARR),
          arrow("z3", 340, 600, 380, 600, ARR),
          arrow("z4", 700, 600, 740, 590, ARR),
          arrow("z5", 880, 620, 880, 660, ARR),
          arrow("z6", 880, 720, 880, 750, ARR),
          arrow("z7", 740, 785, 680, 785, ARR)]
    return s


DIAGRAMS = [
    ("01-architecture", d1_architecture),
    ("02-session-lifecycle", d2_lifecycle),
    ("03-index-pipeline", d3_index),
    ("04-search-recall", d4_search),
    ("05-background-curation", d5_curation),
]


def main():
    here = Path(__file__).resolve().parent
    repo = here.parent.parent
    view_dir = repo / ".scratch" / "diagrams"
    here.mkdir(parents=True, exist_ok=True)
    view_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS:
        _reset_seed()
        spec = fn()
        scene = to_full(spec)
        n = lint_full(scene, spec, name)
        (here / f"{name}.excalidraw").write_text(json.dumps(scene, indent=1), encoding="utf-8")
        short = to_shorthand(spec)
        (view_dir / f"{name}.createview.json").write_text(json.dumps(short, separators=(",", ":")), encoding="utf-8")
        print(f"OK  {name}: {len(scene['elements'])} els, {n} text els (lint passed)")


if __name__ == "__main__":
    main()
