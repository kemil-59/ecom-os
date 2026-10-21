"""Agent 02 — Competitor Intelligence Engine (8 Phases P0 à P7) — Version B3 Product-First.

Garanties B3 :
1. Interdiction absolue d'un `AUDIT_COMPLETE` sans preuve locale, sans preuve par gap ou sans gap évalué.
2. Empreinte déterministe `solution_fingerprint` (incluant supplier, sku_spec, landed_cost_eur).
3. Vérification de la pertinence d'un contre-exemple sur la caractéristique précise (`required_feature_keywords`).
4. Rejet strict de toute preuve `SHOP`/`SKU`/`AD` ou estimation de trafic utilisée comme VoC (`SHOP_ESTIMATE_REJECTED_AS_VOC`).
5. Scorecard `/25` réellement contraignante :
   - Vérifie que chaque référence de `scores[gap_id]["source_refs"]` existe dans `ev_index`.
   - Exige `score_25 >= 18`, chaque dimension >= 3/5 et zéro `hard_blockers` pour attribuer
     `PRIORITY_FOR_VALIDATION_NOT_APPROVED`.
   - Tout échec de la scorecard bloque `agent02_status` (`HARD_BLOCKED`, `LOW_TRANSFERABILITY_SCORE_25`,
     `INSUFFICIENT_SCORECARD_EVIDENCE`) afin d'empêcher le passage du GATE 6.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

PHASES = (
    "INTAKE",
    "FR_BASELINE",
    "BIG_FOUR_SCOUT",
    "FUNNEL_DEEP_DIVE",
    "GAP_FORMULATION",
    "FR_COUNTEREXAMPLE",
    "TRANSFERABILITY",
    "HANDOFF",
)
GAPS = ("PRODUCT_FEATURE", "AVATAR_OR_JTBD", "OFFER_VALUE", "CREATIVE_FUNNEL")
SOURCE_CLASSES = (
    "DIRECT_OBSERVATION",
    "PLATFORM_REPORTED",
    "THIRD_PARTY_ESTIMATE",
    "INDEX_COUNT",
    "SOURCE_ASSERTION",
    "UNKNOWN",
)
SCORECARD_DIMS = (
    "fr_problem_fit",
    "distinct_customer_benefit",
    "supply_feasibility",
    "fr_regulatory_fit",
    "unit_economics_potential",
)


def _text(s: Any, limit: int = 400) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


def _valid_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return p.scheme == "https" and bool(p.netloc) and not p.username and not p.password
    except Exception:
        return False


def _date_ok(iso: str) -> bool:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d.tzinfo is not None
    except Exception:
        return False


def solution_fingerprint(sol: dict) -> str:
    """Empreinte immuable liant une solution candidate à son audit Agent 02."""
    core = {
        "id": sol.get("id"),
        "mechanism": _text(sol.get("mechanism")),
        "claim": _text((sol.get("reason_to_win") or {}).get("claim")),
        "segment": _text(sol.get("segment")),
        "sku_spec": sol.get("sku_spec") or {},
        "supplier": sol.get("supplier"),
        "landed_cost_eur": (sol.get("economics") or {}).get("landed_cost_eur"),
    }
    raw = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_handoff(handoff: dict) -> list[str]:
    if not isinstance(handoff, dict):
        return ["HANDOFF_NOT_OBJECT"]
    issues = []
    if not _text(handoff.get("mission_id")):
        issues.append("MISSION_ID_MISSING")
    if not _text(handoff.get("target_sale_market")):
        issues.append("TARGET_SALE_MARKET_MISSING")
    jtbd = handoff.get("jtbd")
    if isinstance(jtbd, dict):
        jtbd = [jtbd]
    if not isinstance(jtbd, list) or not jtbd:
        issues.append("JTBD_REQUIRED")
    else:
        for j in jtbd:
            if not _text(j.get("jtbd_id")) or not (_text(j.get("problem_statement")) or _text(j.get("pain_statement"))):
                issues.append("JTBD_ID_AND_PROBLEM_REQUIRED")
                break
    if not isinstance(handoff.get("home_market_sources") or handoff.get("fr_market_sources"), list):
        issues.append("HOME_MARKET_EVIDENCE_LIST_REQUIRED")
    if handoff.get("G1_product_approved") is True:
        issues.append("UPSTREAM_CANNOT_GRANT_G1")
    return sorted(set(issues))


def validate_evidence(
    records: list[dict],
    mission_id: str,
    allowed_markets: set[str],
    *,
    allow_synthetic: bool = False,
) -> tuple[list[dict], list[str]]:
    if not isinstance(records, list) or not records:
        return [], ["EVIDENCE_EMPTY_OR_NOT_LIST"]
    problems, cleaned = [], []
    seen = set()

    for idx, rec in enumerate(records):
        tag = f"EVIDENCE_{idx}"
        if not isinstance(rec, dict):
            problems.append(f"{tag}_NOT_OBJECT")
            continue
        rid = _text(rec.get("source_ref"))
        if not rid or rid in seen:
            problems.append(f"{tag}_MISSING_OR_DUPLICATE_REF")
            continue
        seen.add(rid)

        if rec.get("mission_id") != mission_id:
            problems.append(f"{tag}_MISSION_MISMATCH")
        if rec.get("market_geo") not in allowed_markets:
            problems.append(f"{tag}_INVALID_MARKET:{rec.get('market_geo')}")
        if rec.get("source_class") not in SOURCE_CLASSES:
            problems.append(f"{tag}_BAD_SOURCE_CLASS")
        if not _date_ok(rec.get("observed_at", "")):
            problems.append(f"{tag}_MISSING_TIMESTAMP_WITH_OFFSET")
        if not (_valid_url(rec.get("source_url", "")) or _text(rec.get("tool_receipt"))):
            problems.append(f"{tag}_SOURCE_LOCATION_MISSING")
        if not _text(rec.get("observation")):
            problems.append(f"{tag}_EMPTY_OBSERVATION")
        if rec.get("synthetic") and not allow_synthetic:
            problems.append(f"{tag}_SYNTHETIC_NOT_ALLOWED_IN_LIVE")
        if rec.get("claimed_metric") in ("PROVEN_SKU_SALES", "PROVEN_ROAS", "PROVEN_PROFIT"):
            problems.append(f"{tag}_UNSUPPORTED_COMMERCIAL_CLAIM")
        if rec.get("entity_scope") == "AD" and rec.get("linked_sku_id") and not rec.get("sku_link_source_ref"):
            problems.append(f"{tag}_UNPROVEN_AD_SKU_JOIN")

        noise = list(rec.get("noise_flags") or [])
        if (rec.get("product_count") or 0) >= 500:
            noise.append("GENERALIST_CATALOG")
        if (rec.get("active_ads_count") or 0) >= 400:
            noise.append("GENERALIST_ADVERTISER")
        if rec.get("eu_reach") is not None and rec.get("eu_reach") < 100:
            noise.append("LOW_REACH")

        cleaned.append({
            **rec,
            "noise_flags": _unique(noise),
            "signal_weight": "DEMOTED" if noise else "USABLE",
            "is_live": not bool(rec.get("synthetic")) and rec.get("provenance_mode", "LIVE") == "LIVE",
        })

    return cleaned, sorted(set(problems))


def _feature_matches(gap: dict, row: dict, ev_index: dict[str, dict] | None) -> bool:
    req_kws = [k.lower() for k in (gap.get("required_feature_keywords") or []) if isinstance(k, str) and k.strip()]
    if not req_kws:
        return True
    haystack = " ".join([
        str(row.get("matched_feature") or ""),
        str(row.get("observation") or ""),
        *[
            str((ev_index or {}).get(r, {}).get("observation") or "")
            + " "
            + str((ev_index or {}).get(r, {}).get("feature") or "")
            for r in (row.get("source_refs") or [])
        ],
    ]).lower()
    return any(k in haystack for k in req_kws)


def falsify_home_gap(
    gap: dict,
    negative_checks: list[dict],
    home_market: str,
    valid_home_refs: set[str] | None = None,
    ev_index: dict[str, dict] | None = None,
) -> dict:
    rel = [
        c for c in (negative_checks or [])
        if c.get("gap_id") == gap.get("gap_id") and c.get("market_geo") == home_market
    ]
    queries = _unique([_text(x.get("query")) for x in rel])
    surfaces = _unique([_text(x.get("surface")) for x in rel])
    valid_home_refs = set(valid_home_refs or ())

    contradiction_refs = []
    feature_mismatch_refs = []
    for x in rel:
        if not x.get("counterexample_found"):
            continue
        valid_refs = [r for r in (x.get("source_refs") or []) if r in valid_home_refs]
        if not valid_refs:
            continue
        if _feature_matches(gap, x, ev_index):
            contradiction_refs.extend(valid_refs)
        else:
            feature_mismatch_refs.extend(valid_refs)

    contradiction_refs = _unique(contradiction_refs)
    feature_mismatch_refs = _unique(feature_mismatch_refs)

    if contradiction_refs:
        status = "COUNTEREXAMPLE_FOUND"
    elif feature_mismatch_refs:
        status = "GAP_UNCONFIRMED_FEATURE_MISMATCH"
    elif (
        len(queries) >= 3
        and {"shops", "marketplace"}.issubset(set(surfaces))
        and all(x.get("result_evidence_ref") in valid_home_refs for x in rel)
    ):
        status = "GAP_NOT_FOUND_IN_SAMPLED_SEARCHES"
    else:
        status = "GAP_UNCONFIRMED"

    return {
        "gap_id": gap.get("gap_id"),
        "home_market": home_market,
        "status": status,
        "queries_count": len(queries),
        "surfaces_checked": surfaces,
        "counterexample_refs": contradiction_refs,
        "feature_mismatch_refs": feature_mismatch_refs,
    }


def score_transferability(gap: dict, dimensions: dict, valid_ev_refs: set[str] | None = None) -> dict:
    provided = {k: (dimensions or {}).get(k) for k in SCORECARD_DIMS}
    verified = all(type(v) is int and 0 <= v <= 5 for v in provided.values())
    blockers = sorted(set((dimensions or {}).get("hard_blockers") or []))
    refs = _unique((dimensions or {}).get("source_refs") or [])
    unknown_refs = [r for r in refs if valid_ev_refs is not None and r not in valid_ev_refs]
    score_25 = sum(provided.values()) if verified else None

    if blockers:
        status = "HARD_BLOCKED"
    elif not verified or not refs or unknown_refs:
        status = "INSUFFICIENT_VALIDATED_INPUTS"
    elif gap.get("status") not in ("TESTABLE_DIFFERENTIATION_HYPOTHESIS", "RED_OCEAN_DIFFERENTIATED_HYPOTHESIS"):
        status = "GAP_NOT_READY"
    elif score_25 >= 18 and min(provided.values()) >= 3:
        status = "PRIORITY_FOR_VALIDATION_NOT_APPROVED"
    else:
        status = "LOW_PRIORITY_OR_WEAK_FIT"

    return {
        "gap_id": gap.get("gap_id"),
        "dimensions": provided,
        "hard_blockers": blockers,
        "score_25": score_25,
        "status": status,
        "source_refs": refs,
        "unknown_source_refs": unknown_refs,
    }


def _is_valid_voc_evidence(ev: dict) -> bool:
    """Refuse qu'une estimation tierce de trafic/catalogue boutique passe pour de la VoC client."""
    if ev.get("source_class") in ("THIRD_PARTY_ESTIMATE", "INDEX_COUNT", "UNKNOWN"):
        return False
    if ev.get("entity_scope") in ("SHOP", "SKU", "AD", "ADVERTISER", "MARKET"):
        return False
    return ev.get("entity_scope") in ("REVIEW", "COMMENT", "FORUM", "VOC", "CUSTOMER_TESTIMONY")


