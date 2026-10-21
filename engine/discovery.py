"""Couche DISCOVERY : exploration ouverte du Web public accessible, sans liste de domaines.

A. DISCOVERY  — ce module : toute source publique effectivement vue est enregistrée (tout site, toute plateforme),
                statut DISCOVERED_UNVERIFIED tant que le moteur n'a pas vérifié une citation sur la page.
B. EVIDENCE   — engine/web_capture.py : vérification générique de la page téléchargée (tout site public accessible).
C. VALIDATION — engine/demand.py, engine/research_depth.py, gates : convergence de nombreuses sources indépendantes.

Le registre `RESEARCH_INBOX/discovery_registry.jsonl` est en ajout seul. Une capture dont la page est inaccessible
(robots, HTTP, CAPTCHA, connexion) y reste DISCOVERED_UNVERIFIED avec la raison, jamais promue en preuve.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import web_capture

STATUS = "DISCOVERED_UNVERIFIED"
FAMILY_HINTS = {"FORUM", "COMMUNITY", "REDDIT", "MARKETPLACE_REVIEW", "REVIEW_PLATFORM", "VIDEO_COMMENT", "BLOG_COMMENT",
                "COMPARATOR", "SPECIALIST_SITE", "INSTITUTIONAL_STUDY", "PRESS", "SEARCH_ENGINE", "SOCIAL", "BRAND_SITE", "OTHER"}
# Secteurs de découverte (missions générales) : diversité exigée au GATE 2A, aucun secteur privilégié.
SECTORS = ("beaute_soin", "sante_bien_etre", "hygiene", "mode_vetements", "maison_entretien", "cuisine_alimentation",
           "jardin_exterieur", "animaux", "parentalite_enfants", "seniors_aidants", "sport_activite", "loisirs_creativite",
           "voyages_mobilite", "auto_moto", "travail_etudes", "tech_numerique", "sommeil_confort", "organisation_rangement",
           "budget_consommation", "couple_relations")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def registry_path(mdir: Path) -> Path:
    return Path(mdir) / "RESEARCH_INBOX" / "discovery_registry.jsonl"


def _load(mdir: Path) -> list[dict]:
    p = registry_path(mdir)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def record(mdir: Path, entries: list[dict], origin: str) -> dict:
    """Enregistre des sources découvertes (url https obligatoire). Doublons url+territoire ignorés."""
    have = {(e["url"], e.get("territory_id")) for e in _load(mdir)}
    new, rejected = [], []
    for e in entries:
        url = str(e.get("url") or "").strip()
        if web_capture.check_url(url):
            rejected.append({"url": url, "why": "URL_UNSAFE_OR_NOT_HTTPS"})
            continue
        key = (url, e.get("territory_id"))
        if key in have:
            continue
        have.add(key)
        host = urlsplit(url).hostname.casefold()
        rec = {"discovery_id": "DSC-" + hashlib.sha256(f"{url}|{e.get('territory_id')}".encode()).hexdigest()[:10],
               "url": url, "host": host, "registrable_domain": web_capture.registrable_domain(host), "status": STATUS,
               "origin": origin, "family_hint": e.get("family_hint") if e.get("family_hint") in FAMILY_HINTS else "OTHER",
               "adapter_available": host in web_capture.ADAPTERS, "territory_id": e.get("territory_id"),
               "micro_id": e.get("micro_id"), "signal": str(e.get("signal") or "")[:400],
               "unverified_quote": str(e.get("quote") or "")[:400] or None,
               "access": e.get("access") or "NOT_CHECKED_BY_ENGINE", "recorded_at": _now()}
        new.append(rec)
    if new:
        p = registry_path(mdir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"recorded": len(new), "rejected": rejected}


def explored_domains(mdir: Path) -> set:
    """Domaines effectivement explorés : sources découvertes + pages que le moteur a tenté de vérifier."""
    doms = {r["registrable_domain"] for r in _load(mdir)}
    rc = Path(mdir) / "RESEARCH_INBOX" / "receipts.jsonl"
    if rc.exists():
        for line in rc.read_text(encoding="utf-8").splitlines():
            if line.strip():
                host = urlsplit(json.loads(line).get("url", "")).hostname
                if host:
                    doms.add(web_capture.registrable_domain(host))
    return doms


def summary(mdir: Path) -> dict:
    rows = _load(mdir)
    doms = Counter(r["registrable_domain"] for r in rows)
    by_t: dict[str, list[dict]] = {}
    for r in rows:
        by_t.setdefault(r.get("territory_id") or "_", []).append(r)
    return {"total": len(rows), "distinct_domains": len(doms), "explored_domains_incl_verification": len(explored_domains(mdir)),
            "top_domains": doms.most_common(20),
            "by_territory": {t: {"sources": len(rs), "domains": len({r["registrable_domain"] for r in rs}),
                                 "family_hints": dict(Counter(r["family_hint"] for r in rs))} for t, rs in sorted(by_t.items())},
            "inaccessible": sum(1 for r in rows if str(r.get("access", "")).startswith("BLOCKED"))}


def check_territory_discovery(territories: list[dict], mdir: Path, *, require_sectors: bool = True) -> list[str]:
    """GATE 2A : chaque territoire s'appuie sur des sources DÉCOUVERTES (tout site) ; secteurs et domaines variés."""
    reg = {r["url"]: r for r in _load(mdir)}
    issues, doms, sectors = [], set(), Counter()
    for t in territories:
        tid = t.get("territory_id", "?")
        known = [reg[u] for u in (t.get("discovery_refs") or []) if isinstance(u, str) and u in reg]
        if not known:
            issues.append(f"TERRITORY_WITHOUT_DISCOVERY_SOURCES:{tid}")
        doms |= {k["registrable_domain"] for k in known}
        if require_sectors:
            sec = t.get("sector")
            if sec not in SECTORS:
                issues.append(f"TERRITORY_SECTOR_UNKNOWN:{tid}:{sec}")
            sectors[sec] += 1
    if territories and len(doms) < 5:
        issues.append(f"DISCOVERY_DOMAINS_BELOW_5:{len(doms)}")
    if require_sectors:
        if len([s for s in sectors if s in SECTORS]) < 5:
            issues.append(f"TERRITORY_SECTOR_DIVERSITY_BELOW_5:{len(sectors)}")
        for s, n in sectors.items():
            if n > 2:
                issues.append(f"TERRITORY_SECTOR_OVERREPRESENTED:{s}:{n}")
    return issues
