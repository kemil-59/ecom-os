"""Stage 1 → Stage 2 : profondeur JTBD basée sur le registre FETCH_VERIFIED.

Ne réalise aucune collecte externe. Les métriques décrivent seulement l'échantillon accessible,
non la taille du marché. Aucun chiffre financier ni signal de recherche non qualifié n'est
converti en preuve client. Les propositions LLM_Provider sont non fiables et revalidées ici.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from . import ledger

PAIN_KINDS = frozenset(("FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND"))
SOLUTION_KINDS = frozenset(("SATISFACTION", "REPEAT_PURCHASE"))
# Un signal multi-source exploratoire n'est PAS une validation de masse / un GATE 2B PASS.
STAGE1_MIN_ACCOUNTS = 3
STAGE1_MIN_FAMILIES = 2
STAGE2_MIN_ACCOUNTS = 2
STAGE2_MIN_FAMILIES = 2
MICRO_FIELDS = ("who", "moment", "trigger", "specific_friction", "attempted_solution", "unresolved_reason")


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _family(r):
    return ledger.FAMILY.get(r.get("source_type"), "UNKNOWN")


def _eligible(r, market):
    return (r.get("provenance_mode") == ledger.FETCH_VERIFIED
            and r.get("market_geo") == market
            and r.get("geo_basis") in ledger.GEO_BASES_ACCEPTED
            and r.get("spontane") is True
            and bool(r.get("author_key"))
            and _family(r) not in ("UNKNOWN", "SEARCH", "EDITORIAL"))


def analyze(rows: list[dict], proposal: dict | None, *, market: str = "FR", v2: dict | None = None) -> dict:
    """Mesure des signaux Stage 1 puis vérifie chaque micro-problème proposé au Stage 2.

    Une annotation LLM_Provider ne prouve jamais une cause, une identité ou un réachat.
    Les statuts CROSS_SOURCE_* ne sont jamais des gates formels.
    """
    if not isinstance(rows, list):
        raise ValueError("ROWS_MUST_BE_LIST")
    if proposal is None:
        proposal = {"micro_problems": []}
    if not isinstance(proposal, dict) or not isinstance(proposal.get("micro_problems", []), list):
        raise ValueError("MICRO_PROBLEMS_MUST_BE_LIST")
    idx = {r["observation_id"]: r for r in rows if r.get("observation_id")}
    by_territory = defaultdict(list)
    for r in rows:
        if r.get("territory_id"):
            by_territory[r["territory_id"]].append(r)
    stage1 = {}
    for tid, observed in sorted(by_territory.items()):
        pain = [r for r in observed if _eligible(r, market) and r.get("kind") in PAIN_KINDS]
        accounts = {r["author_key"] for r in pain}
        families = {_family(r) for r in pain}
        status = ("CROSS_SOURCE_SIGNAL_NOT_POPULATION_VALIDATION"
                  if len(accounts) >= STAGE1_MIN_ACCOUNTS and len(families) >= STAGE1_MIN_FAMILIES
                  else "INSUFFICIENT_INDEPENDENT_BREADTH")
        sat = [r for r in observed if _eligible(r, market) and r.get("kind") == "SATISFACTION"]
        rep = [r for r in observed if _eligible(r, market) and r.get("kind") == "REPEAT_PURCHASE"]
        stage1[tid] = {
            "status": status,
            "pain_refs": sorted({r["observation_id"] for r in pain}),
            "distinct_pain_accounts": len(accounts), "pain_families": sorted(families),
            "satisfaction_refs": sorted({r["observation_id"] for r in sat}),
            "repeat_purchase_refs": sorted({r["observation_id"] for r in rep}),
            "index_counts_are_not_population_estimates": True,
            "missing": ([] if status.startswith("CROSS_SOURCE") else [
                f"Au moins {STAGE1_MIN_ACCOUNTS} comptes indépendants avec plaintes spontanées qualifiées",
                f"Au moins {STAGE1_MIN_FAMILIES} familles de sources indépendantes"]),
        }
    stage2 = []
    seen = set()
    for m in proposal.get("micro_problems", []):
        if not isinstance(m, dict):
            raise ValueError("MICRO_PROBLEM_NOT_OBJECT")
        mid = str(m.get("id") or "").strip()
        tid = str(m.get("territory_id") or "").strip()
        if not mid or mid in seen:
            raise ValueError("MICRO_PROBLEM_ID_MISSING_OR_DUPLICATE")
        seen.add(mid)
        missing_fields = [key for key in MICRO_FIELDS if not str(m.get(key) or "").strip()]
        # Refs sont des chaînes provenant du registre, jamais des URLs ou citations réinventées.
        refs = m.get("pain_refs", [])
        counter_refs = m.get("counter_refs", [])
        if not isinstance(refs, list) or not isinstance(counter_refs, list):
            raise ValueError("MICRO_REFS_MUST_BE_LISTS")
        invalid = sorted({str(ref) for ref in refs + counter_refs if not isinstance(ref, str) or ref not in idx})
        wrong_territory = sorted({ref for ref in refs + counter_refs if isinstance(ref, str) and ref in idx
                                  and idx[ref].get("territory_id") != tid})
        if invalid or wrong_territory:
            stage2.append({"id": mid, "territory_id": tid, "status": "INVALID_EVIDENCE_REFS",
                           "invalid_refs": invalid, "wrong_territory_refs": wrong_territory,
                           "missing_fields": missing_fields})
            continue
        eligible_pain = [idx[ref] for ref in dict.fromkeys(refs)
                         if idx[ref].get("kind") in PAIN_KINDS and _eligible(idx[ref], market)]
        # Une satisfaction / un avis sollicité ne peut constituer la preuve du problème.
        accepted_counter = [idx[ref] for ref in dict.fromkeys(counter_refs)
                            if idx[ref].get("kind") == "SATISFACTION" and _eligible(idx[ref], market)]
        acc = {r["author_key"] for r in eligible_pain}
        fam = {_family(r) for r in eligible_pain}
        broad = stage1.get(tid, {}).get("status", "")
        if missing_fields:
            status = "INCOMPLETE_MICRO_CONTEXT"
        elif not broad.startswith("CROSS_SOURCE"):
            status = "BLOCKED_STAGE1_BREADTH"
        elif len(acc) >= STAGE2_MIN_ACCOUNTS and len(fam) >= STAGE2_MIN_FAMILIES:
            status = "CROSS_SOURCE_MICRO_SIGNAL_NOT_GATE2B"
        else:
            status = "MICRO_HYPOTHESIS_NEEDS_CORROBORATION"
        stage2.append({"id": mid, "territory_id": tid, "status": status,
                       "hypothesis": {key: m.get(key) for key in MICRO_FIELDS},
                       "qualified_pain_refs": sorted({r["observation_id"] for r in eligible_pain}),
                       "qualified_accounts": len(acc), "source_families": sorted(fam),
                       "counterevidence_refs": sorted({r["observation_id"] for r in accepted_counter}),
                       "unqualified_refs": sorted(set(refs) - {r["observation_id"] for r in eligible_pain}),
                       "missing_fields": missing_fields})
    out = {"schema": "ECOM_OS_DEPTH_V1", "provenance_mode": ledger.FETCH_VERIFIED,
           "population_prevalence_measured": False, "commercially_approved": False,
           "stage1": stage1, "stage2": stage2,
           "ready_for_owner_review": [m["id"] for m in stage2 if m["status"] == "CROSS_SOURCE_MICRO_SIGNAL_NOT_GATE2B"],
           "marketpulse": "BLOCKED_PENDING_EXPLICIT_OWNER_CHOICE_NO_AUTOMATIC_CONNECTOR"}
    if v2 is not None:
        _apply_v3(out, rows, by_territory, idx, market, v2, proposal)
    return out


VERDICTS = ("HYPOTHESIS_DETECTED", "PROBLEM_CORROBORATED", "BROAD_DISTRIBUTED_SIGNAL", "SIGNIFICANT_MARKET_DOCUMENTED",
            "COMMERCIAL_OPPORTUNITY_TO_DEEPEN", "INSUFFICIENT_EVIDENCE", "REJECTED")
A02_REJECTING = {"ALREADY_WELL_SOLVED", "INVALIDATED_BY_COUNTER_RESEARCH", "NO_CREDIBLE_ADVANTAGE_FOUND"}


def _nrm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "").casefold())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).replace("'", " ").replace("’", " ").split())


def matches_signature(text: str, terms: list[str]) -> bool:
    """Le témoignage exprime-t-il CETTE micro-friction ? Un terme de signature (normalisé) doit figurer dans la citation."""
    t = " " + _nrm(text) + " "
    return any((" " + _nrm(x) + " ") in t or (len(_nrm(x)) >= 6 and _nrm(x) in t) for x in terms if _nrm(x))


def _apply_v3(out: dict, rows: list[dict], by_territory: dict, idx: dict, market: str, v2: dict, proposal: dict) -> None:
    """Méthode active : niveaux de preuve par convergence multi-sources, segments découverts, verdicts.
    Aucun signal de quelques personnes ne devient une validation ; aucune piste n'est promue sans contre-enquête."""
    from . import competitor_intel, demand, persona
    th, mode, studies = v2["thresholds"], v2["persona_mode"], v2.get("studies") or []
    inv = v2.setdefault("investigation", {})
    explored = v2.get("explored_domains") or set()
    research = v2.get("competitive_research") or {}
    volume = [r for r in rows if r.get("kind") == "INDEX_COUNT"]
    out["schema"] = "ECOM_OS_DEPTH_V3"
    out["persona_mode"] = mode
    out.pop("ready_for_owner_review", None)          # ancien signal à 2 comptes retiré du circuit de décision
    for tid, t in out["stage1"].items():
        terr = by_territory[tid]
        t["demand"] = demand.assess([r for r in terr if r.get("kind") in PAIN_KINDS], sat_rows=terr,
                                    volume_rows=[r for r in volume if r.get("territory_id") == tid],
                                    studies=studies, scope_id=tid, thresholds=th, market=market, explored_domains=explored)
        t["segments"] = persona.segment_profile([r for r in terr if r.get("kind") in PAIN_KINDS], th, studies, tid)
    micros = {m.get("id"): m for m in (proposal or {}).get("micro_problems", []) if isinstance(m, dict)}
    usage: dict[str, list[str]] = {}
    for m in out["stage2"]:
        src = micros.get(m["id"]) or {}
        m["signature_terms"] = [x for x in src.get("signature_terms") or [] if isinstance(x, str) and x.strip()]
        listed = [r for r in (src.get("pain_refs") or []) if r in idx]
        tagged = [r["observation_id"] for r in rows if r.get("micro_id") == m["id"]]
        cand = list(dict.fromkeys(listed + tagged))
        if m["signature_terms"]:
            matched = [r for r in cand if idx[r].get("kind") in PAIN_KINDS and matches_signature(idx[r].get("phrase_exacte"), m["signature_terms"])]
        else:
            matched = []
        m["refs_signature_mismatch"] = sorted(set(r for r in cand if idx[r].get("kind") in PAIN_KINDS) - set(matched))
        m["matched_pain_refs"] = matched
        for r in matched:
            usage.setdefault(r, []).append(m["id"])
    for m in out["stage2"]:
        src = micros.get(m["id"]) or {}
        pain = [idx[r] for r in m["matched_pain_refs"]]
        m["shared_refs_with_other_micros"] = sorted({o for r in m["matched_pain_refs"] for o in usage[r] if o != m["id"]})
        sat = [idx[r] for r in (src.get("counter_refs") or []) if r in idx]
        m["demand"] = demand.assess(pain, sat_rows=sat, studies=studies, scope_id=m["id"], thresholds=th, market=market,
                                    explored_domains=explored)
        m["segments"] = persona.segment_profile(pain, th, studies, m["id"])
        seg_stat = any(s.get("scope") == "SEGMENT" and s.get("verification") == "VERIFIED" and s.get("country") == "FR"
                       and str(s.get("engine_receipt_id") or "").startswith("STU-") and m["id"] in (s.get("applies_to") or [])
                       for s in studies)
        m["persona_fit"] = persona.fit_status([r for r in pain if demand.eligible(r, market)], th, mode, seg_stat)
        key = src.get("candidate_id") or m["id"]
        cand_rows = [{**r, "candidate_ids": list(set((r.get("candidate_ids") or []) + [key]))} for r in pain + sat]
        price_rows = [r for r in rows if r.get("kind") in ("PRICE_OBSERVED", "OFFER_OBSERVED")
                      and (key in (r.get("candidate_ids") or []) or r.get("micro_id") == m["id"])]
        cand_rows += [{**r, "candidate_ids": list(set((r.get("candidate_ids") or []) + [key]))} for r in price_rows]
        m["agent02"] = competitor_intel.agent02_v2_assessment(cand_rows, {"candidate_id": key}, research.get(key), th)
        # Suivi d'enquête : une entrée par lot de nouvelles contributions ; saturation = rendement marginal faible.
        lb = m["demand"]["indicators"]["4_distinct_authors_interval"][0]
        nd = len(m["demand"]["indicators"]["6_distribution"]["domains"])
        hist = inv.setdefault(m["id"], [])
        if not hist or m["demand"]["indicators"]["3_contributions"] > hist[-1]["contributions"]:
            hist.append({"contributions": m["demand"]["indicators"]["3_contributions"], "authors_lb": lb, "domains": nd})
        k = th["investigation"]["saturation_batches"]
        tail = hist[-(k + 1):]
        saturated = len(tail) == k + 1 and all(
            (b["authors_lb"] - a["authors_lb"]) <= th["investigation"]["saturation_new_author_share"] * max(a["authors_lb"], 1)
            and b["domains"] <= a["domains"] for a, b in zip(tail, tail[1:]))
        m["investigation"] = {"batches": len(hist), "saturated": saturated, "history_tail": tail}
        level = m["demand"]["level"]
        a02 = m["agent02"]["verdict"]
        pf = m["persona_fit"]["status"]
        reasons = []
        if m["status"] in ("INVALID_EVIDENCE_REFS", "INCOMPLETE_MICRO_CONTEXT"):
            verdict = "HYPOTHESIS_DETECTED"
            reasons.append(m["status"])
        elif not m["signature_terms"]:
            verdict = "HYPOTHESIS_DETECTED"
            reasons.append("MICRO_SIGNATURE_MISSING")
        elif mode == "PROFILE" and pf == "PERSONA_OUT_OF_TARGET":
            verdict, reasons = "REJECTED", ["PERSONA_OUT_OF_TARGET"]
        elif a02 in A02_REJECTING:
            verdict, reasons = "REJECTED", [f"AGENT02_{a02}"]
        elif level == "SIGNIFICANT_MARKET_DOCUMENTED" and a02 == "DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN" \
                and (mode != "PROFILE" or pf == "PERSONA_FIT_DEMONSTRATED"):
            verdict = "COMMERCIAL_OPPORTUNITY_TO_DEEPEN"
        elif saturated and level != "SIGNIFICANT_MARKET_DOCUMENTED":
            verdict, reasons = "INSUFFICIENT_EVIDENCE", [f"INVESTIGATION_SATURATED_AT_{level}"]
        else:
            verdict = level
            if level == "SIGNIFICANT_MARKET_DOCUMENTED":
                reasons.append(f"AGENT02_{a02}")
            if mode == "PROFILE" and pf != "PERSONA_FIT_DEMONSTRATED":
                reasons.append(pf)
        m["verdict"], m["verdict_reasons"] = verdict, reasons
    cp = {v: [m["id"] for m in out["stage2"] if m.get("verdict") == v] for v in VERDICTS}
    out["owner_checkpoint"] = {"opportunities": cp["COMMERCIAL_OPPORTUNITY_TO_DEEPEN"], "by_verdict": cp,
                               "rule": "Seules les COMMERCIAL_OPPORTUNITY_TO_DEEPEN peuvent être soumises au choix MarketPulse ; "
                                       "tout le reste est présenté avec son niveau de preuve réel."}
    out["marketpulse"] = ("BLOCKED_PENDING_EXPLICIT_OWNER_CHOICE_NO_AUTOMATIC_CONNECTOR" if cp["COMMERCIAL_OPPORTUNITY_TO_DEEPEN"]
                         else "BLOCKED_NO_OPPORTUNITY_MEETS_EVIDENCE_LEVEL")


