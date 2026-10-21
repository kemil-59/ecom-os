"""Priorité C (B2) — Préservation et Interfaçage des Agents 04 (Shopify), 05 (Meta) et 06 (Analytics/Kahel).

Réutilise directement les moteurs officiels `agent04_engine_v013.py`, `agent04_psychology_v014.py`
et `agent05_meta_engine_v017.py` extraits dans `reference_corpus/CORE_ECOM_OS/04_MOTEUR/`,
sans jamais prétendre que Shopify ou Meta sont connectés en LIVE.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def _ensure_corpus_moteur_on_path(root_dir: Path) -> Path:
    moteur = root_dir / "reference_corpus" / "CORE_ECOM_OS" / "04_MOTEUR"
    if str(moteur) not in sys.path:
        sys.path.insert(0, str(moteur))
    return moteur


def compile_agent04_shopify_draft(
    *,
    root_dir: Path,
    mission_id: str,
    creative_plan: dict,
    product_truth: dict,
) -> dict:
    """Exécute le compilateur psychologique P0-P5 (v0.14) et l'architecte Shopify OS2 (v0.13)."""
    moteur = _ensure_corpus_moteur_on_path(root_dir)
    import agent04_psychology_v014 as a04_psy  # noqa: E402

    known_refs = set()
    for a in creative_plan.get("angle_cards", []):
        known_refs.update(a.get("source_refs") or [])

    psy_plan = a04_psy.build_customer_decision_plan(product_truth, known_refs, creative_plan)
    theme_dir = moteur / "theme_starter_v013" / "sections"
    liquid_sections = sorted(p.name for p in theme_dir.glob("*.liquid")) if theme_dir.exists() else []

    status = (
        "BLOCKED_INTAKE"
        if psy_plan["hard_blockers"]
        else ("DRAFT_BLUEPRINT_READY_NO_LIVE_SHOPIFY_WRITE" if psy_plan["status"] != "BLOCKED_CONTRADICTION" else "BLOCKED_INTAKE")
    )
    return {
        "schema": "ECOM_OS_AGENT04_DRAFT_B2",
        "mission_id": mission_id,
        "status": status,
        "implementation_mode": "OFFLINE_DRAFT_ONLY_LIVE_SHOPIFY_NOT_CONNECTED",
        "customer_decision_plan": psy_plan,
        "theme_starter_liquid_sections": liquid_sections,
        "reference_pattern_libraries": ["JOLIE_01", "CARAWAY_02", "RIDGE_03", "JONESROAD_04"],
        "governance": {"G1": "UNVERIFIED_OFFLINE", "G2": "UNVERIFIED_OFFLINE", "G3": "NOT_AUTHORIZED_NO_PUBLISH"},
    }


def compile_agent05_meta_plan(
    *,
    root_dir: Path,
    packet: dict,
    connector_capabilities: dict | None = None,
) -> dict:
    """Exécute le compilateur Meta Launch v0.17 (`agent05_meta_engine_v017.py`)."""
    _ensure_corpus_moteur_on_path(root_dir)
    import agent05_meta_engine_v017 as a05  # noqa: E402

    return a05.compile_plan(packet, connector_capabilities or {"connected": False})


