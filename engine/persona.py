"""Personas : PROBLÈME → PERSONNES CONCERNÉES → SEGMENTS → COMPORTEMENTS D'ACHAT → PERSONA → OPPORTUNITÉ.

- Mode par défaut DISCOVERED : aucun persona imposé ; les segments ÉMERGENT des attributs que les auteurs déclarent
  eux-mêmes (âge, genre, rôle), vérifiés dans leur message, et des statistiques vérifiées. Jamais d'âge ou de genre
  déduit d'un prénom ou d'un pseudo. Les anciennes missions « OUVERT » et les missions sans persona sont DISCOVERED.
- Mode PROFILE uniquement si le propriétaire l'impose explicitement (`--persona PERSONA_A_FR`) : le ciblage est alors
  respecté (GATE 2A persona, fit démontré par déclarations OU statistique applicable au segment).
- Le profil distingue ce que la mission impose (femme, 42–55 ans, FR, achète en ligne) des attributs possibles mais
  non universels (animal, enfants, logement, ménopause…) : un territoire qui en dépend doit les déclarer comme
  sous-segment conditionnel, jamais les attribuer à toutes les Persona_A.
- Le fit persona d'une observation n'est jamais supposé : il provient d'une déclaration de l'auteur retrouvée par le
  moteur dans son bloc (âge, marqueur déclaré), jamais d'un prénom, d'un pseudo, de la langue ou du sujet.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "research" / "personas"
ALIASES = {"persona_a": "PERSONA_A_FR", "persona_a_fr": "PERSONA_A_FR"}
MATCH, PARTIAL, OUT, UNPARSED, NOT_FOUND = ("SELF_DECLARED_MATCH", "SELF_DECLARED_PARTIAL",
                                             "SELF_DECLARED_OUT_OF_TARGET", "SELF_DECLARED_UNPARSED", "CLAIM_NOT_FOUND")
PERSONA_QUALIFYING = {MATCH, PARTIAL}


def _low(s: str) -> str:
    return " ".join(str(s or "").replace("’", "'").replace("\xa0", " ").casefold().split())


def load_profile(persona_id: str) -> dict:
    p = PROFILES / f"{persona_id}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("schema") != "ECOM_OS_PERSONA_PROFILE_V1" or doc.get("persona_id") != persona_id:
        raise ValueError(f"PERSONA_PROFILE_INVALID:{persona_id}")
    return doc


def resolve(cfg: dict) -> dict:
    """Persona effectif d'une mission. PROFILE seulement si `persona_mode: PROFILE` est explicite dans mission.json ;
    sinon DISCOVERED (segments découverts). Aucune dérive silencieuse : le mode est toujours dans le résultat."""
    raw = str(cfg.get("persona") or "").strip()
    key = ALIASES.get(raw.casefold(), raw)
    if cfg.get("persona_mode") == "PROFILE" and (PROFILES / f"{key}.json").exists():
        return {"mode": "PROFILE", "persona_id": key, "profile": load_profile(key), "declared": raw}
    return {"mode": "DISCOVERED", "persona_id": None, "profile": None, "declared": raw or None}


def qualify_claim(claim: str | None, block: str, profile: dict | None) -> dict:
    """Vérifie qu'une déclaration persona figure dans le bloc de l'auteur, puis la classe selon le profil."""
    c, b = _low(claim), _low(block)
    if not c or len(c) < 4 or c not in b:
        return {"status": NOT_FOUND, "claim": (claim or "")[:120]}
    attrs = extract_attributes(c)
    if not profile:
        return {"status": "SELF_DECLARED_ATTRIBUTES" if attrs else UNPARSED, "claim": claim[:120], "attributes": attrs}
    imp, ev = profile["imposed_by_mission"], profile["persona_evidence"]
    age, age_rng = None, None
    for pat in ev.get("age_patterns", []):
        m = re.search(pat, c)
        if m:
            age = int(m.group(1))
            break
    if age is None:
        for phrase, rng in (ev.get("age_range_phrases") or {}).items():
            if phrase in c:
                age_rng = rng
                break
    lo, hi = imp["age_min"], imp["age_max"]
    if age is not None:
        age_status = "IN" if lo <= age <= hi else "OUT"
    elif age_rng:
        age_status = "IN" if age_rng[0] >= lo and age_rng[1] <= hi + 4 else ("OVERLAP" if age_rng[1] >= lo else "OUT")
    else:
        age_status = None
    female = any(x in c for x in ev.get("female_self_declared", []))
    male = any(x in c for x in ev.get("male_self_declared", []))
    cue = any(x in c for x in ev.get("female_linguistic_cues", []))
    if male or age_status == "OUT":
        status = OUT
    elif age_status == "IN" and (female or cue):
        status = MATCH
    elif age_status in ("IN", "OVERLAP") or female or cue:
        status = PARTIAL
    else:
        status = UNPARSED
    return {"status": status, "claim": claim[:120], "age": age, "age_range": age_rng, "attributes": attrs,
            "gender_basis": "SELF_DECLARED" if female else ("LINGUISTIC_CUE" if cue else ("MALE_DECLARED" if male else None))}


def check_territories(territories: list[dict], profile: dict) -> list[str]:
    """GATE 2A persona : chaque territoire part d'une situation de vie de la cible, sans attribut universel imposé."""
    issues, zones = [], {}
    kw = profile.get("conditional_attribute_keywords") or {}
    for t in territories:
        tid = t.get("territory_id", "?")
        pl = t.get("persona_link")
        if not isinstance(pl, dict):
            issues.append(f"PERSONA_LINK_MISSING:{tid}")
            continue
        if pl.get("life_zone") not in profile["life_zones"]:
            issues.append(f"PERSONA_LIFE_ZONE_UNKNOWN:{tid}:{pl.get('life_zone')}")
        for f, n in (("situation", 10), ("why_target", 15)):
            if len(str(pl.get(f) or "").strip()) < n:
                issues.append(f"PERSONA_LINK_{f.upper()}_MISSING:{tid}")
        if pl.get("evidence_status") not in ("HYPOTHESIS", "DOCUMENTED"):
            issues.append(f"PERSONA_LINK_EVIDENCE_STATUS_INVALID:{tid}")
        declared = set(pl.get("conditional_attributes") or [])
        text = _low(" ".join([str(t.get("name", "")), str(pl.get("situation", "")), str(pl.get("why_target", "")),
                              json.dumps(t.get("micro_contexts", []), ensure_ascii=False)]))
        for attr, words in kw.items():
            if attr not in declared and any(re.search(r"(?<!\w)" + re.escape(_low(w)) + r"(?!\w)", text) for w in words):
                issues.append(f"PERSONA_CONDITIONAL_ATTRIBUTE_UNDECLARED:{tid}:{attr}")
        for u in profile.get("forbidden_universal_assumptions", []):
            if _low(u) in text:
                issues.append(f"PERSONA_UNIVERSAL_ASSUMPTION_FORBIDDEN:{tid}")
        zones.setdefault(pl.get("life_zone"), []).append(tid)
    if len(zones) < 5:
        issues.append(f"TERRITORY_LIFE_ZONE_DIVERSITY_BELOW_5:{len(zones)}")
    for z, ts in zones.items():
        if len(ts) > 2:
            issues.append(f"TERRITORY_LIFE_ZONE_OVERREPRESENTED:{z}:{len(ts)}")
    return sorted(set(issues))


