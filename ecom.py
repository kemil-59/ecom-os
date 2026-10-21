#!/usr/bin/env python3
"""CLI Centrale ECOM_OS_CLEAN_B3_MAC_AGENT_FINAL (`python3 ecom.py ...`).

Préserve 100 % des contrats et garde-fous de B1 (28 tests B1 intacts) et ajoute :
- `python3 ecom.py tt ...` (Priorité A : MarketPulse REPLAY / OUTBOX / INBOX / crédits / réconciliation)
- `python3 ecom.py a03 ...` (Priorité B : Agent 03 Creative Center + 45 recettes caméra + Higgsfield REPLAY)
- `python3 ecom.py a04|a05|a06 ...` (Priorité C : Interfaces Draft Agents 04, 05 et 06)
- `python3 ecom.py demo-b2` (Chaîne REPLAY complète Agent 01 -> MarketPulse -> Agent 02 -> Décision -> Agent 03 -> Agents 04/05/06)
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
sys.dont_write_bytecode = True  # avoid polluting protected reference_corpus with __pycache__
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    autopilot,
    competitor_intel,
    corpus_extractor,
    creative_center,
    downstream_agents,
    ledger,
    scoring,
    marketpulse_adapter,
    product_research_pipeline,
    research_queue,
    autonomous_research,
)


OWNER_PUBKEY = ROOT / ".claude" / "ecom_owner_pubkey.pem"


def _runs_dir() -> Path:
    return ROOT / "runs"


def _mission_dir(mission_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", mission_id or ""):
        raise SystemExit("INVALID_MISSION_ID_USE_LETTERS_DIGITS_DASH_UNDERSCORE_MAX64")
    return _runs_dir() / mission_id


def _out(payload: dict | list) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_init(args) -> int:
    mdir = _mission_dir(args.mission)
    cfg = {
        "mission_id": args.mission,
        "target_sale_market": args.market,
        "persona": args.persona if args.persona is not None else "Persona_A",
        "foreign_research_markets": args.foreign_markets,
        "mode": args.mode,
    }
    if args.mode == "FETCH_VERIFIED":
        # Méthode active : en recherche générale, aucun persona imposé — les segments sont découverts à partir des
        # preuves (persona_mode DISCOVERED). Un profil (ex. PERSONA_A_FR) n'est appliqué que s'il est demandé explicitement.
        from engine import persona as persona_mod
        if args.persona is None:
            cfg["persona"], cfg["persona_mode"] = None, "DISCOVERED"
        else:
            res = persona_mod.resolve({"persona": args.persona, "persona_mode": "PROFILE"})
            if res["mode"] != "PROFILE":
                raise SystemExit("FETCH_PERSONA_MUST_BE_A_PROFILE_ID_FROM_research/personas/_OR_OMITTED_FOR_DISCOVERY "
                                 f"(reçu : {args.persona!r})")
            cfg["persona"], cfg["persona_mode"] = res["persona_id"], "PROFILE"
    # Dossiers créés seulement après validation de la configuration (aucun résidu en cas de refus).
    for sub in ("AGENT_01", "AGENT_02", "AGENT_03", "AGENT_04", "AGENT_05", "AGENT_06", "MARKETPULSE"):
        (mdir / sub).mkdir(parents=True, exist_ok=True)
    cfg_path = mdir / "mission.json"
    if cfg_path.exists():
        existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        if existing != cfg:
            raise SystemExit("EXISTING_MISSION_CONFIG_CONFLICT_DO_NOT_OVERWRITE")
        _out({"status": "MISSION_ALREADY_INITIALIZED", "path": str(mdir), "config": cfg})
        return 0
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _out({"status": "MISSION_INITIALIZED", "path": str(mdir), "config": cfg})
    return 0


def build_replay_fixture(mdir: Path) -> dict:
    """Scénario REPLAY B1 (Agent 01 -> Agent 02 -> Décision Autopilot) conservé à l'identique."""
    mission_id = mdir.name
    for sub in ("AGENT_01", "AGENT_02", "AGENT_03", "MARKETPULSE"):
        (mdir / sub).mkdir(parents=True, exist_ok=True)

    mission_cfg = {
        "mission_id": mission_id,
        "target_sale_market": "FR",
        "persona": "Persona_A",
        "foreign_research_markets": ["US", "GB", "CA", "AU"],
        "mode": "REPLAY",
    }
    (mdir / "mission.json").write_text(json.dumps(mission_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    env = scoring.compute_economic_envelope(aov_ttc_eur=69.0, cogs_eur=9.0, target_net_margin_eur=22.0)
    (mdir / "AGENT_01" / "economic_envelope.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    led_path = mdir / "AGENT_01" / "problem_ledger.jsonl"
    if led_path.exists():
        led_path.unlink()
    obs_batch = [
        {
            "territory_id": "T-CUISINE",
            "probleme": "L'eau stagne sous l'égouttoir classique et laisse du calcaire sur le plan de travail",
            "phrase_exacte": "Très bien mais l'eau stagne dans le bac en plastique et finit par sentir mauvais au bout de 3 jours.",
            "source_type": "AMAZON_FR_REVIEW",
            "source_url": "https://www.amazon.fr/dp/B08EXAMPLE1?ref=review1",
            "author_key": "client_fr_01",
            "kind": "SOLUTION_DEFECT_SPEND",
            "incumbent": "EgouttoirBacPlastique",
            "dimension": "drainage et calcaire",
            "candidate_ids": ["JTBD-DRAIN-01"],
        },
        {
            "territory_id": "T-CUISINE",
            "probleme": "Plan de travail toujours trempé autour de l'évier",
            "phrase_exacte": "J'en ai marre d'éponger sous mon égouttoir à chaque vaisselle, le bec verseur goutte à côté.",
            "source_type": "FORUM_FR",
            "source_url": "https://forum.exemple.fr/cuisine/sujet-egouttoir-4421",
            "author_key": "nath_59",
            "kind": "FRUSTRATION",
            "dimension": "drainage et calcaire",
            "candidate_ids": ["JTBD-DRAIN-01"],
        },
        {
            "territory_id": "T-CUISINE",
            "probleme": "Tapis microfibre qui reste humide et moisit",
            "phrase_exacte": "Le tapis d'égouttage en microfibre reste mouillé toute la nuit, obligée de le laver tous les deux jours.",
            "source_type": "YOUTUBE_COMMENT",
            "source_url": "https://www.youtube.com/watch?v=dishdry01",
            "author_key": "marie_cuisine",
            "kind": "SOLUTION_DEFECT",
            "incumbent": "TapisMicrofibre",
            "dimension": "drainage et séchage",
            "candidate_ids": ["JTBD-DRAIN-01"],
        },
        {
            "territory_id": "T-CUISINE",
            "probleme": "Bec d'évacuation trop court sur égouttoir inox",
            "phrase_exacte": "Payé 49€ et le bec verseur est trop court si l'évier a un rebord, l'eau coule sur le plan.",
            "source_type": "TRUSTPILOT_FR",
            "source_url": "https://fr.trustpilot.com/reviews/inoxdrain-fr-001",
            "author_key": "claire_tp",
            "kind": "SOLUTION_DEFECT_SPEND",
            "incumbent": "InoxDrainFR",
            "dimension": "drainage et bec orientable",
            "candidate_ids": ["JTBD-DRAIN-01"],
        },
    ]
    ledger.append(led_path, obs_batch, mission_id=mission_id, target_market="FR", provenance_mode="REPLAY")
    led_rows = ledger.load(led_path)
    g2b_res = ledger.gate_2b(led_rows, "JTBD-DRAIN-01", target_market="FR", allow_replay=True)

    ranked = {
        "jobs": [
            {
                "id": "JTBD-DRAIN-01",
                "title": "Égoutter la vaisselle sans eau stagnante ni nettoyage constant du plan de travail",
                "workaround_cost": "Éponger 2 fois par jour + laver les tapis humides + traces de calcaire",
            },
            {
                "id": "JTBD-LOW-02",
                "title": "Polir l'inox noir sans traces",
                "workaround_cost": "Passer un chiffon microfibre sec (coût faible)",
            },
        ],
        "not_retained_at_step4": [],
    }
    (mdir / "AGENT_01" / "jtbd_ranked.json").write_text(json.dumps(ranked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    known_refs = {r["observation_id"] for r in led_rows}
    score_card = scoring.score_with_evidence(
        {
            "persona_fit": {"score": 4.5, "evidence_refs": ["OBS-0001", "OBS-0002"], "justification": "Tâche quotidienne directe du foyer"},
            "frequency": {"score": 4.5, "evidence_refs": ["OBS-0002", "OBS-0003"], "justification": "Problème répété à chaque vaisselle"},
            "intensity": {"score": 4.0, "evidence_refs": ["OBS-0001", "OBS-0004"], "justification": "Odeurs, humidité et calcaire récurrents"},
            "existing_spend": {"score": 4.0, "evidence_refs": ["OBS-0001", "OBS-0004"], "justification": "Achats prouvés d'égouttoirs à 49€"},
            "dissatisfaction": {"score": 4.5, "evidence_refs": ["OBS-0001", "OBS-0003", "OBS-0004"], "justification": "Bacs stagnants, microfibre humide, bec trop court"},
            "market_depth": {"score": 4.0, "evidence_refs": ["OBS-0001", "OBS-0004"], "justification": "Marché équipement cuisine profond"},
            "creative_demonstrability": {"score": 4.5, "evidence_refs": ["OBS-0002", "OBS-0004"], "justification": "Évacuation d'eau immédiatement visible"},
            "range_ltv": {"score": 3.5, "evidence_refs": ["OBS-0003"], "justification": "Recharges diatomite et accessoires évier"},
        },
        known_refs=known_refs,
    )
    econ_screen = scoring.solution_economics_screen(
        observed_price_ttc_eur=69.0,
        target_cac_eur=15.0,
        sourced_cogs_eur=11.0,
        cogs_source="HYPOTHESIS",
        price_source_refs=["OBS-0004"],
    )

    solutions = [
        {
            "id": "SOL-DIATOMITE-SPOUT",
            "mechanism": "Plateau incliné à canal télescopique + base diatomite absorbante amovible",
            "reason_to_win": {
                "kind": "PRODUCT_MECHANISM",
                "claim": "Évacuation directe par bec télescopique + absorption instantanée des gouttes résiduelles sans bac stagnant",
            },
            "dimension_keywords": ["drainage", "calcaire", "bec", "séchage"],
            "defect_refs": ["OBS-0001", "OBS-0003", "OBS-0004"],
            "incumbents": [
                {"name": "InoxDrainFR", "defect_refs": ["OBS-0001", "OBS-0004"], "satisfied_refs": []}
            ],
            "feasibility": "PLAUSIBLE",
            "supplier": "SUPPLIER_NINGBO_A",
            "sku_spec": {"materials": ["stainless_304", "diatomite"], "dimensions": "420x300x140mm"},
            "economics": {"safety_ratio": 1.85, "cogs_source": "HYPOTHESIS", "ratio_at_zero_cogs": 3.2, "sample_cost_eur": 45.0},
        },
        {
            "id": "SOL-MAT-ONLY",
            "mechanism": "Tapis diatomite plat seul sans structure d'égouttoir",
            "reason_to_win": {"kind": "PRICE", "claim": "Alternative plate économique"},
            "dimension_keywords": ["drainage", "séchage"],
            "defect_refs": ["OBS-0003"],
            "incumbents": [],
            "feasibility": "PLAUSIBLE",
            "supplier": "SUPPLIER_B",
            "sku_spec": {"materials": ["diatomite"], "dimensions": "400x300x9mm"},
            "economics": {"safety_ratio": 0.85, "cogs_source": "PUBLIC_LISTING", "ratio_at_zero_cogs": 1.4},
        },
        {
            "id": "SOL-HEATED-RACK",
            "mechanism": "Égouttoir chauffant soufflant 220V",
            "reason_to_win": {"kind": "SPEED", "claim": "Séchage électrique actif"},
            "dimension_keywords": ["séchage"],
            "defect_refs": ["OBS-0001", "OBS-0003"],
            "incumbents": [],
            "feasibility": "IMPOSSIBLE",
            "supplier": "SUPPLIER_C",
            "sku_spec": {"materials": ["abs_220v"], "dimensions": "500x350x300mm"},
            "economics": {"safety_ratio": 0.7, "cogs_source": "PUBLIC_LISTING", "ratio_at_zero_cogs": 0.9},
        },
    ]

    handoff = {
        "mission_id": mission_id,
        "target_sale_market": "FR",
        "foreign_research_markets": ["US", "GB", "CA", "AU"],
        "jtbd": [{"jtbd_id": "JTBD-DRAIN-01", "problem_statement": "Eau stagnante sous égouttoir"}],
        "home_market_sources": [{"source_ref": "OBS-0004", "market_geo": "FR"}],
        "solution_candidates_if_stage7": solutions,
        "G1_product_approved": False,
    }
    a02_evidence = [
        {
            "mission_id": mission_id,
            "source_ref": "EV-FR-01",
            "market_geo": "FR",
            "observed_at": "2026-10-19T10:00:00+00:00",
            "source_class": "DIRECT_OBSERVATION",
            "entity_scope": "REVIEW",
            "competitor_id": "InoxDrainFR",
            "observation": "SIMULATION de témoignage fictif: bec fixe trop court sur cet égouttoir; aucune preuve réelle",
            "source_url": "https://fr.trustpilot.com/reviews/inoxdrain-fr-001",
            "price": 49.0,
            "currency": "EUR",
            "synthetic": True,
            "provenance_mode": "REPLAY",
        },
        {
            "mission_id": mission_id,
            "source_ref": "EV-US-01",
            "market_geo": "US",
            "observed_at": "2026-10-19T10:05:00+00:00",
            "source_class": "DIRECT_OBSERVATION",
            "entity_scope": "SKU",
            "competitor_id": "DiatomRackUS",
            "observation": "Modèle US combine bec orientable télescopique + base diatomite amovible à 68$",
            "source_url": "https://example-us-store.com/products/diatom-rack",
            "price": 68.0,
            "currency": "USD",
            "synthetic": True,
            "provenance_mode": "REPLAY",
        },
    ]
    a02_gaps = [
        {
            "gap_id": "GAP-DRAIN-01",
            "solution_id": "SOL-DIATOMITE-SPOUT",
            "jtbd_id": "JTBD-DRAIN-01",
            "gap_type": "PRODUCT_FEATURE",
            "customer_value": "Bec télescopique franchissant le rebord d'évier + base diatomite absorbante",
            "original_angle": "Finir la vaisselle sans jamais éponger sous l'égouttoir",
            "home_source_refs": ["EV-FR-01"],
            "foreign_source_refs": ["EV-US-01"],
            "voc_source_refs": ["EV-FR-01"],
        },
        {
            "gap_id": "GAP-DRAIN-02",
            "solution_id": "SOL-MAT-ONLY",
            "jtbd_id": "JTBD-DRAIN-01",
            "gap_type": "OFFER_VALUE",
            "customer_value": "Tapis seul",
            "original_angle": "Tapis absorbant",
            "home_source_refs": ["EV-FR-01"],
            "foreign_source_refs": ["EV-US-01"],
            "voc_source_refs": ["EV-FR-01"],
        },
        {
            "gap_id": "GAP-DRAIN-03",
            "solution_id": "SOL-HEATED-RACK",
            "jtbd_id": "JTBD-DRAIN-01",
            "gap_type": "PRODUCT_FEATURE",
            "customer_value": "Séchage électrique",
            "original_angle": "Séchage chauffant",
            "home_source_refs": ["EV-FR-01"],
            "foreign_source_refs": ["EV-US-01"],
            "voc_source_refs": ["EV-FR-01"],
        },
    ]
    a02_negatives = [
        {
            "gap_id": "GAP-DRAIN-01",
            "market_geo": "FR",
            "query": "egouttoir bec telescopique diatomite",
            "surface": "shops",
            "counterexample_found": True,
            "source_refs": ["EV-FR-01"],
            "result_evidence_ref": "EV-FR-01",
        },
        {
            "gap_id": "GAP-DRAIN-02",
            "market_geo": "FR",
            "query": "tapis diatomite vaisselle",
            "surface": "marketplace",
            "counterexample_found": True,
            "source_refs": ["EV-FR-01"],
            "result_evidence_ref": "EV-FR-01",
        },
        {
            "gap_id": "GAP-DRAIN-03",
            "market_geo": "FR",
            "query": "egouttoir chauffant",
            "surface": "marketplace",
            "counterexample_found": True,
            "source_refs": ["EV-FR-01"],
            "result_evidence_ref": "EV-FR-01",
        },
    ]
    a02_scores = {
        "GAP-DRAIN-01": {
            "fr_problem_fit": 5,
            "distinct_customer_benefit": 4,
            "supply_feasibility": 4,
            "fr_regulatory_fit": 5,
            "unit_economics_potential": 4,
            "source_refs": ["EV-FR-01", "EV-US-01"],
        }
    }
    a02_report = competitor_intel.run_audit(
        handoff, a02_evidence, a02_gaps, a02_negatives, a02_scores, allow_synthetic=True
    )
    a02_path = mdir / "AGENT_02" / "competitor_audit_report.json"
    a02_path.write_text(json.dumps(a02_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dossiers = {
        "JTBD-DRAIN-01": {
            "candidate_id": "JTBD-DRAIN-01",
            "title": ranked["jobs"][0]["title"],
            "stage": 5,
            "score": score_card,
            "gate2b": g2b_res,
            "economic_screen": econ_screen,
            "red_flags": [],
            "gate5": {
                "trigger": ["OBS-0001", "OBS-0002"],
                "current_solution": ["OBS-0001", "OBS-0003"],
                "why_it_fails": ["OBS-0001", "OBS-0004"],
                "real_want": ["OBS-0002"],
                "payment_blockers": ["OBS-0004"],
                "proof_of_better": ["OBS-0004"],
            },
            "solution_mapping_complete": True,
            "solutions": solutions,
            "product_filter": {
                "SOL-DIATOMITE-SPOUT": {f"q{i}": "YES" for i in range(1, 14)} | {"q14": "YES_INDICATIVE"}
            },
            "info_needed": [],
        },
        "JTBD-LOW-02": {
            "candidate_id": "JTBD-LOW-02",
            "title": ranked["jobs"][1]["title"],
            "stage": 5,
            "score": {"status": "SCORED_WITH_EVIDENCE", "score": 24.0},
            "gate2b": {"status": "FAIL"},
            "economic_screen": {"status": "OK"},
            "red_flags": [],
        },
    }
    (mdir / "AGENT_01" / "dossiers.json").write_text(json.dumps(dossiers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    reg_path = mdir / "AGENT_01" / "candidate_registry.json"
    if reg_path.exists():
        reg_path.unlink()
    reg = autopilot.CandidateRegistry.load(reg_path, mission_id)
    autopilot.seed_candidates(
        reg,
        [
            {"id": "JTBD-DRAIN-01", "title": ranked["jobs"][0]["title"], "stage": 5, "score": 33.5, "territory": "T-CUISINE"},
            {"id": "JTBD-LOW-02", "title": ranked["jobs"][1]["title"], "stage": 5, "score": 24.0, "territory": "T-CUISINE"},
        ],
    )
    out = autopilot.step(
        reg,
        dossiers,
        ledger=ledger.load_by_id(led_path),
        agent02_audits=autopilot.load_agent02_audits(a02_path),
        universe=["JTBD-DRAIN-01", "JTBD-LOW-02"],
        provenance_mode="REPLAY",
        _fixture_demo_legacy_filter=True,  # Legacy simulation only; never enabled by auto step CLI
    )
    reg.save(reg_path)
    return {
        "mission_id": mission_id,
        "provenance_mode": "REPLAY_SIMULATION_NOT_LIVE_EVIDENCE",
        "gate2b": g2b_res["status"],
        "market_score_40": score_card["score"],
        "agent02_status": a02_report["status"],
        "autopilot_action": out["action"],
        "selected_candidate": out.get("candidate_id"),
        "selected_solution": (reg.candidates.get(out.get("candidate_id"), {}).get("selected_solution_id")),
        "selected_material_fingerprint": (reg.candidates.get(out.get("candidate_id"), {}).get("selected_material_fingerprint")),
        "owner_authorizations_needed": out.get("owner_authorizations_needed"),
    }


def build_b2_full_replay_demo(mdir: Path) -> dict:
    """Chaîne REPLAY complète B2 : Agent 01 -> MarketPulse REPLAY -> Agent 02 -> Décision -> Agent 03 + Higgsfield -> Agents 04/05/06."""
    b1_res = build_replay_fixture(mdir)
    mission_id = mdir.name

    # 1. MarketPulse REPLAY : solde -> approbation REPLAY -> plan -> ingest -> reconcile
    tt = marketpulse_adapter.MarketPulseAdapter(mdir / "MARKETPULSE", mode="REPLAY", connected=True)
    tt.ingest_balance({"requestId": "req-bal-001", "credits": {"totalRemaining": 9991}})
    tt.record_replay_approval(
        scope="A01_STAGE5",
        max_credits=50,
        owner_statement="Accord REPLAY test MarketPulse sur A01_STAGE5 (plafond 50 crédits)",
    )
    tt_plan = tt.plan_call(
        mission_id=mission_id,
        scope="A01_STAGE5",
        recipe_id="A01_S5_AD_SAMPLE_REACH",
        country="FR",
        stage=5,
        terms=["égouttoir", "vaisselle"],
        locale="fr",
    )
    tt_ingest = tt.ingest_call(
        tt_plan["call_id"],
        {
            "requestId": "req-ads-002",
            "credits": {"costUnits": 4, "creditsRemaining": 9987},
            "data": [
                {
                    "ad_id": "AD-FR-901",
                    "advertiser_name": "InoxDrainFR",
                    "ad_copy": "Un égouttoir vaisselle qui évacue l'eau dans l'évier.",
                    "shop_domain": "inoxdrain.fr",
                    "landing_url": "https://inoxdrain.fr/products/egouttoir-inox",
                    "sku_url": "https://inoxdrain.fr/products/egouttoir-inox",
                    "eu_reach": 18500,
                    "advertiser_active_ads": 12,
                },
                {
                    "ad_id": "AD-FR-902",
                    "advertiser_name": "MegaBazar500",
                    "ad_copy": "Promotion maison générale",
                    "shop_domain": "megabazar.fr",
                    "landing_url": "https://megabazar.fr/collections/all",
                    "eu_reach": 45,
                    "advertiser_active_ads": 650,
                },
            ],
        },
    )
    tt_reconcile = tt.reconcile_credits({"requestId": "req-bal-003", "credits": {"totalRemaining": 9987}})

    # 2. Agent 03 Creative Center : compilation des angles, hooks, briefs, 45 caméras et Higgsfield REPLAY
    reg = autopilot.CandidateRegistry.load(mdir / "AGENT_01" / "candidate_registry.json", mission_id)
    dossiers = json.loads((mdir / "AGENT_01" / "dossiers.json").read_text(encoding="utf-8"))
    led_by_id = ledger.load_by_id(mdir / "AGENT_01" / "problem_ledger.jsonl")
    a02_report = json.loads((mdir / "AGENT_02" / "competitor_audit_report.json").read_text(encoding="utf-8"))
    cand_entry = reg.candidates[b1_res["selected_candidate"]]

    product_master = {
        "sku": "SKU-DRAIN-PRO-01",
        "name": "Égouttoir Canal Télescopique & Base Diatomite",
        "category": "kitchen_organization",
        "solution_id": b1_res["selected_solution"],
        "material_fingerprint": b1_res["selected_material_fingerprint"],
        "materials": ["stainless_304", "diatomite"],
        "dimensions_mm": "420x300x140mm",
        "immutable_attributes": [
            "cadre acier inoxydable 304 brossé",
            "bec verseur télescopique orientable 360°",
            "plaque inférieure en pierre de diatomite gris clair amovible",
        ],
        "sample_status": "SUPPLIER_SPEC_ONLY",
        "reference_media": [
            {"ref_id": "REF-FRONT", "view": "front", "media_id": "uuid-media-front-001", "rights": "SUPPLIER_AUTHORIZED"},
            {"ref_id": "REF-DETAIL", "view": "detail", "media_id": "uuid-media-detail-002", "rights": "SUPPLIER_AUTHORIZED"},
        ],
    }
    (mdir / "AGENT_03" / "product_master.json").write_text(
        json.dumps(product_master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    c_plan = creative_center.compile_creative_center_plan(
        root_dir=ROOT,
        mission_id=mission_id,
        persona="Persona_A",
        target_market="FR",
        candidate_entry=cand_entry,
        dossier=dossiers[b1_res["selected_candidate"]],
        ledger_by_id=led_by_id,
        agent02_report=a02_report,
        product_master=product_master,
    )
    (mdir / "AGENT_03" / "creative_plan.json").write_text(
        json.dumps(c_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    hf_cycle = creative_center.run_higgsfield_replay_cycle(mdir / "AGENT_03", c_plan)

    # 3. Agents 04, 05 & 06 (Interfaces Draft hors ligne)
    a04_draft = downstream_agents.compile_agent04_shopify_draft(
        root_dir=ROOT,
        mission_id=mission_id,
        creative_plan=c_plan,
        product_truth={
            "mission_id": mission_id,
            "sale_market": "FR",
            "currency": "EUR",
            "sku": product_master["sku"],
            "product_name": product_master["name"],
            "jtbd": dossiers[b1_res["selected_candidate"]]["title"],
            "avatar": "Persona_A 42-55 ans",
            "experience_brief": {
                "entry": {"ad_product_id": product_master["sku"], "page_product_id": product_master["sku"]},
                "product_profile": {"technical_mechanism": True},
            },
        },
    )
    a06_diag = downstream_agents.diagnose_agent06_funnel_and_kahel_pillars({
        "spend_eur": 320.0,
        "target_cac_eur": 15.0,
        "orders": 14,
        "link_ctr": 0.016,
        "lpv_per_click": 0.84,
        "atc_rate": 0.065,
        "cvr": 0.024,
        "refund_rate": 0.02,
        "chargeback_rate": 0.0,
        "late_delivery_rate": 0.03,
        "cash_buffer_ok": True,
        "winning_concepts_count": 2,
    })

    return {
        **b1_res,
        "marketpulse_replay": {
            "call_id": tt_plan["call_id"],
            "credits_spent": tt_ingest["credits_spent"],
            "entity_links": tt_ingest["entity_links"],
            "reconcile_status": tt_reconcile["status"],
        },
        "agent03_creative_center": {
            "status": c_plan["status"],
            "angles_count": len(c_plan["angle_cards"]),
            "concepts_count": len(c_plan["concepts"]),
            "briefs_count": len(c_plan["briefs"]),
            "job_specs_count": len(c_plan["job_specs"]),
            "blocked_formats_without_sample": len(c_plan["blocked_formats"]),
            "higgsfield_replay_asset_status": hf_cycle["final_asset_status"],
        },
        "downstream_interfaces": {
            "agent04_status": a04_draft["status"],
            "agent04_archetype": a04_draft["customer_decision_plan"]["architecture"]["archetype"],
            "agent06_decision": a06_diag["decision"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 ecom.py")
    sp = ap.add_subparsers(dest="domain", required=True)

    # init
    p_init = sp.add_parser("init")
    p_init.add_argument("--mission", required=True)
    p_init.add_argument("--market", default="FR")
    p_init.add_argument("--persona", default=None,
                        help="FETCH_VERIFIED : omis = segments découverts ; ID de research/personas/ = ciblage imposé")
    p_init.add_argument("--foreign-markets", nargs="*", default=["US", "GB", "CA", "AU"])
    p_init.add_argument("--mode", choices=["LIVE", "REPLAY", "FETCH_VERIFIED"], default="REPLAY")
    p_src = sp.add_parser("sources", help="Adaptateurs d'extraction de haute confiance (facultatifs) et contrôle des propositions")
    sp_src = p_src.add_subparsers(dest="cmd", required=True)
    sp_src.add_parser("list")
    p_sval = sp_src.add_parser("validate")
    p_sval.add_argument("--proposal", required=True, type=Path)

    # B3 safe, offline Product First commands
    sp.add_parser("healthcheck")
    p_dossier = sp.add_parser("decision-dossier")
    p_dossier.add_argument("--mission", required=True)
    p_dossier.add_argument("--candidate", default=None)
    sp.add_parser("demo-b3")
    p_kit = sp.add_parser("research-kit", help="Créer le cahier de recherche LLM_Provider runtime (aucune requête externe)")
    p_kit.add_argument("--mission", required=True)
    p_stage = sp.add_parser("research-stage", help="Mettre une capture non vérifiée en quarantaine")
    p_stage.add_argument("--mission", required=True)
    p_stage.add_argument("--file", required=True, type=Path)
    p_research = sp.add_parser("research", help="Recherche autonome FETCH_VERIFIED (cycle moteur, limité au GATE 4)")
    sp_research = p_research.add_subparsers(dest="cmd", required=True)
    p_rrun = sp_research.add_parser("run")
    p_rrun.add_argument("--mission", required=True)
    p_rpurge = sp_research.add_parser("purge")
    p_rpurge.add_argument("--mission", required=True)

    # extract-corpus & knowledge
    p_ext = sp.add_parser("extract-corpus")
    p_ext.add_argument("--src-dir", type=Path, default=ROOT.parent)
    p_kn = sp.add_parser("knowledge")
    p_kn.add_argument("--query", default="")

    # a01
    p_a01 = sp.add_parser("a01")
    sp_a01 = p_a01.add_subparsers(dest="cmd", required=True)
    for b3_cmd in ("territories", "jtbd"):
        p_new = sp_a01.add_parser(b3_cmd)
        p_new.add_argument("--mission", required=True)
        p_new.add_argument("--file", type=Path, required=True)
    p_fetch = sp_a01.add_parser("collect-web")
    p_fetch.add_argument("--mission", required=True)
    p_fetch.add_argument("--url", required=True)
    p_fetch.add_argument("--purpose", required=True)
    l_add = sp_a01.add_parser("ledger-add")
    l_add.add_argument("--mission", required=True)
    l_add.add_argument("--file", required=True, type=Path)
    g2b = sp_a01.add_parser("gate2b")
    g2b.add_argument("--mission", required=True)
    g2b.add_argument("--candidate", required=True)
    sc = sp_a01.add_parser("score")
    sc.add_argument("--mission", required=True)
    sc.add_argument("--file", required=True, type=Path)

    # a02
    p_a02 = sp.add_parser("a02")
    sp_a02 = p_a02.add_subparsers(dest="cmd", required=True)
    aud = sp_a02.add_parser("audit")
    aud.add_argument("--mission", required=True)
    aud.add_argument("--handoff", required=True, type=Path)
    aud.add_argument("--evidence", required=True, type=Path)
    aud.add_argument("--gaps", required=True, type=Path)
    aud.add_argument("--negatives", required=True, type=Path)
    aud.add_argument("--scores", required=True, type=Path)
    aud.add_argument("--allow-synthetic", action="store_true")

    # tt (Priorité A)
    p_tt = sp.add_parser("tt")
    sp_tt = p_tt.add_subparsers(dest="cmd", required=True)
    p_keygen = sp_tt.add_parser("owner-keygen", help="Propriétaire seulement, dans son propre Terminal")
    p_keygen.add_argument("--key-dir", type=Path, default=Path.home() / ".ecom_os")
    for tcmd in ("capabilities", "balance", "approve-replay", "approve-live", "plan", "ingest", "cancel", "status", "reconcile",
                 "calibration", "confirm-fields", "resolve-call", "next", "wave", "report"):
        s = sp_tt.add_parser(tcmd)
        s.add_argument("--mission", required=True)
        s.add_argument("--mode", default="REPLAY")
        if tcmd == "balance":
            s.add_argument("--file", required=True, type=Path)
        if tcmd == "reconcile":
            s.add_argument("--file", type=Path)
        if tcmd == "approve-replay":
            s.add_argument("--scope", required=True)
            s.add_argument("--max-credits", type=int, required=True)
            s.add_argument("--statement", required=True)
        if tcmd == "approve-live":
            s.add_argument("--scope", required=True)
            s.add_argument("--max-credits", type=int, required=True)
            s.add_argument("--hours", type=int, default=24)
            s.add_argument("--key", type=Path, default=Path.home() / ".ecom_os" / "owner_signing_key.pem")
            s.add_argument("--autonomous", action="store_true",
                           help="Lie l'approbation à l'empreinte du plan TT_INVESTIGATION_PLAN.json (enchaînement sans confirmation par appel)")
        if tcmd == "plan":
            s.add_argument("--scope", required=True)
            s.add_argument("--recipe", required=True)
            s.add_argument("--country", default="FR")
            s.add_argument("--stage", type=int, default=5)
            s.add_argument("--terms", nargs="*", default=[])
            s.add_argument("--locale", default="fr")
            s.add_argument("--linked-domain", default=None)
            s.add_argument("--linked-ad-ref", default=None)
            s.add_argument("--limit", type=int, default=None)
            s.add_argument("--page", type=int, default=1)
            s.add_argument("--lot", default=None)
        if tcmd in ("ingest", "cancel", "resolve-call"):
            s.add_argument("--call", required=True)
        if tcmd == "resolve-call":
            s.add_argument("--decision", required=True, choices=["NO_CHARGE", "CHARGED"])
            s.add_argument("--charged-credits", type=int, default=0)
            s.add_argument("--justification", required=True)
            s.add_argument("--key", type=Path, default=Path.home() / ".ecom_os" / "owner_signing_key.pem")
        if tcmd in ("calibration", "confirm-fields"):
            s.add_argument("--tool", required=True, choices=sorted(marketpulse_adapter.FIELD_ROLES))
        if tcmd == "confirm-fields":
            s.add_argument("--map", nargs="+", required=True, help="role=cle (ex. id=adId total=meta.total)")
            s.add_argument("--key", type=Path, default=Path.home() / ".ecom_os" / "owner_signing_key.pem")
        if tcmd == "ingest":
            s.add_argument("--file", type=Path)

    # a03 (Priorité B)
    p_a03 = sp.add_parser("a03")
    sp_a03 = p_a03.add_subparsers(dest="cmd", required=True)
    c03 = sp_a03.add_parser("compile")
    c03.add_argument("--mission", required=True)
    c03.add_argument("--product", required=True, type=Path)
    l03 = sp_a03.add_parser("lint")
    l03.add_argument("--text", required=True)
    h03 = sp_a03.add_parser("hf-replay")
    h03.add_argument("--mission", required=True)

    # a04, a05, a06 (Priorité C)
    p_a04 = sp.add_parser("a04")
    p_a04.add_argument("--mission", required=True)
    p_a05 = sp.add_parser("a05")
    sp_a05 = p_a05.add_subparsers(dest="cmd", required=True)
    p_a05_plan = sp_a05.add_parser("plan")
    p_a05_plan.add_argument("--packet", required=True, type=Path)
    p_a06 = sp.add_parser("a06")
    p_a06.add_argument("--metrics", required=True, type=Path)

    # auto
    p_auto = sp.add_parser("auto")
    sp_auto = p_auto.add_subparsers(dest="cmd", required=True)
    for cname in ("seed", "step", "status", "authorize", "operational"):
        c = sp_auto.add_parser(cname)
        c.add_argument("--mission", required=True)
        if cname == "seed":
            c.add_argument("--file", required=True, type=Path)
        if cname == "step":
            c.add_argument("--dossiers", required=True, type=Path)
        if cname == "authorize":
            c.add_argument("--candidate", required=True)
            c.add_argument("--item", required=True)
            c.add_argument("--scope-key", required=True)
            c.add_argument("--grant", action="store_true")
            c.add_argument("--decline", action="store_true")
        if cname == "operational":
            c.add_argument("--candidate", required=True)
            c.add_argument("--solution", required=True)
            c.add_argument("--material-fp", required=True)
            c.add_argument("--pass-gate", action="store_true")
            c.add_argument("--reason", required=True)
            c.add_argument("--refs", nargs="+", required=True)

    sp.add_parser("demo")
    sp.add_parser("replay")
    sp.add_parser("demo-b2")
    sp.add_parser("test")

    args = ap.parse_args(argv)

    if args.domain == "init":
        return cmd_init(args)

    if args.domain == "sources":
        from engine import source_registry
        if args.cmd == "list":
            _out(source_registry.list_sources())
            return 0
        res = source_registry.validate_proposal(args.proposal)
        _out(res)
        return 0 if res["status"] == "PROPOSAL_VALID_PENDING_OWNER" else 3

    if args.domain in ("research-kit", "research-stage"):
        mdir = _mission_dir(args.mission)
        cfgpath = mdir / "mission.json"
        if not cfgpath.exists():
            raise SystemExit("MISSION_NOT_INITIALIZED")
        cfg = json.loads(cfgpath.read_text(encoding="utf-8"))
        if cfg.get("mode") not in ("REPLAY", "FETCH_VERIFIED") or cfg.get("mission_id") != args.mission:
            raise SystemExit("MISSION_NOT_REPLAY_OR_CONFIG_MISMATCH")
        if args.domain == "research-kit":
            _out(research_queue.make_research_kit(mdir, cfg.get("target_sale_market", "FR"), cfg.get("persona", "Unknown")))
            return 0
        result = research_queue.stage_unverified_capture(mdir, json.loads(args.file.read_text(encoding="utf-8")))
        _out(result)
        return 0 if result["status"] in ("CAPTURE_STAGED_UNVERIFIED", "CAPTURE_ALREADY_STAGED") else 3

    if args.domain == "research":
        mdir = _mission_dir(args.mission)
        res = autonomous_research.retention_status(mdir, purge=True) if args.cmd == "purge" else autonomous_research.run(mdir)
        _out(res)
        return 0

    if args.domain == "healthcheck":
        h = product_research_pipeline.healthcheck(ROOT)
        _out(h)
        return 0 if h["status"] == "REPLAY_READY" else 3
    if args.domain == "decision-dossier":
        res = product_research_pipeline.generate_decision_dossier(_mission_dir(args.mission), args.candidate)
        _out(res)
        return 0 if res["status"] == "DRAFT_REPLAY_NOT_COMMERCIAL_APPROVAL" else 3
    if args.domain == "demo-b3":
        import uuid
        mdir = _mission_dir("DEMO_B3_" + uuid.uuid4().hex[:8])
        replay = build_replay_fixture(mdir)
        dossier = product_research_pipeline.generate_decision_dossier(mdir)
        _out({"schema": "B3_SAFE_REPLAY_DEMO", "scenario_1": replay, "dossier": dossier,
              "scenario_2": {"category": "PETS", "result": "RESEARCH_REQUIRED",
                             "reason": "NO_VERIFIED_PROBLEM_LEDGER", "product_selected": None},
              "disclaimer": "SIMULATION ONLY - NO MARKET CLAIMS OR PAID REQUESTS"})
        return 0

    if args.domain == "extract-corpus":
        raise SystemExit("REFERENCE_CORPUS_IMMUTABLE_IN_INSTALLED_B3; historical source extraction disabled to protect audited files")

    if args.domain == "knowledge":
        _out(corpus_extractor.query_knowledge(ROOT / "knowledge" / "nectar_index.json", ROOT / "reference_corpus", args.query))
        return 0

    if args.domain == "a01":
        mdir = _mission_dir(args.mission)
        mcfg = json.loads((mdir / "mission.json").read_text(encoding="utf-8")) if (mdir / "mission.json").exists() else {}
        lpath = mdir / "AGENT_01" / "problem_ledger.jsonl"
        if args.cmd in ("territories", "jtbd", "collect-web"):
            if mcfg.get("mode") not in ("REPLAY", "FETCH_VERIFIED"):
                raise SystemExit("B3_LIVE_COLLECTION_NOT_SUPPORTED")
            if args.cmd == "territories":
                res = product_research_pipeline.register_territories(mdir, json.loads(args.file.read_text(encoding="utf-8")), provenance_mode=mcfg["mode"])
            elif args.cmd == "jtbd":
                payload = json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise SystemExit("JTBD_PAYLOAD_MUST_BE_OBJECT")
                res = product_research_pipeline.register_jtbd_shortlist(mdir, payload.get("jobs", []), payload.get("not_retained_at_step4", []), provenance_mode=mcfg["mode"])
            else:
                res = product_research_pipeline.collect_public_web_source(mdir, url=args.url, purpose=args.purpose)
            _out(res)
            return 0 if res.get("status", "").startswith("PASS_") else 3
        if args.cmd == "ledger-add":
            if mcfg.get("mode") == "FETCH_VERIFIED":
                raise SystemExit("FETCH_VERIFIED_INGESTION_ONLY_VIA_RESEARCH_RUN_RECEIPTS")
            if mcfg.get("mode") != "REPLAY":
                raise SystemExit("LIVE_INGESTION_NOT_SUPPORTED_IN_B1_UNTIL_TRUSTED_CONNECTOR")
            batch = json.loads(args.file.read_text(encoding="utf-8"))
            _out(ledger.append(lpath, batch, mission_id=args.mission, target_market=mcfg.get("target_sale_market", "FR"), provenance_mode=mcfg.get("mode", "LIVE")))
            return 0
        if args.cmd == "gate2b":
            if mcfg.get("mode") == "FETCH_VERIFIED":      # logique GATE 2B unique du pipeline réel
                from engine import demand
                _out(demand.gate_2b_convergence(ledger.load_merged(lpath), args.candidate))
            else:
                _out(ledger.gate_2b(ledger.load_merged(lpath), args.candidate, target_market=mcfg.get("target_sale_market", "FR"),
                                    allow_replay=mcfg.get("mode") == "REPLAY"))
            return 0
        if args.cmd == "score":
            known = set(ledger.load_by_id(lpath).keys())
            _out(scoring.score_with_evidence(json.loads(args.file.read_text(encoding="utf-8")), known_refs=known))
            return 0

    if args.domain == "a02":
        mdir = _mission_dir(args.mission)
        config_path = mdir / "mission.json"
        if not config_path.exists() or json.loads(config_path.read_text(encoding="utf-8")).get("mode") != "REPLAY":
            raise SystemExit("B1_LIVE_AUDIT_DISABLED_UNTIL_TRUSTED_CONNECTORS")
        rd = lambda p: json.loads(p.read_text(encoding="utf-8"))
        handoff = rd(args.handoff)
        config = rd(config_path)
        if handoff.get("mission_id") != args.mission or handoff.get("target_sale_market") != config.get("target_sale_market"):
            raise SystemExit("AGENT02_HANDOFF_MISSION_OR_MARKET_MISMATCH")
        rep = competitor_intel.run_audit(
            handoff, rd(args.evidence), rd(args.gaps), rd(args.negatives), rd(args.scores), allow_synthetic=args.allow_synthetic
        )
        out_path = mdir / "AGENT_02" / "competitor_audit_report.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _out(rep)
        return 0 if rep["status"] == "READY_FOR_AGENT01_REVIEW_NOT_APPROVED" else 2

    if args.domain == "tt":
        if args.cmd == "owner-keygen":
            if not sys.stdin.isatty():
                raise SystemExit("OWNER_KEYGEN_MUST_BE_RUN_BY_OWNER_IN_OWN_TERMINAL")
            _out(marketpulse_adapter.owner_keygen(args.key_dir.expanduser()))
            return 0
        mdir = _mission_dir(args.mission)
        mcfg_path = mdir / "mission.json"
        mission_mode = json.loads(mcfg_path.read_text(encoding="utf-8")).get("mode") if mcfg_path.exists() else None
        if mission_mode == "FETCH_VERIFIED" and args.mode != marketpulse_adapter.LIVE_MODE:
            raise SystemExit("FETCH_VERIFIED_MISSION_MARKETPULSE_ONLY_MCP_SESSION")
        if args.mode == marketpulse_adapter.LIVE_MODE:
            if mission_mode != "FETCH_VERIFIED":
                raise SystemExit("LIVE_MARKETPULSE_REQUIRES_FETCH_VERIFIED_MISSION")
        elif args.mode not in ("REPLAY", "DISCONNECTED"):
            raise SystemExit("LIVE_MARKETPULSE_DISABLED_UNTIL_PROVIDER_AUTHENTICATION_AND_CONSENT")
        tt = marketpulse_adapter.MarketPulseAdapter(mdir / "MARKETPULSE", mode=args.mode, connected=(args.mode != "DISCONNECTED"),
                                                  owner_pubkey=OWNER_PUBKEY)
        if args.cmd == "approve-live":
            if args.mode != marketpulse_adapter.LIVE_MODE:
                raise SystemExit("APPROVE_LIVE_REQUIRES_MODE_MCP_SESSION")
            if not sys.stdin.isatty():
                raise SystemExit("APPROVE_LIVE_MUST_BE_RUN_BY_OWNER_IN_OWN_TERMINAL")
            path = tt.prepare_live_approval(mission_id=args.mission, scope=args.scope, max_credits=args.max_credits, hours=args.hours,
                                            autonomous=args.autonomous)
            print(path.read_text(encoding="utf-8"))
            print("Signature : openssl va demander la phrase secrète de la clé propriétaire.", file=sys.stderr)
            _out(tt.sign_live_approval(path, args.key.expanduser()))
            return 0
        if args.cmd == "status":
            _out(tt.live_status())
            return 0
        if args.cmd == "resolve-call":
            if args.mode != marketpulse_adapter.LIVE_MODE:
                raise SystemExit("RESOLVE_CALL_REQUIRES_MODE_MCP_SESSION")
            if not sys.stdin.isatty():
                raise SystemExit("RESOLVE_CALL_MUST_BE_RUN_BY_OWNER_IN_OWN_TERMINAL")
            path = tt.prepare_call_resolution(mission_id=args.mission, call_id=args.call, decision=args.decision,
                                              charged_credits=args.charged_credits, justification=args.justification)
            print(path.read_text(encoding="utf-8"))
            print("Signature : openssl va demander la phrase secrète de la clé propriétaire.", file=sys.stderr)
            _out(tt.sign_call_resolution(path, args.key.expanduser()))
            return 0
        if args.cmd == "calibration":
            res = tt.calibration_summary(args.tool)
            _out(res)
            return 0 if res["status"] == "CALIBRATION_CAPTURED" else 3
        if args.cmd == "confirm-fields":
            if args.mode != marketpulse_adapter.LIVE_MODE:
                raise SystemExit("CONFIRM_FIELDS_REQUIRES_MODE_MCP_SESSION")
            if not sys.stdin.isatty():
                raise SystemExit("CONFIRM_FIELDS_MUST_BE_RUN_BY_OWNER_IN_OWN_TERMINAL")
            mapping = dict(m.split("=", 1) for m in args.map if "=" in m)
            path = tt.prepare_field_map(mission_id=args.mission, tool=args.tool, mapping=mapping)
            print(path.read_text(encoding="utf-8"))
            print("Signature : openssl va demander la phrase secrète de la clé propriétaire.", file=sys.stderr)
            _out(tt.sign_field_map(path, args.key.expanduser()))
            return 0
        if args.cmd == "cancel":
            _out(tt.cancel_call(args.call))
            return 0
        if args.cmd == "capabilities":
            res = tt.capabilities()
            _out(res)
            return 0 if res["connected"] else 3
        if args.cmd == "balance":
            _out(tt.ingest_balance(json.loads(args.file.read_text(encoding="utf-8"))))
            return 0
        if args.cmd == "approve-replay":
            _out(tt.record_replay_approval(scope=args.scope, max_credits=args.max_credits, owner_statement=args.statement))
            return 0
        if args.cmd == "plan":
            res = tt.plan_call(mission_id=args.mission, scope=args.scope, recipe_id=args.recipe, country=args.country, stage=args.stage,
                               terms=args.terms, locale=args.locale, linked_domain=args.linked_domain, linked_ad_ref=args.linked_ad_ref,
                               limit=args.limit, page=args.page, lot_id=args.lot)
            _out(res)
            return 0 if res["status"] in ("AWAITING_EXECUTION", "CACHE_HIT") else 3
        if args.cmd == "next":
            res = tt.next_call()
            _out(res)
            return 0 if res["status"] in ("AWAITING_EXECUTION", "CACHE_HIT") else 3
        if args.cmd == "wave":
            res = tt.plan_status()
            _out(res)
            return 0 if res["status"] != "PLAN_INVALID" else 3
        if args.cmd == "report":
            res = tt.build_reports()
            _out(res)
            return 0 if res["status"] == "REPORTS_WRITTEN" else 3
        if args.cmd == "ingest":
            raw = json.loads(args.file.read_text(encoding="utf-8")) if args.file else None
            res = tt.ingest_call(args.call, raw)
            _out(res)
            return 3 if res.get("halted") else 0
        if args.cmd == "reconcile":
            res = tt.reconcile_credits(json.loads(args.file.read_text(encoding="utf-8")) if args.file else None)
            _out(res)
            return 0 if res.get("status") == "RECONCILED" else 3

    if args.domain == "a03":
        if args.cmd == "lint":
            res = creative_center.lint_copy(args.text)
            _out(res)
            return 0 if res["status"] != "BLOCKED" else 3
        mdir = _mission_dir(args.mission)
        if args.cmd == "compile":
            reg = autopilot.CandidateRegistry.load(mdir / "AGENT_01" / "candidate_registry.json", args.mission)
            dossiers = json.loads((mdir / "AGENT_01" / "dossiers.json").read_text(encoding="utf-8"))
            led_by_id = ledger.load_by_id(mdir / "AGENT_01" / "problem_ledger.jsonl")
            a02_rep = json.loads((mdir / "AGENT_02" / "competitor_audit_report.json").read_text(encoding="utf-8"))
            prod = json.loads(args.product.read_text(encoding="utf-8"))
            cand = next((c for c in reg.candidates.values() if c["status"] == "PROPOSED_FOR_COMMERCIAL_SELECTION"), None) or {}
            plan = creative_center.compile_creative_center_plan(
                root_dir=ROOT,
                mission_id=args.mission,
                persona=json.loads((mdir / "mission.json").read_text(encoding="utf-8")).get("persona", "UNKNOWN"),
                target_market=json.loads((mdir / "mission.json").read_text(encoding="utf-8")).get("target_sale_market", "UNKNOWN"),
                candidate_entry=cand,
                dossier=dossiers.get(cand.get("id"), {}),
                ledger_by_id=led_by_id,
                agent02_report=a02_rep,
                product_master=prod,
            )
            (mdir / "AGENT_03").mkdir(parents=True, exist_ok=True)
            (mdir / "AGENT_03" / "creative_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _out(plan)
            return 0 if plan["status"] == "DRAFT_PLAN_READY_FOR_REVIEW" else 3
        if args.cmd == "hf-replay":
            plan = json.loads((mdir / "AGENT_03" / "creative_plan.json").read_text(encoding="utf-8"))
            _out(creative_center.run_higgsfield_replay_cycle(mdir / "AGENT_03", plan))
            return 0

    if args.domain == "a04":
        mdir = _mission_dir(args.mission)
        plan = json.loads((mdir / "AGENT_03" / "creative_plan.json").read_text(encoding="utf-8"))
        prod = json.loads((mdir / "AGENT_03" / "product_master.json").read_text(encoding="utf-8"))
        res = downstream_agents.compile_agent04_shopify_draft(
            root_dir=ROOT,
            mission_id=args.mission,
            creative_plan=plan,
            product_truth={"mission_id": args.mission, "sale_market": "FR", "currency": "EUR", "sku": prod["sku"], "product_name": prod["name"]},
        )
        _out(res)
        return 0

    if args.domain == "a05":
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        result = downstream_agents.compile_agent05_meta_plan(root_dir=ROOT, packet=packet, connector_capabilities={"connected": False})
        _out(result)
        return 0 if result.get("meta_calls") == 0 and result.get("money_spent") == 0 else 3

    if args.domain == "a06":
        _out(downstream_agents.diagnose_agent06_funnel_and_kahel_pillars(json.loads(args.metrics.read_text(encoding="utf-8"))))
        return 0

    if args.domain == "auto":
        mdir = _mission_dir(args.mission)
        rpath = mdir / "AGENT_01" / "candidate_registry.json"
        reg = autopilot.CandidateRegistry.load(rpath, args.mission)
        if args.cmd == "seed":
            autopilot.seed_candidates(reg, json.loads(args.file.read_text(encoding="utf-8")))
            reg.save(rpath)
            _out({"seeded": len(reg.candidates)})
            return 0
        if args.cmd == "step":
            if not (mdir / "mission.json").exists():
                raise SystemExit("MISSION_NOT_INITIALIZED")
            amode = json.loads((mdir / "mission.json").read_text()).get("mode")
            if amode not in ("REPLAY", "FETCH_VERIFIED"):
                raise SystemExit("LIVE_AUTOPILOT_NOT_SUPPORTED_IN_B1")
            dossiers = json.loads(args.dossiers.read_text(encoding="utf-8"))
            if amode == "FETCH_VERIFIED":
                dg = mdir / "AGENT_01" / "dossiers.digest"
                if not dg.exists() or autonomous_research.dossiers_digest(dossiers) != dg.read_text().strip():
                    raise SystemExit("FETCH_VERIFIED_DOSSIERS_MUST_COME_FROM_ENGINE_BUILDER")
                merged = {r["observation_id"]: r for r in ledger.load_merged(mdir / "AGENT_01" / "problem_ledger.jsonl")}
                res = autopilot.step(reg, dossiers, ledger=merged, provenance_mode="FETCH_VERIFIED",
                                     target_market=json.loads((mdir / "mission.json").read_text()).get("target_sale_market", "FR"))
                reg.save(rpath)
                _out(res)
                return 0
            lpath = mdir / "AGENT_01" / "problem_ledger.jsonl"
            a02_path = mdir / "AGENT_02" / "competitor_audit_report.json"
            rk_path = mdir / "AGENT_01" / "jtbd_ranked.json"
            univ = None
            if rk_path.exists():
                rk = json.loads(rk_path.read_text(encoding="utf-8"))
                univ = [j["id"] for j in rk.get("jobs", []) + rk.get("not_retained_at_step4", [])]
            res = autopilot.step(
                reg,
                dossiers,
                ledger=ledger.load_by_id(lpath) if lpath.exists() else None,
                agent02_audits=autopilot.load_agent02_audits(a02_path, expected_mission_id=args.mission, expected_market=json.loads((mdir / "mission.json").read_text()).get("target_sale_market", "FR")),
                universe=univ,
                target_market=json.loads((mdir / "mission.json").read_text()).get("target_sale_market", "FR"),
            )
            reg.save(rpath)
            _out(res)
            return 0
        if args.cmd == "authorize":
            raise SystemExit("B1_OWNER_AUTHORIZATION_DISABLED_UNTIL_VERIFIED_INTERACTIVE_APPROVAL")
        if args.cmd == "operational":
            raise SystemExit("B1_OPERATIONAL_VALIDATION_DISABLED_UNTIL_EVIDENCE_VERIFICATION")
        if args.cmd == "status":
            _out({
                "candidates": {cid: {k: c.get(k) for k in ("title", "stage", "score", "status", "closed_reason", "selected_solution_id", "operational_validation")}
                               for cid, c in reg.candidates.items()},
                "authorization_requests": reg.data["authorization_requests"],
            })
            return 0

    if args.domain in ("demo", "replay"):
        import uuid
        res = build_replay_fixture(_mission_dir("DEMO_REPLAY_B1_" + uuid.uuid4().hex[:8]))
        _out(res)
        return 0
    if args.domain == "demo-b2":
        import uuid
        res = build_b2_full_replay_demo(_mission_dir("DEMO_REPLAY_B2_" + uuid.uuid4().hex[:8]))
        _out(res)
        return 0
    if args.domain == "test":
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        return 0 if result.wasSuccessful() else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
