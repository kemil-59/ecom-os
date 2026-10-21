"""Agent 01 — Boîte Économique avec Besoin de Trésorerie (Étape 1) et Market Score /40 sourcé (Étape 5) (B3).

Respecte strictement le processus maître, Kahel (waterfall de trésorerie) et `AUTONOMIE_DECISIONNELLE_V024.md` :
- 8 dimensions notées de 0 à 5 (/40), chacune obligatoirement reliée à des références de preuves existantes.
- Calcul complet de l'enveloppe économique incluant TVA, COGS, logistique, frais, retours, CAC cible,
  scénario pessimiste (Bear Case) et besoin de trésorerie (Working Capital : stock MOQ + décalage PSP + flottant pub).
"""
from __future__ import annotations

EIGHT_AXES = (
    "persona_fit",
    "frequency",
    "intensity",
    "existing_spend",
    "dissatisfaction",
    "market_depth",
    "creative_demonstrability",
    "range_ltv",
)


def compute_economic_envelope(
    *,
    monthly_revenue_target_eur: float = 60000.0,
    aov_ttc_eur: float = 80.0,
    vat_rate: float = 0.20,
    cogs_eur: float = 10.0,
    freight_customs_eur: float = 5.0,
    packaging_eur: float = 1.0,
    fulfillment_shipping_eur: float = 2.0,
    payment_fees_eur: float = 1.5,
    returns_sav_provision_eur: float = 1.5,
    target_net_margin_eur: float = 30.0,
    estimated_cvr: float = 0.02,
    initial_moq_units: int = 200,
    psp_payout_delay_days: int = 7,
    psp_reserve_rate: float = 0.05,
) -> dict:
    aov_ht = round(aov_ttc_eur / (1.0 + vat_rate), 2)
    variable_costs = round(
        cogs_eur
        + freight_customs_eur
        + packaging_eur
        + fulfillment_shipping_eur
        + payment_fees_eur
        + returns_sav_provision_eur,
        2,
    )
    break_even_cac = round(aov_ht - variable_costs, 2)
    target_cac = round(break_even_cac - target_net_margin_eur, 2)
    safety_ratio = round(break_even_cac / target_cac, 2) if target_cac > 0 else 0.0

    # Bear case: CAC +30%, CVR -20%, variable costs +10%
    bear_costs = round(variable_costs * 1.10, 2)
    bear_cac = round(target_cac * 1.30, 2)
    bear_margin = round(aov_ht - bear_costs - bear_cac, 2)

    orders_per_month = round(monthly_revenue_target_eur / aov_ttc_eur, 1) if aov_ttc_eur > 0 else 0.0
    daily_orders = orders_per_month / 30.0

    # Besoin de trésorerie (Working Capital)
    unit_landed_cost = round(cogs_eur + freight_customs_eur + packaging_eur, 2)
    stock_advance_eur = round(initial_moq_units * unit_landed_cost, 2)
    media_float_eur = round(daily_orders * max(target_cac, 0.0) * psp_payout_delay_days, 2)
    psp_hold_reserve_eur = round(monthly_revenue_target_eur * psp_reserve_rate, 2)
    validation_test_budget_eur = round(max(target_cac, 15.0) * 15.0, 2)
    total_working_capital_eur = round(
        stock_advance_eur + media_float_eur + psp_hold_reserve_eur + validation_test_budget_eur, 2
    )

    return {
        "aov_ttc_eur": aov_ttc_eur,
        "vat_rate": vat_rate,
        "aov_ht_eur": aov_ht,
        "variable_costs_ex_ad_eur": variable_costs,
        "break_even_cac_eur": break_even_cac,
        "target_net_margin_eur": target_net_margin_eur,
        "target_cac_eur": target_cac,
        "cac_safety_ratio": safety_ratio,
        "orders_needed_monthly": orders_per_month,
        "estimated_cvr": estimated_cvr,
        "bear_case": {
            "variable_costs_eur": bear_costs,
            "cac_eur": bear_cac,
            "net_margin_per_order_eur": bear_margin,
            "survivable": bear_margin >= 0 and target_cac > 0,
        },
        "working_capital_requirement": {
            "initial_moq_units": initial_moq_units,
            "stock_advance_eur": stock_advance_eur,
            "psp_payout_delay_days": psp_payout_delay_days,
            "media_float_during_payout_delay_eur": media_float_eur,
            "psp_rolling_reserve_eur": psp_hold_reserve_eur,
            "initial_validation_ad_budget_eur": validation_test_budget_eur,
            "total_working_capital_needed_eur": total_working_capital_eur,
        },
        "gate1_status": "PASS_ON_ASSUMPTIONS" if (target_cac > 0 and bear_margin >= 0) else "RED_UNVIABLE_ENVELOPE",
        "below_aov_reference_80": aov_ttc_eur < 80.0,
    }