def fit_status(rows: list[dict], thresholds: dict, mode: str, applicable_segment_statistic: bool = False) -> dict:
    """Fit persona (missions PROFILE seulement) : déclarations vérifiées OU statistique vérifiée applicable au segment."""
    if mode != "PROFILE":
        return {"status": "NOT_APPLICABLE_" + mode, "match_accounts": 0, "partial_accounts": 0, "out_accounts": 0}
    acc = {}
    for r in rows:
        acc.setdefault(r.get("author_key"), set()).add(r.get("persona_basis") or "NONE")
    match = {a for a, s in acc.items() if MATCH in s}
    partial = {a for a, s in acc.items() if PARTIAL in s and a not in match}
    out = {a for a, s in acc.items() if OUT in s and a not in match and a not in partial}
    declared = len(match) + len(partial) + len(out)
    th = thresholds["persona_fit"]
    if len(out) >= th["out_of_target_min_accounts"] and len(out) > len(match) + len(partial):
        st = "PERSONA_OUT_OF_TARGET"
    elif (len(match) >= th["demonstrated_min_match_accounts"] and declared
          and len(match) / declared >= th["demonstrated_min_match_share_of_declared"]) or applicable_segment_statistic:
        st = "PERSONA_FIT_DEMONSTRATED"
    elif match or len(partial) >= 3:
        st = "PERSONA_FIT_PARTIAL"
    else:
        st = "PERSONA_FIT_UNPROVEN"
    return {"status": st, "match_accounts": len(match), "partial_accounts": len(partial), "out_accounts": len(out),
            "undeclared_accounts": len(acc) - declared}


