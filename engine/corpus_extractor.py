"""Extracteur et moteur de consultation à la demande des 142 sources officielles V4.

Permet d'extraire les sections `==================== FILE: <path> ====================`
des fichiers TXT V4 vers `reference_corpus/` sans jamais charger 2,68 Mo dans le contexte LLM.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re

LEGACY_INSTRUCTION_ARCHIVE = {
    'CLAUDE.md': '_LEGACY_INSTRUCTION_ARCHIVE/root_CLAUDE_V024.txt',
    'AGENTS.md': '_LEGACY_INSTRUCTION_ARCHIVE/root_AGENTS_V024.txt',
    '.claude/skills/ecom-agent01-product-research/SKILL.md': '_LEGACY_INSTRUCTION_ARCHIVE/dot_claude/ecom-agent01-product-research_SKILL.txt',
    '.claude/skills/ecom-agent02-competitor-intel/SKILL.md': '_LEGACY_INSTRUCTION_ARCHIVE/dot_claude/ecom-agent02-competitor-intel_SKILL.txt',
    '.claude/skills/ecom-agent03-creative-center/SKILL.md': '_LEGACY_INSTRUCTION_ARCHIVE/dot_claude/ecom-agent03-creative-center_SKILL.txt',
    '.claude/skills/ecom-os/SKILL.md': '_LEGACY_INSTRUCTION_ARCHIVE/dot_claude/ecom-os_SKILL.txt',
    'CORE_ECOM_OS/.claude/skills/ecom-os/SKILL.md': '_LEGACY_INSTRUCTION_ARCHIVE/core_dot_claude/ecom-os_SKILL.txt',
 }

DELIM_RE = re.compile(r"^==================== FILE: (.+?) ====================$", re.M)
V4_FILENAMES = (
    "ECOM_OS_LLM_PROVIDER_01_REGLES_AGENTS_OFFICIELS_V4.txt",
    "ECOM_OS_LLM_PROVIDER_02_MOTEURS_OFFICIELS_V4.txt",
    "ECOM_OS_LLM_PROVIDER_03_NECTAR_RESSOURCES_OFFICIELLES_V4.txt",
)


def extract_v4_files(search_dirs: list[Path], out_root: Path) -> dict:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    extracted = []
    found_txt = []

    for fname in V4_FILENAMES:
        src = next((d / fname for d in search_dirs if (d / fname).exists()), None)
        if not src:
            continue
        found_txt.append(str(src))
        text = src.read_text(encoding="utf-8", errors="replace")
        matches = list(DELIM_RE.finditer(text))
        for i, m in enumerate(matches):
            rel_path = m.group(1).strip()
            # Treat extracted source filenames as untrusted. Never escape the corpus,
            # overwrite the active project configuration, or process an absolute path.
            rel = Path(rel_path)
            if (not rel_path or rel.is_absolute() or any(x in ('', '.', '..') for x in rel.parts)
                    or '\\' in rel_path or '\x00' in rel_path):
                raise ValueError(f"UNSAFE_CORPUS_SOURCE_PATH:{rel_path}")
            start = m.end() + 1
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip("\n") + "\n"
            dest = out_root / LEGACY_INSTRUCTION_ARCHIVE.get(rel_path, rel_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            extracted.append(rel_path)

    source_sha256_by_original_path = {
        original: hashlib.sha256((out_root / LEGACY_INSTRUCTION_ARCHIVE.get(original, original)).read_bytes()).hexdigest()
        for original in sorted(set(extracted))
    }
    manifest = {
        "archived_instruction_path_map": LEGACY_INSTRUCTION_ARCHIVE,
        "source_sha256_by_original_path": source_sha256_by_original_path,
        "v4_txt_found": found_txt,
        "extracted_count": len(extracted),
        "extracted_files": sorted(set(extracted)),
    }
    (out_root / "_EXTRACTION_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def query_knowledge(index_path: Path, corpus_root: Path, query: str) -> dict:
    idx = json.loads(Path(index_path).read_text(encoding="utf-8"))
    q = (query or "").strip().lower()
    if not q:
        return {"available_modules": list(idx["modules"].keys()), "note": "Passer un mot-cle (ex: Smooche, M02-V028, Kahel, Caraway)."}

    hits = []
    for mod_key, mod in idx["modules"].items():
        blob = json.dumps(mod, ensure_ascii=False).lower()
        if q in mod_key.lower() or q in blob:
            hits.append({"module": mod_key, "data": mod})

    # Recherche d'extrait ciblé dans reference_corpus si disponible
    excerpts = []
    if corpus_root.exists():
        for p in sorted(corpus_root.rglob("*")):
            if p.is_file() and "_LEGACY_INSTRUCTION_ARCHIVE" not in p.parts and p.suffix in (".md", ".json", ".txt") and p.name != "_EXTRACTION_MANIFEST.json":
                txt = p.read_text(encoding="utf-8", errors="ignore")
                pos = txt.lower().find(q)
                if pos != -1:
                    snippet_start = max(0, pos - 200)
                    snippet_end = min(len(txt), pos + 600)
                    excerpts.append({
                        "file": str(p.relative_to(corpus_root)),
                        "excerpt": txt[snippet_start:snippet_end].strip(),
                    })
                    if len(excerpts) >= 5:
                        break

    return {"query": query, "index_matches": hits, "corpus_excerpts": excerpts}