def run_audit(
    handoff: dict,
    evidence: list[dict],
    gaps: list[dict],
    negative_checks: list[dict],
    scores: dict,
    *,
    allow_synthetic: bool = False,
) -> dict:
    h_issues = validate_handoff(handoff)
    mission_id = _text((handoff or {}).get("mission_id"))
    home_market = _text((handoff or {}).get("target_sale_market") or "FR")
    foreign_markets = tuple((handoff or {}).get("foreign_research_markets") or ("US", "GB", "CA", "AU"))
    allowed_markets = {home_market, *foreign_markets}

    if h_issues:
        return {"status": "REJECTED_HANDOFF", "mission_id": mission_id, "issues": h_issues}

    cleaned_ev, ev_issues = validate_evidence(
        evidence, mission_id, allowed_markets, allow_synthetic=allow_synthetic
    )
    if ev_issues:
        return {"status": "REJECTED_EVIDENCE", "mission_id": mission_id, "issues": ev_issues}

    home_ev = [e for e in cleaned_ev if e["market_geo"] == home_market]
    if not home_ev:
        return {"status": "BLOCKED_NO_HOME_EVIDENCE", "mission_id": mission_id, "issues": ["NO_HOME_MARKET_EVIDENCE"]}

    if not isinstance(gaps, list) or not gaps:
        return {"status": "NEEDS_GAP_RESEARCH", "mission_id": mission_id, "issues": ["ZERO_GAPS_SUPPLIED"]}

    ev_index = {e["source_ref"]: e for e in cleaned_ev}
    solutions_in = {
        s["id"]: s for s in (handoff.get("solution_candidates_if_stage7") or [])
        if isinstance(s, dict) and s.get("id")
    }

    evaluated_gaps = []
    solution_audits = {}

    for g in gaps:
        gid = _text(g.get("gap_id"))
        sol_id = _text(g.get("solution_id"))
        gtype = g.get("gap_type")
        if not gid or gtype not in GAPS or not _text(g.get("jtbd_id")):
            evaluated_gaps.append({"gap_id": gid, "status": "INVALID_GAP", "reasons": ["MISSING_ID_TYPE_OR_JTBD"]})
            continue

        home_refs = [
            r for r in (g.get("home_source_refs") or g.get("fr_source_refs") or [])
            if r in ev_index and ev_index[r]["market_geo"] == home_market
        ]
        foreign_refs = [
            r for r in (g.get("foreign_source_refs") or [])
            if r in ev_index and ev_index[r]["market_geo"] in foreign_markets
        ]
        raw_voc_refs = [r for r in (g.get("voc_source_refs") or []) if r in ev_index]
        voc_refs = [
            r for r in raw_voc_refs
            if _is_valid_voc_evidence(ev_index[r])

        ]
        rejected_shop_as_voc = [r for r in raw_voc_refs if r not in voc_refs]

        f_check = falsify_home_gap(g, negative_checks, home_market, {e["source_ref"] for e in home_ev}, ev_index)

        reasons = []
        if not home_refs:
            reasons.append("HOME_BASELINE_NOT_EVIDENCED")
        if rejected_shop_as_voc:
            reasons.append(f"SHOP_ESTIMATE_REJECTED_AS_VOC:{rejected_shop_as_voc}")
        if not voc_refs:
            reasons.append("CUSTOMER_VALUE_UNEVIDENCED")
        if not _text(g.get("customer_value")):
            reasons.append("CUSTOMER_VALUE_NOT_DESCRIBED")
        if g.get("false_claim_or_ip_risk"):
            reasons.append("CLAIMS_OR_IP_BLOCKER")

        if reasons:
            g_status = "NEEDS_EVIDENCE"
            route = "RESEARCH_REQUIRED"
            req_proof = "SUPPLY_MISSING_EVIDENCE"
        elif f_check["status"] == "COUNTEREXAMPLE_FOUND":
            g_status = "RED_OCEAN_DIFFERENTIATED_HYPOTHESIS"
            route = "RED_OCEAN_DIFFERENTIATED"
            req_proof = "HEAD_TO_HEAD_SUPERIORITY_VS_COUNTEREXAMPLE"
        elif f_check["status"].startswith("GAP_UNCONFIRMED"):
            g_status = "NEEDS_EVIDENCE"
            route = "COUNTERSEARCH_INCOMPLETE"
            req_proof = "COMPLETE_COUNTERSEARCH_ON_SHOPS_AND_MARKETPLACES"
        else:
            g_status = "TESTABLE_DIFFERENTIATION_HYPOTHESIS"
            route = "STAR_FEATURE_OR_ADAPTATION" if foreign_refs else "RED_OCEAN_DIFFERENTIATED"
            req_proof = "STEP_10_SAMPLE_PROOF"

        gap_record = {
            "gap_id": gid,
            "solution_id": sol_id,
            "jtbd_id": g.get("jtbd_id"),
            "gap_type": gtype,
            "customer_value": _text(g.get("customer_value")),
            "original_angle": _text(g.get("original_angle")),
            "status": g_status,
            "strategic_route": route,
            "required_proof": req_proof,
            "home_falsification": f_check,
            "home_source_refs": home_refs,
            "foreign_source_refs": foreign_refs,
            "voc_source_refs": voc_refs,
            "reasons": reasons,
        }
        has_explicit_scorecard = isinstance((scores or {}).get(gid), dict) and bool((scores or {}).get(gid))
        sc = score_transferability(gap_record, (scores or {}).get(gid, {}), valid_ev_refs=set(ev_index.keys()))
        gap_record["transferability_scorecard"] = sc
        evaluated_gaps.append(gap_record)

        if sol_id and sol_id in solutions_in:
            sol_obj = solutions_in[sol_id]
            fp = solution_fingerprint(sol_obj)
            # La scorecard /25 est désormais contraignante pour la solution évaluée
            if sc["status"] == "HARD_BLOCKED":
                a02_status = "HARD_BLOCKED_BY_SCORECARD_25"
            elif sc["status"] == "LOW_PRIORITY_OR_WEAK_FIT":
                a02_status = "LOW_TRANSFERABILITY_SCORE_25"
            elif has_explicit_scorecard and sc["status"] == "INSUFFICIENT_VALIDATED_INPUTS":
                a02_status = "INVALID_SCORECARD_25_INPUTS"
            elif g_status not in ("TESTABLE_DIFFERENTIATION_HYPOTHESIS", "RED_OCEAN_DIFFERENTIATED_HYPOTHESIS"):
                a02_status = "NEEDS_EVIDENCE"
            elif sc["status"] != "PRIORITY_FOR_VALIDATION_NOT_APPROVED":
                a02_status = "SCORECARD_25_MISSING_OR_UNVALIDATED"
            else:
                a02_status = (
                    "NOT_A_CONFIRMED_GAP"
                    if f_check["status"] == "COUNTEREXAMPLE_FOUND"
                    else "AUDITED_READY"
                )
            solution_audits[sol_id] = {
                "solution_id": sol_id,
                "gap_id": gid,
                "jtbd_id": _text(g.get("jtbd_id")),
                "solution_fingerprint": fp,
                "agent02_status": a02_status,
                "gap_status": g_status,
                "strategic_route": route,
                "transferability_score_25": sc["score_25"],
                "transferability_status": sc["status"],
                "counterexample_refs": f_check["counterexample_refs"],
                "evidence_refs": _unique(home_refs + foreign_refs + voc_refs),
            }

    valid_gaps = [
        g for g in evaluated_gaps
        if g.get("status") in ("TESTABLE_DIFFERENTIATION_HYPOTHESIS", "RED_OCEAN_DIFFERENTIATED_HYPOTHESIS")
        and (g.get("transferability_scorecard") or {}).get("status") == "PRIORITY_FOR_VALIDATION_NOT_APPROVED"
    ]
    overall_status = (
        "READY_FOR_AGENT01_REVIEW_NOT_APPROVED" if valid_gaps else "NEEDS_EVIDENCE_NO_VALIDATED_GAP"
    )
    provenance_mode = "REPLAY" if any(not e["is_live"] for e in cleaned_ev) else "LIVE"

    payload = {
        "schema": "ECOM_OS_AGENT02_AUDIT_B1",
        "mission_id": mission_id,
        "target_sale_market": home_market,
        "research_markets": [home_market, *foreign_markets],
        "phases_executed": list(PHASES),
        "provenance_mode": provenance_mode,
        "status": overall_status,
        "evidence_count": len(cleaned_ev),
        "validated_gap_count": len(valid_gaps),
        "evaluated_gaps": evaluated_gaps,
        "solution_audits": solution_audits,
        "evidence_ledger": cleaned_ev,
    }
    payload["audit_digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return payload


# --- Agent 02 en mode FETCH_VERIFIED : dossier concurrentiel EXPLORATOIRE (jamais un audit GATE 6) ---
_DEFECTS = ("FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND")
_ACCEPTED_GEO = ("RESIDENCE_DECLARED", "FR_MARKET_SITE")


def _norm(x: Any) -> str:
    return _text(x, 120).casefold()


def exploratory_competitive_dossier(rows: list[dict], candidates: list[dict], solutions: dict) -> dict:
    """Concurrents, gaps et solutions à partir des SEULES observations FETCH_VERIFIED rattachées aux candidats.

    - Concurrents = solutions en place nommées (`incumbent`) dans des observations vérifiées.
    - Gap = dimension où >= 2 comptes FR qualifiés signalent un défaut et aucune satisfaction vérifiée ne contredit.
    - Solution proposée par LLM_Provider = SUPPORTED seulement si elle vise un gap étayé et comporte une composante
      de réachat ; sinon UNSUPPORTED / NO_RECURRENCE. Aucune donnée financière, aucun statut d'audit GATE 6.
    """
    out = {}
    for cand in candidates:
        cid = cand["candidate_id"]
        mine = [r for r in rows if cid in (r.get("candidate_ids") or []) and r.get("provenance_mode") == "FETCH_VERIFIED"]
        comp: dict[str, dict] = {}
        for r in mine:
            inc = _text(r.get("incumbent"), 80)
            if not inc:
                continue
            c = comp.setdefault(inc, {"incumbent": inc, "urls": [], "defect_refs": [], "satisfaction_refs": [],
                                      "repeat_purchase_refs": []})
            c["urls"] = _unique(c["urls"] + [r["source_url"]])
            key = ("defect_refs" if r.get("kind") in _DEFECTS else "satisfaction_refs" if r.get("kind") == "SATISFACTION"
                   else "repeat_purchase_refs" if r.get("kind") == "REPEAT_PURCHASE" else None)
            if key:
                c[key].append(r["observation_id"])
        gaps: dict[str, dict] = {}
        for r in mine:
            dim = _norm(r.get("dimension"))
            if dim and r.get("kind") in _DEFECTS:
                g = gaps.setdefault(dim, {"dimension": dim, "defect_refs": [], "accounts": set(), "qualified_accounts": set(),
                                          "contradicting_satisfactions": []})
                g["defect_refs"].append(r["observation_id"])
                g["accounts"].add(r.get("author_key"))
                if r.get("geo_basis") in _ACCEPTED_GEO and r.get("spontane") is True:
                    g["qualified_accounts"].add(r.get("author_key"))
        for r in mine:
            sd = _norm(r.get("satisfied_dimension"))
            if r.get("kind") == "SATISFACTION" and sd in gaps:
                gaps[sd]["contradicting_satisfactions"].append(r["observation_id"])
        for g in gaps.values():
            q = len(g["qualified_accounts"])
            g["status"] = ("CONTESTED" if g["contradicting_satisfactions"] else
                           "EXPLORATORY_GAP" if q >= 2 else "WEAK_SIGNAL")
            g["accounts"], g["qualified_accounts"] = len(g["accounts"]), q
        sols = []
        rec = (cand.get("recurrence") or {}).get("status")
        for s in solutions.get(cid) or []:
            dims = [_norm(d) for d in s.get("addresses_dimensions") or []]
            backed = [d for d in dims if gaps.get(d, {}).get("status") in ("EXPLORATORY_GAP", "CONTESTED", "WEAK_SIGNAL")]
            # Méthode active : la récurrence d'achat est un avantage évalué, jamais un motif d'exclusion.
            if not backed:
                st = "UNSUPPORTED_NO_VERIFIED_GAP"
            elif any(gaps[d]["status"] == "EXPLORATORY_GAP" for d in backed):
                st = "EXPLORATORY_SUPPORTED"
            else:
                st = "EXPLORATORY_WEAK"
            sols.append({"id": _text(s.get("id"), 60), "description": _text(s.get("description"), 300),
                         "recurrence_component": _text(s.get("recurrence_component"), 120),
                         "addresses_dimensions": dims, "gap_refs": {d: gaps[d]["defect_refs"] for d in backed},
                         "status": st, "source": "LLM_PROPOSAL_ENGINE_CHECKED",
                         "recurrence_component_present": bool(str(s.get("recurrence_component") or "").strip())})
        g2b = str(cand.get("gate2b") or "")
        unc = ["GATE_2B_NOT_VALIDATED"] if (not g2b.startswith("CONVERGENCE_") or g2b == "CONVERGENCE_HYPOTHESIS_DETECTED") else []
        unc += ["NO_NAMED_COMPETITOR_OBSERVED"] if not comp else []
        unc += ["INTERNATIONAL_SUBSTITUTES_NOT_RESEARCHED", "AUTHOR_IDENTITY_NOT_VERIFIED"]
        out[cid] = {"competitors": list(comp.values()), "gaps": list(gaps.values()), "solutions": sols,
                    "recurrence": rec, "uncertainties": unc}
    return {"schema": "ECOM_OS_AGENT02_EXPLORATORY_DOSSIER", "status": "EXPLORATORY_NOT_VALIDATED",
            "provenance_mode": "FETCH_VERIFIED", "certified_live": False, "gate6_audit": False,
            "financial_analysis": "HORS_PERIMETRE", "candidates": out}


# --- Agent 02 analyste concurrentiel (méthode active) : contre-enquête obligatoire avant toute conclusion ---
A02_INTENTS = {"FR_COMPETITORS", "SATISFACTION", "NEGATIVE_REVIEWS", "INVALIDATION", "INTERNATIONAL", "SUBSTITUTES"}
A02_COMP_TYPES = {"DIRECT", "INDIRECT", "SUBSTITUTE", "BEHAVIOR"}
A02_RESULTS = {"INVALIDATED", "NOT_INVALIDATED", "INCONCLUSIVE"}


def _policy() -> dict:
    p = Path(__file__).resolve().parents[1] / "contracts" / "research_policy.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"observable_prices_allowed": False}


def agent02_v2_assessment(rows: list[dict], cand: dict, research: dict | None, thresholds: dict,
                          gaps: list[dict] | None = None) -> dict:
    """Verdict Agent 02 calculé par le moteur à partir (1) des observations vérifiées du candidat et (2) du journal de
    contre-recherche proposé par LLM_Provider. Sans contre-recherche complète : aucune conclusion favorable possible."""
    from .demand import distinct_persons, eligible
    th = thresholds["agent02"]
    cid = cand["candidate_id"]
    research = research or {}
    pol = _policy()
    mine = [r for r in rows if cid in (r.get("candidate_ids") or []) and r.get("provenance_mode") == "FETCH_VERIFIED"]
    refs = {r["observation_id"] for r in mine}
    qual = [r for r in mine if eligible(r)]
    sat_p = distinct_persons([r for r in qual if r.get("kind") == "SATISFACTION"])
    def_p = distinct_persons([r for r in qual if r.get("kind") in _DEFECTS])
    queries = [q for q in research.get("queries") or [] if isinstance(q, dict) and q.get("intent") in A02_INTENTS]
    fr_q = [q for q in queries if q.get("market", "FR") == "FR"]
    issues = []
    if len(fr_q) < th["min_fr_queries"]:
        issues.append(f"FR_QUERIES_BELOW_{th['min_fr_queries']}")
    if th["requires_satisfaction_review_analysis"] and not any(q["intent"] == "SATISFACTION" for q in queries):
        issues.append("POSITIVE_REVIEWS_NOT_ANALYSED")
    if not any(q["intent"] == "NEGATIVE_REVIEWS" for q in queries):
        issues.append("NEGATIVE_REVIEWS_NOT_ANALYSED")
    inval = [a for a in research.get("invalidation_attempts") or [] if isinstance(a, dict) and a.get("result") in A02_RESULTS
             and str(a.get("hypothesis") or "").strip() and str(a.get("method") or "").strip()]
    if len(inval) < th["min_invalidation_attempts"]:
        issues.append("NO_INVALIDATION_ATTEMPT")
    comps, prices_dropped = [], 0
    for c in research.get("competitors") or []:
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        ev = [r for r in c.get("evidence_refs") or [] if r in refs]
        e = {"name": _text(c["name"], 80), "type": c.get("type") if c.get("type") in A02_COMP_TYPES else "UNCLASSIFIED",
             "market": c.get("market", "FR"), "urls": [u for u in c.get("urls") or [] if _valid_url(u)][:10],
             **{k: _text(c.get(k), 300) for k in ("mechanism", "positioning", "ad_angles", "objections",
                                                 "served_segments", "loyalty_reasons") if c.get(k)},
             "evidence_refs": ev, "evidence_status": "VERIFIED_OBSERVATIONS" if ev else "DISCOVERED_UNVERIFIED"}
        if e["market"] != "FR":
            e["role"] = "INSPIRATION_ONLY_NOT_FR_DEMAND"
        if c.get("price_observed") is not None:
            if pol.get("observable_prices_allowed"):
                e["price_observed"] = _text(c["price_observed"], 60)
            else:
                prices_dropped += 1
        comps.append(e)
    # Prix et offres publics : uniquement des observations VÉRIFIÉES sur la page (citation retrouvée), avec source et date.
    prices = [{"amount": r.get("amount"), "currency": r.get("currency", "EUR"), "url": r.get("source_url"),
               "observed_at": r.get("captured_at"), "about": _text(r.get("incumbent"), 80), "ref": r["observation_id"]}
              for r in mine if r.get("kind") == "PRICE_OBSERVED" and r.get("amount") is not None]
    offers = [{"text": _text(r.get("phrase_exacte"), 200), "url": r.get("source_url"), "observed_at": r.get("captured_at"),
               "about": _text(r.get("incumbent"), 80), "ref": r["observation_id"]} for r in mine if r.get("kind") == "OFFER_OBSERVED"]
    if not pol.get("observable_prices_allowed"):
        prices_dropped += len(prices)
        prices = []
    elif th.get("requires_price_observation_or_explicit_absence") and not prices and not research.get("no_public_price_found"):
        issues.append("PUBLIC_PRICES_NOT_OBSERVED")
    # Les gaps sont recalculés ici si non fournis : dimension où des auteurs distincts signalent un défaut sans satisfaction.
    if gaps is None:
        dims: dict[str, dict] = {}
        for r in qual:
            d = _norm(r.get("dimension")) if r.get("kind") in _DEFECTS else None
            if d:
                dims.setdefault(d, {"dimension": d, "def": [], "sat": 0})["def"].append(r)
        for r in qual:
            sd = _norm(r.get("satisfied_dimension"))
            if r.get("kind") == "SATISFACTION" and sd in dims:
                dims[sd]["sat"] += 1
        gaps = [{"dimension": d, "status": "EXPLORATORY_GAP" if len(distinct_persons(g["def"])) >= 2 and not g["sat"]
                 else "CONTESTED" if g["sat"] else "WEAK_SIGNAL"} for d, g in dims.items()]
    exploratory_gaps = [g["dimension"] for g in gaps or [] if g.get("status") == "EXPLORATORY_GAP"]
    if issues:
        verdict = "INSUFFICIENT_COUNTER_RESEARCH"
    elif not comps:
        verdict = "COMPETITION_UNKNOWN_SEARCH_INSUFFICIENT"   # jamais « marché vide »
    elif any(a["result"] == "INVALIDATED" for a in inval):
        verdict = "INVALIDATED_BY_COUNTER_RESEARCH"
    elif sat_p and len(sat_p) >= th["well_solved_satisfaction_ratio"] * max(len(def_p), 1):
        verdict = "ALREADY_WELL_SOLVED"
    elif not exploratory_gaps:
        verdict = "NO_CREDIBLE_ADVANTAGE_FOUND"
    else:
        verdict = "DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN"
    return {"verdict": verdict, "counter_research_issues": issues, "fr_queries": len(fr_q),
            "international_queries": len(queries) - len(fr_q), "invalidation_attempts": inval,
            "competitors": comps, "satisfied_persons": len(sat_p), "dissatisfied_persons": len(def_p),
            "exploratory_gaps": exploratory_gaps, "market_empty_claim_allowed": False,
            "public_prices": prices, "public_price_range": ([min(x["amount"] for x in prices), max(x["amount"] for x in prices)]
                                                            if prices else None), "public_offers": offers,
            "saturation": {"competitors_listed": len(comps), "direct": sum(c["type"] == "DIRECT" for c in comps),
                           "with_verified_evidence": sum(c["evidence_status"] == "VERIFIED_OBSERVATIONS" for c in comps)},
            "amounts_dropped_by_policy": prices_dropped,
            "amount_collection_policy": "ALLOWED_BY_OWNER" if pol.get("observable_prices_allowed") else "DISABLED_PENDING_OWNER_DECISION"}
