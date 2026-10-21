"""Automate Décisionnel ECOM_OS_CLEAN_B3 (Gates 4 à 9) — Product-First.

Ajoute aux garanties B1/B2 les verrouillages B3 :
1. `load_agent02_audits` vérifie `mission_id`, `target_sale_market`, le digest d'intégrité, les preuves
   et l'existence d'au moins un gap avec scorecard `/25` = `PRIORITY_FOR_VALIDATION_NOT_APPROVED` (>= 18/25).
2. `_bind_verified_agent02_audits` vérifie que l'audit Agent 02 de chaque solution correspond au bon
   `candidate_id` (`jtbd_id`) et au bon `solution_fingerprint`. Si la scorecard `/25` de la solution a
   échoué (`HARD_BLOCKED_BY_SCORECARD_25`, `LOW_TRANSFERABILITY_SCORE_25`, `INVALID_SCORECARD_25_INPUTS`,
   `SCORECARD_25_MISSING_OR_UNVALIDATED`), la solution ne peut jamais être retenue comme `CREDIBLE` au GATE 6.
3. `decide_gate7` (Filtre 14 questions) :
   - Supporte les réponses structurées `{"answer": "YES", "evidence_refs": ["OBS-0001"], "note": "..."}`.
   - En mode B3 (`require_filter_evidence=True` ou dès qu'une réponse est structurée ou contient `ASSUMPTION`),
     tout `YES` / `YES_INDICATIVE` dépourvu d'`evidence_refs` vérifiées dans le dossier est reclassé en
     `ASSUMPTION` (`unverified_assumptions`) et ne compte pas dans les 12/14 requis pour avancer.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .competitor_intel import solution_fingerprint
from .ledger import FAMILY

GATE4_MIN_SCORE = 28
GATE5_QUESTIONS = (
    "trigger",
    "current_solution",
    "why_it_fails",
    "real_want",
    "payment_blockers",
    "proof_of_better",
)
GATE6_MIN_CANDIDATES = 3
GATE6_MIN_CREDIBLE = 1
SATISFACTION_MIN_INDEPENDENT = 2
RESIDUAL_FRUSTRATION_MIN = 2
SOLUTION_EVIDENCE_MIN_FAMILIES = 2
AGGREGATE_MIN_N = 30
AGGREGATE_SATISFIED_SHARE = 0.85
PRODUCT_FILTER_QUESTIONS = tuple(f"q{i}" for i in range(1, 15))
PRODUCT_FILTER_MIN_YES = 12

MAX_ROUNDS_PER_GATE = 3
MAX_ITEM_ATTEMPTS = 2
RULES_VERSION = "2026-10-19-B3"

COST_CLASSES = ("FREE", "PAID_MARKETPULSE", "PAID_OTHER", "PURCHASE", "SUPPLIER_CONTACT", "LEGAL")
OPERATIONAL_COSTS = {"PURCHASE", "SUPPLIER_CONTACT", "LEGAL"}
STAGE_GATE = {5: "GATE_4", 6: "GATE_5", 7: "GATE_6", 8: "GATE_7"}
PARKED = (
    "PARKED_INSUFFICIENT_EVIDENCE",
    "PARKED_PROMISING",
    "PARKED_BELOW_SHORTLIST",
    "PARKED_NO_REASON_FOUND",
)
CLOSED = ("REJECTED", *PARKED, "PARKED_AWAITING_AUTHORIZATION", "PROPOSED_FOR_COMMERCIAL_SELECTION")
YES = ("YES", "YES_INDICATIVE")
DEFECT_KINDS = ("FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND")
SATISFACTION_KINDS = ("SATISFACTION",)
CREDIBLE_STATUSES = ("CREDIBLE", "CREDIBLE_UNCERTAIN", "CREDIBLE_CONTESTED")
SCORECARD_BLOCK_STATUSES = (
    "HARD_BLOCKED_BY_SCORECARD_25",
    "LOW_TRANSFERABILITY_SCORE_25",
    "INVALID_SCORECARD_25_INPUTS",
    "SCORECARD_25_MISSING_OR_UNVALIDATED",
)


def _refs(x) -> set[str]:
    return {r for r in (x or []) if isinstance(r, str) and r.strip()}


def _decision(verdict: str, gate: str, reasons: list[str], **kw) -> dict:
    return {"verdict": verdict, "gate": gate, "reasons": list(reasons), **kw}


def compute_scope_key(mission_id: str, candidate_id: str, item: dict) -> str:
    payload = {
        "mission_id": mission_id,
        "candidate_id": candidate_id,
        "item_id": item.get("id"),
        "solution_id": item.get("solution_id") or item.get("solution") or "MARKET_LEVEL",
        "provider": item.get("provider") or item.get("supplier") or item.get("tool") or "DEFAULT_PROVIDER",
        "cost_class": item.get("cost_class", "FREE"),
        "max_amount": float(item.get("est_credits") or item.get("max_amount") or 0.0),
        "currency": item.get("currency") or "CREDITS",
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "SCOPE-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


def solution_material_fingerprint(sol: dict) -> str:
    body = {
        "base_fp": solution_fingerprint(sol),
        "supplier": sol.get("supplier"),
        "landed_cost_eur": (sol.get("economics") or {}).get("landed_cost_eur"),
        "materials": (sol.get("sku_spec") or {}).get("materials"),
        "dimensions": (sol.get("sku_spec") or {}).get("dimensions"),
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "MAT-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


def fingerprint(dossier: dict) -> str:
    core = {k: v for k, v in dossier.items() if k not in ("stage", "info_needed", "_ledger", "_agent02_audits", "_mission_id")}
    core["info_needed"] = sorted(
        f"{i.get('id', '')}:{i.get('provider', '')}:{i.get('max_amount', '')}"
        for i in (dossier.get("info_needed") or [])
    )
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]


def load_agent02_audits(
    audit_path: Path | None,
    *,
    expected_mission_id: str | None = None,
    expected_market: str | None = None,
) -> dict[str, dict]:
    """Charge le rapport signé de l'Agent 02 et vérifie l'intégrité, les preuves et les scorecards /25."""
    if not audit_path or not Path(audit_path).exists():
        return {}
    report = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    if report.get("status") != "READY_FOR_AGENT01_REVIEW_NOT_APPROVED":
        return {}
    if report.get("provenance_mode") != "REPLAY":
        return {}
    if expected_mission_id and report.get("mission_id") != expected_mission_id:
        return {}
    if expected_market and report.get("target_sale_market") != expected_market:
        return {}
    body = {k: v for k, v in report.items() if k != "audit_digest"}
    expect = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    if report.get("audit_digest") != expect:
        return {}
    evidence = report.get("evidence_ledger")
    gaps = report.get("evaluated_gaps")
    if not isinstance(evidence, list) or not isinstance(gaps, list):
        return {}
    valid = [
        g for g in gaps
        if g.get("status") in ("TESTABLE_DIFFERENTIATION_HYPOTHESIS", "RED_OCEAN_DIFFERENTIATED_HYPOTHESIS")
        and (g.get("transferability_scorecard") or {}).get("status") == "PRIORITY_FOR_VALIDATION_NOT_APPROVED"
    ]
    if not evidence or not valid or report.get("evidence_count") != len(evidence) or report.get("validated_gap_count") != len(valid):
        return {}
    refs = {e.get("source_ref") for e in evidence}
    all_gap_ids = {g.get("gap_id") for g in gaps if g.get("gap_id")}
    audits = report.get("solution_audits") or {}
    if not isinstance(audits, dict):
        return {}
    if any(
        a.get("gap_id") not in all_gap_ids or not set(a.get("evidence_refs") or []).issubset(refs)
        for a in audits.values()
        if isinstance(a, dict)
    ):
        return {}
    return audits


def plan_information(
    items: list[dict],
    attempts: dict | None = None,
    *,
    mission_id: str = "",
    candidate_id: str = "",
    granted_scopes: set[str] | None = None,
    declined_scopes: set[str] | None = None,
    commercial: bool = True,
    gate: str | None = None,
) -> dict:
    attempts = attempts or {}
    granted_scopes = granted_scopes or set()
    declined_scopes = declined_scopes or set()
    out = {"free": [], "paid": [], "operational": [], "not_worth_effort": [], "exhausted": [], "declined": []}

    for raw in items or []:
        it = dict(raw)
        if it.get("cost_class", "FREE") not in COST_CLASSES:
            raise ValueError(f"unknown cost_class {it.get('cost_class')}")
        it.setdefault("cost_class", "FREE")
        if gate and it.get("gate") not in (None, gate):
            continue
        skey = compute_scope_key(mission_id, candidate_id, it)
        if it.get("scope_key") and it["scope_key"] != skey:
            raise ValueError("SCOPE_KEY_MISMATCH_FOR_ITEM")
        it["scope_key"] = skey
        attempt_key = f"{it['id']}@{skey}"

        if skey in declined_scopes:
            out["declined"].append(it)
            continue
        if it["cost_class"] in OPERATIONAL_COSTS:
            it["phase"] = "OPERATIONAL"
        if commercial and it.get("phase") == "OPERATIONAL":
            out["operational"].append(it)
        elif not it.get("decisive"):
            out["not_worth_effort"].append({**it, "why": "CANNOT_CHANGE_THE_DECISION"})
        elif attempts.get(attempt_key, 0) >= MAX_ITEM_ATTEMPTS or attempts.get(it["id"], 0) >= MAX_ITEM_ATTEMPTS:
            out["exhausted"].append(it)
        else:
            is_free_or_granted = it["cost_class"] == "FREE" or skey in granted_scopes
            out["free" if is_free_or_granted else "paid"].append(it)
    return out


def _needs(gate: str, reasons: list[str], info: dict, rounds_exhausted: bool, **kw) -> dict:
    if info["free"] and not rounds_exhausted:
        return _decision("RESEARCH", gate, reasons, research=info["free"], **kw)
    if info["paid"]:
        return _decision(
            "REQUEST_AUTHORIZATION",
            gate,
            reasons,
            authorization=info["paid"],
            research_free_exhausted=bool(info["exhausted"]) or rounds_exhausted,
            **kw,
        )
    return _decision(
        "PARK",
        gate,
        reasons + ["DECISIVE_INFORMATION_UNOBTAINABLE_WITH_AUTHORIZED_MEANS"],
        missing=[i["id"] for i in info["free"] + info["exhausted"] + info["declined"]],
        **kw,
    )


# --- GATE 4 ---
def decide_gate4(d: dict, attempts=None, rounds_exhausted: bool = False) -> dict:
    g = "GATE_4"
    major = [
        f["id"]
        for f in d.get("red_flags", [])
        if f.get("major") and f.get("confirmed") and _refs(f.get("refs"))
    ]
    if major:
        return _decision("REJECT", g, [f"MAJOR_RED_FLAG:{x}" for x in major])

    research = bool(d.get("research_mode"))
    econ = None if research else (d.get("economic_screen") or {}).get("status")
    if econ and econ.startswith("RED"):
        return _decision("REJECT", g, ["ECONOMIC_SCREEN_RED"])
    # Méthode active : la récurrence d'achat est un avantage évalué (axe Gamme/LTV), jamais un veto (consigne propriétaire).
    sc = d.get("score") or {}
    reasons, items = [], list(d.get("info_needed", []))
    if sc.get("score") is not None and sc.get("status") != "REJECTED_UNSUPPORTED_SCORE":
        if sc["score"] < GATE4_MIN_SCORE:
            return _decision("PARK_BELOW_SHORTLIST", g, [f"MARKET_SCORE_{sc['score']}_BELOW_{GATE4_MIN_SCORE}"])
    else:
        reasons.append("SCORE_NOT_EVIDENCE_LINKED")
        items.append({
            "id": f"{d['candidate_id']}:score_evidence",
            "decisive": True,
            "cost_class": "FREE",
            "question": "Lier chaque axe du score /40 à des preuves (score_with_evidence)",
            "gate": g,
        })

    if research and d.get("persona_mode") == "PROFILE" and (d.get("persona_fit") or {}).get("status") != "PERSONA_FIT_DEMONSTRATED":
        # INTEL_V2 (consigne propriétaire) : un problème réel sans lien démontré avec la cible n'avance pas.
        pf = (d.get("persona_fit") or {}).get("status", "MISSING")
        if pf == "PERSONA_OUT_OF_TARGET":
            return _decision("REJECT", g, ["PERSONA_OUT_OF_TARGET"])
        reasons.append(f"{pf}")
        items.append({"id": f"{d['candidate_id']}:persona_fit", "decisive": True, "cost_class": "FREE", "gate": g,
                      "question": "Témoignages où l'auteur déclare appartenir à la cible (âge/marqueur vérifiés dans son bloc)"})
    if research and (d.get("demand") or {}).get("level") != "SIGNIFICANT_MARKET_DOCUMENTED":
        # Méthode active : aucun candidat n'avance sans marché significatif documenté (témoignages largement distribués
        # et/ou statistique applicable) ; un score ne contourne jamais cette condition.
        reasons.append(f"EVIDENCE_LEVEL_{(d.get('demand') or {}).get('level', 'MISSING')}")
        items.append({"id": f"{d['candidate_id']}:demand_depth", "decisive": True, "cost_class": "FREE", "gate": g,
                      "question": "Élargir l'enquête multi-sources jusqu'à un marché significatif documenté ou une limite de preuve"})
    if (d.get("gate2b") or {}).get("status") != "PASS":
        reasons.append(f"GATE_2B_{(d.get('gate2b') or {}).get('status', 'MISSING')}")
        items.append({
            "id": f"{d['candidate_id']}:gate2b",
            "decisive": True,
            "cost_class": "FREE",
            "question": "Témoignages indépendants (>=2 personnes identifiées, >=3 familles de sources)",
            "gate": g,
        })

    if not research and econ in (None, "INSUFFICIENT_DATA", "INCOMPLETE_SCREEN"):
        reasons.append("ECONOMIC_SCREEN_MISSING")
        items.append({
            "id": f"{d['candidate_id']}:econ_screen",
            "decisive": True,
            "cost_class": "FREE",
            "question": "Prix observés et sourcés (solution_economics_screen)",
            "gate": g,
        })

    if not reasons:
        return _decision(
            "ADVANCE",
            g,
            ["SCORE_GE_28_GATE2B_PASS_NO_MAJOR_RED_FLAG"],
            score=sc.get("score"),
            economic_screen=econ,
        )
    info = plan_information(
        items,
        attempts,
        mission_id=d.get("_mission_id", ""),
        candidate_id=d["candidate_id"],
        granted_scopes=d.get("_granted_scopes"),
        declined_scopes=d.get("_declined_scopes"),
        gate=g,
    )
    return _needs(g, reasons, info, rounds_exhausted)


# --- GATE 5 ---
def decide_gate5(d: dict, attempts=None, rounds_exhausted: bool = False) -> dict:
    g = "GATE_5"
    answers = d.get("gate5") or {}
    missing = [q for q in GATE5_QUESTIONS if not _refs(answers.get(q))]
    if not missing:
        return _decision("ADVANCE", g, ["SIX_QUESTIONS_ANSWERED_WITH_EVIDENCE"])
    items = list(d.get("info_needed", [])) + [
        {
            "id": f"{d['candidate_id']}:gate5:{q}",
            "decisive": True,
            "cost_class": "FREE",
            "gate": g,
            "question": f"GATE 5 - {q} (verbatims comportement passé)",
        }
        for q in missing
    ]
    info = plan_information(
        items,
        attempts,
        mission_id=d.get("_mission_id", ""),
        candidate_id=d["candidate_id"],
        granted_scopes=d.get("_granted_scopes"),
        declined_scopes=d.get("_declined_scopes"),
        gate=g,
    )
    return _needs(g, [f"GATE5_UNANSWERED:{q}" for q in missing], info, rounds_exhausted)


# --- Validation des références & GATE 6 ---
def _norm(x: str) -> str:
    return "".join(ch for ch in (x or "").lower() if ch.isalnum() or ch == " ").split(" (")[0].strip()


def _same_entity(observed: str, declared: str) -> bool:
    a, b = _norm(observed).split(), _norm(declared).split()
    return bool(a and b) and (a[0] in b or b[0] in a)


def validate_evidence(d: dict, ledger: dict | None = None) -> tuple[dict, list[dict]]:
    d = copy.deepcopy(d)
    issues = []
    seg_of: dict[str, set] = {}
    for key, refs in (d.get("microsegments") or {}).items():
        for r in refs if isinstance(refs, list) else []:
            seg_of.setdefault(r, set()).add(key)

    def drop(sol, inc, role, ref, why):
        issues.append({
            "solution": sol.get("id"),
            "incumbent": inc and inc.get("name"),
            "role": role,
            "ref": ref,
            "issue": why,
        })

    for sol in d.get("solutions") or []:
        incs = sol.get("incumbents") or []
        neg_all = _refs(sol.get("defect_refs")).union(*[_refs(i.get("defect_refs")) for i in incs])
        pos_all = set().union(*[_refs(i.get("satisfied_refs")) for i in incs])
        both = neg_all & pos_all
        kws = [k.lower() for k in sol.get("dimension_keywords") or []]

        def clean(refs, role, inc=None):
            keep, authors = [], set()
            for r in refs or []:
                obs = (ledger or {}).get(r)
                why = None
                if r in both:
                    why = "SAME_REF_USED_AS_DEFECT_AND_SATISFACTION"
                elif ledger is not None and r.startswith("OBS-") and obs is None:
                    why = "UNKNOWN_REF"
                elif obs and (obs.get("inferred") or obs.get("evidence_type") == "INFERENCE"):
                    why = "INFERRED_NOT_OBSERVED"
                elif obs and role == "satisfied" and obs.get("kind") not in SATISFACTION_KINDS:
                    why = f"POLARITY_MISMATCH:{obs.get('kind')}_USED_AS_SATISFACTION"
                elif obs and role != "satisfied" and obs.get("kind") in SATISFACTION_KINDS:
                    why = f"POLARITY_MISMATCH:{obs.get('kind')}_USED_AS_DEFECT"
                elif obs and inc and obs.get("incumbent") and not _same_entity(obs["incumbent"], inc.get("name")):
                    why = f"ATTRIBUTION_MISMATCH:{obs['incumbent']}"
                elif sol.get("segment") and r in seg_of and sol["segment"] not in seg_of[r]:
                    why = f"SEGMENT_MISMATCH:{','.join(sorted(seg_of[r]))}"
                elif obs and role == "satisfied" and kws:
                    dim = (obs.get("satisfied_dimension") or "").lower()
                    if not dim or "non précis" in dim:
                        why = "GENERAL_SATISFACTION_NOT_ON_TARGETED_DIMENSION"
                    elif not any(k in dim for k in kws):
                        why = f"DIMENSION_MISMATCH:{dim}"
                elif obs and role != "satisfied" and kws:
                    dim = f"{obs.get('dimension') or ''} {obs.get('probleme') or ''}".lower()
                    if not any(k in dim for k in kws):
                        why = "DEFECT_NOT_ON_TARGETED_DIMENSION"
                if not why and obs and obs.get("author_key"):
                    if obs["author_key"] in authors:
                        why = "SAME_AUTHOR_NOT_INDEPENDENT"
                    authors.add(obs["author_key"])
                if why:
                    drop(sol, inc, role, r, why)
                else:
                    keep.append(r)
            return keep

        if "defect_refs" in sol:
            sol["defect_refs"] = clean(sol.get("defect_refs"), "solution_defect")
            if ledger is not None:
                fams = {
                    FAMILY.get(ledger[r]["source_type"], ledger[r]["source_type"])
                    for r in sol["defect_refs"]
                    if ledger.get(r, {}).get("source_type")
                }
                if fams:
                    sol["_defect_families"] = sorted(fams)
        for inc in incs:
            for key, role in (("defect_refs", "incumbent_defect"), ("satisfied_refs", "satisfied")):
                if key in inc:
                    inc[key] = clean(inc.get(key), role, inc)
    return d, issues


def assess_solution(s: dict, *, parent_refs: set | None = None, unknown_ok: bool = False) -> dict:
    sid, reasons, contradictions, proofs = s.get("id"), [], [], []
    if s.get("same_product_new_logo"):
        return {
            "id": sid,
            "status": "REJECTED",
            "reasons": ["SAME_PRODUCT_NEW_LOGO"],
            "situation": "DIFFERENTIATION_ALREADY_EXPLOITED",
        }
    defects = _refs(s.get("defect_refs"))
    if s.get("derivative_of"):
        if parent_refs is None or defects <= parent_refs:
            return {"id": sid, "status": "NOT_DISTINCT", "reasons": [f"DERIVATIVE_OF:{','.join(s['derivative_of'])}"]}
        reasons.append("POSITIONING_OR_OFFER_VARIANT_WITH_OWN_EVIDENCE")
    if not ((s.get("reason_to_win") or {}).get("claim")):
        return {"id": sid, "status": "MAPPED_NOT_A_CANDIDATE", "reasons": ["NO_STATED_REASON_TO_WIN"]}
    if s.get("feasibility") == "IMPOSSIBLE":
        return {"id": sid, "status": "REJECTED", "reasons": ["NOT_FEASIBLE"]}

    # Verrouillage B3 : Scorecard /25 contraignante sur la solution
    a02_st = s.get("_verified_agent02_status")
    if a02_st == "HARD_BLOCKED_BY_SCORECARD_25":
        return {
            "id": sid,
            "status": "REJECTED",
            "reasons": ["AGENT02_SCORECARD_25_HARD_BLOCKED"],
            "situation": "COMMERCIALLY_UNATTRACTIVE_DEMONSTRATED",
        }
    if a02_st in ("LOW_TRANSFERABILITY_SCORE_25", "INVALID_SCORECARD_25_INPUTS", "SCORECARD_25_MISSING_OR_UNVALIDATED"):
        return {
            "id": sid,
            "status": "UNPROVEN",
            "reasons": [f"AGENT02_SCORECARD_25_NOT_PASSED:{a02_st}"],
            "situation": "INSUFFICIENTLY_PROVEN",
        }

    eco = s.get("economics") or {}
    if eco.get("ratio_at_zero_cogs") is not None and eco["ratio_at_zero_cogs"] < 1:
        return {
            "id": sid,
            "status": "REJECTED",
            "reasons": ["ECONOMICS_FAIL_EVEN_AT_ZERO_COGS"],
            "situation": "COMMERCIALLY_UNATTRACTIVE_DEMONSTRATED",
        }
    if eco.get("safety_ratio") is not None and eco["safety_ratio"] < 1:
        if eco.get("cogs_source") in ("SUPPLIER_QUOTE", "PUBLIC_LISTING"):
            return {
                "id": sid,
                "status": "REJECTED",
                "reasons": ["INDICATIVE_ECONOMICS_BELOW_TARGET_CAC"],
                "situation": "COMMERCIALLY_UNATTRACTIVE_DEMONSTRATED",
            }
        reasons.append("ECONOMICS_TIGHT_UNQUOTED")

    if not defects:
        why = (
            "FOREIGN_INNOVATION_IS_NOT_PROOF"
            if _refs((s.get("foreign") or {}).get("traction_refs")) or _refs((s.get("foreign") or {}).get("claim_refs"))
            else "NO_CUSTOMER_EVIDENCE_OF_THE_PROBLEM"
        )
        return {"id": sid, "status": "UNPROVEN", "reasons": reasons + [why], "situation": "INSUFFICIENTLY_PROVEN"}
    if len(defects) < 2:
        return {
            "id": sid,
            "status": "UNPROVEN",
            "reasons": reasons + ["SINGLE_CUSTOMER_COMPLAINT"],
            "situation": "INSUFFICIENTLY_PROVEN",
        }

    incumbents = s.get("incumbents") or []
    if not incumbents:
        if s.get("_verified_agent02_status") == "NOT_A_CONFIRMED_GAP":
            return {
                "id": sid,
                "status": "UNPROVEN",
                "reasons": reasons + ["COUNTEREXAMPLE_NOT_ASSESSED"],
                "situation": "INSUFFICIENTLY_PROVEN",
            }
        reasons.append("NO_INCUMBENT_IDENTIFIED")

    status, situation = "CREDIBLE", "RESIDUAL_FRUSTRATION"

    def strength(i):
        n = max(len(_refs(i.get("satisfied_refs"))), len(_refs(i.get("defect_refs"))))
        return 0 if n >= SATISFACTION_MIN_INDEPENDENT else 1 if n or _refs(i.get("gap_refs")) else 2

    for inc in sorted(incumbents, key=strength):
        neg = _refs(inc.get("defect_refs")) | _refs(inc.get("gap_refs"))
        pos = _refs(inc.get("satisfied_refs"))
        name = inc.get("name", "?")
        residual = _refs(inc.get("defect_refs")) if pos else neg
        thin = len(pos) < SATISFACTION_MIN_INDEPENDENT and len(residual) < RESIDUAL_FRUSTRATION_MIN

        if not neg and not pos or (pos and thin):
            tag = "INCUMBENT_PERFORMANCE_UNKNOWN" if not pos else "INCUMBENT_EVIDENCE_TOO_THIN"
            if not unknown_ok:
                return {
                    "id": sid,
                    "status": "UNPROVEN",
                    "reasons": reasons + [f"{tag}:{name}"],
                    "situation": "INSUFFICIENTLY_PROVEN",
                }
            if inc.get("offers_same_differentiation"):
                return {
                    "id": sid,
                    "status": "NOT_CREDIBLE_SAME_OFFER",
                    "situation": "MECHANISM_ALREADY_OFFERED",
                    "reasons": reasons + [f"INCUMBENT_ALREADY_OFFERS_IT_NO_DEFECT_EVIDENCE:{name}"],
                }
            reasons.append(f"INCUMBENT_WITHOUT_CUSTOMER_EVIDENCE:{name}")
            proofs.append(f"HEAD_TO_HEAD_VS:{name}")
            status = "CREDIBLE_UNCERTAIN" if status == "CREDIBLE" else status
            continue

        if len(pos) >= SATISFACTION_MIN_INDEPENDENT and not residual:
            return {
                "id": sid,
                "status": "REJECTED",
                "situation": "DIFFERENTIATION_ALREADY_EXPLOITED",
                "reasons": reasons + [f"INCUMBENT_ALREADY_SATISFIES:{name}", "SOLUTION_LEVEL_ONLY"],
            }
        if len(pos) >= SATISFACTION_MIN_INDEPENDENT and len(residual) < RESIDUAL_FRUSTRATION_MIN:
            return {
                "id": sid,
                "status": "NOT_CREDIBLE_CONTESTED",
                "situation": "PARTIALLY_SATISFIED_RESIDUAL_UNPROVEN",
                "reasons": reasons + [f"INCUMBENT_ALREADY_SATISFIES:{name}", f"RESIDUAL_FRUSTRATION_UNPROVEN:{name}"],
                "contradictions": [{"incumbent": name, "defects": len(neg), "satisfied": len(pos)}],
            }
        agg = inc.get("aggregate") or {}
        if (
            agg.get("ref")
            and agg.get("on_targeted_dimension")
            and agg.get("n", 0) >= AGGREGATE_MIN_N
            and agg.get("positive_share", 0) >= AGGREGATE_SATISFIED_SHARE
        ):
            return {
                "id": sid,
                "status": "NOT_CREDIBLE_CONTESTED",
                "situation": "PARTIALLY_SATISFIED_RESIDUAL_MINORITY",
                "reasons": reasons + [f"PUBLISHED_RATING_{agg['positive_share']:.0%}_OF_{agg['n']}_SATISFIED:{name}"],
                "contradictions": [{"incumbent": name, "defects": len(neg), "satisfied": len(pos)}],
            }
        if pos:
            status, situation = "CREDIBLE_CONTESTED", "PARTIALLY_SATISFIED_RESIDUAL_FRUSTRATION"
            contradictions.append({"incumbent": name, "defects": len(neg), "satisfied": len(pos)})
            proofs.append(f"HEAD_TO_HEAD_VS:{name}")

    fams = s.get("_defect_families")
    if fams is not None and len(fams) < SOLUTION_EVIDENCE_MIN_FAMILIES:
        return {
            "id": sid,
            "status": "UNPROVEN",
            "reasons": reasons + [f"SINGLE_SOURCE_FAMILY:{','.join(fams)}"],
            "situation": "INSUFFICIENTLY_PROVEN",
        }
    if s.get("feasibility") != "PLAUSIBLE":
        reasons.append("FEASIBILITY_UNCONFIRMED")
    return {
        "id": sid,
        "status": status,
        "reasons": reasons + ["CUSTOMER_DEFECTS_NOT_SOLVED_BY_INCUMBENTS"],
        "situation": situation,
        "contradictions": contradictions,
        "defect_evidence": len(defects),
        "required_proof": proofs,
    }


def _bind_verified_agent02_audits(
    solutions: list[dict],
    verified_audits: dict[str, dict],
    *,
    candidate_id: str | None = None,
) -> tuple[list[dict], list[str], list[str]]:
    """Ignore tout `agent02_status` saisi par LLM_Provider et vérifie le `solution_fingerprint` + `candidate_id` (`jtbd_id`)."""
    bound, missing_audit, stale_audit = [], [], []
    for raw_sol in solutions or []:
        s = dict(raw_sol)
        s.pop("agent02_status", None)
        sid = s.get("id")
        audit = (verified_audits or {}).get(sid)
        if not audit:
            missing_audit.append(sid)
        else:
            current_fp = solution_fingerprint(s)
            if candidate_id and audit.get("jtbd_id") and audit.get("jtbd_id") != candidate_id:
                missing_audit.append(sid)
            elif audit.get("solution_fingerprint") != current_fp:
                stale_audit.append(sid)
            elif audit.get("agent02_status") in ("AUDITED_READY", "NOT_A_CONFIRMED_GAP", *SCORECARD_BLOCK_STATUSES):
                s["_verified_agent02_status"] = audit["agent02_status"]
                s["_agent02_evidence_refs"] = audit.get("evidence_refs") or []
            else:
                missing_audit.append(sid)
        bound.append(s)
    return bound, missing_audit, stale_audit


def decide_gate6(d: dict, attempts=None, rounds_exhausted: bool = False) -> dict:
    g = "GATE_6"
    d = dict(d)
    ledger = d.pop("_ledger", None)
    verified_audits = d.pop("_agent02_audits", None) or {}
    d, ev_issues = validate_evidence(d, ledger)

    bound_sols, missing_a02, stale_a02 = _bind_verified_agent02_audits(
        d.get("solutions", []), verified_audits, candidate_id=d.get("candidate_id")
    )
    d["solutions"] = bound_sols

    refs_by_sol = {s.get("id"): _refs(s.get("defect_refs")) for s in bound_sols}
    strict = [
        assess_solution(
            s,
            unknown_ok=False,
            parent_refs=set().union(*[refs_by_sol.get(p, set()) for p in s.get("derivative_of", [])]),
        )
        for s in bound_sols
    ]
    lenient = [
        assess_solution(
            s,
            unknown_ok=True,
            parent_refs=set().union(*[refs_by_sol.get(p, set()) for p in s.get("derivative_of", [])]),
        )
        for s in bound_sols
    ]

    items = list(d.get("info_needed", []))
    if missing_a02:
        items.append({
            "id": f"{d['candidate_id']}:agent02_audit",
            "decisive": True,
            "cost_class": "FREE",
            "gate": g,
            "question": f"Exécuter un audit Agent 02 sourcé (`ecom.py a02 audit`) pour {missing_a02}",
        })
    if stale_a02:
        items.append({
            "id": f"{d['candidate_id']}:agent02_reaudit_stale",
            "decisive": True,
            "cost_class": "FREE",
            "gate": g,
            "question": f"Ré-exécuter l'audit Agent 02 car la solution a changé matériellement : {stale_a02}",
        })

    explored = [
        a for a in lenient
        if a["status"] not in ("MAPPED_NOT_A_CANDIDATE", "NOT_DISTINCT")
        and "SAME_PRODUCT_NEW_LOGO" not in a["reasons"]
    ]
    if len(explored) < GATE6_MIN_CANDIDATES:
        items.append({
            "id": f"{d['candidate_id']}:explore_alternatives",
            "decisive": True,
            "cost_class": "FREE",
            "gate": g,
            "question": f"Explorer >= {GATE6_MIN_CANDIDATES} solutions distinctes ({len(explored)}/{GATE6_MIN_CANDIDATES})",
        })

    info = plan_information(
        items,
        attempts,
        mission_id=d.get("_mission_id", ""),
        candidate_id=d["candidate_id"],
        granted_scopes=d.get("_granted_scopes"),
        declined_scopes=d.get("_declined_scopes"),
        gate=g,
    )
    can_research = bool(info["free"]) and not rounds_exhausted
    assessed = strict if can_research else lenient
    credible = [a for a in assessed if a["status"] in CREDIBLE_STATUSES]
    audit_missing_or_stale = bool(missing_a02 or stale_a02)

    kw = {
        "assessed": assessed,
        "credible": [a["id"] for a in credible],
        "explored": [a["id"] for a in explored],
        "missing_agent02_audit": missing_a02,
        "stale_agent02_audit": stale_a02,
        "evidence_issues": ev_issues,
    }

    if len(credible) >= GATE6_MIN_CANDIDATES and not audit_missing_or_stale and not can_research:
        return _decision("ADVANCE", g, [f"{len(credible)}_SOLUTIONS_WITH_REAL_REASON_TO_WIN"], **kw)
    if can_research:
        extra = (["STALE_AGENT02_AUDIT_SOLUTION_CHANGED"] if stale_a02 else []) + (
            ["AGENT02_AUDIT_MISSING_OR_UNVERIFIED"] if missing_a02 else []
        )
        return _decision(
            "RESEARCH",
            g,
            extra + [f"ONLY_{len(credible)}_OF_{GATE6_MIN_CANDIDATES}_SOLUTIONS_READY"],
            research=info["free"],
            **kw,
        )
    if len(credible) >= GATE6_MIN_CREDIBLE and len(explored) >= GATE6_MIN_CANDIDATES and not audit_missing_or_stale:
        reasons = [f"{len(credible)}_OF_{len(explored)}_EXPLORED_SOLUTIONS_KEEP_A_REASON_TO_WIN"]
        if len(credible) < GATE6_MIN_CANDIDATES:
            reasons.append("BEST_CANDIDATE_GOES_TO_PRODUCT_FILTER_NOT_VALIDATED")
        return _decision("ADVANCE", g, reasons, **kw)
    if info["paid"]:
        return _decision("REQUEST_AUTHORIZATION", g, ["DECISIVE_INFORMATION_IS_PAID"], authorization=info["paid"], **kw)
    if credible:
        why = (["STALE_AGENT02_AUDIT_SOLUTION_CHANGED"] if stale_a02 else []) + (
            ["AGENT02_AUDIT_MISSING"] if missing_a02 else []
        )
        return _decision("PARK_PROMISING", g, why + ["GATE6_NOT_YET_PASSED_MARKET_NOT_INVALIDATED"], **kw)
    if explored and all(a.get("situation") == "COMMERCIALLY_UNATTRACTIVE_DEMONSTRATED" for a in explored):
        return _decision("REJECT", g, ["ALL_EXPLORED_SOLUTIONS_COMMERCIALLY_UNATTRACTIVE_DEMONSTRATED"], **kw)
    return _decision("PARK_NO_REASON_FOUND", g, ["NO_REASON_TO_WIN_FOUND_YET", "MARKET_NOT_INVALIDATED"], **kw)


def _known_valid_refs_for_candidate(d: dict) -> set[str]:
    """Only actual local ledger refs and verified Agent02 audit refs can ground a filter answer.

    The untrusted dossier's gate5 lists and defect_refs do not create provenance.
    """
    refs: set[str] = set()
    if isinstance(d.get("_ledger"), dict):
        refs.update(d["_ledger"].keys())
    if isinstance(d.get("_agent02_audits"), dict):
        for audit in d["_agent02_audits"].values():
            if isinstance(audit, dict):
                refs.update(_refs(audit.get("evidence_refs")))
    return refs


def evaluate_product_filter_14(ans: dict, valid_refs: set[str], *, require_evidence: bool = True) -> dict:
    """Évalue les 14 questions du filtre produit. Un YES sans preuve vérifiée devient ASSUMPTION."""
    yes_verified, indicative_verified, assumptions, unknown, no_list = [], [], [], [], []
    normalized = {}

    for q in PRODUCT_FILTER_QUESTIONS:
        raw = (ans or {}).get(q)
        if isinstance(raw, dict):
            val = str(raw.get("answer") or raw.get("status") or "UNKNOWN").upper()
            q_refs = [r for r in (raw.get("evidence_refs") or []) if isinstance(r, str) and r.strip()]
            has_valid_refs = bool(q_refs) and bool(valid_refs) and set(q_refs).issubset(valid_refs)
            if val in YES:
                if has_valid_refs:
                    yes_verified.append(q)
                    if val == "YES_INDICATIVE":
                        indicative_verified.append(q)
                    normalized[q] = {"status": val, "evidence_refs": q_refs}
                else:
                    assumptions.append(q)
                    normalized[q] = {"status": "ASSUMPTION", "evidence_refs": q_refs, "reason": "UNVERIFIED_OR_EMPTY_EVIDENCE_REFS"}
            elif val == "ASSUMPTION":
                assumptions.append(q)
                normalized[q] = {"status": "ASSUMPTION", "evidence_refs": q_refs}
            elif val == "NO":
                no_list.append(q)
                normalized[q] = {"status": "NO", "evidence_refs": q_refs}
            else:
                unknown.append(q)
                normalized[q] = {"status": "UNKNOWN", "evidence_refs": q_refs}
        else:
            val = str(raw or "UNKNOWN").upper()
            if val in YES:
                if require_evidence:
                    assumptions.append(q)
                    normalized[q] = {"status": "ASSUMPTION", "evidence_refs": [], "reason": "BARE_YES_WITHOUT_EVIDENCE_REFS"}
                else:
                    yes_verified.append(q)
                    if val == "YES_INDICATIVE":
                        indicative_verified.append(q)
                    normalized[q] = {"status": val, "evidence_refs": []}
            elif val == "ASSUMPTION":
                assumptions.append(q)
                normalized[q] = {"status": "ASSUMPTION", "evidence_refs": []}
            elif val == "NO":
                no_list.append(q)
                normalized[q] = {"status": "NO", "evidence_refs": []}
            else:
                unknown.append(q)
                normalized[q] = {"status": "UNKNOWN", "evidence_refs": []}

    return {
        "yes_count": len(yes_verified),
        "yes_questions": yes_verified,
        "indicative": indicative_verified,
        "assumptions": assumptions,
        "unknown": unknown,
        "no": no_list,
        "normalized": normalized,
    }


# --- GATE 7 ---
def decide_gate7(d: dict, attempts=None, rounds_exhausted: bool = False) -> dict:
    g = "GATE_7"
    g6 = decide_gate6(copy.deepcopy(d), attempts, rounds_exhausted=True)
    if g6["verdict"] != "ADVANCE":
        return _decision(
            "PARK_PROMISING" if g6.get("credible") else "PARK_NO_REASON_FOUND",
            g,
            ["GATE6_PRECONDITION_NOT_MET"] + g6.get("reasons", []),
        )

    eligible = set(g6.get("credible", []))
    sol_map = {s["id"]: s for s in d.get("solutions", []) if isinstance(s, dict) and s.get("id")}
    valid_refs = _known_valid_refs_for_candidate(d)
    require_ev = not bool(d.get("_trusted_demo_fixture_legacy_filter", False))
    rows, items = [], list(d.get("info_needed", []))

    for sid in sorted(eligible):
        ans = (d.get("product_filter") or {}).get(sid, {})
        ev_res = evaluate_product_filter_14(ans, valid_refs, require_evidence=require_ev)
        yes = ev_res["yes_count"]
        unresolved = ev_res["unknown"] + ev_res["assumptions"]
        a = next(x for x in g6["assessed"] if x["id"] == sid)
        rows.append({
            "id": sid,
            "yes": yes,
            "unknown": ev_res["unknown"],
            "assumptions": ev_res["assumptions"],
            "indicative": ev_res["indicative"],
            "normalized_answers": ev_res["normalized"],
            "evidence_rank": CREDIBLE_STATUSES.index(a["status"]),
            "required_proof": a.get("required_proof", []),
            "defect_evidence": a.get("defect_evidence", 0),
            "material_fingerprint": solution_material_fingerprint(sol_map[sid]),
        })
        if yes + len(unresolved) >= PRODUCT_FILTER_MIN_YES:
            items += [
                {
                    "id": f"{sid}:filter:{q}",
                    "solution_id": sid,
                    "decisive": True,
                    "cost_class": "FREE",
                    "gate": g,
                    "question": f"Filtre 14 - Fournir preuve vérifiable pour `{q}` sur {sid} (actuellement UNKNOWN/ASSUMPTION)",
                }
                for q in unresolved
            ]

    passing = sorted(
        [r for r in rows if r["yes"] >= PRODUCT_FILTER_MIN_YES],
        key=lambda r: (-r["yes"], r["evidence_rank"], -r["defect_evidence"], r["id"]),
    )
    info = plan_information(
        items,
        attempts,
        mission_id=d.get("_mission_id", ""),
        candidate_id=d["candidate_id"],
        granted_scopes=d.get("_granted_scopes"),
        declined_scopes=d.get("_declined_scopes"),
        gate=g,
    )

    if passing:
        primary, backup = passing[0], (passing[1] if len(passing) > 1 else None)
        p_id = primary["id"]
        op_items = [
            {
                "id": f"{p_id}:supplier_quotes",
                "solution_id": p_id,
                "provider": (sol_map.get(p_id) or {}).get("supplier") or "PRIMARY_SUPPLIER",
                "phase": "OPERATIONAL",
                "cost_class": "SUPPLIER_CONTACT",
                "max_amount": 1.0,
                "currency": "QUOTE_REQUEST",
                "question": "Étape 9 : obtenir vrais devis, landed cost et scénarios bear/base/bull",
            },
            {
                "id": f"{p_id}:sample_test",
                "solution_id": p_id,
                "provider": (sol_map.get(p_id) or {}).get("supplier") or "PRIMARY_SUPPLIER",
                "phase": "OPERATIONAL",
                "cost_class": "PURCHASE",
                "max_amount": float(((sol_map.get(p_id) or {}).get("economics") or {}).get("sample_cost_eur") or 50.0),
                "currency": "EUR",
                "question": "Étape 10 : commander et tester l'échantillon physique face aux alternatives",
            },
        ]
        for op in op_items:
            op["scope_key"] = compute_scope_key(d.get("_mission_id", ""), d["candidate_id"], op)

        return _decision(
            "PROPOSE_FOR_COMMERCIAL_SELECTION",
            g,
            ["PRODUCT_FILTER_STRONG_MAJORITY_EVIDENCE_BACKED"] + ([] if backup else ["NO_BACKUP_CANDIDATE"]),
            primary=p_id,
            primary_material_fingerprint=primary["material_fingerprint"],
            backup=backup and backup["id"],
            filter=rows,
            operational_validation=op_items,
            blocks_commercial_selection=False,
        )

    reasons = ["NO_CANDIDATE_REACHES_PRODUCT_FILTER_MAJORITY"]
    if any(r.get("assumptions") for r in rows):
        reasons.append("UNVERIFIED_YES_DOWNGRADED_TO_ASSUMPTION")
    if any(r["yes"] + len(r["unknown"]) + len(r.get("assumptions", [])) >= PRODUCT_FILTER_MIN_YES for r in rows):
        return _needs(g, reasons, info, rounds_exhausted, filter=rows)
    return _decision(
        "PARK_NO_REASON_FOUND",
        g,
        reasons + ["EVALUATED_SOLUTIONS_FAILED_FILTER_14", "MARKET_NOT_INVALIDATED"],
        filter=rows,
    )


DECIDERS = {5: decide_gate4, 6: decide_gate5, 7: decide_gate6, 8: decide_gate7}


class CandidateRegistry:
    def __init__(self, data: dict | None = None, mission_id: str | None = None):
        self.data = data or {
            "mission_id": mission_id,
            "rules_version": RULES_VERSION,
            "candidates": {},
            "authorization_requests": [],
            "events": [],
        }

    @classmethod
    def load(cls, path: Path, mission_id: str | None = None) -> "CandidateRegistry":
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else cls(mission_id=mission_id)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    @property
    def candidates(self) -> dict:
        return self.data["candidates"]

    def add(
        self,
        cid: str,
        title: str,
        *,
        stage: int,
        score=None,
        origin="",
        status="BACKLOG",
        territory=None,
        closed_reason=None,
        evidence_fingerprint=None,
    ) -> dict:
        return self.candidates.setdefault(
            cid,
            {
                "id": cid,
                "title": title,
                "stage": stage,
                "score": score,
                "origin": origin,
                "territory": territory,
                "status": status,
                "closed_reason": closed_reason,
                "rounds": {},
                "attempts": {},
                "history": [],
                "evidence_fingerprint": evidence_fingerprint,
                "granted_scopes": [],
                "declined_scopes": [],
                "selected_solution_id": None,
                "selected_material_fingerprint": None,
                "operational_validation": None,
            },
        )

    def next_candidate(self, exclude: set | None = None) -> str | None:
        open_ = [c for c in self.candidates.values()
                 if c["status"] in ("ACTIVE", "BACKLOG") and c["id"] not in (exclude or set())]
        if not open_:
            return None
        open_.sort(key=lambda c: (c["status"] != "ACTIVE", -c["stage"], -(c["score"] or 0), c["id"]))
        return open_[0]["id"]

    def check_material_integrity(self, cid: str, dossier: dict) -> str | None:
        c = self.candidates.get(cid)
        if not c or not c.get("selected_solution_id") or not c.get("selected_material_fingerprint"):
            return None
        sol = next(
            (s for s in (dossier.get("solutions") or []) if s.get("id") == c["selected_solution_id"]),
            None,
        )
        now_fp = "MISSING_SELECTED_SOLUTION" if sol is None else solution_material_fingerprint(sol)
        if now_fp != c["selected_material_fingerprint"]:
            c["status"] = "ACTIVE"
            c["stage"] = 7
            c["operational_validation"] = "INVALIDATED_AFTER_MATERIAL_CHANGE"
            c["closed_reason"] = None
            c["selected_material_fingerprint"] = None
            self.data["events"].append({
                "candidate_id": cid,
                "verdict": "STALE_PASS_INVALIDATED_MATERIAL_CHANGE",
                "new_material_fingerprint": now_fp,
            })
            return "STALE_PASS_INVALIDATED_MATERIAL_CHANGE"
        return None

    def should_study(self, cid: str, fp: str) -> tuple[bool, str]:
        c = self.candidates[cid]
        if c["status"] in CLOSED and c["status"] != "PARKED_AWAITING_AUTHORIZATION":
            if c.get("evidence_fingerprint") == fp and c.get("rules_version") == RULES_VERSION:
                return False, f"ALREADY_DECIDED_SAME_EVIDENCE:{c['status']}:{c.get('closed_reason')}"
            return True, "REOPENED_NEW_EVIDENCE"
        return True, "OPEN"

    def count_attempts(self, cid: str, dossier: dict, fp: str, count_on_fp_change: bool = True) -> list[str]:
        """`count_on_fp_change=False` (FETCH_VERIFIED) : seule une recherche déclarée faite et sans progrès
        (`research_done`, calculé par l'orchestrateur) compte ; un changement de dossier n'est pas une tentative."""
        c = self.candidates[cid]
        done = set(dossier.get("research_done") or [])
        counted = []
        for item_id, fp0 in list((c.get("requested") or {}).items()):
            if (count_on_fp_change and fp != fp0) or item_id in done:
                c["attempts"][item_id] = c["attempts"].get(item_id, 0) + 1
                del c["requested"][item_id]
                counted.append(item_id)
        if counted:
            gate = STAGE_GATE.get(dossier.get("stage", c["stage"]))
            if gate:
                c["rounds"][gate] = c["rounds"].get(gate, 0) + 1
        return counted

    def resolve_authorization(self, cid: str, item_id: str, scope_key: str, granted: bool) -> dict:
        if cid not in self.candidates:
            raise KeyError(f"UNKNOWN_CANDIDATE:{cid}")
        matching = [
            r
            for r in self.data["authorization_requests"]
            if r.get("candidate_id") == cid
            and r.get("id") == item_id
            and r.get("scope_key") == scope_key
            and r.get("status") == "PENDING_OWNER"
        ]
        if not matching:
            raise KeyError(f"UNKNOWN_OR_MISMATCHED_SCOPE_KEY:{cid}:{item_id}:{scope_key}")
        if any(compute_scope_key(self.data.get("mission_id") or "", cid, r) != scope_key for r in matching):
            raise ValueError("STORED_AUTHORIZATION_SCOPE_CONTENT_MISMATCH")

        for r in matching:
            r["status"] = "GRANTED" if granted else "DECLINED"

        c = self.candidates[cid]
        target_list = c.setdefault("granted_scopes" if granted else "declined_scopes", [])
        if scope_key not in target_list:
            target_list.append(scope_key)

        if c["status"] == "PARKED_AWAITING_AUTHORIZATION":
            c.update(status="ACTIVE", closed_reason=None)

        self.data["events"].append({
            "candidate_id": cid,
            "item_id": item_id,
            "scope_key": scope_key,
            "verdict": "AUTHORIZATION_GRANTED" if granted else "AUTHORIZATION_DECLINED",
        })
        return {"candidate_id": cid, "item_id": item_id, "scope_key": scope_key, "granted": granted}

    def operational_result(
        self,
        cid: str,
        *,
        solution_id: str,
        material_fingerprint: str,
        passed: bool,
        reason: str,
        refs: list[str],
    ) -> dict:
        if cid not in self.candidates:
            raise KeyError(f"UNKNOWN_CANDIDATE:{cid}")
        c = self.candidates[cid]
        if c["status"] != "PROPOSED_FOR_COMMERCIAL_SELECTION":
            raise ValueError(f"PRECONDITION_FAILED_NOT_COMMERCIALLY_SELECTED:{c['status']}")
        if c.get("selected_solution_id") != solution_id:
            raise ValueError(f"SOLUTION_MISMATCH:{solution_id}!={c.get('selected_solution_id')}")
        if c.get("selected_material_fingerprint") != material_fingerprint:
            raise ValueError("MATERIAL_FINGERPRINT_MISMATCH_REAUDIT_REQUIRED")
        if not _refs(refs):
            raise ValueError("OPERATIONAL_RESULT_REQUIRES_EVIDENCE_REFS")

        op_reqs = [
            r
            for r in self.data["authorization_requests"]
            if r.get("candidate_id") == cid and r.get("gate") == "GATE_8_9" and r.get("solution_id") == solution_id
        ]
        if not op_reqs or not all(r.get("status") == "GRANTED" for r in op_reqs):
            raise ValueError("OPERATIONAL_AUTHORIZATIONS_NOT_GRANTED_FOR_SOLUTION")

        c["history"].append({
            "gate": "GATE_8_9",
            "verdict": "PASS" if passed else "REJECT",
            "reasons": [reason],
            "refs": list(refs),
            "material_fingerprint": material_fingerprint,
        })
        if passed:
            c["operational_validation"] = "PASSED_READY_FOR_G1"
            c["closed_reason"] = "OPERATIONAL_VALIDATION_PASSED_AWAITING_HUMAN_G1"
        else:
            c["status"] = "REJECTED"
            c["operational_validation"] = "FAILED"
            c["selected_solution_id"] = None
            c["selected_material_fingerprint"] = None
            c["closed_reason"] = f"OPERATIONAL_VALIDATION_FAILED:{reason}"

        self.data["events"].append({
            "candidate_id": cid,
            "gate": "GATE_8_9",
            "verdict": "PASS" if passed else "REJECT",
        })
        return {
            "candidate_id": cid,
            "status": c["status"],
            "operational_validation": c["operational_validation"],
            "closed_reason": c["closed_reason"],
        }

    def record(self, cid: str, decision: dict, fp: str) -> None:
        c = self.candidates[cid]
        gate, v = decision["gate"], decision["verdict"]
        c["history"].append({"gate": gate, "verdict": v, "reasons": decision["reasons"], "fingerprint": fp})
        c["evidence_fingerprint"] = fp
        c["rules_version"] = RULES_VERSION

        if v == "RESEARCH":
            c.setdefault("requested", {}).update({it["id"]: fp for it in decision.get("research", [])})
            c["status"] = "ACTIVE"
        elif v == "ADVANCE":
            c["stage"] = max(c["stage"], next(s for s, gg in STAGE_GATE.items() if gg == gate) + 1)
            c["status"] = "ACTIVE"
        else:
            c["status"] = {
                "REJECT": "REJECTED",
                "PARK": "PARKED_INSUFFICIENT_EVIDENCE",
                "PARK_PROMISING": "PARKED_PROMISING",
                "PARK_BELOW_SHORTLIST": "PARKED_BELOW_SHORTLIST",
                "PARK_NO_REASON_FOUND": "PARKED_NO_REASON_FOUND",
                "REQUEST_AUTHORIZATION": "PARKED_AWAITING_AUTHORIZATION",
                "PROPOSE_FOR_COMMERCIAL_SELECTION": "PROPOSED_FOR_COMMERCIAL_SELECTION",
            }[v]
            c["closed_reason"] = "; ".join(decision["reasons"])

        if v == "PROPOSE_FOR_COMMERCIAL_SELECTION":
            c["selected_solution_id"] = decision.get("primary")
            c["selected_material_fingerprint"] = decision.get("primary_material_fingerprint")
            c["operational_validation"] = "PENDING_STEPS_9_10"

        if v == "REQUEST_AUTHORIZATION":
            existing_keys = {r.get("scope_key") for r in self.data["authorization_requests"]}
            for it in decision["authorization"]:
                if it["scope_key"] not in existing_keys:
                    self.data["authorization_requests"].append({
                        "candidate_id": cid,
                        "gate": gate,
                        **it,
                        "status": "PENDING_OWNER",
                    })
        self.data["events"].append({"candidate_id": cid, "gate": gate, "verdict": v})


def seed_candidates(registry: CandidateRegistry, rows: list[dict]) -> None:
    for c in rows:
        c = dict(c)
        if c.get("status", "BACKLOG") not in ("BACKLOG", "ACTIVE", "REJECTED", *PARKED):
            raise ValueError("SEED_CANNOT_SET_VALIDATED_STATUS")
        if c.get("stage", 5) != 5 and c.get("status", "BACKLOG") in ("BACKLOG", "ACTIVE"):
            raise ValueError("SEED_CANNOT_SKIP_INITIAL_GATES")
        if c.get("status", "BACKLOG") in ("REJECTED", *PARKED) and not (
            c.get("closed_reason") and _refs(c.get("closed_refs"))
        ):
            raise ValueError(f"OFF_ENGINE_ELIMINATION_WITHOUT_RECORDED_JUSTIFICATION:{c.get('id')}")
        refs = c.pop("closed_refs", None)
        cid = c.pop("id")
        registry.add(cid, c.pop("title"), **c)
        if refs:
            registry.candidates[cid]["closed_refs"] = list(refs)


def step(
    registry: CandidateRegistry,
    dossiers: dict[str, dict],
    max_decisions: int = 50,
    *,
    ledger: dict | None = None,
    agent02_audits: dict[str, dict] | None = None,
    universe: list[str] | None = None,
    provenance_mode: str = "REPLAY",
    target_market: str = "FR",
    _fixture_demo_legacy_filter: bool = False,
) -> dict:
    if provenance_mode not in ("REPLAY", "FETCH_VERIFIED"):
        raise ValueError("LIVE_AUTOPILOT_NOT_SUPPORTED_UNTIL_TRUSTED_EVIDENCE_PIPELINE")
    fetch = provenance_mode == "FETCH_VERIFIED"
    handled: set[str] = set()
    decisions, owner = [], []
    untracked = [x for x in (universe or []) if x not in registry.candidates]
    if untracked:
        return {
            "action": "REGISTER_UNTRACKED_CANDIDATES",
            "cost_class": "FREE",
            "candidate_ids": untracked,
            "why": "JOB_EXCLUDED_OUTSIDE_THE_ENGINE_WITHOUT_RECORDED_DECISION",
            "decisions": decisions,
            "owner_authorizations_needed": owner,
        }

    for cid, d in dossiers.items():
        invalidation = registry.check_material_integrity(cid, d)
        if invalidation:
            decisions.append({"candidate_id": cid, "verdict": "REOPEN", "reasons": [invalidation]})
        c = registry.candidates.get(cid)
        if c and c["status"] in ("REJECTED", *PARKED) and c.get("evidence_fingerprint"):
            if fingerprint(d) != c["evidence_fingerprint"]:
                c.update(status="ACTIVE", closed_reason=None, rounds={})
                decisions.append({"candidate_id": cid, "verdict": "REOPEN", "reasons": ["NEW_EVIDENCE"]})

    for _ in range(max_decisions):
        proposed = [c for c in registry.candidates.values() if c["status"] == "PROPOSED_FOR_COMMERCIAL_SELECTION"]
        if proposed:
            if any(
                not c.get("selected_solution_id")
                or not c.get("selected_material_fingerprint")
                or not any(
                    h.get("gate") == "GATE_7" and h.get("verdict") == "PROPOSE_FOR_COMMERCIAL_SELECTION"
                    for h in c.get("history", [])
                )
                for c in proposed
            ):
                raise ValueError("PROPOSED_STATE_WITHOUT_ENGINE_GATE7_DECISION")
            pending = [r for r in registry.data["authorization_requests"] if r["status"] == "PENDING_OWNER"]
            return {
                "action": "AWAIT_OPERATIONAL_VALIDATION",
                "candidate_id": proposed[0]["id"],
                "selected_solution_id": proposed[0].get("selected_solution_id"),
                "selected_material_fingerprint": proposed[0].get("selected_material_fingerprint"),
                "provenance_mode": provenance_mode,
                "decisions": decisions,
                "owner_authorizations_needed": owner or pending,
            }

        cid = registry.next_candidate(handled if fetch else None)
        if fetch and cid is not None and registry.candidates[cid]["stage"] > 5:
            # Intégration FETCH_VERIFIED limitée au GATE 4 : jamais de GATE 5+ automatique.
            handled.add(cid)
            decisions.append({"candidate_id": cid, "verdict": "STOP_AT_GATE_4",
                              "reasons": ["FETCH_VERIFIED_PIPELINE_LIMITED_TO_GATE_4_OWNER_REVIEW_REQUIRED"]})
            continue
        if cid is None and fetch:
            return {"action": "FETCH_CYCLE_DONE", "cost_class": "FREE", "provenance_mode": provenance_mode,
                    "certified_live": False, "decisions": decisions, "owner_authorizations_needed": owner}
        if cid is None:
            row = lambda c: {
                "id": c["id"],
                "title": c["title"],
                "territory": c.get("territory"),
                "status": c["status"],
                "reason": c.get("closed_reason"),
            }
            return {
                "action": "EXPLORE_NEW_TERRITORIES",
                "cost_class": "FREE",
                "why": "NO_OPEN_CANDIDATE",
                "do_not_restudy": [row(c) for c in registry.candidates.values() if c["status"] == "REJECTED"],
                "reserve_reopen_on_new_evidence": [row(c) for c in registry.candidates.values() if c["status"] in PARKED],
                "decisions": decisions,
                "owner_authorizations_needed": owner,
            }

        c = registry.candidates[cid]
        d = dossiers.get(cid)
        if d is None:
            return {
                "action": "BUILD_DOSSIER",
                "candidate_id": cid,
                "gate": STAGE_GATE.get(c["stage"], "GATE_4"),
                "cost_class": "FREE",
                "why": "NO_EVIDENCE_DOSSIER_FOR_OPEN_CANDIDATE",
                "decisions": decisions,
                "owner_authorizations_needed": owner,
            }

        if c["stage"] > 5:
            req_gates = [STAGE_GATE[s] for s in range(5, c["stage"]) if s in STAGE_GATE]
            passed_gates = {h.get("gate") for h in (c.get("history") or []) if h.get("verdict") == "ADVANCE"}
            if not set(req_gates).issubset(passed_gates):
                raise ValueError("TAMPERED_REGISTRY_STAGE_WITHOUT_PRIOR_GATE_ADVANCE")
        if d.get("stage", c["stage"]) > c["stage"]:
            raise ValueError("DOSSIER_CANNOT_ADVANCE_REGISTRY_STAGE")
        d = {**d, "stage": c["stage"]}
        fp = fingerprint(d)
        ok, why = registry.should_study(cid, fp)
        if not ok:
            decisions.append({"candidate_id": cid, "verdict": "SKIP", "reasons": [why]})
            continue

        work = {
            **d,
            "_mission_id": registry.data.get("mission_id") or "",
            "_granted_scopes": set(c.get("granted_scopes") or []),
            "_declined_scopes": set(c.get("declined_scopes") or []),
            "_ledger": ledger,
            "_agent02_audits": agent02_audits or {},
            "_trusted_demo_fixture_legacy_filter": bool(_fixture_demo_legacy_filter and provenance_mode == "REPLAY"),
        }
        stage = d["stage"]
        if stage == 5:
            from .scoring import score_with_evidence
            from .ledger import gate_2b
            if ledger is None:
                work["gate2b"] = {"status": "FAIL", "reason": "LEDGER_MISSING"}
                work["score"] = {"status": "REJECTED_UNSUPPORTED_SCORE", "score": None}
            elif fetch:
                from .autonomous_research import recurrence_status
                from .scoring import score_with_evidence_v2
                from .demand import load_thresholds
                # Recherche d'opportunités : aucune donnée économique ; barème /40 non financier ; récurrence d'achat
                # recalculée depuis le registre ; les red flags du dossier ne sont jamais crus sans références.
                work["research_mode"] = True
                work.pop("economic_screen", None)
                from .demand import gate_2b_convergence
                th = load_thresholds()
                # GATE 2B (méthode active) : qualité, indépendance et convergence des preuves multi-sources.
                work["gate2b"] = gate_2b_convergence(list(ledger.values()), cid, thresholds=th)
                dem = d.get("demand") or {}
                work["score"] = score_with_evidence_v2((d.get("score") or {}).get("axes") or {}, ref_index=ledger,
                                                       candidate_id=cid, thresholds=th,
                                                       persona_mode=d.get("persona_mode") or "DISCOVERED",
                                                       level=dem.get("level", "HYPOTHESIS_DETECTED"),
                                                       both_paths=all((dem.get("evidence_paths") or {}).values()) if dem else False)
                work["recurrence"] = recurrence_status(list(ledger.values()), cid, d.get("recurrence_hypothesis"))
                work["red_flags"] = [f for f in d.get("red_flags") or []
                                     if _refs(f.get("refs")) and _refs(f.get("refs")) <= set(ledger)]
            else:
                work["gate2b"] = gate_2b(list(ledger.values()), cid, target_market=target_market, allow_replay=True)
                work["score"] = score_with_evidence((d.get("score") or {}).get("axes") or {}, known_refs=set(ledger))
        if stage not in DECIDERS:
            return {
                "action": "OPERATIONAL_VALIDATION",
                "candidate_id": cid,
                "decisions": decisions,
                "owner_authorizations_needed": owner,
            }

        gate = STAGE_GATE[stage]
        registry.count_attempts(cid, d, fp, count_on_fp_change=not fetch)
        exhausted = c["rounds"].get(gate, 0) >= MAX_ROUNDS_PER_GATE
        dec = DECIDERS[stage](work, c["attempts"], rounds_exhausted=exhausted)
        dec["candidate_id"] = cid
        dec["provenance_mode"] = provenance_mode
        registry.record(cid, dec, fp)
        decisions.append(dec)

        if fetch:
            dec.update(evidence_tier="FETCH_VERIFIED", certified_live=False,
                       gate2b=work.get("gate2b"), score_detail=work.get("score"),
                       recurrence=work.get("recurrence"))
            handled.add(cid)
            if dec["verdict"] in ("ADVANCE", "RESEARCH", "REJECT", "PARK", "PARK_PROMISING",
                                  "PARK_BELOW_SHORTLIST", "PARK_NO_REASON_FOUND", "REQUEST_AUTHORIZATION"):
                if dec["verdict"] == "REQUEST_AUTHORIZATION":
                    owner.extend({"candidate_id": cid, **it} for it in dec["authorization"])
                continue
        if dec["verdict"] == "ADVANCE":
            dossiers[cid] = {**d, "stage": stage + 1}
            continue
        if dec["verdict"] == "REQUEST_AUTHORIZATION":
            owner.extend({"candidate_id": cid, **it} for it in dec["authorization"])
            continue
        if dec["verdict"] in ("REJECT", "PARK", "PARK_PROMISING", "PARK_BELOW_SHORTLIST", "PARK_NO_REASON_FOUND"):
            continue
        if dec["verdict"] == "PROPOSE_FOR_COMMERCIAL_SELECTION":
            ops = [it for it in dec["operational_validation"] if it.get("cost_class") in OPERATIONAL_COSTS]
            existing_keys = {r.get("scope_key") for r in registry.data["authorization_requests"]}
            for it in ops:
                owner.append({"candidate_id": cid, **it})
                if it["scope_key"] not in existing_keys:
                    registry.data["authorization_requests"].append({
                        "candidate_id": cid,
                        "gate": "GATE_8_9",
                        **it,
                        "status": "PENDING_OWNER",
                    })
        return {
            "action": dec["verdict"],
            "candidate_id": cid,
            "provenance_mode": provenance_mode,
            "decision": dec,
            "decisions": decisions,
            "owner_authorizations_needed": owner,
        }
    raise RuntimeError("max_decisions reached: possible loop in dossiers")