def solution_economics_screen(
    *,
    observed_price_ttc_eur: float,
    vat_rate: float = 0.20,
    non_cogs_variable_costs_eur: float = 11.0,
    target_cac_eur: float = 15.0,
    optimistic_cac_eur: float = 10.0,
    sourced_cogs_eur: float | None = None,
    cogs_source: str = "HYPOTHESIS",
    price_source_refs: list[str] | None = None,
) -> dict:
    """Écran économique historique, utilisé uniquement par les fixtures REPLAY. Jamais appelé par la
    recherche d'opportunités FETCH_VERIFIED (décision financière = propriétaire, hors moteur)."""
    if not price_source_refs:
        return {"status": "INSUFFICIENT_DATA", "reason": "NO_PRICE_SOURCE_REFS"}
    aov_ht = round(observed_price_ttc_eur / (1.0 + vat_rate), 2)
    margin_at_zero_cogs = round(aov_ht - non_cogs_variable_costs_eur, 2)
    ratio_at_zero_cogs = round(margin_at_zero_cogs / optimistic_cac_eur, 2) if optimistic_cac_eur > 0 else 0.0

    if margin_at_zero_cogs < optimistic_cac_eur:
        return {
            "status": "RED_DEFICIT_AT_ZERO_COGS",
            "margin_at_zero_cogs": margin_at_zero_cogs,
            "ratio_at_zero_cogs": ratio_at_zero_cogs,
            "safety_ratio": ratio_at_zero_cogs,
            "cogs_source": cogs_source,
        }

    if sourced_cogs_eur is not None:
        margin_after_cogs = round(margin_at_zero_cogs - sourced_cogs_eur, 2)
        safety_ratio = round(margin_after_cogs / target_cac_eur, 2) if target_cac_eur > 0 else 0.0
        if cogs_source in ("SUPPLIER_QUOTE", "PUBLIC_LISTING") and safety_ratio < 1.0:
            status = "RED_SOURCED_COGS_BELOW_TARGET_CAC"
        elif safety_ratio < 1.3:
            status = "WARN_TIGHT_MARGIN"
        else:
            status = "OK"
    else:
        hypothetical_cogs = round(observed_price_ttc_eur * 0.20, 2)
        margin_after_cogs = round(margin_at_zero_cogs - hypothetical_cogs, 2)
        safety_ratio = round(margin_after_cogs / target_cac_eur, 2) if target_cac_eur > 0 else 0.0
        status = "OK" if safety_ratio >= 1.2 else "WARN_UNQUOTED_TIGHT"

    return {
        "status": status,
        "observed_price_ttc_eur": observed_price_ttc_eur,
        "aov_ht_eur": aov_ht,
        "margin_at_zero_cogs": margin_at_zero_cogs,
        "ratio_at_zero_cogs": ratio_at_zero_cogs,
        "safety_ratio": safety_ratio,
        "cogs_source": cogs_source,
        "price_source_refs": list(price_source_refs),
        "below_aov_reference": observed_price_ttc_eur < 80.0,
    }


# Barème /40 NON FINANCIER de la recherche d'opportunités (8 axes × 0..5, présélection >= 28).
# Remplace `existing_spend` et `range_ltv` (financiers) par l'adoption de solutions et la récurrence d'ACHAT.
RESEARCH_AXES = (
    "persona_fit",
    "frequency",
    "intensity",
    "dissatisfaction",
    "solution_adoption",
    "purchase_recurrence",
    "market_depth",
    "creative_demonstrability",
)
# Classes d'observation admissibles par axe (observations uniquement ; le jugement analytique n'est jamais admis).
_TESTIMONY = {"FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND"}
AXIS_KINDS = {
    "persona_fit": _TESTIMONY, "frequency": _TESTIMONY, "intensity": _TESTIMONY,
    "dissatisfaction": _TESTIMONY | {"SATISFACTION"},
    # Comportement observé : la personne achète/utilise déjà des solutions pour ce problème (pas de prix).
    "solution_adoption": {"SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND", "SATISFACTION", "REPEAT_PURCHASE"},
    # Réachat / réapprovisionnement témoigné du produit ou de ses consommables (pas un simple usage répété).
    "purchase_recurrence": {"REPEAT_PURCHASE"},
    # Compteur observé sur une page FR (nombre d'avis/réponses) ; jamais des ventes ni une estimation tierce.
    "market_depth": {"INDEX_COUNT", "PLATFORM_REPORTED"},
    # Démonstration visuelle publiée et observée.
    "creative_demonstrability": {"DEMO_OBSERVED"},
}
MISSING_EVIDENCE = {
    "persona_fit": "témoignages FR rattachés au candidat", "frequency": "témoignages FR décrivant la récurrence du problème",
    "intensity": "témoignages FR (frustration ou défaut de solution)", "dissatisfaction": "défauts ou satisfactions observés",
    "solution_adoption": "témoignage d'utilisation ou d'achat de solutions existantes",
    "purchase_recurrence": "témoignage FR vérifié de réachat / réapprovisionnement (REPEAT_PURCHASE)",
    "market_depth": "compteur observé (nombre d'avis/réponses, INDEX_COUNT) ; une estimation tierce n'est jamais admise ici",
    "creative_demonstrability": "démonstration visuelle publiée observée (DEMO_OBSERVED) ; le jugement analytique ne suffit pas",
}


