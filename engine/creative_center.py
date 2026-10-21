"""Priorité B (B2) — Moteur Agent 03 Creative Center & Gouverneur Higgsfield.

Construit à partir des ressources officielles M03, M04, Kahel et Higgsfield :
- Vérifie en entrée la cohérence mission -> candidat sélectionné (Gate 7) -> audit Agent 02 signé
  -> empreinte matérielle (`material_fingerprint`) du produit et droits des médias de référence.
- Génère :
  1. Matrice `Avatar × Problème (JTBD) × Motivator (Core Desire) × Awareness × Sophistication`
  2. Angles et hooks distincts (3 à 5 par angle) reliés aux verbatims `OBS-XXXX`
  3. Concepts publicitaires (`NEW_MESSAGE`, `STYLE_VARIATION`, `SINGLE_VARIABLE_ITERATION`)
     et routage des formats (bloquant démo/comparaison si `sample_status != 'SAMPLE_TESTED'`)
  4. Briefs statiques (`STATIC_AD_DESIGN_SPEC`) et vidéos (`VIDEO_SHOT_PLAN` avec budget de mots FR)
  5. Sélection parmi les 45 recettes caméra canoniques Higgsfield (résolution de l'alias `HF-CAM-P02-03` -> `HF-CAM-P01-11`
     et normalisation physique `Pedestal` = `vertical_translation`)
  6. Prompts exploitables par Higgsfield + routage modèle (`flux_3_image`, `seedance_2_0`, `marketing_studio_*`)
  7. Contrôles `lint_copy` (médical, résultats garantis, superlatifs non prouvés, fausse urgence, faux témoignage, âgisme)
  8. Plan d'expérimentation des angles et références explicites aux études *Gymshark, Smooche, Naali, Grüns, Nuzzle,
     Caraway, Jolie, Ridge, Jones Road*.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from .autopilot import solution_material_fingerprint
from .competitor_intel import solution_fingerprint

AWARENESS_LEVELS = ("unaware", "problem_aware", "solution_aware", "product_aware", "most_aware")

AWARENESS_PLAYBOOK = {
    "unaware": {
        "job": "Faire reconnaître une situation vécue avant de parler produit",
        "product_reveal": "LATE",
        "formats": ("NATIVE_STATIC", "UGC_STORY", "SITUATION_STATIC"),
        "levers": ("PATTERN_INTERRUPT", "OPEN_LOOP", "EMOTIONAL_RELEVANCE"),
    },
    "problem_aware": {
        "job": "Nommer le problème avec les mots exacts de la cliente, montrer la conséquence et introduire le mécanisme",
        "product_reveal": "MID",
        "formats": ("PROBLEM_SOLUTION_STATIC", "UGC_PROBLEM_SOLUTION", "DEMO_VIDEO"),
        "levers": ("EMOTIONAL_RELEVANCE", "QUESTION_CALLOUT", "NEGATIVE_REFRAME", "OPEN_LOOP"),
    },
    "solution_aware": {
        "job": "Différencier le mécanisme physique face aux solutions classiques qui échouent (gap Agent 02)",
        "product_reveal": "EARLY",
        "formats": ("COMPARISON_STATIC", "DEMO_VIDEO", "PRODUCT_HERO_VIDEO", "PROBLEM_SOLUTION_STATIC"),
        "levers": ("DEMONSTRATION_FIRST", "NEGATIVE_REFRAME", "OPEN_LOOP", "QUESTION_CALLOUT"),
    },
    "product_aware": {
        "job": "Lever les objections résiduelles avec des preuves vérifiées",
        "product_reveal": "IMMEDIATE",
        "formats": ("PROOF_STATIC", "DEMO_VIDEO", "DETAIL_STATIC"),
        "levers": ("DEMONSTRATION_FIRST", "QUESTION_CALLOUT", "EMOTIONAL_RELEVANCE"),
    },
    "most_aware": {
        "job": "Rappeler l'offre réelle vérifiée en boutique sans fausse urgence",
        "product_reveal": "IMMEDIATE",
        "formats": ("OFFER_STATIC", "PRODUCT_HERO_VIDEO"),
        "levers": ("DEMONSTRATION_FIRST",),
    },
}

SOPHISTICATION_MAP = {
    1: "DIRECT_PROMISE",
    2: "AMPLIFIED_PROMISE_WITH_PROOF",
    3: "NEW_MECHANISM",
    4: "IMPROVED_MECHANISM",
    5: "IDENTIFICATION_EXPERIENCE",
}

FORMATS_META = {
    "NATIVE_STATIC": ("image", False, False, "4:5"),
    "SITUATION_STATIC": ("image", False, False, "4:5"),
    "PROBLEM_SOLUTION_STATIC": ("image", False, False, "4:5"),
    "COMPARISON_STATIC": ("image", True, False, "4:5"),
    "PROOF_STATIC": ("image", True, False, "4:5"),
    "DETAIL_STATIC": ("image", False, False, "4:5"),
    "OFFER_STATIC": ("image", False, False, "4:5"),
    "PRODUCT_HERO_VIDEO": ("video", False, False, "9:16"),
    "DEMO_VIDEO": ("video", True, False, "9:16"),
    "UGC_PROBLEM_SOLUTION": ("video", False, True, "9:16"),
    "UGC_STORY": ("video", False, True, "9:16"),
}

SHOT_OBJECTIVE_TO_CAMERA = {
    "hook_interrupt": ("HF-CAM-P02-05", "HF-CAM-P01-17", "HF-CAM-P01-13"),
    "problem_scene": ("HF-CAM-P01-13", "HF-CAM-P02-03", "HF-CAM-P01-01"),
    "product_intro": ("HF-CAM-P01-10", "HF-CAM-P01-12", "HF-CAM-P02-01"),
    "hands_on_demo": ("HF-CAM-P01-14", "HF-CAM-P01-06", "HF-CAM-P01-01"),
    "detail_proof": ("HF-CAM-P02-04", "HF-CAM-P01-04", "HF-CAM-P01-06"),
    "product_hero": ("HF-CAM-P01-01", "HF-CAM-P02-10", "HF-CAM-P02-14"),
    "cta_end": ("HF-CAM-P01-01", "HF-CAM-P02-14"),
}

COPY_LINT_RULES = [
    ("MEDICAL_OR_CURE", r"\b(gu[eé]ri[rst]?|soigne|traite(ment)?|cliniquement|m[eé]dical|docteur|m[eé]decin|th[eé]rapeut)", "BLOCK"),
    ("GUARANTEED_RESULT", r"(\bgaranti[es]?\b|\b100\s?%|\br[eé]sultats? assur[eé]s?\b|\bmiracle\b|\bd[eé]finitivement\b)", "BLOCK"),
    ("UNPROVEN_SUPERLATIVE", r"\b(le meilleur|la meilleure|n°\s?1|num[eé]ro un|unique au monde|r[eé]volutionnaire)\b", "NEEDS_CLAIM"),
    ("NUMBER_OR_STAT", r"\b\d+([.,]\d+)?\s?(%|(fois|x|kg|heures?|jours?|min)\b)", "NEEDS_CLAIM"),
    ("FAKE_URGENCY", r"(derni[eè]res? (pi[eè]ces|heures)|stock limit[eé]|plus que \d+|aujourd'hui seulement|offre expire)", "NEEDS_OFFER_FACT"),
    ("PRICE_OR_PROMO", r"(\d+\s?€|-\s?\d+\s?%|promo|r[eé]duction|soldes|gratuit)", "NEEDS_OFFER_FACT"),
    ("FIRST_PERSON_TESTIMONY", r"\b(j'ai test[eé]|je l'utilise|depuis que j'(ai|utilise)|ma peau|mon r[eé]sultat)\b", "NEEDS_REAL_CUSTOMER"),
    ("AGE_REMINDER", r"(apr[eè]s (40|45|50) ans|[aà] votre [aâ]ge|senior|vieillir|vieillissement|vieille)", "WARN"),
]

NECTAR_CASE_REFERENCES = {
    "M04-SMOOCHE": "Matrice 2 avatars × angles distincts + démonstration immédiate de la Star Feature qui inverse l'objection principale",
    "M04-GYMSHARK": "Segmentation fine des sous-avatars et occasions d'usage + distribution créative par sous-segment",
    "M04-NAALI": "Différenciation par mécanisme/composant héros identifiable et alignement culturel FR",
    "M04-GRUNS": "Architecture d'offre par habitude quotidienne et empilement de preuves",
    "M04-NUZZLE": "Séquence problème douloureux -> démonstration mécanique -> offre bundle progressive",
    "KAHEL-6D": "Formule 6 dimensions (FORMAT + CONCEPT + ANGLE + AVATAR + CONTENT + COPYWRITING) + isolation Hook vs Body",
    "STORE-PATTERNS": "Continuité ad-to-PDP inspirée de Jolie (hero simple), Caraway (bundles clairs), Ridge (vue technique numérotée) et Jones Road (choix assisté)",
}


def _short_id(prefix: str, *parts) -> str:
    h = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{h}"


def load_camera_catalog(root_dir: Path) -> dict:
    candidates = [
        root_dir / "reference_corpus" / "CORE_ECOM_OS" / "02_BASE_DE_CONNAISSANCES" / "Higgsfield_camera_45_recettes.json",
        root_dir.parent / "ECOM_OS_CLEAN_B1" / "reference_corpus" / "CORE_ECOM_OS" / "02_BASE_DE_CONNAISSANCES" / "Higgsfield_camera_45_recettes.json",
    ]
    for p in candidates:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
            alias_map = raw.get("alias_map", {"HF-CAM-P02-03": "HF-CAM-P01-11"})
            canonical = {e["id"]: e for e in entries if e["id"] not in alias_map}
            if len(entries) != 46 or len(canonical) != 45:
                raise ValueError("INVALID_HIGGSFIELD_CAMERA_CATALOG_COUNTS")
            return {"entries": {e["id"]: e for e in entries}, "canonical": canonical, "alias_map": alias_map}
    raise FileNotFoundError("Higgsfield_camera_45_recettes.json introuvable dans reference_corpus/")


def select_camera_recipe(objective: str, catalog: dict, requested_id: str | None = None) -> dict:
    alias_map = catalog["alias_map"]
    entries = catalog["entries"]
    raw_id = requested_id or SHOT_OBJECTIVE_TO_CAMERA.get(objective, ("HF-CAM-P01-01",))[0]
    canonical_id = alias_map.get(raw_id, raw_id)
    entry = entries[canonical_id]
    physical_cat = (
        "vertical_translation"
        if canonical_id in ("HF-CAM-P02-01", "HF-CAM-P02-02")
        else entry.get("motion_mechanics")
    )
    return {
        "requested_id": raw_id,
        "canonical_id": canonical_id,
        "is_alias": raw_id != canonical_id,
        "source_title": entry["source_title"],
        "source_category": entry["source_category"],
        "physical_mechanics": physical_cat,
        "operational_definition_fr": entry["operational_definition_fr"],
        "core_constraints_fr": entry["core_constraints_fr"],
        "qa_reject_if_fr": entry["qa_reject_if_fr"],
        "production_complexity": entry["production_complexity"],
    }


def lint_copy(
    text: str,
    *,
    approved_claims: list[dict] | None = None,
    offer: dict | None = None,
    first_person_allowed: bool = False,
    max_words: int | None = 12,
) -> dict:
    findings = []
    approved = [
        c for c in (approved_claims or [])
        if c.get("review_status") == "APPROVED" and c.get("evidence_refs")
    ]
    low = (text or "").lower()
    for code, pat, sev in COPY_LINT_RULES:
        for m in re.finditer(pat, low, re.I):
            frag = m.group(0)
            if sev == "NEEDS_CLAIM" and any(frag in (c.get("text") or "").lower() for c in approved):
                continue
            if sev == "NEEDS_OFFER_FACT" and (offer or {}).get("verified_in_store") and frag in str((offer or {}).get("copy_allowed", "")).lower():
                continue
            if sev == "NEEDS_REAL_CUSTOMER" and first_person_allowed:
                continue
            findings.append({
                "code": code,
                "fragment": frag,
                "severity": "WARN" if sev == "WARN" else "BLOCK",
            })
    words = len((text or "").split())
    if max_words and words > max_words:
        findings.append({"code": "TOO_LONG", "fragment": f"{words}>{max_words} mots", "severity": "WARN"})
    blocked = any(f["severity"] == "BLOCK" for f in findings)
    return {
        "status": "BLOCKED" if blocked else ("PASS_WITH_WARNINGS" if findings else "PASS"),
        "words": words,
        "findings": findings,
    }


def validate_product_master(product: dict, expected_solution: dict) -> list[str]:
    issues = []
    if not isinstance(product, dict):
        return ["PRODUCT_NOT_OBJECT"]
    for k in ("sku", "name", "solution_id", "immutable_attributes", "reference_media"):
        if not product.get(k):
            issues.append(f"PRODUCT_MISSING_{k.upper()}")
    if product.get("solution_id") != expected_solution.get("id"):
        issues.append("PRODUCT_SOLUTION_ID_MISMATCH")

    # Vérification stricte de la correspondance matérielle avec la solution validée au Gate 7
    expected_mat_fp = solution_material_fingerprint(expected_solution)
    if product.get("material_fingerprint") and product["material_fingerprint"] != expected_mat_fp:
        issues.append("PRODUCT_MATERIAL_FINGERPRINT_MISMATCH")
    expected_mats = (expected_solution.get("sku_spec") or {}).get("materials")
    if expected_mats and product.get("materials") and list(product["materials"]) != list(expected_mats):
        issues.append("PRODUCT_MATERIALS_MISMATCH_WITH_SOLUTION")

    refs = product.get("reference_media") or []
    views = {r.get("view") for r in refs if isinstance(r, dict)}
    if "front" not in views:
        issues.append("PRODUCT_FRONT_REFERENCE_VIEW_MISSING")
    for r in refs:
        if r.get("rights") not in ("OWNED", "LICENSED", "SUPPLIER_AUTHORIZED"):
            issues.append(f"PRODUCT_MEDIA_RIGHTS_UNVERIFIED:{r.get('ref_id')}")
        if not r.get("media_id") or str(r.get("media_id")).startswith("http"):
            issues.append(f"PRODUCT_MEDIA_ID_MUST_BE_UPLOADED_UUID_NOT_URL:{r.get('ref_id')}")
    return sorted(set(issues))


def compile_creative_center_plan(
    *,
    root_dir: Path,
    mission_id: str,
    persona: str,
    target_market: str,
    candidate_entry: dict,
    dossier: dict,
    ledger_by_id: dict[str, dict],
    agent02_report: dict,
    product_master: dict,
    approved_claims: list[dict] | None = None,
    offer: dict | None = None,
) -> dict:
    """Compile le plan créatif complet Agent 03 à partir des sorties vérifiées Agents 01 & 02."""
    blockers = []
    if candidate_entry.get("status") != "PROPOSED_FOR_COMMERCIAL_SELECTION":
        blockers.append("CANDIDATE_NOT_COMMERCIALLY_SELECTED_AT_GATE_7")
    sol_id = candidate_entry.get("selected_solution_id")
    sol = next((s for s in (dossier.get("solutions") or []) if s.get("id") == sol_id), None)
    if not sol:
        blockers.append("SELECTED_SOLUTION_MISSING_FROM_DOSSIER")
        return {"status": "BLOCKED_INTAKE", "mission_id": mission_id, "blockers": blockers}

    if solution_material_fingerprint(sol) != candidate_entry.get("selected_material_fingerprint"):
        blockers.append("SELECTED_SOLUTION_MATERIAL_FINGERPRINT_STALE")

    a02_sol = ((agent02_report or {}).get("solution_audits") or {}).get(sol_id)
    if not a02_sol:
        blockers.append("AGENT02_SOLUTION_AUDIT_MISSING")
    # Digest = integrity only, not origin authentication. LIVE remains disabled.
    import hashlib as _hashlib
    body = {k: v for k, v in (agent02_report or {}).items() if k != "audit_digest"}
    digest = _hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    if agent02_report.get("audit_digest") != digest:
        blockers.append("AGENT02_DIGEST_INVALID")
    if agent02_report.get("provenance_mode") != "REPLAY":
        blockers.append("AGENT02_LIVE_NOT_TRUSTED_IN_B2")
    if agent02_report.get("mission_id") != mission_id or agent02_report.get("target_sale_market") != target_market:
        blockers.append("AGENT02_MISSION_OR_MARKET_MISMATCH")
    if a02_sol and a02_sol.get("solution_fingerprint") != solution_fingerprint(sol):
        blockers.append("AGENT02_SOLUTION_FINGERPRINT_MISMATCH")
    if a02_sol and a02_sol.get("agent02_status") not in ("AUDITED_READY", "NOT_A_CONFIRMED_GAP"):
        blockers.append("AGENT02_NOT_REVIEW_READY")

    prod_issues = validate_product_master(product_master, sol)
    blockers.extend(prod_issues)
    if blockers:
        return {"status": "BLOCKED_INTAKE", "mission_id": mission_id, "blockers": blockers}

    catalog = load_camera_catalog(root_dir)
    defect_refs = [r for r in (sol.get("defect_refs") or []) if r in ledger_by_id]
    if not defect_refs:
        return {"status": "BLOCKED_INTAKE", "mission_id": mission_id, "blockers": ["NO_VERIFIED_VOC_DEFECT_REFS"]}

    verbatims = [ledger_by_id[r] for r in defect_refs]
    sample_tested = product_master.get("sample_status") == "SAMPLE_TESTED"

    # Trois stratégies DISTINCTES construites à partir de la VoC fournie.
    # Aucun avatar, contexte d'usage ou bénéfice géographique déduit d'un nom de persona.
    symptom = str(verbatims[0].get("probleme") or verbatims[0].get("phrase_exacte") or "UNKNOWN")
    second_problem = str((verbatims[1] if len(verbatims) > 1 else verbatims[0]).get("probleme") or symptom)
    jtbd = str(dossier.get("title") or symptom)
    mechanism = str(sol.get("mechanism") or "UNKNOWN_MECHANISM")
    declared_audience = f"{persona} ({target_market}) — SEGMENT_DECLARE_NON_PROUVE"
    raw_angles = [
        {"angle_id": _short_id("ANG", mission_id, sol_id, "problem_aware"),
         "avatar": declared_audience, "problem": symptom,
         "motivator": "Résoudre la friction documentée dans la VoC, sans résultat garanti",
         "awareness": "problem_aware", "sophistication": 3,
         "mechanism": mechanism, "star_feature": sol.get("reason_to_win", {}).get("claim", "HYPOTHESIS"),
         "source_refs": defect_refs[:3], "case_study_refs": ["M04-SMOOCHE", "M04-NUZZLE", "KAHEL-6D"]},
        {"angle_id": _short_id("ANG", mission_id, sol_id, "solution_aware"),
         "avatar": declared_audience, "problem": second_problem,
         "motivator": "Comparer honnêtement les solutions et limites observées",
         "awareness": "solution_aware", "sophistication": 4,
         "mechanism": mechanism, "star_feature": sol.get("reason_to_win", {}).get("claim", "HYPOTHESIS"),
         "source_refs": defect_refs, "case_study_refs": ["M04-NAALI", "M04-GYMSHARK", "STORE-PATTERNS"]},
        {"angle_id": _short_id("ANG", mission_id, sol_id, "unaware"),
         "avatar": declared_audience, "problem": jtbd,
         "motivator": "Faire reconnaître la situation documentée avant de montrer une solution",
         "awareness": "unaware", "sophistication": 3,
         "mechanism": mechanism, "star_feature": sol.get("reason_to_win", {}).get("claim", "HYPOTHESIS"),
         "source_refs": defect_refs[:2], "case_study_refs": ["M04-GRUNS", "KAHEL-6D"]},
    ]

    angle_cards = []
    concepts = []
    briefs = []
    job_specs = []
    blocked_formats = []

    for ang in raw_angles:
        aw = ang["awareness"]
        pb = AWARENESS_PLAYBOOK[aw]
        quote_txt = str(verbatims[0].get("phrase_exacte") or "")[:90]
        # Templates de recherche uniquement, NOT production copy. Pas de claim non prouvé.
        hooks = [
            {"hook_id": _short_id("HK", ang["angle_id"], "VOC"),
             "lever": "EMOTIONAL_RELEVANCE", "text": f"Problème observé : {ang['problem']}",
             "verbatim_anchor": quote_txt, "evidence_refs": ang["source_refs"][:1]},
            {"hook_id": _short_id("HK", ang["angle_id"], "QUESTION"),
             "lever": "QUESTION_CALLOUT", "text": "Ce problème vous concerne-t-il ?",
             "verbatim_anchor": quote_txt, "evidence_refs": ang["source_refs"][:2]},
            {"hook_id": _short_id("HK", ang["angle_id"], "HYPOTHESIS"),
             "lever": "MECHANISM_HYPOTHESIS", "text": "Mécanisme à tester : " + mechanism,
             "verbatim_anchor": mechanism, "evidence_refs": ang["source_refs"][:2]},
        ]
        for hk in hooks:
            hk["lint"] = lint_copy(hk["text"], approved_claims=approved_claims, offer=offer, max_words=12)

        card = {
            **ang,
            "sophistication_strategy": SOPHISTICATION_MAP[ang["sophistication"]],
            "playbook": pb,
            "hooks": hooks,
            "status": "RESEARCH_READY_FOR_TEST",
        }
        angle_cards.append(card)

        # Sélection des formats éligibles selon l'état de l'échantillon physique (Étape 10)
        usable_formats = []
        for fmt in pb["formats"]:
            media, needs_sample, needs_person, ratio = FORMATS_META[fmt]
            if needs_sample and not sample_tested:
                blocked_formats.append({"angle_id": ang["angle_id"], "format": fmt, "why": "REQUIRES_SAMPLE_TESTED_AT_STEP_10"})
                continue
            usable_formats.append(fmt)

        # Garantir 1 format statique + 1 format vidéo par angle
        if "PRODUCT_HERO_VIDEO" not in usable_formats and not any(FORMATS_META[f][0] == "video" for f in usable_formats):
            usable_formats.append("PRODUCT_HERO_VIDEO")

        for idx, fmt in enumerate(usable_formats[:2]):
            media, _, needs_person, ratio = FORMATS_META[fmt]
            hk = hooks[idx % len(hooks)]
            cid = _short_id("CON", ang["angle_id"], fmt, hk["hook_id"])
            concept = {
                "concept_id": cid,
                "angle_id": ang["angle_id"],
                "format": fmt,
                "media": media,
                "aspect_ratio": ratio,
                "awareness": aw,
                "idea_type": "NEW_MESSAGE" if idx == 0 else "STYLE_VARIATION",
                "hook_id": hk["hook_id"],
                "hook_text": hk["text"],
                "needs_person": needs_person,
                "evidence_refs": ang["source_refs"],
                "kahel_6d": {
                    "FORMAT": fmt,
                    "CONCEPT": "Problem/Solution Demo" if media == "video" else "Visual Hierarchy Static",
                    "ANGLE": ang["problem"],
                    "AVATAR": ang["avatar"],
                    "CONTENT": f"Product Master {product_master['sku']}",
                    "COPYWRITING": hk["text"],
                },
                "test_hypothesis": {
                    "hypothesis_id": _short_id("HYP", cid),
                    "statement": f"Sur {ang['avatar']}, l'angle '{ang['problem']}' en format {fmt} améliore le CTR lien et le coût par ajout panier.",
                    "primary_metric": "CPA / Cost per AddToCart",
                    "guardrails": ["Thumbstop 3s", "CTR lien", "CVR Landing Page"],
                    "controlled_variable": "angle_message" if idx == 0 else "creative_format",
                },
            }
            concepts.append(concept)

            p_lock = {
                "sku": product_master["sku"],
                "name": product_master["name"],
                "solution_id": sol["id"],
                "material_fingerprint": solution_material_fingerprint(sol),
                "immutable_attributes": product_master["immutable_attributes"],
                "materials": (sol.get("sku_spec") or {}).get("materials", []),
                "dimensions": (sol.get("sku_spec") or {}).get("dimensions"),
                "reference_media": product_master["reference_media"],
            }

            if media == "image":
                cam = select_camera_recipe("product_hero", catalog, "HF-CAM-P01-01")
                brief_id = _short_id("BRF", cid, "static")
                prompt_en = (
                    f"SUBJECT: Exact product {p_lock['name']} (SKU {p_lock['sku']}), preserving {', '.join(p_lock['immutable_attributes'])}. "
                    f"SCENE: Neutral product studio; do not infer home, location, demographics or usage setting from persona name. "
                    f"COMPOSITION: Aspect {ratio}, product anchored center-low, top 30% clean negative space for post-prod French copy overlay. "
                    f"CAMERA: {cam['source_title']} ({cam['physical_mechanics']}). "
                    "AVOID: any written text, letters, watermarks, altered product geometry, extra parts."
                )
                briefs.append({
                    "brief_id": brief_id,
                    "concept_id": cid,
                    "angle_id": ang["angle_id"],
                    "kind": "STATIC_AD_DESIGN_SPEC",
                    "aspect_ratio": ratio,
                    "product_truth": p_lock,
                    "camera_recipe": cam,
                    "text_overlay_post_prod": hk["text"],
                    "generate_without_baked_text": True,
                    "prompt_en": prompt_en,
                    "evidence_refs": ang["source_refs"],
                })
                model_id = "flux_3_image" if len(p_lock["reference_media"]) >= 2 else "marketing_studio_2_image"
                role = "image_references" if model_id == "flux_3_image" else "product_image"
                job_specs.append({
                    "job_spec_id": _short_id("JS", brief_id, model_id),
                    "brief_id": brief_id,
                    "concept_id": cid,
                    "angle_id": ang["angle_id"],
                    "tool": "generate_image",
                    "params": {
                        "model": model_id,
                        "prompt": prompt_en,
                        "aspect_ratio": ratio if model_id == "flux_3_image" else "3:4",
                        "count": 1,
                        "use_unlim": False,
                        "medias": [{"value": m["media_id"], "role": role} for m in p_lock["reference_media"][:2]],
                        **({"resolution": "1k"} if model_id == "flux_3_image" else {"type": "product_shots"}),
                    },
                    "status": "REPLAY_DRAFT_REQUIRES_HUMAN_REVIEW",
                })
            else:
                beats = [
                    ("hook_interrupt", 2, "HF-CAM-P02-05"),
                    ("problem_scene", 3, "HF-CAM-P02-03"),  # Teste la résolution de l'alias Side tracking -> Tracking !
                    ("product_intro", 3, "HF-CAM-P02-01"),  # Teste Pedestal down -> vertical_translation !
                    ("detail_proof", 3, "HF-CAM-P02-04"),
                    ("cta_end", 2, "HF-CAM-P01-01"),
                ]
                shots = []
                for s_idx, (obj, dur, req_cam) in enumerate(beats, 1):
                    c_rec = select_camera_recipe(obj, catalog, req_cam)
                    shots.append({
                        "shot_id": f"S{s_idx}",
                        "objective": obj,
                        "duration_s": dur,
                        "camera_recipe": c_rec,
                    })
                total_dur = sum(s["duration_s"] for s in shots)
                vo_script = f"{hk['text']} Mécanisme à vérifier avant toute publicité : {mechanism}."
                word_count = len(vo_script.split())
                brief_id = _short_id("BRF", cid, "video")
                prompt_en = (
                    f"SUBJECT: Exact product {p_lock['name']} (SKU {p_lock['sku']}), preserving {', '.join(p_lock['immutable_attributes'])}. "
                    + " ".join(
                        f"SHOT {s['shot_id']} ({s['duration_s']}s, {s['camera_recipe']['source_title']} [{s['camera_recipe']['canonical_id']}]): {s['objective']}."
                        for s in shots
                    )
                    + " AVOID: morphing product geometry, baked-in text, distorted hands."
                )
                briefs.append({
                    "brief_id": brief_id,
                    "concept_id": cid,
                    "angle_id": ang["angle_id"],
                    "kind": "VIDEO_SHOT_PLAN",
                    "aspect_ratio": ratio,
                    "total_duration_s": total_dur,
                    "script_fr": vo_script,
                    "script_words": word_count,
                    "script_word_budget": {"min_words": int(total_dur * 2.0), "max_words": int(total_dur * 2.7)},
                    "product_truth": p_lock,
                    "shots": shots,
                    "prompt_en": prompt_en,
                    "evidence_refs": ang["source_refs"],
                })
                job_specs.append({
                    "job_spec_id": _short_id("JS", brief_id, "seedance_2_0"),
                    "brief_id": brief_id,
                    "concept_id": cid,
                    "angle_id": ang["angle_id"],
                    "tool": "generate_video",
                    "params": {
                        "model": "seedance_2_0",
                        "prompt": prompt_en,
                        "aspect_ratio": "9:16",
                        "duration": total_dur,
                        "mode": "fast",
                        "resolution": "720p",
                        "count": 1,
                        "use_unlim": False,
                        "medias": [{"value": m["media_id"], "role": "image_references"} for m in p_lock["reference_media"][:2]],
                    },
                    "status": "REPLAY_DRAFT_REQUIRES_HUMAN_REVIEW",
                })

    experiment_plan = {
        "learning_unit": "ANGLE",
        "no_spend_is_not_failure": True,
        "angles_to_test": [
            {
                "angle_id": a["angle_id"],
                "awareness": a["awareness"],
                "concept_ids": [c["concept_id"] for c in concepts if c["angle_id"] == a["angle_id"]],
                "case_study_references": a["case_study_refs"],
            }
            for a in angle_cards
        ],
        "reference_frameworks": NECTAR_CASE_REFERENCES,
    }

    return {
        "schema": "ECOM_OS_AGENT03_CREATIVE_PLAN_B2",
        "mission_id": mission_id,
        "status": "DRAFT_PLAN_READY_FOR_REVIEW",
        "copy_status": "RESEARCH_DRAFT_ONLY_NOT_APPROVED_FOR_ADVERTISING",
        "localization_status": "REQUIRES_LOCALIZATION" if target_market != "FR" else "DRAFT_FR",
        "provenance_mode": "REPLAY",
        "selected_candidate_id": candidate_entry["id"],
        "selected_solution_id": sol_id,
        "selected_material_fingerprint": solution_material_fingerprint(sol),
        "camera_catalog_counts": {"source_blocks": 46, "canonical_recipes": 45, "aliases": 1},
        "angle_cards": angle_cards,
        "concepts": concepts,
        "blocked_formats": blocked_formats,
        "briefs": briefs,
        "job_specs": job_specs,
        "angle_experiment_plan": experiment_plan,
        "governance": {"G1": "HUMAN_REQUIRED", "G2": "NOT_AUTHORIZED_FOR_LIVE_SPEND", "G3": "NOT_AUTHORIZED"},
    }


def run_higgsfield_replay_cycle(workdir: Path, creative_plan: dict) -> dict:
    """Exécute en mode REPLAY le cycle complet de gouvernance Higgsfield (`studio.py` + `qa.py`)."""
    root_dir = workdir.parents[2]
    sys.path.insert(0, str(root_dir / "reference_corpus" / "CORE_ECOM_OS" / "04_MOTEUR"))
    from higgsfield.studio import HiggsfieldStudio  # noqa: E402
    from creative import qa as cr_qa  # noqa: E402

    hf_dir = workdir / "HIGGSFIELD"
    fixtures_dir = hf_dir / "FIXTURES"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "index.json").write_text(
        json.dumps({
            "quote:*": "quote_default.json",
            "submit:*": "submit_default.json",
            "result:*": "result_default.json",
        }),
        encoding="utf-8",
    )
    (fixtures_dir / "quote_default.json").write_text(json.dumps({"response": {"cost": {"credits": 2.0}}}), encoding="utf-8")
    (fixtures_dir / "submit_default.json").write_text(json.dumps({"response": {"job_id": "HF-SIM-JOB-01"}}), encoding="utf-8")
    (fixtures_dir / "result_default.json").write_text(
        json.dumps({"response": {"job_id": "HF-SIM-JOB-01", "status": "completed", "result_urls": ["https://replay.example.com/asset01.png"]}}),
        encoding="utf-8",
    )

    st = HiggsfieldStudio(hf_dir, mode="REPLAY", fixtures_dir=fixtures_dir)
    first_spec = creative_plan["job_specs"][0]
    planned = st.plan_job({
        **first_spec,
        "mission_id": creative_plan["mission_id"],
        "plan_id": "PLAN-REPLAY",
        "purpose": "REPLAY_DEMO_STATIC",
        "lineage": {"job_spec_id": first_spec["job_spec_id"]},
    })
    jkey = planned["job_key"]

    # 1. Vérifier que sans devis et sans G2, prepare_execution est BLOQUÉ
    blocked_no_quote = st.prepare_execution(jkey)
    quote = st.ingest_quote(jkey)
    blocked_no_g2 = st.prepare_execution(jkey)

    # 2. Enregistrement d'un accord G2 de test REPLAY et soumission
    st.approve_g2(
        first_spec["plan_id"] if "plan_id" in first_spec else "PLAN-REPLAY",
        [jkey],
        max_credits=10.0,
        owner_statement="Accord REPLAY test hors ligne pour job_key " + jkey,
    )
    # Assurer que plan_id correspond
    exec_res = st.prepare_execution(jkey)
    assets = st.manifest()
    asset_id = assets[0]["asset_id"]

    # 3. QA Visuelle : LLM_VISION seul = HOLD ; HUMAN = ACCEPTED_REPLAY_ONLY_NOT_A_REAL_AD
    all_pass_scores = {
        "product_fidelity": "PASS",
        "commercial_truth": "PASS",
        "rights_and_consent": "PASS",
        "brand_consistency": "PASS",
        "text_safe_zone_and_spacing": "PASS",
        "hierarchy_hook_subject_cta": "PASS",
        "mobile_legibility": "PASS",
        "background_contrast": "PASS",
        "angle_message_match": "PASS",
        "technical_ratio_duration_resolution": "PASS",
        "placement_preview_checked": "PASS",
    }
    qa_llm = cr_qa.review(assets[0], all_pass_scores, reviewer="claude_vision", reviewer_type="LLM_VISION")
    qa_human = cr_qa.review(assets[0], all_pass_scores, reviewer="owner_human", reviewer_type="HUMAN")
    final_asset = st.record_qa(asset_id, qa_human)

    return {
        "job_key": jkey,
        "blocked_without_quote_reasons": blocked_no_quote["reasons"],
        "quoted_credits": quote["credits"],
        "blocked_without_g2_reasons": blocked_no_g2["reasons"],
        "execution_result": exec_res,
        "llm_vision_only_decision": qa_llm["decision"],
        "human_qa_decision": qa_human["decision"],
        "final_asset_status": final_asset["status"],
    }
