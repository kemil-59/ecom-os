"""Profondeur et distribution de la demande : un même problème à travers de nombreuses sources indépendantes.

Grandeurs toujours séparées, jamais additionnées (indicateurs obligatoires 1–10) :
 1 sites/communautés explorés · 2 pages pertinentes · 3 contributions exprimant le problème ·
 4 auteurs distincts identifiables (INTERVALLE : borne basse = même pseudo sur plusieurs sites et textes quasi
   identiques fusionnés ; borne haute = un pseudo par site + contributions non rattachables) ·
 5 doublons et incertitude d'unicité · 6 répartition entre domaines et types de sources ·
 7 concentration (domaine, fil, produit) · 8 récence et persistance · 9 contextes différents ·
 10 contre-signaux (satisfaction).
Volumes (avis, vues, likes, compteurs) : jamais des personnes. Études : une étude générale ne valide jamais un
micro-problème ; seule une statistique VÉRIFIÉE, française, dont la portée est ce micro-problème/segment, est applicable.
Plancher codé : moins de 6 auteurs distincts (borne basse) = HYPOTHESIS_DETECTED, quel que soit le reste.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

from . import ledger

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = ROOT / "contracts" / "research_thresholds.json"
HARD_FLOOR_DISTINCT_PERSONS = 6
PAIN = {"FRUSTRATION", "SOLUTION_DEFECT", "SOLUTION_DEFECT_SPEND"}
LEVELS = ("HYPOTHESIS_DETECTED", "PROBLEM_CORROBORATED", "BROAD_DISTRIBUTED_SIGNAL", "SIGNIFICANT_MARKET_DOCUMENTED")
EXCLUDED_GEO = {"FOREIGN_SECTION", "SYNDICATED_OR_FOREIGN", "SYNDICATED_ORIGIN_UNKNOWN"}
FR_QUALIFIED = {"RESIDENCE_DECLARED", "FR_MARKET_SITE"}
# Catégories géographiques (jamais confondues) : un auteur d'un site marchand FR n'est PAS un résident français prouvé.
GEO_CLASSES = ("FR_RESIDENCE_DECLARED", "FR_MARKET_SITE_NO_RESIDENCE", "UNKNOWN_COUNTRY", "INTERNATIONAL")


def geo_class(r: dict) -> str:
    gb = r.get("geo_basis")
    if r.get("market_geo") == "FOREIGN" or gb in EXCLUDED_GEO or r.get("source_type") in ledger.FOREIGN_TYPES:
        return "INTERNATIONAL"
    if gb == "RESIDENCE_DECLARED":
        return "FR_RESIDENCE_DECLARED"
    if gb == "FR_MARKET_SITE" or _domain(r).endswith(".fr"):
        return "FR_MARKET_SITE_NO_RESIDENCE"       # marché FR du site (adaptateur ou domaine .fr), résidence non prouvée
    return "UNKNOWN_COUNTRY"


def independent_publications(rows: list[dict]) -> list[dict]:
    """Une publication reprise sur plusieurs domaines (avis syndiqués, reposts, copies) ne compte qu'une fois :
    on garde un représentant par texte quasi identique ; seuls les domaines de ces représentants sont indépendants."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x.get("observation_id") or ""):
        k = _prefix(r.get("phrase_exacte")) or ledger._norm_text_hash(r.get("phrase_exacte", ""))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out