def score_with_evidence(axes_payload: dict, known_refs: set[str] | None = None, *, allow_replay: bool = True,
                        ref_index: dict[str, dict] | None = None, candidate_id: str | None = None,
                        axes: tuple = EIGHT_AXES) -> dict:
    """Valide que chaque axe 0..5 possède des `evidence_refs` non vides existant dans `known_refs` et une justification.

    Avec `ref_index` (mode FETCH_VERIFIED, barème `RESEARCH_AXES`) : chaque référence doit être une ligne
    FETCH_VERIFIED, rattachée au candidat et d'une classe admissible pour l'axe. Sinon l'axe est NON_EVALUABLE.
    """
    inadmissible = []
    if ref_index is not None:
        known_refs = set(ref_index)
        for ax, entry in list((axes_payload or {}).items()):
            if ax not in axes:
                continue
            refs = entry.get("evidence_refs") or [] if isinstance(entry, dict) else []
            for r in refs:
                o = ref_index.get(r) or {}
                linked = candidate_id in (o.get("candidate_ids") or [])
                if o.get("provenance_mode") != "FETCH_VERIFIED" or not linked or o.get("kind") not in AXIS_KINDS.get(ax, set()):
                    inadmissible.append(f"{ax}:INADMISSIBLE_REF:{r}")
    missing, unsupported, scores = [], [], {}
    for ax in axes:
        entry = (axes_payload or {}).get(ax)
        if not isinstance(entry, dict) or "score" not in entry:
            missing.append(ax)
            continue
        val = entry["score"]
        refs = [r for r in (entry.get("evidence_refs") or []) if isinstance(r, str) and r.strip()]
        just = str(entry.get("justification") or "").strip()
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not (0 <= val <= 5):
            raise ValueError(f"INVALID_AXIS_SCORE:{ax}={val}")
        if not refs or len(just) < 8:
            unsupported.append(ax)
        elif known_refs is not None and any(r not in known_refs for r in refs):
            unsupported.append(f"{ax}:UNKNOWN_REF")
        scores[ax] = float(val)

    if known_refs is None:
        unsupported.append("REFERENCE_REGISTRY_REQUIRED")
    unsupported.extend(inadmissible)

    if missing or unsupported:
        return {
            "status": "REJECTED_UNSUPPORTED_SCORE",
            "evaluability": "NON_EVALUABLE",
            "missing_evidence": {ax.split(":")[0]: MISSING_EVIDENCE.get(ax.split(":")[0], "") for ax in missing + unsupported
                                 if ax.split(":")[0] in MISSING_EVIDENCE},
            "score": None,
            "missing_axes": missing,
            "unsupported_axes": unsupported,
        }

    total = round(sum(scores.values()), 2)
    return {
        "status": "SCORED_WITH_EVIDENCE",
        "score": total,
        "max_score": 40,
        "shortlist_pass": total >= 28.0,
        "axes": axes_payload,
        "allow_replay": allow_replay,
    }