# --- Segments découverts (mode DISCOVERED et complément du mode PROFILE) ---
GENERIC = {
    "age": [r"j'ai (\d{2}) ans", r"j ai (\d{2}) ans", r"âgée? de (\d{2}) ans", r"agée? de (\d{2}) ans", r"moi (\d{2}) ans",
            r"du haut de mes (\d{2}) ans"],
    "female": ["je suis une femme", "en tant que femme", "maman de", "je suis maman", "mère de", "grand-mère", "je suis enceinte",
               "je suis ménopausée", "ma ménopause"],
    "male": ["je suis un homme", "en tant qu'homme", "je suis papa", "père de", "grand-père"],
    "roles": {"parent": ["maman", "papa", "mes enfants", "mon fils", "ma fille", "mère de", "père de"],
              "retraite": ["retraité", "retraitée", "à la retraite"], "etudiant": ["étudiant", "étudiante"],
              "aidant": ["ma mère âgée", "mon père âgé", "aidant", "aidante"], "enceinte": ["enceinte"],
              "proprietaire_animal": ["mon chat", "ma chatte", "mon chien", "ma chienne"],
              "teletravail": ["télétravail"], "sportif": ["je cours", "marathon", "salle de sport"]},
}
AGE_BANDS = ((18, 24), (25, 34), (35, 44), (45, 54), (55, 64), (65, 120))


def extract_attributes(claim_low: str) -> dict:
    """Attributs auto-déclarés dans la phrase vérifiée (jamais déduits d'un prénom, d'un pseudo ou du sujet)."""
    out = {}
    for pat in GENERIC["age"]:
        m = re.search(pat, claim_low)
        if m and 12 <= int(m.group(1)) <= 110:
            out["age"] = int(m.group(1))
            out["age_band"] = next(f"{a}-{b}" for a, b in AGE_BANDS if a <= out["age"] <= b) if out["age"] >= 18 else "<18"
            break
    if any(x in claim_low for x in GENERIC["female"]):
        out["gender"] = "F"
    elif any(x in claim_low for x in GENERIC["male"]):
        out["gender"] = "M"
    roles = sorted(r for r, ws in GENERIC["roles"].items() if any(w in claim_low for w in ws))
    if roles:
        out["roles"] = roles
    return out


# Indices de CONTEXTE (texte du témoignage uniquement, jamais un pseudo ou un prénom) → caractéristiques PROBABLES.
# Une inférence n'est jamais une caractéristique individuelle prouvée ; confiance LOW ou MEDIUM seulement.
INFERENCE_CUES = (
    (r"\bje suis (?:tr[eè]s )?(?:d[ée]çue|ravie|contente|satisfaite|oblig[ée]e|[ée]puis[ée]e|all[ée]e|habitu[ée]e|convaincue)\b",
     "gender", "F", "MEDIUM", "accord grammatical féminin"),
    (r"\bje suis (?:tr[eè]s )?(?:d[ée]çu|ravi|content|satisfait|oblig[ée]|[ée]puis[ée]|all[ée]|habitu[ée]|convaincu)\b(?!e)",
     "gender", "M", "MEDIUM", "accord grammatical masculin"),
    (r"\bmon mari\b|\bmon conjoint\b", "gender", "F", "LOW", "mention d'un mari/conjoint (non exclusif)"),
    (r"\bma femme\b|\bmon épouse\b|\bmon epouse\b", "gender", "M", "LOW", "mention d'une épouse (non exclusif)"),
    (r"m[ée]nopause|bouff[ée]es de chaleur|p[ée]rim[ée]nopause", "life_stage", "menopause_45_55", "LOW", "sujet ménopause"),
    (r"petits?-enfants", "age_band", "55+", "LOW", "mention de petits-enfants"),
    (r"\bmon b[ée]b[ée]\b|allaitement|\bma grossesse\b", "life_stage", "jeune_parent", "MEDIUM", "bébé / allaitement"),
    (r"\bmes ados\b|\bmon ado\b|\bmes adolescents\b", "life_stage", "parent_d_ados", "MEDIUM", "adolescents"),
    (r"\bau lyc[ée]e\b|\bmes profs\b|\ben cours\b", "age_band", "15-24", "LOW", "contexte scolaire"),
    (r"\b[àa] la retraite\b|\bdepuis ma retraite\b", "age_band", "60+", "LOW", "retraite"),
    (r"\bau bureau\b|\bmon travail\b|\bmes coll[èe]gues\b", "role", "actif_en_emploi", "LOW", "contexte professionnel"),
)