def work_orders(result: dict) -> list[dict]:
    """Recherches gratuites utiles, sans déclencher de collecte ni de MarketPulse. Avec la méthode active (V3) :
    ordres ADAPTATIFS déduits des manques mesurés (diversité, concentration, persistance, contre-signaux, chiffres)."""
    orders = []
    for m in result.get("stage2", []):
        if "verdict" not in m or m["verdict"] in ("REJECTED", "COMMERCIAL_OPPORTUNITY_TO_DEEPEN", "INSUFFICIENT_EVIDENCE"):
            continue
        ind = m["demand"]["indicators"]
        todo = []
        if not m.get("signature_terms"):
            todo.append("définir signature_terms (mots exacts des consommateurs) pour rapprocher les témoignages")
        if len(ind["6_distribution"]["domains"]) < 10:
            todo.append("explorer d'autres communautés et plateformes indépendantes (synonymes, expressions réelles)")
        if len([k for k in ind["6_distribution"]["source_kinds"] if k != "UNKNOWN"]) < 3:
            todo.append("diversifier les types de sources (forums, avis clients, commentaires, Q&R, blogs)")
        if ind["7_concentration"]["max_domain_share"] > 0.35:
            todo.append("réduire la dépendance à un seul site")
        if len(ind["8_recency_persistence"]["years"]) < 2:
            todo.append("chercher des discussions historiques et récentes (persistance)")
        if not ind["10_counter_signals"]["satisfied_authors"]:
            todo.append("chercher volontairement des consommateurs satisfaits des solutions existantes")
        if not m["demand"]["applicable_statistics"]:
            todo.append("chercher une statistique française vérifiable applicable au segment ou au micro-problème")
        if m["agent02"]["verdict"] == "INSUFFICIENT_COUNTER_RESEARCH":
            todo.append("Agent 02 : " + ", ".join(m["agent02"]["counter_research_issues"]))
        if m.get("refs_signature_mismatch"):
            todo.append(f"{len(m['refs_signature_mismatch'])} références ne correspondent pas à la signature : les réaffecter, ne pas les fusionner")
        orders.append({"task": "ADAPTIVE_DEEPEN", "micro_id": m["id"], "territory_id": m["territory_id"],
                       "current_level": m["verdict"], "authors_interval": ind["4_distinct_authors_interval"],
                       "domains": len(ind["6_distribution"]["domains"]), "next_actions": todo})
    if any("verdict" in m for m in result.get("stage2", [])):
        for tid, broad in result.get("stage1", {}).items():
            if not any(m["territory_id"] == tid for m in result.get("stage2", [])):
                orders.append({"task": "STAGE2_MICRO_DISCOVERY", "territory_id": tid,
                               "why": "Isoler des micro-frictions précises avec signature, puis élargir à de nombreuses sources"})
        return orders
    for tid, broad in result.get("stage1", {}).items():
        if broad["status"] != "CROSS_SOURCE_SIGNAL_NOT_POPULATION_VALIDATION":
            orders.append({"task": "STAGE1_CROSS_SOURCE_DISCOVERY", "territory_id": tid,
                           "why": "Diversifier les témoignages spontanés et comptes qualifiés ; ne pas estimer la population"})
        elif not any(m["territory_id"] == tid for m in result.get("stage2", [])):
            orders.append({"task": "STAGE2_MICRO_DISCOVERY", "territory_id": tid,
                           "why": "Identifier des micro-contextes réels avec références et contre-exemples"})
    for micro in result.get("stage2", []):
        if micro["status"] in ("MICRO_HYPOTHESIS_NEEDS_CORROBORATION", "INCOMPLETE_MICRO_CONTEXT"):
            orders.append({"task": "STAGE2_CORROBORATE_MICRO", "territory_id": micro["territory_id"],
                           "micro_id": micro["id"], "why": micro["status"]})
    return orders


def save(mdir: Path, result: dict) -> Path:
    target = Path(mdir) / "AGENT_01" / "STAGE12_DEPTH_REPORT.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