# --- Méthode active : 8 dimensions historiques du processus maître (ÉTAPE 5 « Market Score ») ---
# Fit Persona_A, Fréquence, Intensité, Dépense existante, Insatisfaction actuelle, Taille/profondeur marché,
# Démontrabilité publicitaire, Potentiel gamme/LTV. « Dépense existante » = comportement observé d'achat/remplacement
# d'une solution nommée ; « gamme/LTV » = réachat témoigné (un avantage, pas un veto). Chaque note est PLAFONNÉE par le
# NIVEAU DE PREUVE du candidat (engine/demand.py) : 1/5 pour une hypothèse, 5/5 seulement si le marché significatif est
# documenté par les deux voies (témoignages ET statistique). Le score ne contourne jamais les conditions de preuve.
SCORE_AXES_V2 = EIGHT_AXES
AXIS_KINDS_V2 = {
    "persona_fit": _TESTIMONY, "frequency": _TESTIMONY, "intensity": _TESTIMONY,
    "existing_spend": {"SOLUTION_DEFECT_SPEND", "REPEAT_PURCHASE", "SOLUTION_DEFECT", "SATISFACTION"},
    "dissatisfaction": _TESTIMONY | {"SATISFACTION"},
    "market_depth": {"INDEX_COUNT", "PLATFORM_REPORTED"},
    "creative_demonstrability": {"DEMO_OBSERVED"},
    "range_ltv": {"REPEAT_PURCHASE"},
}
MISSING_EVIDENCE_V2 = {
    **MISSING_EVIDENCE,
    "persona_fit": "témoignages dont l'auteur déclare appartenir à la cible (déclaration vérifiée par le moteur dans son bloc)",
    "existing_spend": "témoignage d'achat, de remplacement ou de réachat d'une solution nommée (aucun montant collecté)",
    "range_ltv": "témoignage FR vérifié de réachat / réapprovisionnement (REPEAT_PURCHASE)",
}


def _qualified(o: dict) -> bool:
    return (o.get("geo_basis") in ("RESIDENCE_DECLARED", "FR_MARKET_SITE") and o.get("spontane") is True
            and o.get("market_geo") == "FR")


def score_with_evidence_v2(axes_payload: dict, *, ref_index: dict, candidate_id: str, thresholds: dict,
                           persona_mode: str = "PROFILE", level: str = "HYPOTHESIS_DETECTED",
                           both_paths: bool = False) -> dict:
    from .demand import score_cap
    caps_by_level = thresholds["score_caps_by_level"]
    level_cap = caps_by_level["SIGNIFICANT_MARKET_BOTH_PATHS"] if (level == "SIGNIFICANT_MARKET_DOCUMENTED" and both_paths) \
        else caps_by_level.get(level, 1)
    depth_ladder = thresholds["market_depth_caps_by_index_count"]
    missing, unsupported, capped, scores, caps = [], [], {}, {}, {}
    for ax in SCORE_AXES_V2:
        entry = (axes_payload or {}).get(ax)
        if not isinstance(entry, dict) or "score" not in entry:
            missing.append(ax)
            continue
        val = entry["score"]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not (0 <= val <= 5):
            raise ValueError(f"INVALID_AXIS_SCORE:{ax}={val}")
        refs = [r for r in (entry.get("evidence_refs") or []) if isinstance(r, str) and r.strip()]
        if not refs or len(str(entry.get("justification") or "").strip()) < 8:
            unsupported.append(ax)
            continue
        rows, bad = [], []
        for r in refs:
            o = ref_index.get(r)
            ok = (o is not None and o.get("provenance_mode") == "FETCH_VERIFIED"
                  and candidate_id in (o.get("candidate_ids") or []) and o.get("kind") in AXIS_KINDS_V2[ax])
            if ok and ax == "persona_fit":
                # Profil imposé : déclaration dans la cible ; recherche générale : attributs déclarés (segment découvert).
                ok = (o.get("persona_basis") in ("SELF_DECLARED_MATCH", "SELF_DECLARED_PARTIAL") if persona_mode == "PROFILE"
                      else bool(o.get("declared_attributes")))
            if ok and ax == "existing_spend":
                ok = bool(str(o.get("incumbent") or "").strip())
            (rows if ok else bad).append(o if ok else r)
        if bad:
            unsupported.append(f"{ax}:INADMISSIBLE_REF:{','.join(map(str, bad))}")
            continue
        if ax == "market_depth":
            cap = min(score_cap(max(int(o.get("count") or 0) for o in rows), depth_ladder), level_cap)
        else:
            cap = level_cap
        caps[ax] = cap
        scores[ax] = float(min(val, cap))
        if val > cap:
            capped[ax] = {"claimed": val, "allowed": cap}
    if missing or unsupported:
        return {"status": "REJECTED_UNSUPPORTED_SCORE", "evaluability": "NON_EVALUABLE", "score": None,
                "axes_version": "MASTER_8_AXES_V2", "missing_axes": missing, "unsupported_axes": unsupported,
                "missing_evidence": {a.split(":")[0]: MISSING_EVIDENCE_V2.get(a.split(":")[0], "") for a in missing + unsupported},
                "caps": caps, "capped_axes": capped}
    total = round(sum(scores.values()), 2)
    return {"status": "SCORED_WITH_EVIDENCE", "score": total, "max_score": 40, "shortlist_pass": total >= 28.0,
            "axes_version": "MASTER_8_AXES_V2", "axes": scores, "caps": caps, "capped_axes": capped,
            "evidence_level": level, "thresholds_status": thresholds.get("status")}
