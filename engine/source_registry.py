"""Adaptateurs d'extraction de HAUTE CONFIANCE (facultatifs) — ce n'est pas une liste d'admission.

Tout site public accessible est vérifié par la méthode générique de engine/web_capture.py. Un adaptateur ajoute seulement
un rattachement auteur/date/géographie plus fiable (niveau ADAPTER) pour un site dont la structure est connue.
- Adaptateurs actifs : `contracts/source_registry.json` (revue propriétaire).
- Propositions : `research/source_proposals/*.json`, statut PROPOSED, validées hors réseau par fixtures
  (`python3 ecom.py sources validate`) ; elles n'empêchent jamais la vérification générique du site.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "source_registry.json"
PROPOSALS = ROOT / "research" / "source_proposals"
SOURCE_TYPES = {"FORUM_FR", "QA_SITE_FR", "RETAILER_REVIEW_FR", "BLOG_COMMENT_FR", "REDDIT_FR", "YOUTUBE_COMMENT",
                "TIKTOK_COMMENT", "BRAND_SITE_REVIEW_FR", "TRUSTPILOT_FR"}
GEO_MODES = {"RESIDENCE_ONLY", "MARKET_SITE"}
REQUIRED = ("host", "status", "source_type", "geo", "header")


def _check_entry(e: dict) -> list[str]:
    errs = [f"MISSING:{k}" for k in REQUIRED if not e.get(k)]
    if errs:
        return errs
    if e["source_type"] not in SOURCE_TYPES:
        errs.append(f"UNKNOWN_SOURCE_TYPE:{e['source_type']}")
    if e["geo"] not in GEO_MODES:
        errs.append(f"UNKNOWN_GEO_MODE:{e['geo']}")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", e["host"]):
        errs.append(f"BAD_HOST:{e['host']}")
    try:
        rx = re.compile(e["header"])
        if "author" not in rx.groupindex:
            errs.append("HEADER_WITHOUT_AUTHOR_GROUP")
    except re.error as x:
        errs.append(f"BAD_HEADER_REGEX:{x}")
    for m in e.get("foreign_markers") or []:
        if not isinstance(m, str) or not m.strip():
            errs.append("BAD_FOREIGN_MARKER")
    return errs


def load_active(path: Path = REGISTRY) -> dict:
    """{host: conf} des seules sources ACTIVES valides. Erreur bloquante si le registre est corrompu."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != "ECOM_OS_SOURCE_REGISTRY_V1":
        raise ValueError("SOURCE_REGISTRY_SCHEMA_INVALID")
    out = {}
    for e in doc.get("sources", []):
        errs = _check_entry(e)
        if errs:
            raise ValueError(f"SOURCE_REGISTRY_ENTRY_INVALID:{e.get('host')}:{','.join(errs)}")
        if e["status"] != "ACTIVE":
            continue
        if e["host"] in out:
            raise ValueError(f"SOURCE_REGISTRY_DUPLICATE:{e['host']}")
        out[e["host"]] = {k: v for k, v in e.items() if k not in ("host", "status", "activated", "fixtures", "notes")}
    return out


def validate_proposal(path: Path) -> dict:
    """Contrôle hors réseau d'une proposition : structure + fixtures (page réelle sauvegardée, citations attendues).
    Ne l'active jamais : le résultat le plus favorable est PROPOSAL_VALID_PENDING_OWNER."""
    from . import web_capture
    p = Path(path)
    e = json.loads(p.read_text(encoding="utf-8"))
    errs = _check_entry(e)
    if e.get("status") != "PROPOSED":
        errs.append("PROPOSAL_STATUS_MUST_BE_PROPOSED")
    fixtures = e.get("fixtures") or []
    if not fixtures:
        errs.append("FIXTURE_REQUIRED")
    results = []
    if not errs:
        rx = re.compile(e["header"])
        for fx in fixtures:
            raw_path = (p.parent / fx["file"]).resolve()
            raw = gzip.decompress(raw_path.read_bytes()) if raw_path.suffix == ".gz" else raw_path.read_bytes()
            text = web_capture.html_to_text(raw)
            heads = list(rx.finditer(text))
            for exp in fx.get("expect", []):
                q = web_capture.norm(exp["quote"])
                pos = text.find(q)
                before = [h for h in heads if h.start() < pos] if pos >= 0 else []
                got = web_capture.norm(before[-1].group("author")) if before else None
                ok = pos >= 0 and got is not None and got.casefold().startswith(web_capture.norm(exp["author"]).casefold())
                results.append({"quote": exp["quote"][:60], "expected_author": exp["author"], "parsed_author": got, "ok": ok})
        if not results or not all(r["ok"] for r in results):
            errs.append("FIXTURE_EXPECTATIONS_FAILED")
    return {"host": e.get("host"), "status": "PROPOSAL_INVALID" if errs else "PROPOSAL_VALID_PENDING_OWNER",
            "errors": errs, "fixture_results": results, "activation": "MANUAL_OWNER_REVIEW_ONLY"}


def list_sources() -> dict:
    act = load_active()
    props = []
    if PROPOSALS.exists():
        for f in sorted(PROPOSALS.glob("*.json")):
            try:
                props.append({"file": f.name, **validate_proposal(f)})
            except Exception as x:  # proposition illisible : signalée, jamais chargée
                props.append({"file": f.name, "status": "PROPOSAL_INVALID", "errors": [str(x)]})
    return {"active": sorted(act), "proposals": props}
