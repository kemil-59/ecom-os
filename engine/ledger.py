"""Agent 01 — Registre des Observations (Étape 3), Déduplication Multi-Niveaux et GATE 2B (B3).

Garanties B3 Product-First :
- Les signaux Google (`SEARCH`) sont uniquement de la découverte et ne comptent jamais comme témoignage.
- Une URL de témoignage doit pointer vers une page précise (chemin ou query non vide).
- Déduplication stricte sur :
  1. `(source_url, phrase_exacte[:80])`
  2. Empreinte normalisée du texte (`msg_hash`) pour bloquer le copier-coller du même avis sur plusieurs URLs.
- GATE 2B exige >= 2 personnes indépendantes identifiées (`author_key`) ET >= 3 familles de sources sur le marché cible.
  Un même auteur sur 3 sites compte pour 1 seule personne. Un avis sans `author_key` ne crée jamais d'indépendance.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

FAMILY = {
    "AMAZON_FR_REVIEW": "MARKETPLACE",
    "RETAILER_REVIEW_FR": "MARKETPLACE",
    "MARKETPLACE_REVIEW": "MARKETPLACE",
    "FORUM_FR": "FORUM_QA",
    "QA_SITE_FR": "FORUM_QA",
    "REDDIT_FR": "FORUM_QA",
    "YOUTUBE_COMMENT": "VIDEO",
    "TIKTOK_COMMENT": "VIDEO",
    "BLOG_COMMENT_FR": "BLOG_COMMENT",
    "GOOGLE_FR_PAA": "SEARCH",
    "GOOGLE_FR_AUTOCOMPLETE": "SEARCH",
    "GOOGLE_FR_SERP_SNIPPET": "SEARCH",
    "BRAND_SITE_REVIEW_FR": "BRAND_REVIEW",
    "TRUSTPILOT_FR": "REVIEW_PLATFORM",
    "PRESS_TEST_FR": "EDITORIAL",
    "AMAZON_INTL_REVIEW": "MARKETPLACE",
    "BRAND_SITE_REVIEW_INTL": "BRAND_REVIEW",
    "REDDIT_EN": "FORUM_QA",
    "FORUM_EN": "FORUM_QA",
    "TRUSTPILOT_INTL": "REVIEW_PLATFORM",
    "PRESS_TEST_INTL": "EDITORIAL",
    # Vérification générique (tout site public) : type déterminé par les données structurées de la page, sinon proposé.
    "WEB_FORUM": "FORUM_QA",
    "WEB_QA": "FORUM_QA",
    "WEB_REVIEW": "CUSTOMER_REVIEW",
    "WEB_COMMENT": "PUBLIC_COMMENT",
    "WEB_BLOG": "BLOG_COMMENT",
    "WEB_SOCIAL": "SOCIAL",
    "WEB_NEWS": "EDITORIAL",
    "WEB_OTHER": "OTHER_WEB",
}
FOREIGN_TYPES = {
    "AMAZON_INTL_REVIEW",
    "BRAND_SITE_REVIEW_INTL",
    "REDDIT_EN",
    "FORUM_EN",
    "TRUSTPILOT_INTL",
    "PRESS_TEST_INTL",
}
DISCOVERY_ONLY = {"SEARCH"}
# FETCH_VERIFIED : citation retrouvée par le moteur dans une page téléchargée (reçu sha256).
# Ne prouve ni l'identité, ni l'authenticité, ni la résidence. Jamais LIVE.
FETCH_VERIFIED = "FETCH_VERIFIED"
GEO_BASES_ACCEPTED = {"RESIDENCE_DECLARED", "FR_MARKET_SITE"}
FETCH_KINDS = {"FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND", "SATISFACTION", "CONTEXT",
               "INDEX_COUNT", "REPEAT_PURCHASE", "DEMO_OBSERVED", "PRICE_OBSERVED", "OFFER_OBSERVED"}
FETCH_REQUIRED = ("kind", "spontane", "market_geo", "geo_basis", "author_key", "receipt_id")
TESTIMONY_KINDS = {"FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND"}
REQUIRED = ("territory_id", "probleme", "phrase_exacte", "source_type", "source_url")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _has_path(url: str) -> bool:
    u = urlparse(url)
    return bool((u.path or "").strip("/") or u.query)


def _norm_text_hash(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", (text or "").lower()).strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_by_id(path: Path) -> dict[str, dict]:
    return {r["observation_id"]: r for r in load(path) if r.get("observation_id")}


def append(
    path: Path,
    batch: list[dict],
    *,
    mission_id: str,
    target_market: str = "FR",
    provenance_mode: str = "LIVE",
    cycle: int | None = None,
) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if provenance_mode not in ("REPLAY", FETCH_VERIFIED):
        raise ValueError("LIVE_VOC_REQUIRES_TRUSTED_CONNECTOR_NOT_AVAILABLE_IN_B1")
    receipts = _load_receipts(path) if provenance_mode == FETCH_VERIFIED else {}
    existing = load(path)
    seen = {e.get("dedup") for e in existing if e.get("dedup")}
    seen_msg = {e.get("msg_hash") for e in existing if e.get("msg_hash")}
    n, added, dup = len(existing), [], 0

    for o in batch:
        reserved = {"observation_id", "mission_id", "captured_at", "is_live", "dedup", "msg_hash"}
        if reserved.intersection(o):
            raise ValueError("USER_CANNOT_SET_INTERNAL_PROVENANCE_FIELDS")
        if o.get("provenance_mode", provenance_mode) != provenance_mode:
            raise ValueError("PROVENANCE_MODE_CANNOT_BE_UPGRADED")
        miss = [k for k in REQUIRED if not o.get(k)]
        if miss:
            raise ValueError(f"MISSING_FIELDS:{miss}")
        if o["source_type"] not in FAMILY:
            raise ValueError(f"UNKNOWN_SOURCE_TYPE:{o['source_type']}")
        if not str(o["source_url"]).startswith("https://"):
            raise ValueError("SOURCE_URL_MUST_BE_HTTPS")
        if FAMILY[o["source_type"]] not in DISCOVERY_ONLY and not _has_path(o["source_url"]):
            raise ValueError("TESTIMONY_URL_NEEDS_EXACT_PAGE")
        if provenance_mode == FETCH_VERIFIED:
            # Aucune valeur par défaut : chaque attribut doit être explicite et adossé à un reçu moteur.
            miss = [k for k in FETCH_REQUIRED if o.get(k) is None or o.get(k) == ""]
            if miss:
                raise ValueError(f"FETCH_VERIFIED_MISSING_EXPLICIT_FIELDS:{miss}")
            if o["kind"] not in FETCH_KINDS or not isinstance(o["spontane"], bool):
                raise ValueError("FETCH_VERIFIED_INVALID_KIND_OR_SPONTANEITY")
            rc = receipts.get(o["receipt_id"])
            if not rc or rc.get("status") != "QUOTE_VERIFIED" or rc.get("url") != o["source_url"] \
                    or _norm_text_hash(rc.get("quote", "")) != _norm_text_hash(o["phrase_exacte"]):
                raise ValueError("FETCH_VERIFIED_RECEIPT_MISSING_OR_MISMATCH")
            if rc.get("author_key") != o["author_key"] or rc.get("geo_basis") != o["geo_basis"] \
                    or rc.get("market_geo") != o["market_geo"] or rc.get("source_type") != o["source_type"]:
                raise ValueError("FETCH_VERIFIED_FIELDS_DIFFER_FROM_RECEIPT")
            if (rc.get("persona") or {}).get("status") != o.get("persona_basis"):
                raise ValueError("FETCH_VERIFIED_PERSONA_DIFFERS_FROM_RECEIPT")
            if rc.get("contribution_nature") != o.get("contribution_nature"):
                raise ValueError("FETCH_VERIFIED_CONTRIBUTION_NATURE_DIFFERS_FROM_RECEIPT")

        dedup_key = hashlib.sha256(
            (o["source_url"] + "|" + o["phrase_exacte"][:80]).encode("utf-8")
        ).hexdigest()[:12]
        msg_hash = _norm_text_hash(o["phrase_exacte"])
        if dedup_key in seen or (len(o["phrase_exacte"].strip()) >= 20 and msg_hash in seen_msg):
            dup += 1
            continue
        n += 1
        seen.add(dedup_key)
        seen_msg.add(msg_hash)
        default_geo = "INTL" if o["source_type"] in FOREIGN_TYPES else target_market
        rec = {
            "observation_id": f"OBS-{n:04d}",
            "mission_id": mission_id,
            "captured_at": _now(),
            "market_geo": o.get("market_geo") or o.get("fr_us") or default_geo,
            "spontane": True,
            "source_date": None,
            "candidate_ids": [],
            "kind": "FRUSTRATION",
            "provenance_mode": o.get("provenance_mode") or provenance_mode,
            "is_live": (o.get("provenance_mode") or provenance_mode) == "LIVE",
            **({"cycle": cycle} if cycle is not None else {}),
            **o,
            "dedup": dedup_key,
            "msg_hash": msg_hash,
        }
        added.append(rec)

    with path.open("a", encoding="utf-8") as f:
        for r in added:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {
        "added": len(added),
        "duplicates_skipped": dup,
        "total": n,
        "ids": [r["observation_id"] for r in added],
    }


def _load_receipts(ledger_path: Path) -> dict[str, dict]:
    p = Path(ledger_path).parent.parent / "RESEARCH_INBOX" / "receipts.jsonl"
    return {r["receipt_id"]: r for r in load(p) if r.get("receipt_id")}


def links_path(ledger_path: Path) -> Path:
    return Path(ledger_path).parent / "candidate_links.jsonl"


def add_links(ledger_path: Path, links: list[dict]) -> int:
    """Rattachement observation -> candidat en ajout seul (le registre reste immuable)."""
    known = load_by_id(ledger_path)
    lp = links_path(ledger_path)
    have = {(x["observation_id"], x["candidate_id"]) for x in load(lp)}
    new = []
    for x in links:
        key = (x.get("observation_id"), x.get("candidate_id"))
        if key[0] not in known or not key[1] or not str(x.get("reason") or "").strip():
            raise ValueError(f"LINK_INVALID:{key}")
        if key not in have:
            have.add(key)
            new.append({"observation_id": key[0], "candidate_id": key[1], "reason": x["reason"], "linked_at": _now()})
    with lp.open("a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)


def load_merged(path: Path) -> list[dict]:
    """Observations avec candidate_ids = valeur d'origine ∪ liens ajoutés ensuite."""
    rows = load(path)
    extra: dict[str, set] = {}
    for x in load(links_path(path)):
        extra.setdefault(x["observation_id"], set()).add(x["candidate_id"])
    for r in rows:
        if r.get("observation_id") in extra:
            r["candidate_ids"] = sorted(set(r.get("candidate_ids") or []) | extra[r["observation_id"]])
    return rows