def infer_attributes(text: str) -> dict:
    """{dimension: {value, confidence, cue}} à partir du seul texte vérifié. Jamais HIGH, jamais une preuve individuelle."""
    t = _low(text)
    out = {}
    for pat, dim, val, conf, why in INFERENCE_CUES:
        if dim not in out and re.search(pat, t):
            out[dim] = {"value": val, "confidence": conf, "cue": why}
    return out


def segment_profile(rows: list[dict], thresholds: dict, studies: list[dict] | None = None, scope_id: str | None = None) -> dict:
    """Segments construits à partir des TÉMOIGNAGES (identité non requise), en 4 couches séparées :
    1 déclaré (auto-déclaration vérifiée dans le texte), 2 inféré du contexte (probable, avec confiance),
    3 établi par étude (statistique vérifiée par le moteur, portée segment), 4 inconnu. Une inférence ou une
    auto-déclaration d'une minorité de témoins n'est jamais une mesure représentative."""
    units: dict[str, dict] = {}
    for r in rows:
        a = r.get("author_key")
        key = a if a and not str(a).startswith("unbound:") else f"obs:{r.get('observation_id')}"
        u = units.setdefault(key, {"declared": {}, "inferred": {}})
        for k, v in (r.get("declared_attributes") or {}).items():
            u["declared"].setdefault(k, v)
        for k, v in (r.get("inferred_attributes") or {}).items():
            u["inferred"].setdefault(k, v)
    dist = {"age_band": {}, "gender": {}, "roles": {}}
    inferred: dict[str, dict] = {}
    declaring = unknown = 0
    for u in units.values():
        d = u["declared"]
        if d:
            declaring += 1
        if d.get("age_band"):
            dist["age_band"][d["age_band"]] = dist["age_band"].get(d["age_band"], 0) + 1
        if d.get("gender"):
            dist["gender"][d["gender"]] = dist["gender"].get(d["gender"], 0) + 1
        for ro in d.get("roles") or []:
            dist["roles"][ro] = dist["roles"].get(ro, 0) + 1
        for dim, inf in u["inferred"].items():
            if dim in d or (dim == "role" and d.get("roles")):
                continue                                  # le déclaré prime toujours sur l'inféré
            slot = inferred.setdefault(dim, {}).setdefault(inf["value"], {"LOW": 0, "MEDIUM": 0})
            slot[inf["confidence"]] += 1
        if not d and not u["inferred"]:
            unknown += 1
    se = thresholds.get("segment_emergence") or {"min_declaring_authors": 10, "min_share_of_declaring": 0.4}
    emergent = []
    for dim, vals in dist.items():
        tot = sum(vals.values())
        for v, n in vals.items():
            if n >= se["min_declaring_authors"] and tot and n / tot >= se["min_share_of_declaring"]:
                emergent.append({"dimension": dim, "value": v, "testimonies": n, "share_of_declaring": round(n / tot, 2),
                                 "status": "EMERGENT_SEGMENT_HYPOTHESIS"})
    inferred_hyp = [{"dimension": dim, "value": v, "testimonies": c["LOW"] + c["MEDIUM"], "by_confidence": c,
                     "status": "INFERRED_SEGMENT_HYPOTHESIS_NOT_INDIVIDUAL_PROOF"}
                    for dim, vals in inferred.items() for v, c in vals.items()
                    if c["LOW"] + c["MEDIUM"] >= se["min_declaring_authors"]]
    study_layer = [{"id": s.get("id"), "population": s.get("population"), "figure": s.get("figure_quote"), "url": s.get("url")}
                   for s in studies or [] if s.get("scope") == "SEGMENT" and s.get("verification") == "VERIFIED"
                   and str(s.get("engine_receipt_id") or "").startswith("STU-") and (not scope_id or scope_id in (s.get("applies_to") or []))]
    total = len(units)
    return {"testimonies_total": total, "testimonies_declaring": declaring,
            "declaration_coverage": round(declaring / total, 2) if total else 0.0,
            "layers": {"1_declared": dist, "2_inferred_probable": inferred, "3_established_by_studies": study_layer,
                       "4_unknown_testimonies": unknown},
            "emergent_segments": emergent, "inferred_segment_hypotheses": inferred_hyp,
            "limits": "Déclarations et inférences portent sur des témoignages, pas sur des individus identifiés ; "
                      "seules les études vérifiées décrivent un segment de population."}