_MONTHS = {m: i for i, m in enumerate(("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                                         "septembre", "octobre", "novembre", "décembre"), 1)}
_MONTHS.update({"fevrier": 2, "aout": 8, "decembre": 12})


def load_thresholds(path: Path = THRESHOLDS) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != "ECOM_OS_RESEARCH_THRESHOLDS_V2":
        raise ValueError("THRESHOLDS_SCHEMA_INVALID")
    lv = doc["levels"]
    # Un fichier ne peut jamais descendre sous le plancher codé ni sous l'ordre des niveaux.
    for k in ("corroborated", "broad_distributed", "significant_testimony_path"):
        if "min_distinct_testimonies" not in lv[k]:
            raise ValueError("THRESHOLDS_MIN_DISTINCT_TESTIMONIES_REQUIRED")
    lv["corroborated"]["min_distinct_testimonies"] = max(int(lv["corroborated"]["min_distinct_testimonies"]),
                                                         HARD_FLOOR_DISTINCT_PERSONS)
    lv["corroborated"]["min_independent_domains"] = max(int(lv["corroborated"]["min_independent_domains"]), 2)
    lv["broad_distributed"]["min_distinct_testimonies"] = max(int(lv["broad_distributed"]["min_distinct_testimonies"]),
                                                              lv["corroborated"]["min_distinct_testimonies"])
    lv["significant_testimony_path"]["min_distinct_testimonies"] = max(
        int(lv["significant_testimony_path"]["min_distinct_testimonies"]), lv["broad_distributed"]["min_distinct_testimonies"])
    fr = doc.get("fr_market")
    if not isinstance(fr, dict) or not {"testimony_min_fr_anchored", "testimony_min_fr_residence",
                                        "testimony_min_fr_domains", "min_fr_anchored_share",
                                        "statistic_min_fr_anchored"} <= set(fr):
        raise ValueError("THRESHOLDS_FR_MARKET_BLOCK_REQUIRED")
    return doc


def _prefix(text: str) -> str:
    t = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", str(text or "").lower()).split()
    return " ".join(t[:12]) if len(t) >= 8 else ""


def _domain(r: dict) -> str:
    if r.get("registrable_domain"):
        return r["registrable_domain"]
    host = ((r.get("source_url") or "").split("/") + ["", "", ""])[2].casefold()
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def distinct_persons(rows: list[dict]) -> set[str]:
    """Borne basse : comptes fusionnés si même empreinte de pseudo (tous sites) ou message quasi identique (copie)."""
    parent: dict[str, str] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            a = parent[a]
        return a

    by_prefix: dict[str, str] = {}
    for r in rows:
        a = r.get("author_key")
        if not a or str(a).startswith("unbound:"):
            continue
        find(a)
        p = _prefix(r.get("phrase_exacte"))
        if p:
            if p in by_prefix:
                parent[find(a)] = find(by_prefix[p])
            else:
                by_prefix[p] = a
    return {find(a) for a in parent}


def distinct_testimonies(rows: list[dict]) -> int:
    """Auteurs identifiés (fusion prudente) + contributions non attribuées dont le texte n'est pas une copie."""
    bound = [r for r in rows if r.get("author_key") and not str(r.get("author_key")).startswith("unbound:")]
    unbound = [r for r in rows if str(r.get("author_key") or "").startswith("unbound:")]
    seen = {_prefix(r.get("phrase_exacte")) or ledger._norm_text_hash(r.get("phrase_exacte", "")) for r in bound}
    extra = 0
    for r in independent_publications(unbound):
        k = _prefix(r.get("phrase_exacte")) or ledger._norm_text_hash(r.get("phrase_exacte", ""))
        if k not in seen:
            extra += 1
    return len(distinct_persons(bound)) + extra


USER_CONTRIBUTION = {"USER_CONTRIBUTION_ADAPTER", "USER_CONTRIBUTION_STRUCTURED", "USER_CONTRIBUTION_MARKUP"}


def is_user_contribution(r: dict) -> bool:
    """Contribution d'utilisateur établie par la structure de la page (jamais par le type proposé par LLM_Provider).
    Lignes historiques sans ce champ : seuls les extracteurs dédiés (types non génériques) sont admis."""
    nat = r.get("contribution_nature")
    if nat is not None:
        return nat in USER_CONTRIBUTION
    return not str(r.get("source_type") or "").startswith("WEB_")


def eligible(r: dict, market: str = "FR") -> bool:
    """Témoignage admissible pour la demande : vérifié par le moteur, contribution d'utilisateur (pas un contenu
    éditorial/marketing ni de nature incertaine), spontané, non étranger ni syndiqué."""
    return (r.get("provenance_mode") == ledger.FETCH_VERIFIED and r.get("spontane") is True and is_user_contribution(r)
            and r.get("geo_basis") not in EXCLUDED_GEO and r.get("market_geo") not in ("FOREIGN",)
            and bool(r.get("author_key"))
            and ledger.FAMILY.get(r.get("source_type"), "UNKNOWN") not in ("UNKNOWN", "SEARCH", "EDITORIAL"))


def _year(r: dict, now: dt.date) -> int | None:
    d = str(r.get("source_date") or "").strip().casefold()
    if not d:
        return None
    m = re.match(r"(\d{4})-\d{2}-\d{2}", d)
    if m:
        return int(m.group(1))
    m = re.match(r"\d{1,2}/\d{1,2}/(\d{2,4})$", d)
    if m:
        y = int(m.group(1))
        return y + 2000 if y < 100 else y
    m = re.match(r"\d{1,2} (\w+) (\d{4})$", d)
    if m and m.group(1) in _MONTHS:
        return int(m.group(2))
    m = re.match(r"il y a (\d+) (ans?|mois|semaines?|jours?)", d)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return now.year - (n if u.startswith("an") else n // 12 if u == "mois" else 0)
    return None


def assess(pain_rows: list[dict], *, sat_rows: list[dict] | None = None, volume_rows: list[dict] | None = None,
           studies: list[dict] | None = None, scope_id: str | None = None, thresholds: dict | None = None,
           market: str = "FR", explored_domains: set | None = None) -> dict:
    th = thresholds or load_thresholds()
    lv = th["levels"]
    now = dt.date.today()
    raw_pain = [r for r in pain_rows if r.get("kind") in PAIN]
    pain = [r for r in raw_pain if eligible(r, market)]
    non_ugc = Counter(r.get("contribution_nature") or "UNKNOWN" for r in raw_pain
                      if r.get("provenance_mode") == ledger.FETCH_VERIFIED and not is_user_contribution(r))
    bound = [r for r in pain if not str(r.get("author_key")).startswith("unbound:")]
    unbound = len(pain) - len(bound)
    lower = distinct_persons(bound)
    upper = len({r.get("author_site_key") or r.get("author_key") for r in bound}) + unbound
    by_handle_sites: dict[str, set] = {}
    for r in bound:
        by_handle_sites.setdefault(r["author_key"], set()).add(_domain(r))
    cross_site_handles = sum(1 for s in by_handle_sites.values() if len(s) > 1)
    # Témoignages distincts = auteurs identifiés (fusionnés) + contributions non attribuées indépendantes (copies retirées).
    # L'identité des internautes n'est jamais requise pour mesurer l'expression d'un problème.
    fr_res_rows = [r for r in pain if geo_class(r) == "FR_RESIDENCE_DECLARED"]
    fr_anch_rows = [r for r in pain if geo_class(r) in ("FR_RESIDENCE_DECLARED", "FR_MARKET_SITE_NO_RESIDENCE")]
    fr_res, fr_anch = distinct_testimonies(fr_res_rows), distinct_testimonies(fr_anch_rows)
    geo_counts = {g: distinct_testimonies([r for r in pain if geo_class(r) == g]) for g in GEO_CLASSES[:3]}
    intl_rows = [r for r in raw_pain if geo_class(r) == "INTERNATIONAL" and r.get("provenance_mode") == ledger.FETCH_VERIFIED]
    geo_counts["INTERNATIONAL"] = distinct_testimonies(intl_rows)
    n_t = distinct_testimonies(pain)
    indep = independent_publications(pain)
    raw_domains = Counter(_domain(r) for r in pain)
    domains = Counter(_domain(r) for r in indep)          # indépendance réelle des publications, pas des URL
    kinds = Counter(ledger.FAMILY.get(r.get("source_type"), "UNKNOWN") for r in pain)
    threads = Counter(r.get("source_url") for r in pain)
    products = Counter(str(r.get("incumbent") or "").casefold() for r in pain if r.get("incumbent"))
    total = sum(domains.values()) or 1
    max_dom = max(domains.values()) / total if domains else 0.0
    hhi = round(sum((v / total) ** 2 for v in domains.values()), 3) if domains else 0.0
    years = sorted({y for y in (_year(r, now) for r in pain) if y})
    recent = sum(1 for r in pain if (_year(r, now) or 0) >= now.year - th["investigation"]["recent_months"] // 12)
    contexts = {str(r.get("context")).casefold() for r in pain if r.get("context")} or {
        (_domain(r), ledger.FAMILY.get(r.get("source_type"))) for r in pain}
    sat = [r for r in (sat_rows or []) if r.get("kind") == "SATISFACTION" and eligible(r, market)]
    sat_p = distinct_persons(sat)
    volume = [{"observation_id": r["observation_id"], "count": r.get("count"), "url": r.get("source_url")}
              for r in (volume_rows or []) if r.get("kind") == "INDEX_COUNT" and r.get("provenance_mode") == ledger.FETCH_VERIFIED]
    applicable, general = [], []
    for s in studies or []:
        target = scope_id and scope_id in (s.get("applies_to") or [])
        # Applicable = reçu MOTEUR vérifié (jamais un statut proposé), statistique française, portée = ce micro/segment.
        if s.get("verification") == "VERIFIED" and str(s.get("engine_receipt_id") or "").startswith("STU-") \
                and s.get("scope") in ("MICRO_PROBLEM", "SEGMENT") and target and s.get("country") == "FR":
            applicable.append(s.get("id"))
        else:
            general.append(s.get("id"))
    n, nd, nk = len(lower), len(domains), len([k for k in kinds if k != "UNKNOWN"])
    c, b, st = lv["corroborated"], lv["broad_distributed"], lv["significant_testimony_path"]
    corroborated = n_t >= max(c["min_distinct_testimonies"], HARD_FLOOR_DISTINCT_PERSONS) and \
        nd >= c["min_independent_domains"] and nk >= c["min_source_kinds"]
    broad = corroborated and n_t >= b["min_distinct_testimonies"] and nd >= b["min_independent_domains"] and \
        nk >= b["min_source_kinds"] and max_dom <= b["max_domain_share"] and \
        (years[-1] - years[0] + 1 if years else 0) >= b["min_years_spanned"] and len(contexts) >= b["min_contexts"]
    frp = th["fr_market"]
    fr_domains = {_domain(r) for r in independent_publications(fr_anch_rows)}
    fr_share = fr_anch / n_t if n_t else 0.0
    fr_testimony = fr_anch >= frp["testimony_min_fr_anchored"] and fr_res >= frp["testimony_min_fr_residence"] and \
        len(fr_domains) >= frp["testimony_min_fr_domains"] and fr_share >= frp["min_fr_anchored_share"]
    fr_statistic = bool(applicable) and fr_anch >= frp["statistic_min_fr_anchored"]
    large = broad and n_t >= st["min_distinct_testimonies"] and nd >= st["min_independent_domains"]
    path_testimony = large and fr_testimony
    path_statistic = corroborated and fr_statistic
    if path_testimony or path_statistic:
        level = "SIGNIFICANT_MARKET_DOCUMENTED"
    elif broad:
        level = "BROAD_DISTRIBUTED_SIGNAL"
    elif corroborated:
        level = "PROBLEM_CORROBORATED"
    else:
        level = "HYPOTHESIS_DETECTED"
    fr_status = ("FR_SCALE_DEMONSTRATED_BY_TESTIMONY_AND_STATISTIC" if path_testimony and path_statistic else
                 "FR_SCALE_DEMONSTRATED_BY_TESTIMONY" if path_testimony else
                 "FR_SCALE_DEMONSTRATED_BY_STATISTIC" if path_statistic else "FR_SCALE_NOT_DEMONSTRATED")
    biases = []
    if large and not fr_testimony:
        biases.append("LARGE_SIGNAL_BUT_FR_SCALE_NOT_DEMONSTRATED")
    if raw_domains and len(domains) < len(raw_domains):
        biases.append(f"CROSS_DOMAIN_DUPLICATE_PUBLICATIONS:{len(pain) - len(indep)}")
    if max_dom > b["max_domain_share"]:
        biases.append(f"DOMAIN_CONCENTRATION:{round(max_dom, 2)}")
    if threads and max(threads.values()) / total > 0.5:
        biases.append("THREAD_CONCENTRATION")
    if products and max(products.values()) / total > 0.6:
        biases.append("PRODUCT_CONCENTRATION")
    if len(raw_pain) - len(pain):
        biases.append(f"EXCLUDED_SOLICITED_FOREIGN_OR_SYNDICATED:{len(raw_pain) - len(pain)}")
    return {
        "level": level,
        "evidence_paths": {"testimony": path_testimony, "statistic": path_statistic},
        "indicators": {
            "1_sites_explored": len(explored_domains or set()) or nd,
            "2_relevant_pages": len(threads),
            "3_contributions": len(pain),
            "3b_distinct_testimonies": n_t,
            "4_distinct_authors_interval": [n, max(upper, n)],
            "5_uniqueness_uncertainty": {"unbound_contributions": unbound, "same_handle_on_several_sites": cross_site_handles,
                                         "note": "témoignages distincts = auteurs identifiés fusionnés + contributions non attribuées "
                                                 "indépendantes ; une même personne peut écrire plusieurs contributions anonymes ; "
                                                 "l'identité inter-plateformes n'est pas établie et n'est pas requise"},
            "6_distribution": {"domains": dict(domains.most_common(30)), "source_kinds": dict(kinds), "hhi": hhi},
            "7_concentration": {"max_domain_share": round(max_dom, 2),
                                "max_thread_share": round(max(threads.values()) / total, 2) if threads else 0.0,
                                "max_product_share": round(max(products.values()) / total, 2) if products else 0.0},
            "8_recency_persistence": {"years": years, "contributions_recent": recent},
            "9_contexts": len(contexts),
            "10_counter_signals": {"satisfied_authors": len(sat_p), "satisfaction_contributions": len(sat)},
        },
        "fr_qualified_authors": len(distinct_persons([r for r in fr_res_rows if not str(r.get("author_key")).startswith("unbound:")])),
        "fr_market": {"status": fr_status,
                      "testimonies_by_geography": geo_counts,
                      "fr_anchored_testimonies": fr_anch, "fr_residence_declared_testimonies": fr_res,
                      "fr_anchored_share": round(fr_share, 2), "fr_independent_domains": len(fr_domains),
                      "verified_fr_statistics": applicable,
                      "uncertainty": "Les auteurs de sites marchands FR ou de domaines .fr ne sont pas des résidents prouvés ; "
                                     "les auteurs de pays inconnu et internationaux sont des signaux utiles, jamais une preuve "
                                     "de l'ampleur française à eux seuls."},
        "market_information_not_testimonies": dict(non_ugc),
        "independence": {"raw_domains": len(raw_domains), "independent_domains": len(domains),
                         "duplicate_publications_across_domains": len(pain) - len(indep)},
        "volume_indicators_not_persons": volume,
        "applicable_statistics": applicable, "general_studies_not_validating_micro": general,
        "market_size_estimate": "UNKNOWN" if not applicable else "SEE_APPLICABLE_STATISTICS",
        "biases": biases, "population_prevalence_measured": False,
        "hard_floor_distinct_persons": HARD_FLOOR_DISTINCT_PERSONS, "thresholds_status": th.get("status"),
        # compatibilité des lecteurs existants
        "tier": level, "distinct_persons_observed": n, "distinct_testimonies": n_t,
        "source_families": sorted(k for k in kinds if k != "UNKNOWN"),
    }


def gate_2b_convergence(rows: list[dict], candidate_id: str, *, thresholds: dict | None = None) -> dict:
    """GATE 2B (méthode active) : le problème revient chez plusieurs auteurs distincts, sur plusieurs domaines
    indépendants et plusieurs types de sources (PROBLEM_CORROBORATED au minimum). Ne dépend d'aucune famille
    d'extracteurs. N'est jamais une validation commerciale."""
    mine = [r for r in rows if candidate_id in (r.get("candidate_ids") or [])]
    a = assess([r for r in mine if r.get("kind") in PAIN], sat_rows=mine, thresholds=thresholds)
    ok = a["level"] != "HYPOTHESIS_DETECTED"
    return {"status": "PASS" if ok else ("FAIL" if a["distinct_testimonies"] < 2 else "EN_ATTENTE"),
            "validation": f"CONVERGENCE_{a['level']}", "evidence_level": a["level"],
            "independent_accounts": a["distinct_persons_observed"], "distinct_testimonies": a["distinct_testimonies"],
            "contributions": a["indicators"]["3_contributions"],
            "authors_interval": a["indicators"]["4_distinct_authors_interval"],
            "independent_domains": len(a["indicators"]["6_distribution"]["domains"]),
            "testimony_families": a["source_families"], "certified_live": False}


def score_cap(n: int, ladder: list) -> int:
    cap = 0
    for mn, mx in ladder:
        if n >= mn:
            cap = mx
    return cap
