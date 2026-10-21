"""Orchestration autonome Agent 01 (mode FETCH_VERIFIED), limitée au GATE 4 — recherche d'opportunités,
sans aucune donnée économique (prix, fournisseurs, coûts, marges, CAC, LTV : hors périmètre du moteur).

Répartition des rôles :
- LLM_Provider (non fiable) dépose des propositions dans RESEARCH_INBOX/proposals/ : territoires, captures
  (URL + citation + pseudo + classification proposée), JTBD (avec hypothèse de récurrence d'achat),
  évaluations /40 non financières.
- Le moteur vérifie chaque capture par téléchargement, enregistre les preuves, calcule le GATE 2B, classe les
  JTBD par preuves, enregistre les candidats, construit les dossiers, exécute le pilote, compte les tentatives
  (fin sans boucle infinie) et produit le rapport, la shortlist exploratoire et les ordres de travail gratuits.
Aucune proposition ne franchit un gate sans preuve vérifiée par le moteur.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import autopilot, competitor_intel, demand, discovery, ledger, persona, product_research_pipeline as prp, research_depth, web_capture

MODE = ledger.FETCH_VERIFIED
NON_EVAL_WORKAROUND = "NON_EVALUABLE: effort de contournement non documenté par une observation"
# Cycles sans aucune recherche (ni capture ni découverte) avant l'arrêt propre IDLE_AWAITING_NEW_RESEARCH.
# L'inactivité n'est jamais une tentative et ne met jamais un candidat en réserve.
IDLE_CYCLES_BEFORE_STOP = 2
# Observations qui ne constituent pas une preuve pour le candidat (contexte d'analyse).
NON_EVIDENCE_KINDS = {"CONTEXT"}
RECURRENCE_HYPOTHESES = {"CONSUMABLE", "REFILL", "REPLACEMENT_WEAR", "SUBSCRIPTION"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _read(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _paths(mdir: Path) -> dict:
    a1, inbox = mdir / "AGENT_01", mdir / "RESEARCH_INBOX"
    return {"ledger": a1 / "problem_ledger.jsonl", "registry": a1 / "candidate_registry.json",
            "dossiers": a1 / "dossiers.json", "digest": a1 / "dossiers.digest", "jtbd": a1 / "jtbd_ranked.json",
            "territories": a1 / "territory_map.json", "proposals": inbox / "proposals",
            "processed": inbox / "proposals" / "_processed.json", "failures": inbox / "failures.jsonl",
            "report": a1 / "research_report.json", "report_md": a1 / "RESEARCH_REPORT.md",
            "orders": inbox / "WORK_ORDERS.json", "state": a1 / "orchestrator_state.json"}


def dossiers_digest(dossiers: dict) -> str:
    return hashlib.sha256(json.dumps(dossiers, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def promote_captures(mdir: Path, rows: list[dict], fetcher=web_capture.http_fetch, profile: dict | None = None) -> dict:
    """Vérifie chaque capture proposée puis l'inscrit au registre FETCH_VERIFIED et la rattache au JTBD proposé.
    Tout site public accessible est vérifiable. Une capture dont la page est inaccessible ou ne contient pas la citation
    n'est pas perdue : elle rejoint le registre DISCOVERY (DISCOVERED_UNVERIFIED), sans jamais devenir une preuve."""
    P = _paths(mdir)
    added, failed, cache, parked = [], [], {}, []
    for c in rows:
        count = bool(c.get("count_capture"))
        rc = web_capture.verify(mdir, url=c.get("url", ""), quote=c.get("quote", ""), author=c.get("author"),
                                location_claim=c.get("location_claim"), count_capture=count,
                                fetcher=fetcher, robots_cache=cache,
                                persona_claim=c.get("persona_claim"), persona_profile=profile,
                                capture_kind=c.get("kind") if c.get("kind") in ("PRICE_OBSERVED", "OFFER_OBSERVED") else None,
                                source_kind=c.get("source_kind"))
        if rc["status"] != "QUOTE_VERIFIED":
            failed.append({"url": c.get("url"), "jtbd": c.get("jtbd"), "status": rc["status"], "at": _now()})
            if rc["status"].startswith("BLOCKED") or rc["status"] in ("QUOTE_NOT_FOUND", "NON_TEXT_CONTENT"):
                parked.append({"url": c.get("url"), "territory_id": c.get("territory_id"), "quote": c.get("quote"),
                               "signal": c.get("probleme"), "family_hint": c.get("family_hint"),
                               "access": rc["status"]})
            continue
        row = {"territory_id": c.get("territory_id"), "probleme": c.get("probleme") or ("Compteur observé" if count else None),
               "phrase_exacte": rc["quote"], "source_type": rc["source_type"], "source_url": c["url"],
               "author_key": rc["author_key"],
               "kind": "INDEX_COUNT" if count else rc.get("kind_hint") if rc.get("kind_hint") in ("PRICE_OBSERVED", "OFFER_OBSERVED") else c.get("kind"),
               "spontane": (not rc["solicited"]) and not count and rc.get("kind_hint") not in ("PRICE_OBSERVED", "OFFER_OBSERVED"),
               "market_geo": rc["market_geo"], "geo_basis": rc["geo_basis"], "receipt_id": rc["receipt_id"],
               "source_date": rc.get("testimony_date"), "provenance_mode": MODE, "classification_source": "LLM_PROPOSAL",
               **{k: c[k] for k in ("dimension", "incumbent", "satisfied_dimension") if c.get(k)},
               **({"persona_basis": rc["persona"]["status"], "persona_age": rc["persona"].get("age")} if rc.get("persona") else {}),
               # Caractéristiques DÉCLARÉES (auto-déclaration dans le texte vérifié ou persona_claim vérifié) et PROBABLES
               # (indices de contexte, avec confiance) — jamais déduites d'un pseudo, jamais une preuve individuelle.
               **_attributes(rc),
               **{k: rc[k] for k in ("author_site_key", "registrable_domain", "binding", "evidence_confidence", "page_lang",
                                     "amount", "currency", "contribution_nature", "source_kind_proposed") if rc.get(k) is not None},
               **{k: c[k] for k in ("micro_id", "context") if c.get(k)},
               **({"count": rc["count"]} if rc.get("count") is not None else {}),
               **({"syndicated_from": rc["syndicated_from"]} if rc.get("syndicated_from") else {})}
        try:
            res = ledger.append(P["ledger"], [row], mission_id=mdir.name, provenance_mode=MODE)
        except ValueError as e:
            failed.append({"url": c.get("url"), "jtbd": c.get("jtbd"), "status": f"LEDGER_REJECTED:{e}", "at": _now()})
            continue
        oid = (res["ids"] or [None])[0]
        if oid is None:  # doublon : retrouver l'observation existante
            h = ledger._norm_text_hash(rc["quote"])
            oid = next((r["observation_id"] for r in ledger.load(P["ledger"])
                        if r.get("source_url") == c["url"] and r.get("msg_hash") == h), None)
        if oid and c.get("jtbd"):
            ledger.add_links(P["ledger"], [{"observation_id": oid, "candidate_id": c["jtbd"], "reason": "LLM_PROPOSAL"}])
        added.append(oid)
    if parked:
        discovery.record(mdir, [x for x in parked if str(x.get("url") or "").startswith("https://")], "CAPTURE_NOT_CERTIFIABLE")
    if failed:
        with P["failures"].open("a", encoding="utf-8") as f:
            for x in failed:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return {"verified_and_recorded": len([a for a in added if a]), "failed": failed}