def diagnose_agent06_funnel_and_kahel_pillars(metrics: dict) -> dict:
    """Exécute le diagnostic entonnoir en 8 points (Étape 18-20) + diagnostic des 6 piliers Kahel."""
    spend = float(metrics.get("spend_eur") or 0.0)
    target_cac = float(metrics.get("target_cac_eur") or 15.0)
    orders = int(metrics.get("orders") or 0)
    refund_rate = float(metrics.get("refund_rate") or 0.0)
    chargeback_rate = float(metrics.get("chargeback_rate") or 0.0)
    late_delivery_rate = float(metrics.get("late_delivery_rate") or 0.0)
    cash_buffer_ok = bool(metrics.get("cash_buffer_ok", True))

    # Disjoncteur P0 Backend (Kahel Pilier VI)
    hard_stops = []
    if refund_rate > 0.12:
        hard_stops.append("HIGH_REFUND_RATE_PRODUCT_OR_PROMISE_MISMATCH")
    if chargeback_rate > 0.01:
        hard_stops.append("PSP_CHARGEBACK_DANGER")
    if late_delivery_rate > 0.15:
        hard_stops.append("FULFILLMENT_SLA_BREACH")
    if not cash_buffer_ok:
        hard_stops.append("INSUFFICIENT_CASHFLOW_BUFFER")

    if hard_stops:
        return {
            "schema": "ECOM_OS_AGENT06_DIAGNOSIS_B2",
            "decision": "STOP_AND_REPAIR_BACKEND_P0",
            "primary_bottleneck": "BACKEND_OR_PRODUCT_QUALITY",
            "hard_stops": hard_stops,
            "scale_allowed": False,
        }

    # Échantillon insuffisant (< 3x Target CAC et < 5 commandes)
    if spend < target_cac * 3 and orders < 5:
        return {
            "schema": "ECOM_OS_AGENT06_DIAGNOSIS_B2",
            "decision": "INCONCLUSIVE_INSUFFICIENT_SAMPLE",
            "primary_bottleneck": "UNKNOWN_SAMPLE_TOO_SMALL",
            "spend_eur": spend,
            "minimum_recommended_spend_eur": round(target_cac * 10, 2),
            "scale_allowed": False,
        }

    ctr = float(metrics.get("link_ctr") or 0.0)
    lpv_rate = float(metrics.get("lpv_per_click") or 0.0)
    atc_rate = float(metrics.get("atc_rate") or 0.0)
    cvr = float(metrics.get("cvr") or 0.0)
    actual_cac = round(spend / orders, 2) if orders > 0 else 999.0
    distinct_winning_concepts = int(metrics.get("winning_concepts_count") or 0)

    if ctr < 0.008:
        bottleneck, step_fix = ("CREATIVE_HOOK", "Étape 18.1 : Peu d'attention/clic -> retravailler Hook & Angle (Pilier III Creative)")
    elif lpv_rate < 0.70:
        bottleneck, step_fix = ("LANDING_LOAD_OR_MISMATCH", "Étape 18.3 : Clics mais peu de vues LP -> vérifier vitesse mobile et Message Match (Pilier V)")
    elif atc_rate < 0.04:
        bottleneck, step_fix = ("OFFER_OR_PROOF_ON_PDP", "Étape 18.4 : Vues LP mais peu d'ATC -> renforcer démonstration, offre et preuve sur la PDP (Pilier V)")
    elif cvr < 0.015:
        bottleneck, step_fix = ("CHECKOUT_FRICTION", "Étape 18.5/18.6 : ATC mais abandon checkout -> vérifier frais de port, délais et réassurance (Pilier V)")
    elif actual_cac > target_cac:
        bottleneck, step_fix = ("UNIT_ECONOMICS_AOV_OR_CREATIVE_EFFICIENCY", "Étape 18.7 : Achats présents mais CAC > Target CAC -> optimiser bundles/AOV et itérer hooks gagnants")
    else:
        bottleneck, step_fix = ("NONE_TARGET_CAC_MET", "Étape 18.8 / Étape 20 : CAC cible atteint")

    can_scale = (
        bottleneck == "NONE_TARGET_CAC_MET"
        and orders >= 20
        and distinct_winning_concepts >= 2
    )
    decision = "SCALE_WITH_CAP_REQUIRES_G3" if can_scale else ("ITERATE" if orders > 0 else "KILL_OR_PIVOT_ANGLE")
    return {
        "schema": "ECOM_OS_AGENT06_DIAGNOSIS_B2",
        "decision": decision,
        "primary_bottleneck": bottleneck,
        "diagnostic_explanation": step_fix,
        "actual_cac_eur": actual_cac,
        "target_cac_eur": target_cac,
        "scale_allowed": can_scale,
        "why_two_concepts_required": "Étape 20 : au moins 2 concepts créatifs distincts doivent générer des ventes avant un test de scale.",
    }