def gate_2b(ledger: list[dict], candidate_id: str, *, target_market: str = "FR", allow_replay: bool = True,
            tier: str | None = None) -> dict:
    """GATE 2B. Missions réelles (FETCH_VERIFIED) : délégation à la logique UNIQUE de convergence
    (engine/demand.gate_2b_convergence). La règle historique ci-dessous ne sert plus qu'aux simulations REPLAY."""
    if tier == FETCH_VERIFIED:
        from .demand import gate_2b_convergence
        g = gate_2b_convergence(ledger, candidate_id)
        mine = [o for o in ledger if candidate_id in (o.get("candidate_ids") or [])]
        return {**g, "testimony_ids": [o["observation_id"] for o in mine if o.get("kind") in TESTIMONY_KINDS],
                "independent_persons": g["independent_accounts"]}
    obs = [o for o in ledger if candidate_id in (o.get("candidate_ids") or [])]
    testimonies, excluded = [], []
    seen_texts: set[str] = set()

    for o in obs:
        fam = FAMILY.get(o.get("source_type"), "UNKNOWN")
        fetch_row = o.get("provenance_mode") == FETCH_VERIFIED
        # Une ligne FETCH_VERIFIED n'a jamais de géo implicite.
        geo = o.get("market_geo") if fetch_row else (o.get("market_geo") or o.get("fr_us") or target_market)
        mhash = o.get("msg_hash") or _norm_text_hash(o.get("phrase_exacte", ""))
        if tier == FETCH_VERIFIED and not fetch_row:
            excluded.append((o["observation_id"], "NOT_FETCH_VERIFIED_EXCLUDED"))
        elif fetch_row and o.get("geo_basis") not in GEO_BASES_ACCEPTED:
            excluded.append((o["observation_id"], f"GEO_BASIS_NOT_ACCEPTED:{o.get('geo_basis')}"))
        elif fetch_row and o.get("spontane") is not True:
            excluded.append((o["observation_id"], "NOT_SPONTANEOUS"))
        elif not allow_replay and (not o.get("is_live", False) or o.get("provenance_mode") != "LIVE"):
            excluded.append((o["observation_id"], "NON_LIVE_REPLAY_EXCLUDED"))
        elif fam in DISCOVERY_ONLY:
            excluded.append((o["observation_id"], "SEARCH_DISCOVERY_ONLY"))
        elif o.get("source_type") in FOREIGN_TYPES or geo != target_market:
            excluded.append((o["observation_id"], "FOREIGN_TESTIMONY_AGENT02_ONLY"))
        elif o.get("excluded_reason"):
            excluded.append((o["observation_id"], o["excluded_reason"]))
        elif o.get("inferred") or o.get("evidence_type") == "INFERENCE":
            excluded.append((o["observation_id"], "INFERRED_NOT_OBSERVED"))
        elif o.get("kind") not in TESTIMONY_KINDS:
            excluded.append((o["observation_id"], f"NOT_A_TESTIMONY:{o.get('kind')}"))
        elif len(str(o.get("phrase_exacte") or "").strip()) >= 20 and mhash in seen_texts:
            excluded.append((o["observation_id"], "DUPLICATE_VERBATIM_TEXT"))
        else:
            seen_texts.add(mhash)
            testimonies.append(o)

    persons = {
        str(o.get("author_key") or "").strip().casefold()
        for o in testimonies
        if str(o.get("author_key") or "").strip()
    }
    missing_author_ids = [
        o["observation_id"] for o in testimonies if not str(o.get("author_key") or "").strip()
    ]
    families = sorted({FAMILY[o["source_type"]] for o in testimonies})

    if len(persons) < 2:
        status = "EN_ATTENTE" if missing_author_ids else "FAIL"
    elif len(families) >= 3:
        status = "PASS"
    else:
        status = "EN_ATTENTE"

    out_tier = {}
    if tier == FETCH_VERIFIED:
        out_tier = {
            "evidence_tier": FETCH_VERIFIED,
            "certified_live": False,
            "identity_verified": False,
            "independent_accounts": len(persons),
            "geo_bases": sorted({o.get("geo_basis") for o in testimonies}),
            "validation": "PASS_PROVISIONAL_FETCH_VERIFIED" if status == "PASS" else "EXPLORATORY_NOT_VALIDATED",
        }
    return {
        **out_tier,
        "candidate_id": candidate_id,
        "target_market": target_market,
        "status": status,
        "independent_persons": len(persons),
        "independence_unverified_observations": missing_author_ids,
        "testimony_families": families,
        "testimony_ids": [o["observation_id"] for o in testimonies],
        "excluded": excluded,
        "rule": ">=2 personnes independantes identifiees ET >=3 familles de sources sur le marche cible ; Google = decouverte uniquement",
    }