def _evidence(rows: list[dict], jid: str, market: str) -> dict:
    g = demand.gate_2b_convergence(rows, jid)          # logique GATE 2B unique (convergence multi-sources)
    linked = [r for r in rows if jid in (r.get("candidate_ids") or [])]
    return {"refs": [r["observation_id"] for r in linked], "gate2b": g,
            "testimonies": g["distinct_testimonies"], "accounts": g["independent_accounts"],
            "families": len(g["testimony_families"])}


def register_jobs(mdir: Path, proposal: dict, market: str) -> dict:
    """Classe les JTBD proposés selon les preuves du moteur (jamais selon l'avis de LLM_Provider) puis applique le GATE 3."""
    P = _paths(mdir)
    rows = ledger.load_merged(P["ledger"])
    idx = {r["observation_id"]: r for r in rows}
    scored, no_evidence = [], []
    for j in proposal.get("jobs", []):
        ev = _evidence(rows, j.get("id", ""), market)
        if not ev["refs"]:
            no_evidence.append(j.get("id"))
            continue
        wrefs = [r for r in j.get("workaround_refs") or []
                 if idx.get(r, {}).get("kind") in ("SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND", "REPEAT_PURCHASE")]
        work = j.get("workaround_cost") if wrefs and str(j.get("workaround_cost") or "").strip() else NON_EVAL_WORKAROUND
        scored.append(({**{k: v for k, v in j.items() if k != "workaround_refs"}, "voc_refs": ev["refs"],
                        "workaround_cost": work, "workaround_refs": wrefs,
                        "evidence_summary": {k: ev[k] for k in ("testimonies", "accounts", "families")} |
                        {"gate2b_validation": ev["gate2b"].get("validation")}},
                       (ev["gate2b"]["status"] == "PASS", ev["testimonies"], ev["accounts"], ev["families"])))
    scored.sort(key=lambda x: x[1], reverse=True)
    jobs = [s[0] for s in scored[:8]]
    not_ret = [{"id": s[0]["id"], "title": s[0].get("title"), "closed_reason": "RANG_SUPERIEUR_A_8_SELON_PREUVES",
                "closed_refs": s[0]["voc_refs"]} for s in scored[8:]]
    for x in proposal.get("not_retained", []):
        refs = _evidence(rows, x.get("id", ""), market)["refs"]
        if refs and x.get("closed_reason"):
            not_ret.append({**x, "closed_refs": refs})
    out = prp.register_jtbd_shortlist(mdir, jobs, not_ret, provenance_mode=MODE)
    out["jobs_without_evidence_not_registered"] = no_evidence
    if out["status"] != f"PASS_{MODE}_STRUCTURE_ONLY":
        _write(mdir / "AGENT_01" / "jtbd_exploratory.json", {**out, "validated": False})
    return out


def _attributes(rc: dict) -> dict:
    if rc.get("kind_hint") or rc.get("binding") == "SITE_LEVEL":
        return {}
    declared = dict(persona.extract_attributes(" ".join(str(rc.get("quote") or "").replace("’", "'").casefold().split())))
    declared.update((rc.get("persona") or {}).get("attributes") or {})
    inferred = {k: v for k, v in persona.infer_attributes(rc.get("quote") or "").items() if k not in declared}
    return {**({"declared_attributes": declared} if declared else {}), **({"inferred_attributes": inferred} if inferred else {})}


def _studies(mdir: Path, fetcher=web_capture.http_fetch) -> list[dict]:
    """Études proposées (proposals/studies.json) VÉRIFIÉES PAR LE MOTEUR sur la page HTML (web_capture.verify_study).
    Le statut, le pays et le lien au micro-problème viennent uniquement du reçu moteur ; tout champ « verification »
    proposé par LLM_Provider est ignoré. Reçus réutilisés tant que la proposition ne change pas (pas de téléchargement à chaque cycle)."""
    P = _paths(mdir)
    doc = _read(P["proposals"] / "studies.json", {"studies": []})
    micros = {m.get("id"): m for m in _read(P["proposals"] / "micro_problems.json", {"micro_problems": []}).get("micro_problems", [])
              if isinstance(m, dict)}
    cache_p = mdir / "RESEARCH_INBOX" / "study_cache.json"
    cache = _read(cache_p, {})
    out, rcache = [], {}
    for s in doc.get("studies", []) if isinstance(doc, dict) else []:
        if not (isinstance(s, dict) and s.get("id") and s.get("url") and s.get("figure_quote")):
            continue
        terms = list(s.get("link_terms") or [])
        for mid in s.get("applies_to") or []:
            terms += list((micros.get(mid) or {}).get("signature_terms") or [])
        key = hashlib.sha256(json.dumps({**{k: v for k, v in s.items() if k != "verification"}, "terms": sorted(set(terms))},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rc = cache.get(key)
        if rc is None:
            rc = web_capture.verify_study(mdir, url=s["url"], figure_quote=s["figure_quote"], date_claim=s.get("date"),
                                          country_claim=s.get("country"), population_claim=s.get("population"),
                                          link_terms=terms, method_claim=s.get("method"),
                                          denominator_claim=s.get("denominator"), fetcher=fetcher, robots_cache=rcache)
        cache[key] = rc
        out.append({"id": s["id"], "url": s["url"], "scope": s.get("scope"), "applies_to": s.get("applies_to") or [],
                    "figure_quote": s["figure_quote"], "date": s.get("date"), "population": s.get("population"),
                    "verification": rc["status"], "country": rc.get("country", "UNKNOWN_OR_NON_FR"),
                    "missing": rc.get("missing"), "engine_receipt_id": rc["receipt_id"]})
    _write(cache_p, cache)
    return out


def _candidate_v2(mdir: Path, cid: str, v2: dict) -> dict:
    """Fit persona et profondeur de la demande d'un candidat, recalculés depuis le registre (jamais depuis LLM_Provider)."""
    rows = [r for r in ledger.load_merged(_paths(mdir)["ledger"]) if cid in (r.get("candidate_ids") or [])]
    pain = [r for r in rows if r.get("kind") in research_depth.PAIN_KINDS]
    vol = [r for r in rows if r.get("kind") == "INDEX_COUNT"]
    dem = demand.assess(pain, sat_rows=rows, volume_rows=vol, studies=v2.get("studies"), scope_id=cid,
                        thresholds=v2["thresholds"], explored_domains=discovery.explored_domains(mdir))
    pf = persona.fit_status([r for r in pain if demand.eligible(r, "FR")], v2["thresholds"], v2["persona_mode"])
    return {"persona_mode": v2["persona_mode"], "persona_fit": pf, "demand": dem}


def seed_from_jtbd(mdir: Path) -> int:
    P = _paths(mdir)
    rk = _read(P["jtbd"], {})
    reg = autopilot.CandidateRegistry.load(P["registry"], mdir.name)
    rows = [{"id": j["id"], "title": j.get("title", j["id"]), "stage": 5, "territory": j.get("territory_id"),
             "origin": "GATE_3_FETCH_VERIFIED"} for j in rk.get("jobs", []) if j["id"] not in reg.candidates]
    rows += [{"id": j["id"], "title": j.get("title") or j["id"], "stage": 5, "status": "REJECTED",
              "closed_reason": j["closed_reason"], "closed_refs": j["closed_refs"], "origin": "GATE_3_NOT_RETAINED"}
             for j in rk.get("not_retained_at_step4", []) if j["id"] not in reg.candidates]
    autopilot.seed_candidates(reg, rows)
    reg.save(P["registry"])
    return len(rows)


def build_dossiers(mdir: Path, assessments: dict, auto_done: dict | None = None, v2: dict | None = None) -> dict:
    """Dossiers GATE 4 non financiers construits par le moteur : hypothèse de récurrence issue du JTBD,
    notes /40 = propositions dont chaque référence est revalidée par le pilote (sinon NON_EVALUABLE)."""
    P = _paths(mdir)
    reg = autopilot.CandidateRegistry.load(P["registry"], mdir.name)
    jobs = {j["id"]: j for j in _read(P["jtbd"], {}).get("jobs", [])}
    out = {}
    for cid, c in reg.candidates.items():
        if c["status"] not in ("ACTIVE", "BACKLOG") or c["stage"] != 5:
            continue
        a, j = assessments.get(cid) or {}, jobs.get(cid) or {}
        # Seule l'orchestration (recherche ciblée documentée sans preuve nouvelle) déclare une tentative ;
        # un `research_done` saisi dans assessments.json n'est ni une preuve ni une tentative.
        done = sorted(set((auto_done or {}).get(cid, [])))
        out[cid] = {"candidate_id": cid, "stage": 5, "research_mode": True,
                    "score": {"axes": a.get("score_axes") or {}},
                    "recurrence_hypothesis": j.get("recurrence_hypothesis"),
                    "recurrence_rationale": j.get("recurrence_rationale"),
                    "red_flags": a.get("red_flags") or [], "research_done": done, "provenance_mode": MODE}
        if v2 is not None:
            out[cid].update(_candidate_v2(mdir, cid, v2))
    _write(P["dossiers"], out)
    P["digest"].write_text(dossiers_digest(out) + "\n", encoding="utf-8")
    return out


def _work_orders(mdir: Path, step_res: dict, jt: dict | None, failures: list[dict]) -> list[dict]:
    orders = []
    if jt and not str(jt.get("status", "")).startswith("PASS_"):
        orders.append({"task": "COMPLETER_JTBD", "why": jt.get("issues"),
                       "do": "Collecter des témoignages FR (forums avec localisation, avis marchands FR) pour 5 à 8 JTBD distincts"})
    for d in step_res.get("decisions", []):
        for it in d.get("research", []) or []:
            orders.append({"task": "RECHERCHE_GRATUITE", "candidate_id": d.get("candidate_id"), "item": it.get("id"),
                           "question": it.get("question"), "gate": it.get("gate"),
                           "gate2b": (d.get("gate2b") or {}).get("validation"),
                           "families": (d.get("gate2b") or {}).get("testimony_families"),
                           "score_unsupported": (d.get("score_detail") or {}).get("unsupported_axes"),
                           "recurrence": (d.get("recurrence") or {}).get("status")})
    for f in failures:
        orders.append({"task": "SOURCE_ALTERNATIVE", "jtbd": f.get("jtbd"), "failed_url": f.get("url"), "status": f.get("status")})
    return orders


def run(mdir: Path, fetcher=web_capture.http_fetch) -> dict:
    """Un cycle complet. Idempotent ; à relancer après chaque dépôt de propositions par LLM_Provider."""
    mdir = Path(mdir)
    cfg = _read(mdir / "mission.json", {})
    if cfg.get("mode") != MODE:
        raise ValueError("MISSION_NOT_FETCH_VERIFIED")
    market = cfg.get("target_sale_market", "FR")
    ctx = persona.resolve(cfg)
    P = _paths(mdir)
    inv_path = mdir / "RESEARCH_INBOX" / "investigation_state.json"
    v2 = {"thresholds": demand.load_thresholds(), "persona_mode": ctx["mode"], "profile": ctx["profile"],
          "studies": _studies(mdir, fetcher), "investigation": _read(inv_path, {}),
          "competitive_research": _read(P["proposals"] / "competitive_research.json", {})}
    P["proposals"].mkdir(parents=True, exist_ok=True)
    processed = _read(P["processed"], {})
    state = _read(P["state"], {"idle_cycles": 0, "cycles": 0})
    state.setdefault("evidence", {})
    log = {"cycle_at": _now()}
    # Recherche réellement effectuée ce cycle = nouveau fichier de captures ou de découverte traité.
    research_activity, targeted = 0, set()

    log["persona"] = {"mode": ctx["mode"], "persona_id": ctx["persona_id"], "declared": ctx["declared"]}
    for f in sorted(P["proposals"].glob("discovery*.json")):
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if processed.get(f.name) != h:
            doc = _read(f, {})
            with (mdir / "RESEARCH_INBOX" / "discovery_log.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps({"file": f.name, "logged_at": _now(), **doc}, ensure_ascii=False) + "\n")
            if isinstance(doc, dict) and isinstance(doc.get("sources"), list):
                log.setdefault("discovery_recorded", []).append(
                    {"file": f.name, **discovery.record(mdir, [x for x in doc["sources"] if isinstance(x, dict)], f.name)})
            processed[f.name] = h
            research_activity += 1

    # GATE 2A : la cible est fixée AVANT les territoires ; les territoires viennent de la DISCOVERY (tout site).
    terr = P["proposals"] / "territories.json"
    if terr.exists() and not P["territories"].exists():
        tlist = _read(terr, [])
        issues = []
        if isinstance(tlist, list):
            # Méthode unique : territoires issus de la DISCOVERY (tout site) ; secteurs variés en recherche générale,
            # lien persona seulement si le propriétaire impose un profil.
            issues = discovery.check_territory_discovery(tlist, mdir, require_sectors=ctx["mode"] != "PROFILE")
            if ctx["mode"] == "PROFILE":
                issues += persona.check_territories(tlist, ctx["profile"])
        if issues:
            log["gate_2a"] = "GATE_2A_INCOMPLETE"
            _write(mdir / "AGENT_01" / "territory_issues.json", {"status": "GATE_2A_INCOMPLETE", "issues": sorted(set(issues)),
                                                                "persona_mode": ctx["mode"], "checked_at": _now()})
        else:
            log["gate_2a"] = prp.register_territories(mdir, tlist, provenance_mode=MODE)["status"]

    failures = []
    for f in sorted(P["proposals"].glob("captures*.json")):
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if processed.get(f.name) == h:
            continue
        rows_in = _read(f, [])
        research_activity += 1
        targeted |= {c.get("jtbd") for c in rows_in if isinstance(c, dict) and c.get("jtbd")}
        res = promote_captures(mdir, rows_in, fetcher=fetcher, profile=ctx["profile"])
        failures += res["failed"]
        log.setdefault("captures", []).append({"file": f.name, "verified_and_recorded": res["verified_and_recorded"],
                                               "failed": len(res["failed"])})
        processed[f.name] = h
    _write(P["processed"], processed)

    jt = None
    gate2a_open = P["territories"].exists()
    if (P["proposals"] / "jtbd.json").exists() and not gate2a_open:
        # INTEL_V2 : « les gates donnent l'autorisation d'avancer » — pas de JTBD/candidats tant que le GATE 2A
        # (cible + découverte) n'est pas franchi. Les preuves déjà vérifiées restent au registre.
        log["gate_3"] = "GATE_3_BLOCKED_BY_GATE_2A"
    elif (P["proposals"] / "jtbd.json").exists():
        jt = register_jobs(mdir, _read(P["proposals"] / "jtbd.json", {}), market)
        log["gate_3"] = jt["status"]
        if jt["status"].startswith("PASS_"):
            log["seeded"] = seed_from_jtbd(mdir)

    state["cycles"] += 1
    state["idle_cycles"] = 0 if research_activity else state["idle_cycles"] + 1
    log["idle_cycles"] = state["idle_cycles"]
    step_res = {"decisions": []}
    reg = autopilot.CandidateRegistry.load(P["registry"], mdir.name)
    if reg.candidates:
        # Progrès = nouvelle observation FETCH_VERIFIED (hors contexte) rattachée au candidat depuis le cycle précédent.
        merged_rows = ledger.load_merged(P["ledger"])
        sig = {cid: sorted({r["observation_id"] for r in merged_rows if cid in (r.get("candidate_ids") or [])
                            and r.get("provenance_mode") == MODE and r.get("kind") not in NON_EVIDENCE_KINDS})
               for cid in reg.candidates}
        progress = {cid: bool(set(sig[cid]) - set(state["evidence"].get(cid, []))) for cid in reg.candidates}
        # Réouverture d'un candidat en réserve : uniquement sur nouvelle preuve vérifiée.
        reopened = []
        for cid, c in reg.candidates.items():
            if c["status"] in autopilot.PARKED and progress[cid]:
                # Les tentatives épuisées portaient sur l'ancien état des preuves : nouveau départ.
                c.update(status="ACTIVE", closed_reason=None, rounds={}, attempts={}, requested={})
                reg.data["events"].append({"candidate_id": cid, "verdict": "REOPEN_NEW_VERIFIED_EVIDENCE"})
                reopened.append(cid)
        # Tentative infructueuse = recherche ciblée documentée ce cycle, sans nouvelle preuve pour ce candidat.
        auto_done = {cid: list((c.get("requested") or {}).keys()) for cid, c in reg.candidates.items()
                     if c["status"] in ("ACTIVE", "BACKLOG") and cid in targeted and not progress[cid]}
        reg.save(P["registry"])
        dossiers = build_dossiers(mdir, _read(P["proposals"] / "assessments.json", {}), auto_done, v2=v2)
        merged = {r["observation_id"]: r for r in merged_rows}
        step_res = autopilot.step(reg, dossiers, ledger=merged, provenance_mode=MODE, target_market=market)
        reg.save(P["registry"])
        state["evidence"] = sig
        log["autopilot"] = step_res.get("action")
        log["attempts_counted_for"] = sorted(auto_done)
        log["reopened"] = reopened
    _write(P["state"], state)

    # Stage 1 = signaux larges réellement observés ; Stage 2 = micro-hypothèses
    # rattachées à des références du registre. Jamais un PASS de prévalence nationale.
    depth_input = _read(P["proposals"] / "micro_problems.json", {"micro_problems": []})
    v2["explored_domains"] = discovery.explored_domains(mdir)
    depth_result = research_depth.analyze(ledger.load_merged(P["ledger"]), depth_input, market=market, v2=v2)
    _write(inv_path, v2["investigation"])
    research_depth.save(mdir, depth_result)
    orders = _work_orders(mdir, step_res, jt, failures) + research_depth.work_orders(depth_result)
    _write(P["orders"], {"generated_at": _now(), "orders": orders, "cost_class": "FREE"})
    report = build_report(mdir, step_res, jt, log, orders, v2=v2)
    return {"status": report["status"], "report": str(P["report_md"]), "work_orders": len(orders), "log": log}


def recurrence_status(rows: list[dict], cid: str, hypothesis: str | None) -> dict:
    """Récurrence d'ACHAT (produit ou consommable), jamais une simple récurrence d'usage. Aucun calcul de LTV."""
    obs = [r["observation_id"] for r in rows if cid in (r.get("candidate_ids") or [])
           and r.get("kind") == "REPEAT_PURCHASE" and r.get("provenance_mode") == MODE]
    if obs:
        return {"status": "DOCUMENTED", "refs": obs, "hypothesis": hypothesis}
    if hypothesis in RECURRENCE_HYPOTHESES:
        return {"status": "HYPOTHESIS", "refs": [], "hypothesis": hypothesis,
                "missing": "témoignage FR vérifié de réachat / réapprovisionnement (REPEAT_PURCHASE)"}
    if hypothesis == "NONE":
        return {"status": "ABSENT", "refs": [], "hypothesis": hypothesis}
    return {"status": "UNKNOWN", "refs": [], "hypothesis": hypothesis,
            "missing": "hypothèse de récurrence d'achat non formulée (CONSUMABLE|REFILL|REPLACEMENT_WEAR|SUBSCRIPTION|NONE)"}


def build_report(mdir: Path, step_res: dict, jt: dict | None, log: dict, orders: list, v2: dict | None = None) -> dict:
    P = _paths(mdir)
    reg = autopilot.CandidateRegistry.load(P["registry"], mdir.name)
    rows = ledger.load_merged(P["ledger"])
    jobs = {j["id"]: j for j in _read(P["jtbd"], {}).get("jobs", [])}
    rc = ledger.load(mdir / "RESEARCH_INBOX" / "receipts.jsonl")
    last = {d.get("candidate_id"): d for d in step_res.get("decisions", [])}
    short = []
    for cid, c in reg.candidates.items():
        d = last.get(cid, {})
        refs = [r for r in rows if cid in (r.get("candidate_ids") or [])]
        short.append({"candidate_id": cid, "title": c["title"], "status": c["status"], "stage": c["stage"],
                      "last_verdict": d.get("verdict"), "reasons": d.get("reasons") or ([c.get("closed_reason")] if c.get("closed_reason") else []),
                      "gate2b": (d.get("gate2b") or {}).get("validation"),
                      "families": (d.get("gate2b") or {}).get("testimony_families"),
                      "independent_accounts": (d.get("gate2b") or {}).get("independent_accounts"),
                      "score_40": (d.get("score_detail") or {}).get("score"),
                      "score_evaluability": (d.get("score_detail") or {}).get("evaluability", "EVALUATED" if (d.get("score_detail") or {}).get("score") is not None else None),
                      "recurrence": recurrence_status(rows, cid, (jobs.get(cid) or {}).get("recurrence_hypothesis")),
                      "jtbd_refs": (jobs.get(cid) or {}).get("voc_refs", []),
                      "evidence": [{"id": r["observation_id"], "url": r["source_url"], "kind": r["kind"],
                                    "geo_basis": r["geo_basis"], "quote": r["phrase_exacte"][:160]} for r in refs]})
    if v2 is not None:
        for s in short:
            s.update(_candidate_v2(mdir, s["candidate_id"], v2))
    explo = exploratory_ranking(rows, short)
    official = [s["candidate_id"] for s in short if s["stage"] > 5]
    # Agent 02 exploratoire : candidats de la shortlist exploratoire et officielle, jamais un audit GATE 6.
    keep = {e["candidate_id"] for e in explo} | set(official)
    a02 = competitor_intel.exploratory_competitive_dossier(
        rows, [s for s in short if s["candidate_id"] in keep], _read(P["proposals"] / "solutions.json", {}))
    if v2 is not None:
        cr = _read(P["proposals"] / "competitive_research.json", {})
        for s in short:
            if s["candidate_id"] in a02["candidates"]:
                a02["candidates"][s["candidate_id"]]["agent02_v2"] = competitor_intel.agent02_v2_assessment(
                    rows, s, cr.get(s["candidate_id"]) if isinstance(cr, dict) else None, v2["thresholds"],
                    gaps=a02["candidates"][s["candidate_id"]]["gaps"])
        a02["schema"] = "ECOM_OS_AGENT02_EXPLORATORY_DOSSIER_V2"
    _write(mdir / "AGENT_02" / "competitive_dossier.json", a02)
    for e in explo:
        c = a02["candidates"].get(e["candidate_id"], {})
        e["competitors"] = [x["incumbent"] for x in c.get("competitors", [])]
        e["gaps"] = {g["dimension"]: g["status"] for g in c.get("gaps", [])}
        e["solutions"] = {s["id"]: s["status"] for s in c.get("solutions", [])}
    status = ("NEEDS_OWNER_DECISION" if official or step_res.get("owner_authorizations_needed")
              else "NEEDS_LLM_RESEARCH" if any(s["status"] in ("ACTIVE", "BACKLOG") for s in short) or (jt and not jt["status"].startswith("PASS_"))
              else "NO_OPEN_CANDIDATE_RESEARCH_EXHAUSTED" if reg.candidates else "NEEDS_LLM_DISCOVERY")
    if status == "NEEDS_LLM_RESEARCH" and log.get("idle_cycles", 0) >= IDLE_CYCLES_BEFORE_STOP:
        status = "IDLE_AWAITING_NEW_RESEARCH"   # arrêt propre : ni tentative, ni mise en réserve
    rep = {"schema": "ECOM_OS_AUTONOMOUS_RESEARCH_REPORT", "mission_id": mdir.name, "status": status,
           "evidence_tier": MODE, "certified_live": False, "commercially_approved": False,
           "financial_analysis": "HORS_PERIMETRE_DECISION_PROPRIETAIRE",
           "receipts": {"total": len(rc), "verified": sum(r.get("status") == "QUOTE_VERIFIED" for r in rc),
                        "failed_by_status": _count([r["status"] for r in rc if r.get("status") != "QUOTE_VERIFIED"])},
           "ledger_rows": len(rows), "gate_3": (jt or {}).get("status"), "candidates": short, "cycle": log,
           "official_shortlist_gate4": official, "exploratory_shortlist": explo,
           "discovery": _discovery_summary(mdir), "retention": retention_status(mdir), "work_orders": orders,
           "stage12_depth": _read(mdir / "AGENT_01" / "STAGE12_DEPTH_REPORT.json", {})}
    if v2 is not None:
        rep["persona"] = log.get("persona")
        rep["discovery_v2"] = discovery.summary(mdir)
        rep["owner_checkpoint"] = rep["stage12_depth"].get("owner_checkpoint")
        rep["thresholds_status"] = v2["thresholds"].get("status")
    _write(P["report"], rep)
    md = [f"# Rapport de recherche autonome — {mdir.name}", "",
          f"**Statut :** {status} | **Niveau de preuve :** FETCH_VERIFIED (provisoire, jamais LIVE) | **Analyse financière :** hors périmètre (décision propriétaire)", "",
          f"Reçus : {rep['receipts']['verified']} citations vérifiées / {rep['receipts']['total']} tentatives. Échecs : {rep['receipts']['failed_by_status']}",
          f"GATE 3 : {rep['gate_3']} | Shortlist officielle (GATE 4 franchi, revue propriétaire requise) : {official or 'aucune'}", "",
          "## Candidats (JTBD)", ""]
    for s in short:
        md.append(f"- **{s['candidate_id']}** — {s['title']} : {s['status']} (étape {s['stage']}), verdict {s['last_verdict']} ; "
                  f"GATE 2B {s['gate2b']} familles {s['families']} comptes {s['independent_accounts']} ; "
                  f"score /40 {s['score_40'] if s['score_40'] is not None else 'NON_EVALUABLE'} ; récurrence d'achat {s['recurrence']['status']}")
        for e in s["evidence"][:6]:
            md.append(f"  - {e['id']} [{e['kind']}, {e['geo_basis']}] « {e['quote']} » — {e['url']}")
    md += ["", "## Shortlist exploratoire (NON VALIDÉE, aucun score chiffré ; récurrence ABSENT/UNKNOWN exclue)", ""]
    md += [f"{i}. {e['candidate_id']} — {e['signal']} ; récurrence {e['recurrence']} : {e['basis']} ; "
           f"concurrents {e['competitors']} ; gaps {e['gaps']} ; solutions {e['solutions']}" for i, e in enumerate(explo, 1)]
    depth = rep["stage12_depth"]
    md += ["", "## Profondeur de recherche : Stage 1 → Stage 2", "",
           "Stage 1 = signal dans les sources disponibles, **pas** mesure de prévalence ou validation de masse."]
    for tid, t in depth.get("stage1", {}).items():
        md.append(f"- {tid} : {t['status']} ; {t['distinct_pain_accounts']} comptes ; "
                  f"{len(t['pain_families'])} familles ; {len(t['satisfaction_refs'])} contre-signaux de satisfaction")
    for m in depth.get("stage2", []):
        md.append(f"- micro {m['id']} ({m['territory_id']}) : {m['status']} ; "
                  f"{m.get('qualified_accounts', 0)} comptes ; références {m.get('qualified_pain_refs', [])}")
    if v2 is not None:
        oc = rep.get("owner_checkpoint") or {}
        md += ["", f"## Persona : {(rep.get('persona') or {}).get('mode')} {(rep.get('persona') or {}).get('persona_id') or ''}",
               f"Opportunités commerciales à approfondir : {oc.get('opportunities') or 'aucune'}"]
        md += [f"- {v} : {ids}" for v, ids in (oc.get("by_verdict") or {}).items() if ids]
        md += [f"Seuils : {rep.get('thresholds_status')} ; plancher codé : {demand.HARD_FLOOR_DISTINCT_PERSONS} auteurs distincts",
               f"DISCOVERY : {rep['discovery_v2']['total']} sources, {rep['discovery_v2']['distinct_domains']} domaines ; "
               f"{rep['discovery_v2']['explored_domains_incl_verification']} domaines explorés au total"]
    md += ["", "Revue des micro-problèmes par le propriétaire avant tout MarketPulse ; "
           "aucun connecteur déclenché automatiquement.", "",
           "Agent 02 (exploratoire, non validé, sans audit GATE 6) : AGENT_02/competitive_dossier.json", ""]
    dz = rep["discovery"]
    md += ["", f"## Découverte : {dz['queries']} requêtes, {dz['urls_seen']} URL vues, {dz['urls_rejected']} écartées avant vérification", ""]
    md += ["", "## Ordres de travail gratuits", ""] + [f"- {json.dumps(o, ensure_ascii=False)}" for o in orders[:40]]
    md += ["", "Limites : identité, authenticité et résidence des auteurs non vérifiées ; pas d'avance au-delà du GATE 4 sans le propriétaire.", ""]
    P["report_md"].write_text("\n".join(md), encoding="utf-8")
    return rep


def exploratory_ranking(rows: list[dict], short: list[dict]) -> list[dict]:
    """Shortlist exploratoire qualitative à partir des seules preuves enregistrées ; jamais un score /40.
    Les candidats sans récurrence d'achat crédible (ABSENT) ou non formulée (UNKNOWN) en sont exclus."""
    out = []
    for s in short:
        rec = s["recurrence"]["status"]
        if s["status"] == "REJECTED":   # la récurrence d'achat est évaluée, jamais un veto
            continue
        mine = [r for r in rows if s["candidate_id"] in (r.get("candidate_ids") or [])]
        q = [r for r in mine if r.get("geo_basis") in ledger.GEO_BASES_ACCEPTED]
        pain = [r for r in q if r.get("kind") in ("FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND")]
        sat = [r for r in q if r.get("kind") == "SATISFACTION"]
        acc = len({r["author_key"] for r in pain})
        fam = len({ledger.FAMILY.get(r["source_type"]) for r in pain})
        tier = (s.get("demand") or {}).get("tier")
        if tier:   # INTEL_V2 : le libellé suit la profondeur de la demande, jamais un simple comptage de 2–5 comptes
            sig = "NIVEAU_" + tier
        else:
            sig = "SIGNAL_FORT" if acc >= 3 and fam >= 2 else "SIGNAL_MOYEN" if acc >= 2 else "SIGNAL_FAIBLE"
        if sat and len(sat) >= len(pain):
            sig += "_CONTREDIT_PAR_SATISFACTIONS"
        lang = sum(r.get("geo_basis") == "LANGUAGE_ONLY" and r.get("kind") in ("FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND") for r in mine)
        out.append({"candidate_id": s["candidate_id"], "signal": sig, "recurrence": rec,
                    **({"persona_fit": s["persona_fit"]["status"], "demand_tier": s["demand"]["tier"],
                        "distinct_persons": s["demand"]["distinct_persons_observed"]} if s.get("demand") else {}),
                    "validation": "EXPLORATORY_NOT_VALIDATED",
                    "basis": f"{len(pain)} douleurs FR qualifiées / {acc} comptes / {fam} familles ; {lang} douleurs non "
                             f"qualifiées (langue seule) ; {len(sat)} satisfactions ; "
                             f"{sum(r.get('kind') == 'REPEAT_PURCHASE' for r in mine)} réachats observés",
                    "_k": (rec == "DOCUMENTED", acc, fam, len(pain) - len(sat))})
    out.sort(key=lambda e: e["_k"], reverse=True)
    return [{k: v for k, v in e.items() if k != "_k"} for e in out]


def _discovery_summary(mdir: Path) -> dict:
    log = ledger.load(mdir / "RESEARCH_INBOX" / "discovery_log.jsonl")
    seen = [u for e in log for u in e.get("urls", [])]
    return {"queries": sum(len(e.get("queries", [])) for e in log), "urls_seen": len(seen),
            "urls_rejected": sum(1 for u in seen if u.get("decision") == "REJECTED")}


RETENTION_DAYS = 30


def retention_status(mdir: Path, purge: bool = False) -> dict:
    """Politique : un instantané référencé par une ligne du registre est conservé (preuve) ;
    un instantané orphelin (capture échouée) devient supprimable après 30 jours, sur commande explicite."""
    import time
    rc = ledger.load(mdir / "RESEARCH_INBOX" / "receipts.jsonl")
    used = {r.get("receipt_id") for r in ledger.load(_paths(mdir)["ledger"])}
    keep = {r.get("sha256") for r in rc if r.get("receipt_id") in used}
    snaps = list((mdir / "RESEARCH_INBOX" / "SNAPSHOTS").glob("*.html.gz"))
    old = [p for p in snaps if p.name.split(".")[0] not in keep and time.time() - p.stat().st_mtime > RETENTION_DAYS * 86400]
    if purge:
        for p in old:
            p.unlink()
    return {"snapshots": len(snaps), "kept_as_evidence": sum(p.name.split(".")[0] in keep for p in snaps),
            "orphan_purgeable_after_days": RETENTION_DAYS, "purgeable_now": len(old), "purged": len(old) if purge else 0}


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out
