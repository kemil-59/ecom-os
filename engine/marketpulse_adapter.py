"""Priorité A (B2) — Adaptateur et Gouverneur MarketPulse (REPLAY & FILE_DROP/MCP_SESSION).

Implémente les spécifications officielles `MARKETPULSE_TOOLKIT_V023.md` et `04_MOTEUR/marketpulse/` :
- Retourne `BLOCKED_CONNECTOR` si MarketPulse n'est pas connecté ou si aucun solde frais (< 24h) n'est présent.
- Contrôle et réservation des crédits :
  - `usage_get` / `check_credits` = 0 crédit
  - `search_ads` = 2 crédits / annonce retournée (0 si vide)
  - `search_shops` = 4 crédits / boutique retournée
  - `search_advertisers` = 3 crédits / annonceur retourné
  - Outils d'écriture (`add_to_brandtracker`, etc.) = strictement interdits (`FORBIDDEN_WRITE_TOOL`)
  - Outils non calibrés (`find_similar_shops`, `brief_competitor`, `scan_ad`, `daily_radar`, `find_winning_products`)
    = bloqués sans approbation nominative + `per_call_ceiling`
  - `find_winning_products` (`A01_S7_SOLUTION_PRODUCTS`) bloqué avant l'étape 7 (`STAGE_BELOW_7_SKU_FORBIDDEN`)
  - Termes français refusés sur US/GB/AU (`LOCALIZATION_ERROR_ENGLISH_REQUIRED`)
- Gestion `OUTBOX/<call_id>.request.json` -> `INBOX/<call_id>.response.json`
- Normalisation atomique avant règlement des crédits, gel de l'outil si `costUnits > reserved_max` (`COST_MODEL_VIOLATION`)
- Déduplication, flags anti-bruit (`GENERALIST_CATALOG`, `GENERALIST_ADVERTISER`, `LOW_REACH`, `TERM_NOT_IN_COPY`),
  et rapprochement d'entités `AD -> ADVERTISER -> SHOP -> LANDING -> SKU` (`RELATION_UNVERIFIED` si lien manquant)
- Réconciliation des appels et des crédits (`reconcile_credits`).

Mode `MCP_SESSION` (lecture seule, mission FETCH_VERIFIED uniquement) :
- Approbation du propriétaire = fichier signé (RSA, clé privée chiffrée par phrase secrète) vérifié par `/usr/bin/openssl`;
  plafond codé en dur 500 crédits, outils limités à search_ads / search_advertisers / search_shops, réserve >= 500.
- Un seul appel en cours; l'envoi MCP est autorisé par le hook `engine/tt_live_guard.py` uniquement si les paramètres
  sont identiques à la réservation (paramètres alignés sur les schémas MCP réels).
- Solde capturé par le hook (PostToolUse `check_credits`), jamais saisi; coût réglé par différence de solde.
- Tout écart (réponse inattendue, débit inexpliqué, reçu incohérent, dépassement) gèle le moteur (`HALT.json`)
  jusqu'à une nouvelle approbation signée.

Intelligence concurrentielle (moteur réutilisable ; marchés, formulations et lexiques fournis par la mission) :
- Plan d'investigation de mission `TT_INVESTIGATION_PLAN.json` (lots S1–S4, vagues V1/V2/contre-échantillons,
  budgets par enveloppe, règles d'arrêt) ; `next_call` choisit l'appel suivant selon le rendement observé.
- Qualification explicite de chaque résultat (`qualify_row`), nouveauté inter-appels, rendement par lot.
- Mode autonome : approbation signée `autonomous` liée à l'empreinte du plan ; le hook PermissionRequest
  n'accorde l'appel que pour la réservation AUTHORIZED identique, une seule fois. La règle `ask` reste en place.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from urllib.parse import urlsplit
import uuid

MEASURED_COSTS_PER_ROW = {
    "search_ads": 2,
    "search_shops": 4,
    "search_advertisers": 3,
}
FREE_TOOLS = frozenset({"usage_get", "check_credits"})
FORBIDDEN_WRITE_TOOLS = frozenset({
    "add_to_brandtracker",
    "create_brandtracker_folder",
    "delete_brandtracker_folder",
    "rename_brandtracker_folder",
    "delete_from_brandtracker",
    "move_tracked_brands",
    "create_board",
    "share_link_create",
    # Noms réels des outils d'écriture du connecteur MCP MarketPulse
    "add_board_item",
    "archive_board",
    "create_ad_share_link",
    "create_board_share_link",
    "delete_ad_share_link",
    "move_board",
    "move_board_item",
    "remove_board_item",
    "restore_board",
    "update_board",
})
UNCALIBRATED_TOOLS = frozenset({
    "find_similar_shops",
    "brief_competitor",
    "scan_ad",
    "lookup",
    "lookup_filter_ids",
    "search_tiktok_library",
    "search_google_ads_library",
    "daily_radar",
    "analyze_tracked_brand",
    "find_winning_products",
})

# `live_params` : tri / statut explicites des schémas MCP réels (sans tri explicite, search_ads applique
# trend_signal=reach_growth_7d qui injecte min_reach=1, soit un filtre caché).
# `strategy` : S1 densité · S2 analyse publicitaire · S3 annonceurs · S4 boutiques liées. `max_page` : pagination
# contrôlée (search_advertisers n'a pas de paramètre `page`). `window_days` : fenêtre `created_after` (premier vu).
# `max_ads_per_brand` (schéma search_ads, 0–15) : diversité des annonceurs dans un échantillon.
RECIPES = {
    "A01_S5_AD_DENSITY_PROBE": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 1, "max_credits": 2, "geo_semantics": "AD_AUDIENCE", "strategy": "S1", "live_params": {"status": "all", "sort_by": "relevance"}, "requires_fields": ["total", "id"]},
    "A01_S5_AD_SAMPLE_REACH": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 8, "max_credits": 16, "geo_semantics": "AD_AUDIENCE", "strategy": "S2", "max_page": 3, "live_params": {"status": "active", "sort_by": "reach", "max_ads_per_brand": 2}, "requires_fields": ["id", "copy"]},
    "A01_S5_AD_SAMPLE_LONGEST": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 8, "max_credits": 16, "geo_semantics": "AD_AUDIENCE", "strategy": "S2", "max_page": 3, "live_params": {"status": "active", "sort_by": "longestRunning", "max_ads_per_brand": 2}, "requires_fields": ["id", "copy"]},
    # Croissance de portée sur 30 jours : filtre de portée positive rendu explicite (sinon injecté en last7d).
    "A01_S5_AD_SAMPLE_GROWTH": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 8, "max_credits": 16, "geo_semantics": "AD_AUDIENCE", "strategy": "S2", "max_page": 2, "live_params": {"status": "active", "sort_by": "reachDelta30d", "reach_period": "last30d", "min_reach": 1, "max_ads_per_brand": 2}, "requires_fields": ["id", "copy"]},
    # Lancements récents (premier vu dans la fenêtre) : nouveaux entrants observés au niveau de l'annonce.
    "A01_S5_AD_RECENT_LAUNCHES": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 8, "max_credits": 16, "geo_semantics": "AD_AUDIENCE", "strategy": "S3", "max_page": 2, "window_days": 90, "live_params": {"status": "all", "sort_by": "newest", "max_ads_per_brand": 2}, "requires_fields": ["id", "copy"]},
    "A01_S5_NEW_ENTRANTS": {"agent": "01", "min_stage": 5, "tool": "search_advertisers", "limit": 5, "max_credits": 15, "geo_semantics": "AD_AUDIENCE", "strategy": "S3", "live_params": {"sort_by": "newest", "search_in": "ad_copy"}, "requires_fields": ["id", "name"]},
    "A01_S5_ESTABLISHED_ADVERTISERS": {"agent": "01", "min_stage": 5, "tool": "search_advertisers", "limit": 5, "max_credits": 15, "geo_semantics": "AD_AUDIENCE", "strategy": "S3", "live_params": {"sort_by": "activeAds", "search_in": "ad_copy"}, "requires_fields": ["id", "name"]},
    "A01_S5_LINKED_SHOP": {"agent": "01", "min_stage": 5, "tool": "search_shops", "limit": 1, "max_credits": 4, "geo_semantics": "SHOP_TRAFFIC_MAIN", "strategy": "S4", "requires_linked_domain": True, "live_params": {"match_mode": "exact"}, "requires_fields": ["domain"]},
    "A01_S5_FOREIGN_PRELIM_ADS": {"agent": "01", "min_stage": 5, "tool": "search_ads", "limit": 5, "max_credits": 10, "geo_semantics": "AD_AUDIENCE", "foreign_only": True, "live_params": {"status": "active", "sort_by": "reach"}, "requires_fields": ["id", "copy"]},
    "A01_S7_SOLUTION_PRODUCTS": {"agent": "01", "min_stage": 7, "tool": "find_winning_products", "limit": 5, "max_credits": None, "geo_semantics": "SHOP_TRAFFIC_MAIN"},
    "A02_FR_BASELINE_ADS": {"agent": "02", "min_stage": 5, "tool": "search_ads", "limit": 10, "max_credits": 20, "geo_semantics": "AD_AUDIENCE", "strategy": "S2", "max_page": 2, "live_params": {"status": "active", "sort_by": "reach", "max_ads_per_brand": 2}, "requires_fields": ["id", "copy"]},
    # Recherche texte de boutiques (match `contains` sur nom/domaine) : hors cible au calibrage TTC-eb5bb8514d
    # (boutique généraliste pour un terme problème). Désactivée en LIVE ; utiliser A01_S5_LINKED_SHOP.
    "A02_FR_BASELINE_SHOPS": {"agent": "02", "min_stage": 5, "tool": "search_shops", "limit": 5, "max_credits": 20, "geo_semantics": "SHOP_TRAFFIC_MAIN", "live_params": {}, "requires_fields": ["domain"],
                              "live_blocked": "GENERIC_SHOP_TEXT_SEARCH_OFF_TARGET_USE_A01_S5_LINKED_SHOP"},
    "A02_BIG4_ADS": {"agent": "02", "min_stage": 5, "tool": "search_ads", "limit": 8, "max_credits": 16, "geo_semantics": "AD_AUDIENCE", "foreign_only": True, "live_params": {"status": "active", "sort_by": "reach"}, "requires_fields": ["id", "copy"]},
    "A02_BIG4_SHOPS": {"agent": "02", "min_stage": 5, "tool": "search_shops", "limit": 3, "max_credits": 12, "geo_semantics": "SHOP_TRAFFIC_MAIN", "foreign_only": True, "live_params": {}, "requires_fields": ["domain"]},
    # Calibrage LIVE (limit=1) : capture la forme réelle de la réponse, aucune preuve produite.
    "TT_CALIBRATE_SEARCH_ADS": {"agent": "OWNER", "min_stage": 5, "tool": "search_ads", "limit": 1, "max_credits": 2, "geo_semantics": "AD_AUDIENCE", "live_params": {"status": "all", "sort_by": "relevance"}, "calibration": True},
    "TT_CALIBRATE_SEARCH_ADVERTISERS": {"agent": "OWNER", "min_stage": 5, "tool": "search_advertisers", "limit": 1, "max_credits": 3, "geo_semantics": "AD_AUDIENCE", "live_params": {"sort_by": "newest", "search_in": "ad_copy"}, "calibration": True},
    "TT_CALIBRATE_SEARCH_SHOPS": {"agent": "OWNER", "min_stage": 5, "tool": "search_shops", "limit": 1, "max_credits": 4, "geo_semantics": "SHOP_TRAFFIC_MAIN", "live_params": {}, "calibration": True},
    "A02_FR_COUNTER_PROBE": {"agent": "02", "min_stage": 5, "tool": "search_ads", "limit": 1, "max_credits": 2, "geo_semantics": "AD_AUDIENCE", "strategy": "S1", "live_params": {"status": "all", "sort_by": "relevance"}, "requires_fields": ["total", "id"]},
}

ENGLISH_ONLY_MARKETS = {"US", "GB", "AU"}
FRENCH_ACCENT_OR_STOPWORDS = ("é", "è", "ê", "à", "ù", "ç", "œ", "égouttoir", "vaisselle", "problème", "chaussure", "cuisine", "pour ", "dans ", "sans ")
DEFAULT_RESERVE_FLOOR = 500
BALANCE_MAX_AGE_HOURS = 24

# --- MCP_SESSION (LIVE lecture seule) ---
LIVE_MODE = "MCP_SESSION"
MCP_TOOL_PREFIX = "mcp__claude_ai_Marketpulse__"
LIVE_TOOLS = frozenset(MEASURED_COSTS_PER_ROW)
LIVE_MAX_CREDITS_PER_APPROVAL = 500
LIVE_MIN_RESERVE_FLOOR = 500
LIVE_APPROVAL_MAX_HOURS = 48
LIVE_BALANCE_MAX_AGE_MINUTES = 15
# Délai entre PreToolUse (AUTHORIZED) et PermissionRequest pour une levée autonome de la confirmation.
PERMISSION_WINDOW_SECONDS = 120
# Appel autorisé sans réponse capturée (refus manuel, abandon, échec, interruption) : exécution ou coût incertains.
# Aucun délai ne prouve l'absence de facturation : réservation conservée et dépenses bloquées jusqu'à une
# résolution signée par le propriétaire, cohérente avec le solde observé. Un débit plus tardif est détecté
# (dépense plateforme > registre) et gèle le moteur.
RESOLUTION_SCHEMA = "ECOM_OS_TT_CALL_RESOLUTION_V1"
RESOLUTION_DECISIONS = ("NO_CHARGE", "CHARGED")
FIELD_MAP_SCHEMA = "ECOM_OS_TT_FIELD_MAP_V1"
# Rôles interprétables par outil; aucun nom de champ supposé : la correspondance est confirmée par signature.
# Rôles ajoutés après le calibrage (facultatifs, à signer en complément) : qualification géographique, liens
# annonceur ↔ annonce, ancienneté et spécialisation.
FIELD_ROLES = {
    "search_ads": ("total", "id", "copy", "landing", "domain", "advertiser", "reach", "active_ads", "media",
                   "advertiser_id", "audience_country", "first_seen", "ad_status"),
    "search_advertisers": ("total", "id", "name", "active_ads", "page_created"),
    "search_shops": ("total", "domain", "name", "products", "visits", "main_category", "shop_country"),
}
LIVE_APPROVAL_SCHEMA = "ECOM_OS_TT_LIVE_APPROVAL_V1"
HOOK_BALANCE_SOURCE = "HOOK_POSTTOOLUSE"
OPENSSL = "/usr/bin/openssl" if Path("/usr/bin/openssl").exists() else (shutil.which("openssl") or "openssl")
# Recherche non financière : ces champs ne sont jamais conservés (hors bloc `credits`).
FINANCIAL_KEY_RE = re.compile(r"(price|revenue|sales|spend|cpm|currenc|units_?sold|margin|profit|roas|ltv)", re.I)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(d: dt.datetime | None = None) -> str:
    return (d or _now()).isoformat(timespec="seconds")


def canonical_fingerprint(tool: str, params: dict) -> str:
    raw = json.dumps({"tool": tool, "params": params}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _looks_french(terms: list[str]) -> bool:
    joined = " ".join(terms).lower()
    return any(tok in joined for tok in FRENCH_ACCENT_OR_STOPWORDS)


def _clean_domain(url_or_domain: str) -> str:
    s = str(url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        s = urlsplit(s).netloc.lower()
    return s.removeprefix("www.").split("/")[0]


def parse_receipt(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("RESPONSE_NOT_OBJECT")
    req_id = raw.get("requestId")
    cr = raw.get("credits")
    if not isinstance(req_id, str) or not req_id.strip():
        raise ValueError("RECEIPT_REQUEST_ID_MISSING")
    if isinstance(cr, dict) and "costUnits" in cr:
        cost = cr["costUnits"]
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
            raise ValueError("RECEIPT_COST_INVALID")
        return {
            "request_id": req_id,
            "cost_units": cost,
            "credits_remaining": cr.get("creditsRemaining"),
            "kind": "METERED",
        }
    if isinstance(cr, dict) and "totalRemaining" in cr:
        return {
            "request_id": req_id,
            "cost_units": 0,
            "credits_remaining": cr["totalRemaining"],
            "kind": "USAGE",
        }
    raise ValueError("RECEIPT_CREDITS_BLOCK_MISSING")


def build_live_params(recipe_id: str, *, country: str, limit: int, terms: list[str], locale: str, linked_domain: str | None,
                      page: int = 1, today: dt.date | None = None) -> dict:
    """Paramètres exacts acceptés par les schémas MCP (`additionalProperties: false`). Aucun filtre ajouté
    au-delà de la recette : `locale` n'existe dans aucun outil ; seule la langue d'annonce de search_ads l'exprime."""
    spec = RECIPES[recipe_id]
    tool = spec["tool"]
    extra = dict(spec.get("live_params") or {})
    query = linked_domain if linked_domain else " ".join(terms)
    page = int(page or 1)
    if page > 1 and tool == "search_advertisers":
        raise ValueError("PAGINATION_UNSUPPORTED_BY_SCHEMA:search_advertisers")
    if tool == "search_ads":
        params = {"query": query, "limit": int(limit), "country": country, **extra}
        if locale:
            params["ad_language"] = locale.lower()
        if spec.get("window_days"):
            params["created_after"] = ((today or _now().date()) - dt.timedelta(days=int(spec["window_days"]))).isoformat()
    elif tool == "search_advertisers":
        params = {"query": query, "limit": int(limit), "country": country, **extra}
    elif tool == "search_shops":
        if spec.get("requires_linked_domain"):
            # Recherche exacte de domaine : aucun filtre géographique (search_shops.country = pays de création).
            params = {"query": query, "limit": int(limit), **extra}
        else:
            params = {"query": query, "limit": int(limit), "main_market_countries": [country], **extra}
    else:
        raise ValueError(f"LIVE_TOOL_NOT_ALLOWED:{tool}")
    if page > 1:
        params["page"] = page
    return params


def _json_or_none(text):
    try:
        v = json.loads(text)
    except Exception:
        return None
    return v if isinstance(v, dict) else None


def extract_mcp_payload(tool_response) -> dict | None:
    """Objet JSON transmis par LLM_Provider runtime au hook PostToolUse (structuredContent, bloc texte JSON ou objet)."""
    if isinstance(tool_response, str):
        return _json_or_none(tool_response)
    if isinstance(tool_response, dict):
        if isinstance(tool_response.get("structuredContent"), dict):
            return tool_response["structuredContent"]
        if isinstance(tool_response.get("content"), list):
            return extract_mcp_payload(tool_response["content"])
        return tool_response
    if isinstance(tool_response, list):
        for block in tool_response:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                obj = _json_or_none(block["text"])
                if obj is not None:
                    return obj
    return None


def extract_rows(payload: dict | None) -> list[dict] | None:
    """Liste des résultats; None si la forme est inattendue (aucune ou plusieurs listes candidates)."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), list):
        cands = [payload["data"]]
    else:
        cands = [payload[k] for k in ("ads", "advertisers", "shops", "results", "items", "rows") if isinstance(payload.get(k), list)]
    if len(cands) != 1 or not all(isinstance(r, dict) for r in cands[0]):
        return None
    return cands[0]


_MISSING = object()


def resolve_path(obj, path: str):
    """Chemin pointé (`meta.total`) dans un objet JSON; `_MISSING` si absent."""
    cur = obj
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


# Type scalaire attendu par rôle de ligne : un objet ou une liste n'est jamais converti en texte.
FIELD_ROLE_TYPES = {
    "id": (str, int), "copy": (str,), "landing": (str,), "domain": (str,), "advertiser": (str,), "media": (str,),
    "name": (str,), "reach": (int, float), "active_ads": (int, float), "products": (int, float), "visits": (int, float),
    "advertiser_id": (str, int), "audience_country": (str,), "first_seen": (str,), "ad_status": (str,), "page_created": (str,),
    "main_category": (str,), "shop_country": (str,),
}
# Classe de fiabilité de chaque champ observé (toolkit V023 §4) ; aucune ne prouve des ventes ni une demande.
FIELD_RELIABILITY = {
    "id": "DIRECT_OBSERVATION", "landing": "DIRECT_OBSERVATION", "domain": "DIRECT_OBSERVATION", "advertiser": "DIRECT_OBSERVATION",
    "media": "DIRECT_OBSERVATION", "name": "DIRECT_OBSERVATION", "advertiser_id": "DIRECT_OBSERVATION", "ad_status": "DIRECT_OBSERVATION",
    "reach": "PLATFORM_REPORTED", "audience_country": "PLATFORM_REPORTED", "page_created": "PLATFORM_REPORTED",
    "active_ads": "INDEX_COUNT", "products": "INDEX_COUNT", "first_seen": "THIRD_PARTY_ESTIMATE",
    "visits": "THIRD_PARTY_ESTIMATE", "shop_country": "THIRD_PARTY_ESTIMATE", "main_category": "INFERENCE",
}
FIELD_OK, FIELD_MISSING, FIELD_TYPE_MISMATCH = "OK", "MISSING", "TYPE_MISMATCH"
FIELD_NOT_MAPPED, FIELD_FINANCIAL_FORBIDDEN = "NOT_MAPPED", "FINANCIAL_FORBIDDEN"


def is_financial_path(path: str) -> bool:
    """Vrai si un segment du chemin pointé est un champ financier exclu (même imbriqué : `metrics.estimatedSpend`)."""
    return any(FINANCIAL_KEY_RE.search(part) for part in str(path).split("."))


def resolve_row_field(row: dict, role: str, path: str | None) -> tuple:
    """(valeur, statut) d'un rôle confirmé : traversée explicite, type scalaire vérifié; aucune valeur inventée."""
    if not isinstance(path, str) or not path:
        return None, FIELD_NOT_MAPPED
    if is_financial_path(path):
        return None, FIELD_FINANCIAL_FORBIDDEN
    v = resolve_path(row, path)
    if v is _MISSING or v is None or v == "":
        return None, FIELD_MISSING
    if isinstance(v, bool) or not isinstance(v, FIELD_ROLE_TYPES.get(role, (str,))):
        return None, FIELD_TYPE_MISMATCH
    return v, FIELD_OK


# ---------- Qualification commerciale (moteur réutilisable ; lexique et formulations fournis par la mission) ----------
QUALIFICATION_SCHEMA = "ECOM_OS_TT_QUALIFICATION_V1"
LEXICON_KEYS = ("problem_terms", "mechanism_terms", "avatar_terms", "substitute_terms", "alternative_context_terms", "anchor_terms")
GENERALIST_PRODUCTS_MIN = 500
GENERALIST_ACTIVE_ADS_MIN = 400
# Verdicts isolés : conservés pour l'audit, exclus des signaux et des opportunités.
ISOLATED_VERDICTS = ("OFF_TOPIC_CANDIDATE", "ALTERNATIVE_CONTEXT", "GENERALIST_UNLINKED", "OUT_OF_MARKET_AUDIENCE")
RELEVANT_VERDICTS = ("RELEVANT", "RELEVANT_VIA_LINKED_AD")


def normalize_text(s) -> str:
    """Minuscules sans accents, ponctuation remplacée par des espaces (comparaison de termes uniquement)."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def match_terms(text_norm: str, terms) -> list[str]:
    """Termes présents en mots entiers (« pou » ne correspond pas à « pour »)."""
    out = []
    for t in terms or []:
        tn = normalize_text(t)
        if tn and re.search(r"(?<![a-z0-9])" + re.escape(tn) + r"(?![a-z0-9])", text_norm):
            out.append(t)
    return out


def qualify_row(fields: dict, lexicon: dict, *, tool: str, source_class: str, geo_semantics: str,
                market_geo: str, linked_ref: str | None = None) -> dict:
    """Qualification explicite d'un résultat : problème, mécanisme, avatar, contexte alternatif, géographie,
    spécialisation, chaîne de liens. Aucun lien ni valeur n'est inventé ; l'absence de texte reste UNVERIFIED."""
    lex = {k: list(lexicon.get(k) or []) for k in LEXICON_KEYS}
    basis = {k: fields.get(k) for k in ("copy", "landing", "advertiser", "name", "domain", "main_category") if fields.get(k)}
    text = normalize_text(" ".join(str(v) for v in basis.values()))
    m = {"problem": match_terms(text, lex["problem_terms"]), "mechanism": match_terms(text, lex["mechanism_terms"]),
         "avatar": match_terms(text, lex["avatar_terms"]), "substitute": match_terms(text, lex["substitute_terms"]),
         "alternative_context": match_terms(text, lex["alternative_context_terms"]),
         "anchor": match_terms(text, lex["anchor_terms"])}
    counter = []
    has_copy = bool(fields.get("copy"))
    if tool == "search_ads" and not has_copy:
        verdict = "UNVERIFIED_NO_TEXT"
    elif tool == "search_ads":
        if m["problem"] and m["alternative_context"] and not m["anchor"]:
            verdict = "ALTERNATIVE_CONTEXT"
        elif m["problem"] and m["alternative_context"]:
            # Ancre générique + autre avatar/usage (ex. « incontinence » + « bébé ») : jamais confirmé, conservé ambigu.
            verdict = "AMBIGUOUS_ALTERNATIVE_CONTEXT"
        elif m["problem"]:
            verdict = "RELEVANT"
        elif m["mechanism"] or m["avatar"]:
            verdict = "AMBIGUOUS_NO_PROBLEM_TERM"
        else:
            verdict = "OFF_TOPIC_CANDIDATE"
    elif tool == "search_advertisers":
        # La réponse ne contient aucun texte d'annonce : pertinence seulement via une annonce pertinente déjà observée.
        if linked_ref:
            verdict = "RELEVANT_VIA_LINKED_AD"
        elif m["problem"]:
            verdict = "NAME_MATCH_UNVERIFIED"
        else:
            verdict = "UNVERIFIED_NO_TEXT"
    else:
        verdict = "RELEVANT_VIA_LINKED_AD" if linked_ref else ("NAME_MATCH_UNVERIFIED" if m["problem"] else "UNLINKED_UNVERIFIED")
    products, active_ads = fields.get("products"), fields.get("active_ads")
    if (isinstance(products, (int, float)) and products >= GENERALIST_PRODUCTS_MIN) or \
            (isinstance(active_ads, (int, float)) and active_ads >= GENERALIST_ACTIVE_ADS_MIN):
        specialization = "GENERALIST"
        counter.append("GENERALIST_ACTOR")
        if not linked_ref and tool != "search_ads":
            verdict = "GENERALIST_UNLINKED"
    elif match_terms(normalize_text(f"{fields.get('domain') or ''} {fields.get('name') or fields.get('advertiser') or ''}"),
                     lex["problem_terms"] + lex["mechanism_terms"]):
        specialization = "SPECIALIZED_CANDIDATE"
    else:
        specialization = "UNKNOWN"
    aud = fields.get("audience_country")
    if aud:
        geo = "TARGET_AUDIENCE_OBSERVED" if str(aud).upper() == market_geo else f"OTHER_AUDIENCE_OBSERVED:{str(aud).upper()}"
        if str(aud).upper() != market_geo:
            counter.append(f"AUDIENCE_NOT_{market_geo}")
            # Audience observée hors marché : isolée (jamais comptée ni suivie vers une boutique). Ne s'applique pas
            # au pays d'origine d'une boutique (`shop_country`), qui n'est pas celui de ses clients.
            verdict = "OUT_OF_MARKET_AUDIENCE"
    else:
        geo = f"REQUEST_FILTER_ONLY:{geo_semantics}"
    counter += [f"SUBSTITUTE_MENTIONED:{t}" for t in m["substitute"]]
    counter += [f"ALTERNATIVE_CONTEXT:{t}" for t in m["alternative_context"]]
    if fields.get("shop_country") and str(fields["shop_country"]).upper() != market_geo:
        counter.append(f"SHOP_ORIGIN_{str(fields['shop_country']).upper()}_NOT_CUSTOMER_MARKET")
    landing = str(fields.get("landing") or "")
    chain = {}
    if tool == "search_ads":
        chain = {"ad": "OBSERVED", "advertiser": "OBSERVED" if fields.get("advertiser") else "MISSING",
                 "domain": "OBSERVED" if fields.get("domain") else "MISSING", "shop": "NOT_LOOKED_UP",
                 "product": "PRODUCT_URL_OBSERVED_NOT_VERIFIED" if "/products/" in landing else "MISSING"}
    elif tool == "search_shops":
        chain = {"ad": f"LINKED:{linked_ref}" if linked_ref else "MISSING", "shop": "OBSERVED", "product": "MISSING"}
    else:
        chain = {"advertiser": "OBSERVED", "ad": f"LINKED:{linked_ref}" if linked_ref else "UNVERIFIED"}
    return {"schema": QUALIFICATION_SCHEMA, "verdict": verdict, "isolated": verdict in ISOLATED_VERDICTS,
            "matches": {k: v for k, v in m.items() if v}, "text_basis": sorted(basis), "geo": geo,
            "specialization": specialization, "link_chain": chain, "evidence_level": source_class,
            "counter_signals": counter}


# ---------- Plan d'investigation de mission (données de mission ; le moteur ne connaît aucun marché) ----------
PLAN_SCHEMA = "ECOM_OS_TT_INVESTIGATION_PLAN_V1"
PLAN_FILENAME = "TT_INVESTIGATION_PLAN.json"
FACETS = ("PROBLEM", "CUSTOMER_PHRASE", "MECHANISM", "PROMISE", "AVATAR", "OBJECTION", "SYNONYM", "COUNTER", "DOMAIN")
WAVES = ("V1_FOCUSED", "V2_RELAX_ONE", "V3_COUNTER_SAMPLE")
STRATEGIES = ("S1", "S2", "S3", "S4")
DEFAULT_STOP_RULES = {"max_consecutive_unproductive": 2, "max_isolated_ratio": 0.6, "min_rows_before_ratio": 6,
                      "page_min_new_relevant": 2}
LOT_FINISHED = ("DONE", "STOPPED_DIMINISHING_RETURNS", "STOPPED_OFF_TOPIC", "NO_QUALIFIED_QUERY", "CONDITION_NOT_MET",
                "BUDGET_EXHAUSTED", "NO_LINKED_DOMAIN")


def validate_plan(plan: dict) -> list[str]:
    """Erreurs bloquantes d'un plan ; un plan invalide ne peut ni piloter ni être lié à une approbation autonome."""
    errs = []
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return ["PLAN_SCHEMA_INVALID"]
    for k in ("mission_id", "scope", "market_geo", "locale", "deliverables_dir"):
        if not isinstance(plan.get(k), str) or not plan[k]:
            errs.append(f"PLAN_FIELD_MISSING:{k}")
    if errs:
        return errs
    if not re.fullmatch(r"[A-Z]{2}", plan["market_geo"]):
        errs.append("PLAN_MARKET_GEO_NOT_ISO2")
    dd = plan["deliverables_dir"]
    if dd.startswith(("/", ".")) or ".." in Path(dd).parts or Path(dd).parts[0] == "MARKETPULSE":
        errs.append("PLAN_DELIVERABLES_DIR_OUTSIDE_MISSION_OR_IN_ENGINE_STATE")
    markets, budgets = plan.get("markets") or {}, plan.get("budgets") or {}
    if not markets or not all(isinstance(m, dict) and isinstance(m.get("lexicon"), dict) for m in markets.values()):
        errs.append("PLAN_MARKETS_WITH_LEXICON_REQUIRED")
    else:
        for mk, m in markets.items():
            bad = [k for k in m["lexicon"] if k not in LEXICON_KEYS]
            if bad or not m["lexicon"].get("problem_terms"):
                errs.append(f"PLAN_LEXICON_INVALID:{mk}")
    if not budgets or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in budgets.values()):
        errs.append("PLAN_BUDGETS_INVALID")
    elif sum(budgets.values()) > LIVE_MAX_CREDITS_PER_APPROVAL:
        errs.append(f"PLAN_BUDGETS_ABOVE_{LIVE_MAX_CREDITS_PER_APPROVAL}")
    seen = set()
    for lot in plan.get("lots") or []:
        lid = lot.get("lot_id")
        if not isinstance(lid, str) or not lid or lid in seen:
            errs.append(f"PLAN_LOT_ID_INVALID_OR_DUPLICATE:{lid}")
            continue
        seen.add(lid)
        spec = RECIPES.get(lot.get("recipe"))
        if not spec or "live_params" not in spec or spec.get("live_blocked") or spec["tool"] not in LIVE_TOOLS:
            errs.append(f"PLAN_RECIPE_NOT_LIVE_USABLE:{lid}:{lot.get('recipe')}")
            continue
        if spec.get("foreign_only"):
            errs.append(f"PLAN_FOREIGN_RECIPE_FORBIDDEN:{lid}")
        if lot.get("market") not in markets:
            errs.append(f"PLAN_LOT_MARKET_UNKNOWN:{lid}")
        if lot.get("budget") not in budgets:
            errs.append(f"PLAN_LOT_BUDGET_UNKNOWN:{lid}")
        if lot.get("strategy") not in STRATEGIES or lot.get("strategy") != spec.get("strategy"):
            errs.append(f"PLAN_LOT_STRATEGY_MISMATCH:{lid}")
        if lot.get("wave", "V1_FOCUSED") not in WAVES:
            errs.append(f"PLAN_LOT_WAVE_INVALID:{lid}")
        if lot.get("facet") not in FACETS:
            errs.append(f"PLAN_LOT_FACET_INVALID:{lid}")
        sources = [k for k in ("queries", "queries_from", "domains_from") if lot.get(k)]
        if len(sources) != 1:
            errs.append(f"PLAN_LOT_NEEDS_ONE_SOURCE:{lid}")
        elif "domains_from" in sources and not spec.get("requires_linked_domain"):
            errs.append(f"PLAN_DOMAINS_FROM_REQUIRES_LINKED_SHOP_RECIPE:{lid}")
        elif spec.get("requires_linked_domain") and "domains_from" not in sources:
            errs.append(f"PLAN_LINKED_SHOP_REQUIRES_DOMAINS_FROM:{lid}")
        for q in lot.get("queries") or []:
            if not isinstance(q, str) or not 2 <= len(q.strip()) <= 200:
                errs.append(f"PLAN_QUERY_INVALID:{lid}")
        pages, start = int(lot.get("pages", 1)), int(lot.get("start_page", 1))
        if pages < 1 or start < 1 or start + pages - 1 > int(spec.get("max_page", 1)):
            errs.append(f"PLAN_PAGES_ABOVE_RECIPE_MAX:{lid}")
        if start > 1 and not (lot.get("condition") and (lot.get("queries_from") or {}).get("same_queries")):
            errs.append(f"PLAN_LATER_PAGE_REQUIRES_PRODUCTIVITY_CONDITION:{lid}")
        for ref in [(lot.get("queries_from") or {}).get("lot"), (lot.get("condition") or {}).get("lot"),
                    *((lot.get("domains_from") or {}).get("after_lots") or [])]:
            if ref is not None and ref not in seen:
                errs.append(f"PLAN_LOT_REFERENCE_MUST_PRECEDE:{lid}->{ref}")
    if not seen:
        errs.append("PLAN_HAS_NO_LOTS")
    return errs


def _lot_yields(yields: list[dict], lot_id: str) -> list[dict]:
    return [y for y in yields if y.get("lot_id") == lot_id]


def _query_ranking(yields: list[dict], lot_id: str, min_total: int) -> list[str]:
    """Formulations d'une sonde S1 classées : premier résultat pertinent puis compteur d'index (jamais une demande)."""
    rows = [y for y in _lot_yields(yields, lot_id) if isinstance(y.get("index_total"), int) and y["index_total"] >= min_total
            and y.get("probe_row_verdict") in RELEVANT_VERDICTS]
    return [y["query"] for y in sorted(rows, key=lambda y: -y["index_total"])]


def lot_state(plan: dict, lot: dict, yields: list[dict], evidence: list[dict], states: dict, spent_by_budget: dict) -> dict:
    """État d'un lot et prochain appel candidat ; arrêt sur rendement décroissant, hors-sujet ou budget."""
    spec = RECIPES[lot["recipe"]]
    rules = {**DEFAULT_STOP_RULES, **(plan.get("stop_rules") or {})}
    ly = _lot_yields(yields, lot["lot_id"])
    done = {(y["query"], int(y.get("page") or 1)) for y in ly}
    tail = 0
    for y in reversed(ly):
        if y.get("new_relevant", 0) > 0:
            break
        tail += 1
    rows = sum(y.get("rows", 0) for y in ly)
    isolated = sum(y.get("isolated", 0) for y in ly)
    base = {"lot_id": lot["lot_id"], "calls": len(ly), "credits": sum(y.get("cost", 0) for y in ly),
            "new_relevant": sum(y.get("new_relevant", 0) for y in ly), "rows": rows, "isolated": isolated}
    for dep in [(lot.get("queries_from") or {}).get("lot"), (lot.get("condition") or {}).get("lot"),
                *((lot.get("domains_from") or {}).get("after_lots") or [])]:
        if dep and states.get(dep, {}).get("status") not in LOT_FINISHED:
            return {**base, "status": "WAITING_FOR_LOT", "waiting_for": dep}
    cond = lot.get("condition")
    if cond and states[cond["lot"]].get("new_relevant", 0) < int(cond.get("min_new_relevant", 1)):
        return {**base, "status": "CONDITION_NOT_MET"}
    if lot["strategy"] != "S1" and ly and tail >= int(rules["max_consecutive_unproductive"]):
        return {**base, "status": "STOPPED_DIMINISHING_RETURNS"}
    if rows >= int(rules["min_rows_before_ratio"]) and isolated / rows >= float(rules["max_isolated_ratio"]):
        return {**base, "status": "STOPPED_OFF_TOPIC"}
    # Candidats ordonnés.
    cands = []
    if lot.get("domains_from"):
        df = lot["domains_from"]
        excl = {normalize_text(d) for d in plan.get("domain_exclusions") or []}
        looked = {e.get("entity_id") for e in evidence if e.get("entity_scope") == "SHOP"} | {q for q, _ in done}
        mk_lots = {l["lot_id"] for l in plan["lots"] if l["market"] in (df.get("markets") or [lot["market"]])}
        seen_dom = set()
        for e in evidence:
            dom = e.get("shop_domain")
            if (e.get("entity_scope") == "AD" and dom and e.get("lot_id") in mk_lots and dom not in seen_dom
                    and (e.get("qualification") or {}).get("verdict") in RELEVANT_VERDICTS
                    and normalize_text(dom) not in excl and dom not in looked):
                seen_dom.add(dom)
                cands.append({"terms": [], "page": 1, "linked_domain": dom, "linked_ad_ref": e["source_ref"], "query": dom})
        cands = cands[: max(0, int(df.get("max", 0)) - len(done))]
        if not cands and not ly:
            return {**base, "status": "NO_LINKED_DOMAIN"}
    else:
        if lot.get("queries"):
            queries = [q.strip() for q in lot["queries"]]
        else:
            qf = lot["queries_from"]
            queries = (_query_ranking(yields, qf["lot"], int(qf.get("min_total", 1)))[: int(qf.get("top_k", 1))]
                       if not qf.get("same_queries") else sorted({y["query"] for y in _lot_yields(yields, qf["lot"])}))
            if not queries:
                return {**base, "status": "NO_QUALIFIED_QUERY"}
        start_page = int(lot.get("start_page", 1))
        # Pages précédentes : ce lot et, pour un lot V2 (`same_queries`), le lot source de même recette.
        src = (lot.get("queries_from") or {}).get("lot") if (lot.get("queries_from") or {}).get("same_queries") else None
        prev_pool = ly + [y for y in _lot_yields(yields, src) if y.get("recipe_id") == lot["recipe"]] if src else ly
        for q in queries:
            for p in range(start_page, start_page + int(lot.get("pages", 1))):
                if p > 1:
                    prev = next((y for y in prev_pool if y["query"] == q and int(y.get("page") or 1) == p - 1), None)
                    # Page suivante seulement si la précédente était pleine ET productive (jamais pour remplir un quota),
                    # y compris pour la première page d'un lot V2 (start_page > 1).
                    if not prev or prev.get("rows", 0) < spec["limit"] or prev.get("new_relevant", 0) < int(rules["page_min_new_relevant"]):
                        break
                cands.append({"terms": [q], "page": p, "linked_domain": None, "linked_ad_ref": None, "query": q})
    todo = [c for c in cands if (c["query"], c["page"]) not in done]
    if not todo:
        return {**base, "status": "DONE"}
    cost = int(spec["max_credits"])
    if spent_by_budget.get(lot["budget"], 0) + cost > int(plan["budgets"][lot["budget"]]):
        return {**base, "status": "BUDGET_EXHAUSTED"}
    if lot.get("max_credits") and base["credits"] + cost > int(lot["max_credits"]):
        return {**base, "status": "BUDGET_EXHAUSTED"}
    c = todo[0]
    return {**base, "status": "READY", "next": {"lot_id": lot["lot_id"], "recipe_id": lot["recipe"], "terms": c["terms"],
                                               "page": c["page"], "linked_domain": c["linked_domain"],
                                               "linked_ad_ref": c["linked_ad_ref"], "facet": lot["facet"],
                                               "market": lot["market"], "wave": lot.get("wave", "V1_FOCUSED"),
                                               "strategy": lot["strategy"], "max_credits": cost}}


def plan_states(plan: dict, yields: list[dict], evidence: list[dict], reserved_by_budget: dict | None = None) -> dict:
    """États de tous les lots (ordre du plan) ; le premier lot READY fournit l'appel suivant."""
    spent = dict(reserved_by_budget or {})
    lots_by_id = {l["lot_id"]: l for l in plan["lots"]}
    for y in yields:
        b = lots_by_id.get(y.get("lot_id"), {}).get("budget")
        if b:
            spent[b] = spent.get(b, 0) + int(y.get("cost", 0))
    states: dict = {}
    for lot in plan["lots"]:
        states[lot["lot_id"]] = lot_state(plan, lot, yields, evidence, states, spent)
    nxt = next((s["next"] for s in states.values() if s["status"] == "READY"), None)
    return {"states": states, "next": nxt, "spent_by_budget": spent,
            "status": "READY" if nxt else ("WAITING" if any(s["status"] == "WAITING_FOR_LOT" for s in states.values())
                                           else "PLAN_COMPLETE")}


def _iso_us(d: dt.datetime | None = None) -> str:
    return (d or _now()).isoformat(timespec="microseconds")


def _ts(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except Exception:
        return None


def redact_financial(obj):
    if isinstance(obj, dict):
        return {k: (v if k == "credits" else redact_financial(v)) for k, v in obj.items() if k == "credits" or not FINANCIAL_KEY_RE.search(k)}
    if isinstance(obj, list):
        return [redact_financial(v) for v in obj]
    return obj


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_signature(pubkey: Path, data: Path, sig: Path) -> bool:
    try:
        r = subprocess.run([OPENSSL, "dgst", "-sha256", "-verify", str(pubkey), "-signature", str(sig), str(data)],
                           capture_output=True, timeout=20)
    except Exception:
        return False
    return r.returncode == 0 and b"Verified OK" in r.stdout


def owner_keygen(key_dir: Path) -> dict:
    """À exécuter par le propriétaire dans SON Terminal : openssl demande la phrase secrète (jamais vue par LLM_Provider)."""
    key_dir = Path(key_dir)
    priv, pub = key_dir / "owner_signing_key.pem", key_dir / "owner_signing_pub.pem"
    if priv.exists():
        raise FileExistsError(f"OWNER_KEY_ALREADY_EXISTS:{priv}")
    key_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(key_dir, 0o700)
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-aes-256-cbc", "-out", str(priv)], check=True)
    os.chmod(priv, 0o600)
    subprocess.run([OPENSSL, "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True)
    return {"private_key": str(priv), "public_key": str(pub), "public_key_sha256": sha256_file(pub),
            "next_step": "Installer la clé publique dans .claude/ecom_owner_pubkey.pem et épingler son sha256 dans le hook."}


class MarketPulseAdapter:
    def __init__(self, workdir: Path, *, mode: str = "REPLAY", connected: bool = True, reserve_floor: int = DEFAULT_RESERVE_FLOOR,
                 owner_pubkey: Path | None = None, owner_pubkey_sha256: str | None = None):
        if mode not in ("REPLAY", "FILE_DROP", "MCP_SESSION", "DISCONNECTED"):
            raise ValueError(f"UNKNOWN_MARKETPULSE_MODE:{mode}")
        self.dir = Path(workdir)
        if mode != LIVE_MODE and (self.dir / "STATE" / "live_approvals").exists():
            # Empêche de régler/annuler une réservation LIVE par le chemin REPLAY (réponse saisie à la main).
            raise PermissionError("LIVE_STATE_REQUIRES_MCP_SESSION")
        self.mode = mode
        self.connected = connected and (mode != "DISCONNECTED")
        self.live = mode == LIVE_MODE
        self.reserve_floor = max(reserve_floor, LIVE_MIN_RESERVE_FLOOR) if self.live else reserve_floor
        self.owner_pubkey = Path(owner_pubkey) if owner_pubkey else None
        self.owner_pubkey_sha256 = owner_pubkey_sha256
        for sub in ("OUTBOX", "INBOX", "STATE", "CACHE", "EVIDENCE"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.p_balance = self.dir / "STATE" / "balance.json"
        self.p_approvals = self.dir / "STATE" / "approvals.json"
        self.p_reservations = self.dir / "STATE" / "reservations.json"
        self.p_pending = self.dir / "STATE" / "pending.json"
        self.p_frozen = self.dir / "STATE" / "frozen_tools.json"
        self.p_ledger = self.dir / "STATE" / "credit_ledger.jsonl"
        self.p_calls = self.dir / "STATE" / "call_log.jsonl"
        self.p_halt = self.dir / "STATE" / "HALT.json"
        self.p_baselines = self.dir / "STATE" / "live_baselines.json"
        self.d_live_approvals = self.dir / "STATE" / "live_approvals"

    @contextlib.contextmanager
    def _lock(self):
        """Verrou exclusif inter-processus (appels concurrents moteur / hooks)."""
        with (self.dir / "STATE" / ".lock").open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write(self, p: Path, data) -> None:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)

    def _log_call(self, entry: dict) -> None:
        with self.p_calls.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _iso(), "mode": self.mode, **entry}, ensure_ascii=False) + "\n")

    def _log_credit(self, entry: dict) -> None:
        with self.p_ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _iso(), "mode": self.mode, **entry}, ensure_ascii=False) + "\n")

    def capabilities(self) -> dict:
        if not self.connected or self.mode == "DISCONNECTED":
            return {
                "connected": False,
                "status": "BLOCKED_CONNECTOR",
                "read_only": True,
                "verified_at_runtime": False,
                "reason": "MARKETPULSE_CONNECTOR_NOT_CONNECTED",
            }
        bal = self._read(self.p_balance, None)
        if not bal:
            return {
                "connected": False,
                "status": "BLOCKED_CONNECTOR",
                "read_only": True,
                "verified_at_runtime": False,
                "reason": "BALANCE_SNAPSHOT_MISSING_RUN_USAGE_GET_FIRST",
            }
        age = _now() - dt.datetime.fromisoformat(bal["observed_at"])
        if age > dt.timedelta(hours=BALANCE_MAX_AGE_HOURS):
            return {
                "connected": False,
                "status": "BLOCKED_CONNECTOR",
                "read_only": True,
                "verified_at_runtime": False,
                "reason": "BALANCE_SNAPSHOT_STALE_OVER_24H",
            }
        if self.live and bal.get("source") != HOOK_BALANCE_SOURCE:
            return {
                "connected": False,
                "status": "BLOCKED_CONNECTOR",
                "read_only": True,
                "verified_at_runtime": False,
                "reason": "LIVE_BALANCE_NOT_CAPTURED_BY_HOOK",
            }
        return {
            "connected": True,
            "status": "READY",
            "read_only": True,
            "verified_at_runtime": False,  # Offline record is not an independent provider capability check
            "mode": self.mode,
            "replay": self.mode == "REPLAY",
            "credits_remaining": bal["credits_remaining"],
            "surfaces": ["ads", "shops", "advertisers", "products"],
            "countries": ["FR", "US", "GB", "CA", "AU", "DE", "ES", "IT", "NL"],
        }

    def ingest_balance(self, raw_usage: dict) -> dict:
        if self.live:
            raise PermissionError("LIVE_BALANCE_ONLY_FROM_HOOK_CAPTURE")
        rec = parse_receipt(raw_usage)
        if rec["kind"] != "USAGE":
            raise ValueError("NOT_A_USAGE_SNAPSHOT")
        snap = {
            "credits_remaining": rec["credits_remaining"],
            "request_id": rec["request_id"],
            "observed_at": _iso(),
            "mode": self.mode,
        }
        self._write(self.p_balance, snap)
        self._log_call({"event": "BALANCE", **snap})
        return snap

    def record_replay_approval(
        self,
        *,
        scope: str,
        max_credits: int,
        owner_statement: str,
        tools: list[str] | None = None,
        uncalibrated_tools: list[str] | None = None,
        per_call_ceiling: int | None = None,
    ) -> dict:
        """Enregistre une approbation de crédit bornée (en mode REPLAY uniquement pour les tests/démos)."""
        if self.mode != "REPLAY":
            raise PermissionError("LIVE_CREDIT_APPROVAL_REQUIRES_INTERACTIVE_OWNER_CONFIRMATION")
        if max_credits < 1 or len(owner_statement.strip()) < 12:
            raise ValueError("INVALID_APPROVAL_PARAMETERS")
        for t in (tools or []) + (uncalibrated_tools or []):
            if t in FORBIDDEN_WRITE_TOOLS:
                raise ValueError(f"FORBIDDEN_WRITE_TOOL:{t}")
        if uncalibrated_tools and not per_call_ceiling:
            raise ValueError("UNCALIBRATED_TOOLS_REQUIRE_PER_CALL_CEILING")
        appr = {
            "approval_id": "TT-APR-" + uuid.uuid4().hex[:10],
            "scope": scope,
            "max_credits": int(max_credits),
            "tools": tools or list(MEASURED_COSTS_PER_ROW),
            "uncalibrated_tools": uncalibrated_tools or [],
            "per_call_ceiling": per_call_ceiling,
            "owner_statement": owner_statement.strip(),
            "recorded_at": _iso(),
            "expires_at": _iso(_now() + dt.timedelta(hours=48)),
            "provenance_mode": "REPLAY",
        }
        approvals = self._read(self.p_approvals, [])
        approvals.append(appr)
        self._write(self.p_approvals, approvals)
        self._log_credit({"event": "APPROVAL_RECORDED", **appr})
        return appr

    # ---------- Approbations LIVE signées ----------
    def _pubkey_ok(self) -> bool:
        if not self.owner_pubkey or not self.owner_pubkey.is_file():
            return False
        return not self.owner_pubkey_sha256 or sha256_file(self.owner_pubkey) == self.owner_pubkey_sha256

    def _validate_live_approval(self, a: dict) -> bool:
        try:
            issued = dt.datetime.fromisoformat(a["issued_at"])
            expires = dt.datetime.fromisoformat(a["expires_at"])
            tools = a["tools"]
            return (
                a.get("schema") == LIVE_APPROVAL_SCHEMA
                and isinstance(a.get("max_credits"), int) and not isinstance(a["max_credits"], bool)
                and 1 <= a["max_credits"] <= LIVE_MAX_CREDITS_PER_APPROVAL
                and isinstance(tools, list) and tools and set(tools) <= LIVE_TOOLS
                and not a.get("uncalibrated_tools")
                and int(a.get("reserve_floor", 0)) >= LIVE_MIN_RESERVE_FLOOR
                and issued <= _now() < expires
                and expires - issued <= dt.timedelta(hours=LIVE_APPROVAL_MAX_HOURS)
                and bool(a.get("scope")) and bool(a.get("mission_id"))
                and (not a.get("autonomous") or (a.get("autonomous") is True
                                                 and isinstance(a.get("plan_sha256"), str)
                                                 and re.fullmatch(r"[0-9a-f]{64}", a["plan_sha256"]) is not None))
            )
        except Exception:
            return False

    # ---------- Plan d'investigation (mission) ----------
    @property
    def plan_path(self) -> Path:
        return self.dir.parent / PLAN_FILENAME

    def load_plan(self) -> tuple[dict | None, str | None, list[str]]:
        """(plan, sha256 du fichier, erreurs). Le plan appartient à la mission, jamais au moteur."""
        if not self.plan_path.is_file():
            return None, None, ["PLAN_FILE_MISSING"]
        sha = sha256_file(self.plan_path)
        plan = self._read(self.plan_path, None)
        errs = validate_plan(plan)
        if not errs and plan["mission_id"] != self.dir.parent.name:
            errs.append("PLAN_MISSION_MISMATCH")
        return (plan if not errs else None), sha, errs

    def _yields(self) -> list[dict]:
        p = self.dir / "STATE" / "yield.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def _all_evidence(self) -> list[dict]:
        out = []
        for evf in sorted((self.dir / "EVIDENCE").glob("*.jsonl")):
            out += [json.loads(x) for x in evf.read_text(encoding="utf-8").splitlines() if x.strip()]
        return out

    def plan_status(self) -> dict:
        plan, sha, errs = self.load_plan()
        if errs:
            return {"status": "PLAN_INVALID", "errors": errs, "plan_sha256": sha}
        reserved: dict = {}
        lots = {l["lot_id"]: l for l in plan["lots"]}
        for e in self._read(self.p_pending, {}).values():
            b = lots.get(e.get("lot_id"), {}).get("budget")
            if b:
                reserved[b] = reserved.get(b, 0) + int(e.get("max_credits") or 0)
        st = plan_states(plan, self._yields(), self._all_evidence(), reserved)
        return {"plan_sha256": sha, "scope": plan["scope"], "budgets": plan["budgets"], **st}

    def next_call(self) -> dict:
        """Réserve l'appel suivant du plan (tt next) ; aucun appel hors plan, aucun quota à remplir."""
        st = self.plan_status()
        if st["status"] != "READY":
            return {"status": st["status"], "errors": st.get("errors"),
                    "lots": {k: v["status"] for k, v in (st.get("states") or {}).items()}}
        plan, _, _ = self.load_plan()
        n = st["next"]
        res = self.plan_call(mission_id=plan["mission_id"], scope=plan["scope"], recipe_id=n["recipe_id"],
                             country=plan["market_geo"], stage=5, terms=n["terms"], locale=plan["locale"],
                             linked_domain=n["linked_domain"], linked_ad_ref=n["linked_ad_ref"], page=n["page"],
                             lot_id=n["lot_id"])
        if res.get("status") == "CACHE_HIT":
            # Réponse déjà connue : 0 crédit, aucune entité nouvelle ; le rendement nul est enregistré pour que le lot avance.
            ents = [e for e in res.get("evidence") or [] if e.get("entity_scope") != "MARKET_COUNT"]
            tot = next((e.get("index_count") for e in res.get("evidence") or [] if e.get("entity_scope") == "MARKET_COUNT"), None)
            y = {"call_id": f"CACHE-{res['fingerprint']}-{uuid.uuid4().hex[:6]}", "lot_id": n["lot_id"], "recipe_id": n["recipe_id"],
                 "tool": RECIPES[n["recipe_id"]]["tool"], "query": n["linked_domain"] or " ".join(n["terms"]), "page": n["page"],
                 "cost": 0, "rows": len(ents), "new_entities": 0, "new_relevant": 0,
                 "isolated": sum(1 for e in ents if (e.get("qualification") or {}).get("isolated")), "duplicates": len(ents),
                 "index_total": tot, "probe_row_verdict": ((ents[0].get("qualification") or {}).get("verdict") if ents else None),
                 "outcome": "CACHE_HIT", "at": _iso()}
            with self._lock():
                with (self.dir / "STATE" / "yield.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(y, ensure_ascii=False) + "\n")
        return {**res, "lot": n}

    def live_approvals(self) -> list[dict]:
        """Approbations dont la signature est vérifiée avec la clé publique du propriétaire et les bornes respectées."""
        if not self._pubkey_ok() or not self.d_live_approvals.is_dir():
            return []
        out = []
        for jf in sorted(self.d_live_approvals.glob("*.json")):
            sig = jf.with_suffix(".sig")
            if not sig.is_file() or not verify_signature(self.owner_pubkey, jf, sig):
                continue
            a = self._read(jf, None)
            if isinstance(a, dict) and a.get("approval_id") == jf.stem and self._validate_live_approval(a):
                out.append(a)
        return sorted(out, key=lambda a: a["issued_at"])

    def prepare_live_approval(self, *, mission_id: str, scope: str, max_credits: int, hours: int = 24,
                              tools: list[str] | None = None, autonomous: bool = False) -> Path:
        """Écrit le document d'approbation non signé; seul le propriétaire peut le signer.
        `autonomous` : lie l'approbation à l'empreinte du plan valide de la mission (portée identique) ; toute
        modification ultérieure du plan désactive l'autonomie (retour à la confirmation manuelle)."""
        if not self.live:
            raise PermissionError("LIVE_APPROVAL_REQUIRES_MCP_SESSION_MODE")
        if not 1 <= int(max_credits) <= LIVE_MAX_CREDITS_PER_APPROVAL:
            raise ValueError(f"LIVE_MAX_CREDITS_OUT_OF_BOUNDS:1..{LIVE_MAX_CREDITS_PER_APPROVAL}")
        if not 1 <= int(hours) <= LIVE_APPROVAL_MAX_HOURS:
            raise ValueError(f"LIVE_APPROVAL_HOURS_OUT_OF_BOUNDS:1..{LIVE_APPROVAL_MAX_HOURS}")
        tools = sorted(tools or LIVE_TOOLS)
        if not set(tools) <= LIVE_TOOLS:
            raise ValueError("LIVE_TOOLS_OUTSIDE_CALIBRATED_SET")
        plan_fields = {}
        if autonomous:
            plan, sha, errs = self.load_plan()
            if errs:
                raise ValueError(f"AUTONOMOUS_REQUIRES_VALID_PLAN:{','.join(errs)}")
            if plan["mission_id"] != mission_id or plan["scope"] != scope:
                raise ValueError("AUTONOMOUS_PLAN_MISSION_OR_SCOPE_MISMATCH")
            if sum(plan["budgets"].values()) > int(max_credits):
                raise ValueError("AUTONOMOUS_PLAN_BUDGETS_ABOVE_APPROVAL")
            plan_fields = {"autonomous": True, "plan_sha256": sha, "plan_file": PLAN_FILENAME}
        self.d_live_approvals.mkdir(parents=True, exist_ok=True)
        now = _now()
        appr = {
            "schema": LIVE_APPROVAL_SCHEMA,
            "approval_id": "TT-LIVE-" + uuid.uuid4().hex[:10],
            "mission_id": mission_id,
            "scope": scope,
            "max_credits": int(max_credits),
            "tools": tools,
            "uncalibrated_tools": [],
            "reserve_floor": self.reserve_floor,
            "issued_at": now.isoformat(timespec="microseconds"),
            "expires_at": _iso(now + dt.timedelta(hours=int(hours))),
            **plan_fields,
        }
        p = self.d_live_approvals / f"{appr['approval_id']}.json"
        p.write_text(json.dumps(appr, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p

    def _sign_document(self, path: Path, private_key: Path) -> dict:
        """openssl lit la phrase secrète sur le terminal du propriétaire; échec = document supprimé."""
        sig = path.with_suffix(".sig")
        r = subprocess.run([OPENSSL, "dgst", "-sha256", "-sign", str(private_key), "-out", str(sig), str(path)])
        if r.returncode != 0 or not self._pubkey_ok() or not verify_signature(self.owner_pubkey, path, sig):
            path.unlink(missing_ok=True)
            sig.unlink(missing_ok=True)
            raise PermissionError("OWNER_SIGNATURE_INVALID_OR_PUBKEY_MISSING")
        return self._read(path, {})

    def sign_live_approval(self, approval_path: Path, private_key: Path) -> dict:
        appr = self._sign_document(approval_path, private_key)
        self._log_credit({"event": "LIVE_APPROVAL_SIGNED", **appr})
        return appr

    # ---------- Correspondance des champs de réponse (confirmée par le propriétaire après calibrage) ----------
    def calibration_payload(self, tool: str) -> dict | None:
        return self._read(self.dir / "STATE" / "calibration" / f"{tool}.json", None)

    def calibration_summary(self, tool: str) -> dict:
        """Clés réellement observées (sans valeurs) pour choisir la correspondance."""
        cal = self.calibration_payload(tool)
        if not cal:
            return {"status": "NO_CALIBRATION_CAPTURED", "tool": tool}
        payload = cal["payload"]
        rows = extract_rows(payload)

        def walk(o, prefix=""):
            out = []
            if isinstance(o, dict):
                for k, v in o.items():
                    out.append(prefix + k)
                    if isinstance(v, dict):
                        out += walk(v, prefix + k + ".")
            return out
        return {"status": "CALIBRATION_CAPTURED", "tool": tool, "call_id": cal["call_id"], "payload_paths": walk(payload),
                "row_count": len(rows) if rows is not None else None, "row_keys": sorted(rows[0]) if rows else [],
                "row_paths": walk(rows[0]) if rows else [], "roles": list(FIELD_ROLES[tool])}

    def prepare_field_map(self, *, mission_id: str, tool: str, mapping: dict) -> Path:
        """Valide chaque correspondance contre la réponse de calibrage réelle; rien n'est deviné."""
        if not self.live:
            raise PermissionError("FIELD_MAP_REQUIRES_MCP_SESSION_MODE")
        if tool not in FIELD_ROLES:
            raise ValueError(f"UNKNOWN_TOOL:{tool}")
        cal = self.calibration_payload(tool)
        if not cal:
            raise PermissionError(f"CALIBRATION_REQUIRED_BEFORE_FIELD_MAP:{tool}")
        payload, rows = cal["payload"], extract_rows(cal["payload"])
        for role, key in mapping.items():
            if role not in FIELD_ROLES[tool]:
                raise ValueError(f"UNKNOWN_ROLE:{tool}.{role}")
            if is_financial_path(key):
                raise ValueError(f"FINANCIAL_FIELD_FORBIDDEN:{role}={key}")
            if role == "total":
                v = resolve_path(payload, key)
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(f"TOTAL_PATH_NOT_INTEGER_IN_CALIBRATION:{key}")
                continue
            status = resolve_row_field(rows[0], role, key)[1] if rows else FIELD_MISSING
            if status == FIELD_TYPE_MISMATCH:
                raise ValueError(f"ROW_FIELD_TYPE_MISMATCH_IN_CALIBRATION:{role}={key}")
            if status != FIELD_OK:
                raise ValueError(f"ROW_KEY_NOT_OBSERVED_IN_CALIBRATION:{role}={key}")
        d = self.dir / "STATE" / "field_maps"
        d.mkdir(parents=True, exist_ok=True)
        doc = {"schema": FIELD_MAP_SCHEMA, "mission_id": mission_id, "tool": tool, "mapping": dict(sorted(mapping.items())),
               "calibration_call_id": cal["call_id"], "issued_at": _iso_us()}
        p = d / f"{tool}.json"
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p

    def sign_field_map(self, path: Path, private_key: Path) -> dict:
        doc = self._sign_document(path, private_key)
        self._log_call({"event": "FIELD_MAP_SIGNED", **doc})
        return doc

    def field_map(self, tool: str, mission_id: str) -> dict | None:
        p = self.dir / "STATE" / "field_maps" / f"{tool}.json"
        sig = p.with_suffix(".sig")
        if not self._pubkey_ok() or not p.is_file() or not sig.is_file() or not verify_signature(self.owner_pubkey, p, sig):
            return None
        doc = self._read(p, None)
        if (not isinstance(doc, dict) or doc.get("schema") != FIELD_MAP_SCHEMA or doc.get("tool") != tool
                or doc.get("mission_id") != mission_id or not isinstance(doc.get("mapping"), dict)
                or not set(doc["mapping"]) <= set(FIELD_ROLES[tool])
                or any(not isinstance(v, str) or is_financial_path(v) for v in doc["mapping"].values())):
            return None
        return doc["mapping"]

    def _active_approval(self, scope: str, mission_id: str | None = None) -> dict | None:
        if self.live:
            for a in reversed(self.live_approvals()):
                if a["scope"] == scope and a["mission_id"] == mission_id:
                    return a
            return None
        t = _now()
        for a in reversed(self._read(self.p_approvals, [])):
            if a.get("scope") == scope and dt.datetime.fromisoformat(a["expires_at"]) > t:
                return a
        return None

    # ---------- Gel global ----------
    def _halt(self, reason: str, **ctx) -> None:
        h = {"at": _now().isoformat(timespec="microseconds"), "reason": reason, **ctx}
        self._write(self.p_halt, h)
        self._log_credit({"event": "HALT", **h})

    def halt_state(self) -> dict | None:
        """Le gel ne se lève que par une nouvelle approbation signée émise après lui."""
        h = self._read(self.p_halt, None)
        if not h:
            return None
        at = dt.datetime.fromisoformat(h["at"])
        if any(dt.datetime.fromisoformat(a["issued_at"]) > at for a in self.live_approvals()):
            return None
        return h

    # ---------- Soldes capturés par le hook ----------
    def note_balance_request(self, tool_use_id: str) -> None:
        """PreToolUse check_credits/usage_get : horodate le DÉBUT de la lecture du solde (ordre vis-à-vis des appels payants)."""
        if not self.live or not tool_use_id:
            return
        with self._lock():
            p = self.dir / "STATE" / "balance_requests.json"
            req = self._read(p, {})
            req[str(tool_use_id)] = _iso_us()
            self._write(p, dict(sorted(req.items(), key=lambda kv: kv[1])[-50:]))

    def capture_hook_balance(self, raw_usage: dict, tool_use_id: str | None = None) -> dict:
        if not self.live:
            raise PermissionError("HOOK_BALANCE_CAPTURE_REQUIRES_MCP_SESSION")
        rec = parse_receipt(raw_usage)
        if rec["kind"] != "USAGE":
            raise ValueError("NOT_A_USAGE_SNAPSHOT")
        if isinstance(rec["credits_remaining"], bool) or not isinstance(rec["credits_remaining"], int):
            raise ValueError("USAGE_REMAINING_NOT_INTEGER")
        with self._lock():
            p_req = self.dir / "STATE" / "balance_requests.json"
            req = self._read(p_req, {})
            requested_at = req.pop(str(tool_use_id), None) if tool_use_id else None
            self._write(p_req, req)
            prev = self._read(self.p_balance, {}) or {}
            snap = {
                "credits_remaining": rec["credits_remaining"],
                "request_id": rec["request_id"],
                "observed_at": _iso_us(),
                "requested_at": requested_at,  # None : lecture non horodatée, inutilisable pour régler ou envoyer
                "mode": self.mode,
                "source": HOOK_BALANCE_SOURCE,
                "seq": int(prev.get("seq") or 0) + 1,
            }
            self._write(self.p_balance, snap)
            with (self.dir / "STATE" / "balance_history.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            self._log_call({"event": "BALANCE", **snap})
        return snap

    def _fresh_hook_balance(self) -> dict | None:
        bal = self._read(self.p_balance, None)
        if not bal or bal.get("source") != HOOK_BALANCE_SOURCE or not _ts(bal.get("requested_at")):
            return None
        if _now() - dt.datetime.fromisoformat(bal["observed_at"]) > dt.timedelta(minutes=LIVE_BALANCE_MAX_AGE_MINUTES):
            return None
        return bal

    def _ledger_rows(self) -> list[dict]:
        if not self.p_ledger.exists():
            return []
        return [json.loads(x) for x in self.p_ledger.read_text(encoding="utf-8").splitlines() if x.strip()]

    def _last_settled_seq(self) -> int:
        return max([int(r.get("post_seq") or 0) for r in self._ledger_rows() if r.get("event") == "SETTLED"] or [0])

    def _platform_spent(self, scope: str, remaining: int) -> int:
        """Dépense observée sur la plateforme depuis le premier solde de la portée (indépendante du registre)."""
        base = self._read(self.p_baselines, {})
        if scope not in base:
            base[scope] = {"credits_remaining": int(remaining), "at": _iso()}
            self._write(self.p_baselines, base)
        return max(0, int(base[scope]["credits_remaining"]) - int(remaining))

    def _spent_for_scope(self, scope: str) -> int:
        if not self.p_ledger.exists():
            return 0
        rows = [json.loads(x) for x in self.p_ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
        return sum(r.get("cost_units", 0) for r in rows if r.get("event") == "SETTLED" and r.get("scope") == scope)

    def _reserved_for_scope(self, scope: str) -> int:
        res = self._read(self.p_reservations, {})
        return sum(r["max_credits"] for r in res.values() if r.get("scope") == scope)

    def plan_call(self, **kw) -> dict:
        with self._lock():
            return self._plan_call(**kw)

    def _plan_call(
        self,
        *,
        mission_id: str,
        scope: str,
        recipe_id: str | None = None,
        tool: str | None = None,
        country: str = "FR",
        stage: int = 5,
        terms: list[str] | None = None,
        locale: str = "fr",
        linked_domain: str | None = None,
        linked_ad_ref: str | None = None,
        limit: int | None = None,
        page: int = 1,
        lot_id: str | None = None,
    ) -> dict:
        page = int(page or 1)
        caps = self.capabilities()
        if not caps["connected"]:
            self._log_call({"event": "BLOCKED_CONNECTOR", "reason": caps["reason"], "recipe_id": recipe_id})
            return {"status": "BLOCKED_CONNECTOR", "reasons": [caps["reason"]]}
        if self.live:
            halt = self.halt_state()
            if halt:
                return {"status": "BLOCKED", "reasons": [f"LIVE_HALTED:{halt['reason']}"]}
            if not self._fresh_hook_balance():
                return {"status": "BLOCKED", "reasons": [f"LIVE_BALANCE_OLDER_THAN_{LIVE_BALANCE_MAX_AGE_MINUTES}MIN_RUN_check_credits"]}
            if not recipe_id:
                return {"status": "BLOCKED", "reasons": ["LIVE_REQUIRES_CALIBRATED_RECIPE"]}

        if recipe_id:
            if recipe_id not in RECIPES:
                raise ValueError(f"UNKNOWN_RECIPE:{recipe_id}")
            spec = RECIPES[recipe_id]
            tool = spec["tool"]
            if self.live and (tool not in LIVE_TOOLS or "live_params" not in spec):
                return {"status": "BLOCKED", "reasons": [f"LIVE_TOOL_NOT_ALLOWED:{tool}"]}
            if self.live and spec.get("live_blocked"):
                return {"status": "BLOCKED", "reasons": [f"LIVE_RECIPE_DISABLED:{spec['live_blocked']}"]}
            if page < 1 or page > int(spec.get("max_page", 1)) or (page > 1 and tool == "search_advertisers"):
                return {"status": "BLOCKED", "reasons": [f"PAGE_OUTSIDE_RECIPE_OR_SCHEMA:{page}"]}
            if self.live and limit and int(limit) > spec["limit"]:
                return {"status": "BLOCKED", "reasons": [f"LIVE_LIMIT_ABOVE_RECIPE:{limit}>{spec['limit']}"]}
            limit = limit or spec["limit"]
            if stage < spec["min_stage"]:
                return {"status": "BLOCKED", "reasons": [f"STAGE_BELOW_{spec['min_stage']}_SKU_FORBIDDEN:{recipe_id}"]}
            if spec.get("requires_linked_domain") and not (linked_domain and linked_ad_ref):
                return {"status": "BLOCKED", "reasons": ["LINKED_SHOP_REQUIRES_DOMAIN_AND_AD_REF"]}
            if spec.get("foreign_only") and country == "FR":
                return {"status": "BLOCKED", "reasons": ["FOREIGN_RECIPE_REQUIRES_NON_FR_COUNTRY"]}
        else:
            if not tool:
                raise ValueError("RECIPE_OR_TOOL_REQUIRED")
            limit = limit or 5

        if tool in FORBIDDEN_WRITE_TOOLS:
            return {"status": "BLOCKED", "reasons": [f"FORBIDDEN_WRITE_TOOL:{tool}"]}
        if tool in self._read(self.p_frozen, {}):
            return {"status": "BLOCKED", "reasons": [f"TOOL_FROZEN_AFTER_COST_MODEL_VIOLATION:{tool}"]}

        terms = [t.strip() for t in (terms or []) if isinstance(t, str) and t.strip()]
        if country in ENGLISH_ONLY_MARKETS and (locale.lower() != "en" or _looks_french(terms)):
            return {"status": "BLOCKED", "reasons": [f"LOCALIZATION_ERROR_ENGLISH_REQUIRED_FOR_{country}"]}
        if not terms and not linked_domain:
            return {"status": "BLOCKED", "reasons": ["EMPTY_SEARCH_TERMS"]}

        appr = self._active_approval(scope, mission_id)
        if not appr:
            return {"status": "BLOCKED", "reasons": [f"NO_ACTIVE_CREDIT_APPROVAL_FOR_SCOPE:{scope}"]}
        if self.live and tool not in appr["tools"]:
            return {"status": "BLOCKED", "reasons": [f"TOOL_NOT_IN_APPROVAL_SCOPE:{tool}"]}
        autonomous_sha = None
        if self.live and appr.get("autonomous"):
            # Approbation autonome : seul l'appel suivant du plan signé (même empreinte) peut être réservé.
            plan, sha, errs = self.load_plan()
            if errs or sha != appr["plan_sha256"]:
                return {"status": "BLOCKED", "reasons": ["AUTONOMOUS_PLAN_CHANGED_OR_INVALID_NEW_SIGNATURE_REQUIRED"]}
            nxt = self.plan_status().get("next")
            want = {"lot_id": lot_id, "recipe_id": recipe_id, "terms": terms, "page": page,
                    "linked_domain": _clean_domain(linked_domain) if linked_domain else None, "linked_ad_ref": linked_ad_ref}
            if (not nxt or any(nxt[k] != v for k, v in want.items())
                    or country != plan["market_geo"] or locale.lower() != plan["locale"].lower()):
                return {"status": "BLOCKED", "reasons": ["AUTONOMOUS_CALL_NOT_NEXT_IN_SIGNED_PLAN"]}
            autonomous_sha = sha
        if self.live and spec.get("requires_fields"):
            fmap = self.field_map(tool, mission_id)
            if fmap is None:
                return {"status": "BLOCKED", "reasons": [f"LIVE_FIELD_MAP_NOT_CONFIRMED_RUN_CALIBRATION:{tool}"]}
            missing = [r for r in spec["requires_fields"] if r not in fmap]
            if missing:
                return {"status": "BLOCKED", "reasons": [f"LIVE_FIELD_MAP_MISSING_ROLES:{tool}:{','.join(missing)}"]}
        if self.live and spec.get("requires_linked_domain"):
            # Le domaine doit provenir d'une annonce déjà ingérée (lien annonce -> boutique observé, pas saisi).
            dom = _clean_domain(linked_domain)
            linked = False
            for evf in (self.dir / "EVIDENCE").glob("*.jsonl"):
                for line in evf.read_text(encoding="utf-8").splitlines():
                    ev = json.loads(line) if line.strip() else {}
                    if ev.get("source_ref") == linked_ad_ref and ev.get("entity_scope") == "AD" and ev.get("shop_domain") == dom:
                        linked = True
            if not linked:
                return {"status": "BLOCKED", "reasons": ["LINKED_AD_REF_NOT_FOUND_WITH_THIS_DOMAIN"]}

        # Deduplicate while waiting for provider results. This must happen
        # BEFORE checking remaining credits, otherwise an outstanding request
        # already using all authorized credits is incorrectly rejected on retry.
        if self.live:
            linked_domain = _clean_domain(linked_domain) if linked_domain else None
            params = build_live_params(recipe_id, country=country, limit=int(limit), terms=terms, locale=locale, linked_domain=linked_domain,
                                       page=page)
            if not 2 <= len(params["query"]) <= 200:
                return {"status": "BLOCKED", "reasons": ["LIVE_QUERY_LENGTH_OUTSIDE_SCHEMA_2_200"]}
        else:
            params = {
                "country": country,
                "limit": int(limit),
                "query": linked_domain if linked_domain else " ".join(terms),
                "locale": locale.lower(),
            }
        fp = canonical_fingerprint(tool, params)
        existing_pending = self._read(self.p_pending, {})
        for prior_call_id, prior in existing_pending.items():
            if (prior.get("fingerprint") == fp and prior.get("mission_id") == mission_id
                    and prior.get("scope") == scope and prior.get("tool") == tool):
                request_path = self.dir / "OUTBOX" / f"{prior_call_id}.request.json"
                if request_path.exists():
                    return {"status": "AWAITING_EXECUTION", "call_id": prior_call_id,
                            "max_credits": prior.get("max_credits"),
                            "request": json.loads(request_path.read_text(encoding="utf-8")),
                            "idempotent_reuse": True}
        if self.live and existing_pending:
            return {"status": "BLOCKED", "reasons": [f"LIVE_ONE_OUTSTANDING_CALL:{','.join(sorted(existing_pending))}"]}

        if tool in MEASURED_COSTS_PER_ROW:
            if tool not in (appr.get("tools") or []):
                return {"status": "BLOCKED", "reasons": [f"TOOL_NOT_IN_APPROVAL_SCOPE:{tool}"]}
            max_credits = int(limit) * MEASURED_COSTS_PER_ROW[tool]
        elif tool in UNCALIBRATED_TOOLS:
            if tool not in (appr.get("uncalibrated_tools") or []) or not appr.get("per_call_ceiling"):
                return {"status": "BLOCKED", "reasons": [f"COST_NOT_GUARANTEED_UNCALIBRATED_TOOL:{tool}"]}
            max_credits = int(appr["per_call_ceiling"])
        else:
            return {"status": "BLOCKED", "reasons": [f"UNKNOWN_TOOL:{tool}"]}

        spent = self._spent_for_scope(scope)
        reserved = self._reserved_for_scope(scope)
        if spent + reserved + max_credits > appr["max_credits"]:
            return {"status": "BLOCKED", "reasons": [f"SCOPE_CREDIT_CEILING_EXCEEDED:{spent + reserved + max_credits}>{appr['max_credits']}"]}

        bal = self._read(self.p_balance, {})
        rem = int(bal.get("credits_remaining") or 0)
        if rem - (reserved + max_credits) < self.reserve_floor:
            return {"status": "BLOCKED", "reasons": [f"RESERVE_FLOOR_BREACH:{rem - (reserved + max_credits)}<{self.reserve_floor}"]}
        if self.live:
            platform = self._platform_spent(scope, rem)
            if platform + reserved + max_credits > appr["max_credits"]:
                return {"status": "BLOCKED", "reasons": [f"LIVE_PLATFORM_SPEND_CEILING_EXCEEDED:{platform + reserved + max_credits}>{appr['max_credits']}"]}

        cache_file = self.dir / "CACHE" / f"{fp}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            self._log_call({"event": "CACHE_HIT", "tool": tool, "fingerprint": fp, "cost_units": 0})
            return {
                "status": "CACHE_HIT",
                "fingerprint": fp,
                "credits_spent": 0,
                "evidence": cached.get("evidence", []),
                "entity_links": cached.get("entity_links", []),
            }

        call_id = "TTC-" + uuid.uuid4().hex[:10]
        res_id = "RES-" + uuid.uuid4().hex[:10]
        reservations = self._read(self.p_reservations, {})
        reservations[res_id] = {
            "reservation_id": res_id,
            "call_id": call_id,
            "scope": scope,
            "tool": tool,
            "max_credits": max_credits,
            "created_at": _iso(),
        }
        self._write(self.p_reservations, reservations)

        call_meta = {
            "call_id": call_id,
            "reservation_id": res_id,
            "mission_id": mission_id,
            "scope": scope,
            "recipe_id": recipe_id or "CUSTOM",
            "tool": tool,
            "params": params,
            "terms": terms,
            "linked_domain": linked_domain,
            "linked_ad_ref": linked_ad_ref,
            "max_credits": max_credits,
            "fingerprint": fp,
            "market_geo": country,
            "prepared_at": _iso(),
            "page": page,
            "lot_id": lot_id,
        }
        if self.live:
            call_meta.update({"status": "PREPARED", "approval_id": appr["approval_id"], "autonomous_plan_sha": autonomous_sha})
            if lot_id:
                plan, _, errs = self.load_plan()
                lot = next((l for l in (plan or {}).get("lots", []) if l["lot_id"] == lot_id), None)
                if lot:
                    call_meta.update({k: lot.get(k) for k in ("market", "facet", "strategy", "budget")})
                    call_meta["wave"] = lot.get("wave", "V1_FOCUSED")
        pending = self._read(self.p_pending, {})
        pending[call_id] = call_meta
        self._write(self.p_pending, pending)

        req_payload = {
            "schema": "ECOM_OS_TT_REQUEST_V023",
            "call_id": call_id,
            "tool": tool,
            "params": params,
            "max_credits_authorized": max_credits,
            "rights": "READ_ONLY",
        }
        if self.live:
            req_payload.update({"mcp_tool": MCP_TOOL_PREFIX + tool,
                                "instruction": "Appeler mcp_tool avec EXACTEMENT params (aucun paramètre ajouté ni omis)."})
        (self.dir / "OUTBOX" / f"{call_id}.request.json").write_text(
            json.dumps(req_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._log_call({"event": "PREPARED", "call_id": call_id, "tool": tool, "max_credits": max_credits})
        return {"status": "AWAITING_EXECUTION", "call_id": call_id, "max_credits": max_credits, "request": req_payload}

    # ---------- Barrière MCP (appelée par le hook engine/tt_live_guard.py) ----------
    # Cycle : PREPARED -> AUTHORIZED (PreToolUse, avant la confirmation humaine) -> RESPONDED (PostToolUse)
    #         ou FAILED (PostToolUseFailure) ; un refus manuel ne déclenche aucun hook : l'appel reste AUTHORIZED.
    # Toute réservation non PREPARED bloque plan_call et toute nouvelle autorisation jusqu'à son règlement.
    _OPEN_STATUSES = ("AUTHORIZED", "RESPONDED", "FAILED")

    def _find_pending(self, tool: str, tool_input: dict, statuses: tuple[str, ...], tool_use_id: str | None = None):
        fp = canonical_fingerprint(tool, tool_input if isinstance(tool_input, dict) else {})
        for cid, e in self._read(self.p_pending, {}).items():
            if e.get("tool") != tool or e.get("fingerprint") != fp or e.get("status") not in statuses:
                continue
            bound = (e.get("dispatch") or {}).get("tool_use_id")
            if tool_use_id and bound and bound != tool_use_id:
                continue
            return cid, e
        return None

    def authorize_dispatch(self, tool: str, tool_input: dict, tool_use_id: str | None = None) -> dict:
        """PreToolUse : `ask` (confirmation humaine dans LLM_Provider runtime) seulement pour une réservation identique,
        jamais `allow`. La réservation passe à AUTHORIZED de façon atomique : un second envoi est refusé."""
        if not self.live:
            return {"decision": "deny", "reason": "ADAPTER_NOT_IN_MCP_SESSION"}
        if tool not in LIVE_TOOLS:
            return {"decision": "deny", "reason": f"LIVE_TOOL_NOT_ALLOWED:{tool}"}
        with self._lock():
            pending = self._read(self.p_pending, {})
            open_calls = [cid for cid, e in pending.items() if e.get("status") in self._OPEN_STATUSES]
            hit = self._find_pending(tool, tool_input, ("PREPARED",))
            if not hit:
                if open_calls:
                    return {"decision": "deny", "reason": f"OPEN_CALL_NOT_SETTLED:{','.join(open_calls)}"}
                return {"decision": "no_match", "reason": "NO_RESERVATION_WITH_IDENTICAL_PARAMS"}
            cid, e = hit
            if open_calls:
                return {"decision": "deny", "reason": f"OPEN_CALL_NOT_SETTLED:{','.join(open_calls)}"}
            halt = self.halt_state()
            if halt:
                return {"decision": "deny", "reason": f"LIVE_HALTED:{halt['reason']}"}
            if tool in self._read(self.p_frozen, {}):
                return {"decision": "deny", "reason": f"TOOL_FROZEN:{tool}"}
            appr = self._active_approval(e["scope"], e["mission_id"])
            if not appr or tool not in appr["tools"]:
                return {"decision": "deny", "reason": "NO_VALID_SIGNED_APPROVAL"}
            bal = self._fresh_hook_balance()
            if not bal or int(bal["seq"]) < self._last_settled_seq():
                return {"decision": "deny", "reason": "LIVE_BALANCE_REFRESH_REQUIRED_RUN_check_credits"}
            rem = int(bal["credits_remaining"])
            reserved = self._reserved_for_scope(e["scope"])  # inclut cette réservation
            ledger_spent, platform_spent = self._spent_for_scope(e["scope"]), self._platform_spent(e["scope"], rem)
            if platform_spent > ledger_spent:
                self._halt("UNSETTLED_SPEND_DETECTED", platform_spent=platform_spent, ledger_spent=ledger_spent)
                return {"decision": "deny", "reason": f"LIVE_HALTED:UNSETTLED_SPEND_DETECTED:{platform_spent}>{ledger_spent}"}
            spent = max(ledger_spent, platform_spent)
            if spent + reserved > appr["max_credits"]:
                return {"decision": "deny", "reason": f"SCOPE_CREDIT_CEILING_EXCEEDED:{spent + reserved}>{appr['max_credits']}"}
            if rem - reserved < self.reserve_floor:
                return {"decision": "deny", "reason": f"RESERVE_FLOOR_BREACH:{rem - reserved}<{self.reserve_floor}"}
            autonomous = bool(appr.get("autonomous") and e.get("autonomous_plan_sha")
                              and e["autonomous_plan_sha"] == appr.get("plan_sha256") == self.load_plan()[1])
            pending[cid]["status"] = "AUTHORIZED"
            pending[cid]["dispatch"] = {"at": _iso_us(), "tool_use_id": tool_use_id, "pre_balance": rem,
                                        "pre_seq": int(bal["seq"]), "pre_request_id": bal.get("request_id"),
                                        "pre_requested_at": bal["requested_at"], "autonomous": autonomous}
            self._write(self.p_pending, pending)
            self._log_call({"event": "DISPATCH_AUTHORIZED", "call_id": cid, "tool": tool, "tool_use_id": tool_use_id,
                            "max_credits": e["max_credits"], "autonomous": autonomous})
            # Toujours `ask` : seule la barrière PermissionRequest peut lever la confirmation, et seulement si autonome.
            return {"decision": "ask", "call_id": cid, "reason": (
                f"ECOM OS {cid} · {tool} · max {e['max_credits']} crédits · cumul {spent}+{reserved}/{appr['max_credits']} "
                f"· solde {rem} · réserve {self.reserve_floor} · approbation {appr['approval_id']}"
                + (" · AUTONOME (plan signé)" if autonomous else ""))}

    def permission_request(self, tool: str, tool_input: dict) -> dict:
        """PermissionRequest (après PreToolUse) : `allow` uniquement pour la réservation AUTHORIZED identique, marquée
        autonome, autorisée depuis moins de PERMISSION_WINDOW_SECONDS, approbation et plan inchangés, moteur non gelé,
        une seule fois. Sinon aucune décision : la confirmation humaine s'affiche (jamais d'autorisation implicite)."""
        if not self.live or tool not in LIVE_TOOLS:
            return {"decision": None, "reason": "NOT_A_LIVE_TOOL"}
        with self._lock():
            pending = self._read(self.p_pending, {})
            hit = self._find_pending(tool, tool_input, ("AUTHORIZED",))
            if not hit:
                return {"decision": None, "reason": "NO_AUTHORIZED_RESERVATION_WITH_IDENTICAL_PARAMS"}
            cid, e = hit
            disp = e.get("dispatch") or {}
            if not disp.get("autonomous"):
                return {"decision": None, "reason": "MANUAL_CONFIRMATION_REQUIRED"}
            if disp.get("permission_granted_at"):
                return {"decision": None, "reason": "PERMISSION_ALREADY_GRANTED_ONCE"}
            if _now() - dt.datetime.fromisoformat(disp["at"]) > dt.timedelta(seconds=PERMISSION_WINDOW_SECONDS):
                return {"decision": None, "reason": "AUTHORIZATION_TOO_OLD"}
            if self.halt_state():
                return {"decision": None, "reason": "LIVE_HALTED"}
            appr = self._active_approval(e["scope"], e["mission_id"])
            if (not appr or not appr.get("autonomous") or tool not in appr["tools"]
                    or appr.get("plan_sha256") != e.get("autonomous_plan_sha") or self.load_plan()[1] != appr.get("plan_sha256")):
                return {"decision": None, "reason": "AUTONOMOUS_APPROVAL_OR_PLAN_NOT_VALID"}
            pending[cid]["dispatch"]["permission_granted_at"] = _iso_us()
            self._write(self.p_pending, pending)
            self._log_call({"event": "AUTONOMOUS_PERMISSION_GRANTED", "call_id": cid, "tool": tool, "approval_id": appr["approval_id"]})
            return {"decision": "allow", "call_id": cid, "reason": f"ECOM OS {cid} autonome · approbation {appr['approval_id']}"}

    def record_response(self, tool: str, tool_input: dict, tool_response, tool_use_id: str | None = None) -> dict:
        """PostToolUse : la réponse réelle est écrite par le hook (jamais recopiée par LLM_Provider), données financières retirées."""
        with self._lock():
            hit = self._find_pending(tool, tool_input, ("AUTHORIZED", "FAILED"), tool_use_id)
            if not hit:
                return {"status": "NO_MATCH"}
            cid, _ = hit
            raw = json.dumps(tool_response, ensure_ascii=False, sort_keys=True, default=str)
            payload = extract_mcp_payload(tool_response)
            doc = {
                "schema": "ECOM_OS_TT_HOOK_RESPONSE_V1",
                "call_id": cid,
                "tool_use_id": tool_use_id,
                "captured_by": HOOK_BALANCE_SOURCE,
                "captured_at": _iso_us(),
                "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "raw_length": len(raw),
                "parse_status": "JSON_OBJECT" if payload is not None else "UNPARSEABLE_NOT_STORED",
                "payload": redact_financial(payload) if payload is not None else None,
            }
            self._write(self.dir / "INBOX" / f"{cid}.response.json", doc)
            pending = self._read(self.p_pending, {})
            pending[cid]["status"] = "RESPONDED"
            pending[cid]["response_captured_at"] = doc["captured_at"]
            self._write(self.p_pending, pending)
            self._log_call({"event": "RESPONSE_CAPTURED", "call_id": cid, "parse_status": doc["parse_status"]})
            return {"status": "RECORDED", "call_id": cid}

    def record_failure(self, tool: str, tool_input: dict, tool_use_id: str | None, error: str, is_interrupt: bool | None) -> dict:
        """PostToolUseFailure : échec ou interruption pendant l'exécution; facturation indéterminée, réservation conservée."""
        with self._lock():
            hit = self._find_pending(tool, tool_input, ("AUTHORIZED",), tool_use_id)
            if not hit:
                self._log_call({"event": "FAILURE_UNMATCHED", "tool": tool, "tool_use_id": tool_use_id})
                return {"status": "NO_MATCH"}
            cid, _ = hit
            pending = self._read(self.p_pending, {})
            pending[cid]["status"] = "FAILED"
            pending[cid]["failure"] = {"at": _iso_us(), "error": str(error)[:500], "is_interrupt": bool(is_interrupt)}
            self._write(self.p_pending, pending)
            self._log_call({"event": "CALL_FAILED", "call_id": cid, "is_interrupt": bool(is_interrupt)})
            return {"status": "RECORDED_FAILURE", "call_id": cid}

    def prepare_call_resolution(self, *, mission_id: str, call_id: str, decision: str, charged_credits: int = 0,
                                justification: str) -> Path:
        """Document de résolution d'un appel incertain, à signer par le propriétaire après vérification de
        l'historique de consommation MarketPulse; il ne remplace pas la cohérence avec le solde observé."""
        if not self.live:
            raise PermissionError("RESOLUTION_REQUIRES_MCP_SESSION_MODE")
        e = self._read(self.p_pending, {}).get(call_id)
        if not e or e.get("status") not in ("AUTHORIZED", "FAILED") or e.get("mission_id") != mission_id:
            raise PermissionError("RESOLUTION_ONLY_FOR_AUTHORIZED_OR_FAILED_CALL_WITHOUT_RESPONSE")
        if decision not in RESOLUTION_DECISIONS:
            raise ValueError(f"RESOLUTION_DECISION_MUST_BE:{'|'.join(RESOLUTION_DECISIONS)}")
        if isinstance(charged_credits, bool) or not isinstance(charged_credits, int) or charged_credits < 0 \
                or (decision == "NO_CHARGE" and charged_credits != 0) or (decision == "CHARGED" and charged_credits < 1):
            raise ValueError("RESOLUTION_CHARGED_CREDITS_INCONSISTENT")
        if len(justification.strip()) < 20:
            raise ValueError("RESOLUTION_JUSTIFICATION_TOO_SHORT")
        d = self.dir / "STATE" / "resolutions"
        d.mkdir(parents=True, exist_ok=True)
        doc = {"schema": RESOLUTION_SCHEMA, "mission_id": mission_id, "call_id": call_id, "decision": decision,
               "charged_credits": charged_credits, "justification": justification.strip(),
               "authorized_at": e["dispatch"]["at"], "issued_at": _iso_us()}
        p = d / f"{call_id}.json"
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p

    def sign_call_resolution(self, path: Path, private_key: Path) -> dict:
        doc = self._sign_document(path, private_key)
        self._log_credit({"event": "CALL_RESOLUTION_SIGNED", **doc})
        return doc

    def call_resolution(self, call_id: str, mission_id: str) -> dict | None:
        p = self.dir / "STATE" / "resolutions" / f"{call_id}.json"
        sig = p.with_suffix(".sig")
        if not self._pubkey_ok() or not p.is_file() or not sig.is_file() or not verify_signature(self.owner_pubkey, p, sig):
            return None
        doc = self._read(p, None)
        if (not isinstance(doc, dict) or doc.get("schema") != RESOLUTION_SCHEMA or doc.get("call_id") != call_id
                or doc.get("mission_id") != mission_id or doc.get("decision") not in RESOLUTION_DECISIONS):
            return None
        return doc

    def halt_unmatched(self, tool: str) -> None:
        with self._lock():
            self._halt("PAID_CALL_RESPONSE_WITHOUT_AUTHORIZED_RESERVATION", tool=tool)

    def cancel_call(self, call_id: str) -> dict:
        """Annule une réservation jamais autorisée (PREPARED); une réservation envoyée se règle par `ingest`."""
        with self._lock():
            pending = self._read(self.p_pending, {})
            e = pending.get(call_id)
            if not e:
                raise KeyError(f"UNKNOWN_OR_ALREADY_INGESTED_CALL:{call_id}")
            if self.live and e.get("status") != "PREPARED":
                raise PermissionError("LIVE_CALL_ALREADY_DISPATCHED_SETTLE_WITH_INGEST")
            reservations = self._read(self.p_reservations, {})
            reservations.pop(e["reservation_id"], None)
            self._write(self.p_reservations, reservations)
            del pending[call_id]
            self._write(self.p_pending, pending)
            (self.dir / "OUTBOX" / f"{call_id}.request.json").unlink(missing_ok=True)
            self._log_call({"event": "CANCELLED", "call_id": call_id})
            return {"status": "CANCELLED", "call_id": call_id}

    def live_status(self) -> dict:
        spent = {}
        for r in self._ledger_rows():
            if r.get("event") == "SETTLED":
                spent[r["scope"]] = spent.get(r["scope"], 0) + int(r.get("cost_units") or 0)
        return {
            "mode": self.mode,
            "owner_pubkey_ok": self._pubkey_ok(),
            "approvals": [{k: a[k] for k in ("approval_id", "scope", "max_credits", "tools", "expires_at")} for a in self.live_approvals()],
            "halt": self.halt_state(),
            "balance": self._read(self.p_balance, None),
            "baselines": self._read(self.p_baselines, {}),
            "settled_by_scope": spent,
            "pending": {cid: {k: e.get(k) for k in ("tool", "status", "max_credits", "params", "dispatch", "failure", "response_captured_at")}
                        for cid, e in self._read(self.p_pending, {}).items()},
            "frozen_tools": self._read(self.p_frozen, {}),
            "calibrated_tools": sorted(p.stem for p in (self.dir / "STATE" / "calibration").glob("*.json")),
        }

    # ---------- Livrables de mission (générés depuis le registre et les preuves ; aucune affirmation non sourcée) ----------
    def build_reports(self) -> dict:
        plan, sha, errs = self.load_plan()
        if errs:
            return {"status": "PLAN_INVALID", "errors": errs}
        out_dir = self.dir.parent / plan["deliverables_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        ev_all = self._all_evidence()
        ev = [e for e in ev_all if e.get("lot_id")]
        yields = self._yields()
        st = self.plan_status()
        settled = [r for r in self._ledger_rows() if r.get("event") == "SETTLED"]
        ycall = {y["call_id"]: y for y in yields}

        def cell(v):
            return str(v if v not in (None, "") else "—").replace("|", "/").replace("\n", " ")

        def qv(e):
            return (e.get("qualification") or {}).get("verdict")

        hdr = (f"> Généré par le moteur ECOM OS depuis `MARKETPULSE/STATE` et `MARKETPULSE/EVIDENCE` le {_iso()} · plan `{sha[:12]}`.\n"
               "> Renseignement concurrentiel MarketPulse : jamais preuve de ventes, de rentabilité ni de demande ; jamais PASS de gate.\n\n")
        files = {}
        # 1. Registre des appels
        lines = ["# Registre des appels MarketPulse (moteur)\n", hdr,
                 "| Réglé le | Appel | Lot | Outil | Paramètres exacts | Réservé | Coût réel | Solde avant → après | requestId | Résultat | Lignes | Nouveaux pertinents | Isolés | Alerte |",
                 "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in settled:
            req = self._read(self.dir / "OUTBOX" / f"{r['call_id']}.request.json", {}) or {}
            y = ycall.get(r["call_id"], {})
            lines.append("| " + " | ".join(cell(x) for x in (
                r.get("at"), r["call_id"], y.get("lot_id"), r.get("tool"), json.dumps(req.get("params"), ensure_ascii=False, sort_keys=True),
                r.get("reserved_max"), r.get("cost_units"), f"{r.get('pre_balance')} → {r.get('post_balance')}", r.get("request_id"),
                r.get("outcome"), y.get("rows"), y.get("new_relevant"), y.get("isolated"), r.get("alert"))) + " |")
        lines.append(f"\n**Total réglé : {sum(int(r.get('cost_units') or 0) for r in settled)} crédits.**\n")
        files["10_REGISTRE_APPELS_MOTEUR.md"] = "\n".join(lines)

        # 2. Cartographie
        shops = {e["entity_id"]: e for e in ev_all if e.get("entity_scope") == "SHOP"}
        actors: dict = {}
        for e in ev:
            if e.get("entity_scope") != "AD":
                continue
            of = e.get("observed_fields") or {}
            name = of.get("advertiser") or "ANNONCEUR_NON_OBSERVÉ"
            a = actors.setdefault((e.get("market"), name), {"refs": [], "verdicts": [], "domains": set(), "spec": set()})
            a["refs"].append(e["source_ref"])
            a["verdicts"].append(qv(e))
            if of.get("domain"):
                a["domains"].add(of["domain"])
            a["spec"].add((e.get("qualification") or {}).get("specialization"))
        lines = ["# Cartographie des annonceurs et boutiques\n", hdr,
                 "Chaîne visée : annonce pertinente → annonceur → domaine → boutique liée → produit. Tout maillon absent est signalé.\n",
                 "| Marché | Annonceur (observé) | Annonces pertinentes / total | Domaines (landing) | Boutique liée | Spécialisation | Références |",
                 "|---|---|---|---|---|---|---|"]
        for (mk, name), a in sorted(actors.items(), key=lambda kv: (str(kv[0][0]), -kv[1]["verdicts"].count("RELEVANT"))):
            rel = sum(1 for v in a["verdicts"] if v in RELEVANT_VERDICTS)
            linked = ", ".join(f"{d} ({shops[d]['source_ref']})" for d in sorted(a["domains"]) if d in shops) or "NON_RECHERCHÉE_OU_ABSENTE"
            lines.append("| " + " | ".join(cell(x) for x in (mk, name, f"{rel}/{len(a['verdicts'])}", ", ".join(sorted(a["domains"])),
                                                            linked, ", ".join(sorted(s for s in a["spec"] if s)), ", ".join(a["refs"][:6]))) + " |")
        lines += ["\n## Annonceurs issus de search_advertisers\n", "| Marché | Annonceur | Verdict | Lien vers annonce pertinente | Signaux contraires | Réf. |", "|---|---|---|---|---|---|"]
        for e in ev:
            if e.get("entity_kind") == "ADVERTISER":
                q = e.get("qualification") or {}
                lines.append("| " + " | ".join(cell(x) for x in (e.get("market"), e.get("competitor_id"), q.get("verdict"),
                                                                (q.get("link_chain") or {}).get("ad"), ", ".join(q.get("counter_signals") or []), e["source_ref"])) + " |")
        lines += ["\n## Boutiques (recherche exacte de domaine lié)\n", "| Marché | Domaine | Verdict | Annonce source | Spécialisation | Signaux contraires | Réf. |", "|---|---|---|---|---|---|---|"]
        for e in ev:
            if e.get("entity_scope") == "SHOP":
                q = e.get("qualification") or {}
                lines.append("| " + " | ".join(cell(x) for x in (e.get("market"), e["entity_id"], q.get("verdict"), e.get("linked_ad_ref"),
                                                                q.get("specialization"), ", ".join(q.get("counter_signals") or []), e["source_ref"])) + " |")
        files["11_CARTOGRAPHIE_ACTEURS.md"] = "\n".join(lines)

        # 3. Annonces, angles, mécanismes
        lines = ["# Annonces pertinentes, promesses et mécanismes observés\n", hdr]
        for mk in plan["markets"]:
            ads = [e for e in ev if e.get("market") == mk and e.get("entity_scope") == "AD" and qv(e) in RELEVANT_VERDICTS]
            mech: dict = {}
            for e in ads:
                for t in (e["qualification"].get("matches") or {}).get("mechanism", []):
                    mech.setdefault(t, []).append(e["source_ref"])
            lines += [f"\n## Marché {mk} — {plan['markets'][mk].get('label', '')}\n",
                      f"Annonces pertinentes : {len(ads)} (nombre d'annonces observées, jamais un volume de ventes).\n",
                      "Mécanismes nommés dans les annonces pertinentes : " + (", ".join(f"{t} ×{len(r)} ({', '.join(r[:3])})" for t, r in sorted(mech.items(), key=lambda kv: -len(kv[1]))) or "aucun") + "\n",
                      "| Réf. | Annonceur | Extrait | Problème / mécanisme / avatar | Portée (PLATFORM_REPORTED) | Premier vu | Landing |", "|---|---|---|---|---|---|---|"]
            for e in ads:
                of, m = e.get("observed_fields") or {}, (e["qualification"].get("matches") or {})
                lines.append("| " + " | ".join(cell(x) for x in (e["source_ref"], of.get("advertiser"), e["observation"][:140],
                                                                " / ".join(",".join(m.get(k, [])) or "—" for k in ("problem", "mechanism", "avatar")),
                                                                of.get("reach"), of.get("first_seen"), of.get("landing"))) + " |")
            lines.append("\n**Analyse des promesses et positionnements : À RÉDIGER (Agent 02), en citant les références ci-dessus.**\n")
        files["12_ANNONCES_ANGLES_MECANISMES.md"] = "\n".join(lines)

        # 4. Lacunes, substituts, contre-preuves
        lines = ["# Lacunes, substituts et contre-preuves\n", hdr]
        for mk, mdef in plan["markets"].items():
            mev = [e for e in ev if e.get("market") == mk]
            subs, alt, iso = {}, [], []
            for e in mev:
                q = e.get("qualification") or {}
                for c in q.get("counter_signals") or []:
                    if c.startswith("SUBSTITUTE_MENTIONED:"):
                        subs.setdefault(c.split(":", 1)[1], []).append(e["source_ref"])
                if q.get("verdict") == "ALTERNATIVE_CONTEXT":
                    alt.append(e["source_ref"])
                if q.get("isolated"):
                    iso.append(f"{e['source_ref']} ({q.get('verdict')})")
            covered = {t for e in mev if qv(e) in RELEVANT_VERDICTS for t in (e["qualification"].get("matches") or {}).get("problem", [])}
            uncovered = [t for t in mdef["lexicon"].get("problem_terms", []) if t not in covered]
            unverified = [e["source_ref"] for e in mev if qv(e) in ("UNVERIFIED_NO_TEXT", "NAME_MATCH_UNVERIFIED", "UNLINKED_UNVERIFIED", "AMBIGUOUS_NO_PROBLEM_TERM")]
            lines += [f"\n## Marché {mk}\n",
                      "- Substituts cités dans les annonces : " + (", ".join(f"{t} ({', '.join(r[:3])})" for t, r in subs.items()) or "aucun observé") + ".",
                      f"- Résultats en contexte alternatif (autre usage du même mot) : {', '.join(alt) or 'aucun'}.",
                      f"- Résultats isolés (hors sujet, exclus des signaux) : {', '.join(iso) or 'aucun'}.",
                      f"- Résultats non vérifiés (sans texte d'annonce ou lien manquant) : {', '.join(unverified) or 'aucun'}.",
                      f"- Termes problème sans annonce pertinente observée : {', '.join(uncovered) or 'aucun'} — absence ≠ demande nulle ni lacune prouvée (couverture MarketPulse, formulation).",
                      "- Besoins mal servis, objections et limites des solutions : **À RÉDIGER (Agent 02)** avec VoC Stage 1/2 et références ci-dessus.\n"]
        files["13_LACUNES_SUBSTITUTS_CONTRE_PREUVES.md"] = "\n".join(lines)

        # 5. Comparatif des marchés (indicateurs ; aucune décision forcée)
        lines = ["# Rapport comparatif des marchés\n", hdr,
                 "| Indicateur | " + " | ".join(plan["markets"]) + " |", "|---|" + "---|" * len(plan["markets"])]
        lot_market = {l["lot_id"]: l["market"] for l in plan["lots"]}
        metrics: dict = {}
        for mk in plan["markets"]:
            my = [y for y in yields if lot_market.get(y.get("lot_id")) == mk]
            mev = [e for e in ev if e.get("market") == mk and e.get("entity_scope") != "MARKET_COUNT"]
            rel = [e for e in mev if qv(e) in RELEVANT_VERDICTS]
            new_rel = sum(y.get("new_relevant", 0) for y in my)
            cost = sum(y.get("cost", 0) for y in my)
            metrics[mk] = {
                "appels": len(my), "crédits": cost, "résultats": len(mev), "pertinents": len(rel),
                "nouveaux pertinents": new_rel, "isolés": sum(1 for e in mev if (e.get("qualification") or {}).get("isolated")),
                "annonceurs pertinents distincts": len({(e.get("observed_fields") or {}).get("advertiser") for e in rel if e.get("entity_scope") == "AD"}),
                "spécialistes candidats": len({e.get("competitor_id") for e in rel if (e.get("qualification") or {}).get("specialization") == "SPECIALIZED_CANDIDATE"}),
                "boutiques liées": sum(1 for e in mev if e.get("entity_scope") == "SHOP"),
                "coût par nouveau pertinent": round(cost / new_rel, 1) if new_rel else "—",
                "compteurs d'index (INDEX_COUNT)": "; ".join(f"{y['query']}={y['index_total']}" for y in my if isinstance(y.get("index_total"), int)) or "—",
            }
        for k in next(iter(metrics.values()), {}):
            lines.append(f"| {k} | " + " | ".join(cell(metrics[mk][k]) for mk in plan["markets"]) + " |")
        lines += ["\n## État des lots\n", "| Lot | Statut | Appels | Crédits | Nouveaux pertinents |", "|---|---|---|---|---|"]
        for lid, s in (st.get("states") or {}).items():
            lines.append(f"| {lid} | {s['status']} | {s['calls']} | {s['credits']} | {s['new_relevant']} |")
        lines += ["\n## Décision par marché\n",
                  "Options : **APPROFONDIR** (acteurs pertinents et besoins mal servis documentés), **INCERTAIN** (signaux insuffisants ou contradictoires), **ÉCARTER** (absence de signal pertinent ou contre-preuves fortes ; la seule présence de concurrents établis n'est pas un motif, cf. playbook §3).",
                  "Les compteurs d'index et la portée ne mesurent ni ventes ni demande ; aucune décision n'est déduite automatiquement.\n"]
        for mk in plan["markets"]:
            lines.append(f"- Marché {mk} : **NON RENDUE** — à rendre par Agent 02 / propriétaire, avec références et contre-preuves.")
        files["14_RAPPORT_COMPARATIF_MARCHES.md"] = "\n".join(lines) + "\n"
        summary = {"generated_at": _iso(), "plan_sha256": sha, "metrics": metrics, "lots": {k: v["status"] for k, v in (st.get("states") or {}).items()},
                   "settled_credits": sum(int(r.get("cost_units") or 0) for r in settled)}
        files["intel_summary.json"] = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        for name, text in files.items():
            (out_dir / name).write_text(text, encoding="utf-8")
        return {"status": "REPORTS_WRITTEN", "dir": str(out_dir), "files": sorted(files)}

    def ingest_call(self, call_id: str, raw_response: dict | None = None) -> dict:
        with self._lock():
            if self.live:
                if raw_response is not None:
                    raise PermissionError("LIVE_RESPONSE_ONLY_FROM_HOOK_CAPTURE")
                return self._settle_live(call_id)
            return self._ingest_call(call_id, raw_response)

    def _settle_live(self, call_id: str) -> dict:
        """Règlement par différence entre le solde d'avant l'autorisation et un solde dont la LECTURE a commencé
        après la réponse capturée (ou, sans réponse, après le délai de quiétude). Sinon : refus, réservation conservée."""
        pending = self._read(self.p_pending, {})
        if call_id not in pending:
            raise KeyError(f"UNKNOWN_OR_ALREADY_INGESTED_CALL:{call_id}")
        entry = pending[call_id]
        status = entry.get("status")
        if status == "PREPARED":
            raise PermissionError("LIVE_CALL_NOT_DISPATCHED_USE_CANCEL")
        disp = entry["dispatch"]
        bal = self._read(self.p_balance, {}) or {}
        bal_req = _ts(bal.get("requested_at"))
        if bal.get("source") != HOOK_BALANCE_SOURCE or not bal_req or int(bal.get("seq") or 0) <= int(disp["pre_seq"]):
            raise PermissionError("LIVE_POST_RESPONSE_BALANCE_REQUIRED_RUN_check_credits")
        if status == "RESPONDED":
            if bal_req <= _ts(entry["response_captured_at"]):
                raise PermissionError("LIVE_POST_RESPONSE_BALANCE_REQUIRED_RUN_check_credits")
        resolution = None
        if status != "RESPONDED":  # AUTHORIZED (refus, abandon) ou FAILED (échec, interruption, timeout)
            resolution = self.call_resolution(call_id, entry["mission_id"])
            if resolution is None:
                raise PermissionError("LIVE_INDETERMINATE_CALL_REQUIRES_SIGNED_OWNER_RESOLUTION")
            if bal_req <= _ts(disp["at"]) or bal_req <= _ts(resolution["issued_at"]):
                raise PermissionError("LIVE_POST_RESOLUTION_BALANCE_REQUIRED_RUN_check_credits")
        post = int(bal["credits_remaining"])
        cost = int(disp["pre_balance"]) - post
        if resolution and resolution["decision"] == "NO_CHARGE" and cost != 0:
            raise PermissionError(f"LIVE_RESOLUTION_CONTRADICTS_OBSERVED_DEBIT:{cost}")
        if resolution and resolution["decision"] == "CHARGED":
            cost = max(cost, int(resolution["charged_credits"]))
        reservations = self._read(self.p_reservations, {})
        res_obj = reservations.pop(entry["reservation_id"], {"max_credits": entry["max_credits"], "scope": entry["scope"]})
        reserved_max = int(res_obj["max_credits"])
        spec = RECIPES.get(entry.get("recipe_id"), {})
        doc = self._read(self.dir / "INBOX" / f"{call_id}.response.json", None) if status == "RESPONDED" else None
        payload = (doc or {}).get("payload")
        rows = extract_rows(payload)
        credits_block = payload.get("credits") if isinstance(payload, dict) else None
        receipt_cost = credits_block.get("costUnits") if isinstance(credits_block, dict) else None
        if isinstance(receipt_cost, bool) or not isinstance(receipt_cost, int):
            receipt_cost = None
        alerts, halt_reasons, freeze = [], [], False
        settle_cost = cost
        if cost < 0:
            alerts.append("BALANCE_INCREASED_UNRECONCILABLE")
            halt_reasons.append("UNRECONCILABLE_BALANCE")
            settle_cost = reserved_max
        elif cost > reserved_max:
            alerts.append("COST_MODEL_VIOLATION_TOOL_FROZEN")
            halt_reasons.append("COST_MODEL_VIOLATION")
            freeze = True
        if receipt_cost is not None and receipt_cost != cost:
            alerts.append(f"COST_RECEIPT_MISMATCH:{receipt_cost}!={cost}")
            halt_reasons.append("COST_RECEIPT_MISMATCH")
            settle_cost = max(settle_cost, receipt_cost)
        fmap = self.field_map(entry["tool"], entry["mission_id"]) or {}
        index_total = None
        if doc is None:
            outcome = "RESOLVED_NO_CHARGE" if resolution["decision"] == "NO_CHARGE" else "RESOLVED_CHARGED_WITHOUT_RESPONSE"
            if resolution["decision"] == "CHARGED":
                halt_reasons.append("DEBIT_WITHOUT_CAPTURED_RESPONSE")
        elif spec.get("calibration"):
            if payload is None:
                outcome = "UNEXPECTED_RESPONSE_SHAPE"
                halt_reasons.append("UNEXPECTED_RESPONSE_SHAPE")
                freeze = True
            else:
                outcome = "CALIBRATION_CAPTURED"
                (self.dir / "STATE" / "calibration").mkdir(parents=True, exist_ok=True)
                self._write(self.dir / "STATE" / "calibration" / f"{entry['tool']}.json",
                            {"call_id": call_id, "captured_at": doc["captured_at"], "params": entry["params"], "payload": payload})
        elif rows is None:
            outcome = "UNEXPECTED_RESPONSE_SHAPE"
            halt_reasons.append("UNEXPECTED_RESPONSE_SHAPE")
            freeze = True
        else:
            outcome = "INGESTED"
            if "total" in (spec.get("requires_fields") or []):
                index_total = resolve_path(payload, fmap.get("total", ""))
                if isinstance(index_total, bool) or not isinstance(index_total, int):
                    outcome = "UNEXPECTED_RESPONSE_SHAPE"
                    halt_reasons.append("CONFIRMED_TOTAL_FIELD_MISSING")
                    freeze = True
                    index_total = None
        if freeze:
            frozen = self._read(self.p_frozen, {})
            frozen[entry["tool"]] = {"call_id": call_id, "reserved_max": reserved_max, "actual_cost": cost, "outcome": outcome, "frozen_at": _iso()}
            self._write(self.p_frozen, frozen)
        request_id = (payload or {}).get("requestId") if isinstance(payload, dict) else None
        request_id = request_id if isinstance(request_id, str) and request_id else f"HOOK-{call_id}"
        evidence, links = [], []
        if outcome == "INGESTED":
            evidence, links = self._normalize_rows(entry, rows, {"request_id": request_id}, fmap, intel=self._intel_context(entry))
            if index_total is not None:
                evidence.insert(0, {
                    "mission_id": entry["mission_id"], "source_ref": f"TT-{call_id}-TOTAL", "tool_receipt": f"{request_id}#total",
                    "market_geo": entry.get("market_geo"), "observed_at": _iso(), "source_class": "INDEX_COUNT",
                    "entity_scope": "MARKET_COUNT", "entity_id": entry["params"].get("query"), "competitor_id": None,
                    "observation": f"Total MarketPulse pour « {entry['params'].get('query')} » : {index_total} (compteur d'index, ni ventes ni demande)",
                    "index_count": index_total, "source_url": "https://app.marketpulse.io/ads", "noise_flags": [],
                    "signal_weight": "USABLE", "provenance_mode": self.mode, "synthetic": False, "is_live": True,
                    "collection_method": f"MARKETPULSE_MCP:{entry['tool']}", "gate_eligible": False,
                    **{k: entry.get(k) for k in ("lot_id", "market", "facet", "wave", "strategy") if entry.get(k)}})
        if entry.get("lot_id"):
            ents = [e for e in evidence if e.get("entity_scope") != "MARKET_COUNT"]
            q0 = (ents[0].get("qualification") or {}) if ents else {}
            y = {"call_id": call_id, "lot_id": entry["lot_id"], "recipe_id": entry.get("recipe_id"), "tool": entry["tool"],
                 "query": entry["params"].get("query"), "page": int(entry.get("page") or 1), "cost": settle_cost,
                 "rows": len(ents), "new_entities": sum(1 for e in ents if e.get("novelty") == "NEW"),
                 "new_relevant": sum(1 for e in ents if e.get("novelty") == "NEW"
                                     and (e.get("qualification") or {}).get("verdict") in RELEVANT_VERDICTS),
                 "isolated": sum(1 for e in ents if (e.get("qualification") or {}).get("isolated")),
                 "duplicates": sum(1 for e in ents if e.get("novelty") != "NEW"),
                 "index_total": index_total, "probe_row_verdict": q0.get("verdict"), "outcome": outcome, "at": _iso()}
            with (self.dir / "STATE" / "yield.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(y, ensure_ascii=False) + "\n")
        self._write(self.p_reservations, reservations)
        self._log_credit({
            "event": "SETTLED", "call_id": call_id, "scope": entry["scope"], "tool": entry["tool"],
            "request_id": request_id, "cost_units": settle_cost, "balance_delta": cost, "receipt_cost_units": receipt_cost,
            "reserved_max": reserved_max, "pre_balance": disp["pre_balance"], "post_balance": post,
            "pre_seq": disp["pre_seq"], "post_seq": int(bal["seq"]), "post_requested_at": bal["requested_at"],
            "call_status_at_settlement": status, "failure": entry.get("failure"), "resolution": resolution,
            "outcome": outcome, "alert": ",".join(alerts) or None,
        })
        if halt_reasons:
            self._halt(halt_reasons[0], call_id=call_id, all_reasons=halt_reasons)
        if outcome == "INGESTED":
            (self.dir / "CACHE" / f"{entry['fingerprint']}.json").write_text(
                json.dumps({"evidence": evidence, "entity_links": links, "fetched_at": _iso()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with (self.dir / "EVIDENCE" / f"{entry['recipe_id']}.jsonl").open("a", encoding="utf-8") as f:
                for ev in evidence:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        del pending[call_id]
        self._write(self.p_pending, pending)
        self._log_call({"event": "INGESTED", "call_id": call_id, "request_id": request_id, "cost_units": settle_cost,
                        "rows_ingested": len(evidence), "outcome": outcome, "alert": ",".join(alerts) or None})
        return {"status": outcome, "call_id": call_id, "request_id": request_id, "credits_spent": settle_cost,
                "alert": ",".join(alerts) or None, "halted": bool(halt_reasons), "evidence": evidence, "entity_links": links}

    def _ingest_call(self, call_id: str, raw_response: dict | None = None) -> dict:
        pending = self._read(self.p_pending, {})
        if call_id not in pending:
            raise KeyError(f"UNKNOWN_OR_ALREADY_INGESTED_CALL:{call_id}")
        entry = pending[call_id]
        if raw_response is None:
            inbox_file = self.dir / "INBOX" / f"{call_id}.response.json"
            if not inbox_file.exists():
                raise FileNotFoundError(f"MISSING_INBOX_RESPONSE:{inbox_file.name}")
            raw_response = json.loads(inbox_file.read_text(encoding="utf-8"))

        receipt = parse_receipt(raw_response)
        rows = raw_response.get("data")
        if not isinstance(rows, list):
            raise ValueError("RESPONSE_DATA_ARRAY_MISSING")

        # 1. Normalisation, déduplication, anti-bruit et liens entités AVANT règlement des crédits
        evidence, entity_links = self._normalize_rows(entry, rows, receipt)

        # 2. Règlement des crédits et détection de dépassement du modèle de coût
        res_id = entry["reservation_id"]
        reservations = self._read(self.p_reservations, {})
        res_obj = reservations.pop(res_id, {"max_credits": entry["max_credits"], "scope": entry["scope"]})
        actual_cost = receipt["cost_units"]
        alert = None
        if actual_cost > res_obj["max_credits"]:
            frozen = self._read(self.p_frozen, {})
            frozen[entry["tool"]] = {
                "call_id": call_id,
                "reserved_max": res_obj["max_credits"],
                "actual_cost": actual_cost,
                "frozen_at": _iso(),
            }
            self._write(self.p_frozen, frozen)
            alert = "COST_MODEL_VIOLATION_TOOL_FROZEN"

        self._write(self.p_reservations, reservations)
        self._log_credit({
            "event": "SETTLED",
            "call_id": call_id,
            "scope": entry["scope"],
            "tool": entry["tool"],
            "request_id": receipt["request_id"],
            "cost_units": actual_cost,
            "reserved_max": res_obj["max_credits"],
            "alert": alert,
        })

        # 3. Mise en cache et écriture des preuves
        cache_file = self.dir / "CACHE" / f"{entry['fingerprint']}.json"
        cache_file.write_text(
            json.dumps({"evidence": evidence, "entity_links": entity_links, "fetched_at": _iso()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        ev_file = self.dir / "EVIDENCE" / f"{entry['recipe_id']}.jsonl"
        with ev_file.open("a", encoding="utf-8") as f:
            for ev in evidence:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        del pending[call_id]
        self._write(self.p_pending, pending)
        self._log_call({
            "event": "INGESTED",
            "call_id": call_id,
            "request_id": receipt["request_id"],
            "cost_units": actual_cost,
            "rows_ingested": len(evidence),
            "alert": alert,
        })
        return {
            "status": "INGESTED",
            "call_id": call_id,
            "request_id": receipt["request_id"],
            "credits_spent": actual_cost,
            "alert": alert,
            "evidence": evidence,
            "entity_links": entity_links,
        }

    def _intel_context(self, entry: dict) -> dict | None:
        """Contexte de qualification (lexique du marché du lot) et index des entités déjà observées (nouveauté)."""
        if not self.live:
            return None
        plan, _, _ = self.load_plan()
        lex = ((plan or {}).get("markets", {}).get(entry.get("market") or "", {}) or {}).get("lexicon") or {}
        seen, rel_adv, ad_verdicts = {}, {}, {}
        for e in self._all_evidence():
            scope, ref = e.get("entity_scope"), e.get("source_ref")
            if scope in ("AD", "SHOP", "MARKET") and e.get("entity_id") and not str(e["entity_id"]).startswith("UNIDENTIFIED_"):
                seen.setdefault(f"{scope}:{e['entity_id']}", ref)
            verdict = (e.get("qualification") or {}).get("verdict")
            if scope == "AD":
                ad_verdicts[ref] = verdict
                if verdict in RELEVANT_VERDICTS:
                    of = e.get("observed_fields") or {}
                    for key in (of.get("advertiser_id"), normalize_text(of.get("advertiser") or "")):
                        if key:
                            rel_adv.setdefault(str(key), ref)
        return {"lexicon": lex, "seen": seen, "relevant_advertisers": rel_adv, "ad_verdicts": ad_verdicts,
                "geo_semantics": RECIPES.get(entry.get("recipe_id"), {}).get("geo_semantics", "UNKNOWN")}

    def _qualify_and_mark(self, ev: dict, intel: dict, *, copy_txt: str, tool: str, in_call_duplicate: bool) -> None:
        """Nouveauté inter-appels + qualification ; un résultat hors sujet est isolé, jamais promu en opportunité."""
        of = ev["observed_fields"]
        key = f"{ev['entity_scope']}:{ev['entity_id']}"
        prior = intel["seen"].get(key)
        if in_call_duplicate:
            ev["novelty"] = "DUPLICATE_IN_CALL"
        elif str(ev["entity_id"]).startswith("UNIDENTIFIED_"):
            ev["novelty"] = "UNIDENTIFIED"
        elif prior:
            ev["novelty"] = f"SEEN_BEFORE:{prior}"
        else:
            ev["novelty"] = "NEW"
            intel["seen"][key] = ev["source_ref"]
        if not (intel.get("lexicon") or {}).get("problem_terms"):
            ev["qualification"] = {"schema": QUALIFICATION_SCHEMA, "verdict": "NOT_QUALIFIED_NO_MISSION_LEXICON", "isolated": False}
            return
        linked, extra_counter = None, []
        if tool == "search_advertisers":
            for k in (of.get("id"), normalize_text(of.get("name") or "")):
                if k and str(k) in intel["relevant_advertisers"]:
                    linked = intel["relevant_advertisers"][str(k)]
                    break
        elif tool == "search_shops" and ev.get("linked_ad_ref"):
            if intel["ad_verdicts"].get(ev["linked_ad_ref"]) in RELEVANT_VERDICTS:
                linked = ev["linked_ad_ref"]
            else:
                extra_counter.append("LINKED_AD_NOT_QUALIFIED_RELEVANT")
        fields = {"copy": copy_txt, **{k: of.get(k) for k in ("landing", "advertiser", "name", "domain", "main_category", "products",
                                                             "active_ads", "audience_country", "shop_country")}}
        q = qualify_row(fields, intel["lexicon"], tool=tool, source_class=ev["source_class"],
                        geo_semantics=intel["geo_semantics"], market_geo=ev["market_geo"], linked_ref=linked)
        q["counter_signals"] += extra_counter
        if "TERM_NOT_IN_COPY" in ev["noise_flags"]:
            q["counter_signals"].append("QUERY_TERM_NOT_IN_COPY")
        ev["qualification"] = q
        if q["isolated"]:
            ev["signal_weight"] = "EXCLUDED_OFF_TOPIC"
        elif q["verdict"] not in RELEVANT_VERDICTS and ev["signal_weight"] == "USABLE":
            ev["signal_weight"] = "DEMOTED"

    def _normalize_rows(self, call: dict, rows: list[dict], receipt: dict, fmap: dict | None = None,
                        intel: dict | None = None) -> tuple[list[dict], list[dict]]:
        seen_entities: set[str] = set()
        seen_creatives: set[str] = set()
        evidence = []
        links = []
        terms = [t.lower() for t in (call.get("terms") or [])]

        for idx, r in enumerate(rows):
            if not isinstance(r, dict):
                raise ValueError(f"ROW_{idx}_NOT_OBJECT")
            noise_flags = []
            tool = call["tool"]
            country = call.get("market_geo") or call["params"]["country"]
            live = self.live
            fmap = fmap or {}
            # LIVE : uniquement les champs confirmés (signés) au calibrage, chemins pointés compris ; rôle absent,
            # non confirmé ou de type inattendu = None, avec son statut conservé dans la preuve.
            field_status = {}

            def g(role, _r=r, _st=field_status):
                value, _st[role] = resolve_row_field(_r, role, fmap.get(role))
                return value

            if tool == "search_ads" and live:
                # Noms de champs MCP non calibrés : alias snake/camel, aucun identifiant ni domaine inventé.
                ent_scope = "AD"
                raw_id = g("id")
                ent_id = str(raw_id) if raw_id is not None else f"UNIDENTIFIED_{idx}"
                if raw_id is None:
                    noise_flags.append("ID_MISSING")
                copy_txt = str(g("copy") or "")
                landing_url = str(g("landing") or "")
                domain = _clean_domain(g("domain") or landing_url)
                sku_url = ""  # aucun champ produit confirmé : lien SKU jamais déduit
                advertiser = str(g("advertiser") or domain or "UNKNOWN_ADVERTISER")
                reach = g("reach")
                active_ads = g("active_ads") or 0
                media_url = g("media")
            elif tool == "search_ads":
                ent_scope = "AD"
                ent_id = str(r.get("ad_id") or r.get("id") or f"ad_{idx}")
                copy_txt = str(r.get("ad_copy") or r.get("primary_text") or "")
                domain = _clean_domain(r.get("shop_domain") or r.get("landing_url") or "")
                landing_url = str(r.get("landing_url") or "")
                sku_url = str(r.get("sku_url") or "")
                advertiser = str(r.get("advertiser_name") or r.get("competitor_id") or domain or "unknown_advertiser")
                reach = r.get("eu_reach")
                active_ads = r.get("advertiser_active_ads") or 0
            if tool == "search_ads":
                if not isinstance(reach, (int, float)) or isinstance(reach, bool):
                    reach = None
                if not isinstance(active_ads, (int, float)) or isinstance(active_ads, bool):
                    active_ads = 0

                if reach is not None and reach < 100:
                    noise_flags.append("LOW_REACH")
                if country not in ("FR", "GB", "DE", "ES", "IT", "NL") and reach is None:
                    noise_flags.append("REACH_NOT_REPORTED_NON_EU")
                if active_ads >= 400:
                    noise_flags.append("GENERALIST_ADVERTISER")
                if terms and copy_txt and not any(t in copy_txt.lower() for t in terms):
                    noise_flags.append("TERM_NOT_IN_COPY")

                creative_hash = hashlib.sha256(copy_txt.encode("utf-8")).hexdigest()[:10] if copy_txt else ent_id
                if creative_hash in seen_creatives:
                    noise_flags.append("DUPLICATE_CREATIVE")
                seen_creatives.add(creative_hash)

                # Rapprochement AD -> ADVERTISER -> SHOP -> LANDING -> SKU
                has_sku_link = bool(sku_url and landing_url and _clean_domain(sku_url) == _clean_domain(landing_url) and "/products/" in landing_url)
                links.append({
                    "ad_id": ent_id,
                    "advertiser": advertiser,
                    "shop_domain": domain or None,
                    "landing_url": landing_url or None,
                    "sku_url": sku_url if has_sku_link else None,
                    "link_status": "SAME_DOMAIN_PRODUCT_URL_NOT_VERIFIED" if has_sku_link else "RELATION_UNVERIFIED",
                    "sku_sales_proven": False,
                })
                obs_text = f"Ad {ent_id} par {advertiser}: {copy_txt[:160]}"
                source_class = "DIRECT_OBSERVATION"
                source_url = landing_url if landing_url.startswith("https://") else f"https://{domain}/" if domain else "https://app.marketpulse.io/ads"
                if live and not landing_url.startswith("https://"):
                    source_url = f"https://{domain}/" if domain else (media_url if str(media_url or "").startswith("https://") else "UNKNOWN")
                    if source_url == "UNKNOWN":
                        noise_flags.append("SOURCE_URL_UNKNOWN")

            elif tool == "search_shops" and live:
                ent_scope = "SHOP"
                domain = _clean_domain(g("domain") or "")
                ent_id = domain or f"UNIDENTIFIED_{idx}"
                if not domain:
                    noise_flags.append("DOMAIN_MISSING")
                advertiser = str(g("name") or domain or "UNKNOWN_SHOP")
                prod_count = g("products")
                prod_count = int(prod_count) if isinstance(prod_count, (int, float)) and not isinstance(prod_count, bool) else 0
                curr_traffic = g("visits")
                if prod_count >= 500:
                    noise_flags.append("GENERALIST_CATALOG")
                obs_text = (
                    f"Shop {domain or ent_id}: {prod_count} produits, visites estimées={curr_traffic} "
                    "(THIRD_PARTY_ESTIMATE: jamais preuve de ventes d'un SKU)"
                )
                source_class = "THIRD_PARTY_ESTIMATE"
                source_url = f"https://{domain}/" if domain else "UNKNOWN"
                if not domain:
                    noise_flags.append("SOURCE_URL_UNKNOWN")
            elif tool == "search_shops":
                ent_scope = "SHOP"
                domain = _clean_domain(r.get("domain") or r.get("shop_url") or f"shop{idx}.example.com")
                ent_id = domain
                advertiser = str(r.get("brand_name") or domain)
                prod_count = int(r.get("product_count") or 0)
                prev_traffic = r.get("prev_monthly_visits")
                curr_traffic = r.get("monthly_visits")
                if prod_count >= 500:
                    noise_flags.append("GENERALIST_CATALOG")
                if prev_traffic == 0 and (curr_traffic or 0) > 5000:
                    noise_flags.append("TRAFFIC_DISCONTINUITY")
                if r.get("revenue_30d_usd") is None:
                    noise_flags.append("SALES_NOT_TRACKED")
                obs_text = (
                    f"Shop {domain}: {prod_count} produits, visites estimées={curr_traffic} "
                    "(THIRD_PARTY_ESTIMATE: jamais preuve de ventes d'un SKU)"
                )
                source_class = "THIRD_PARTY_ESTIMATE"
                source_url = f"https://{domain}/"
            elif live:
                ent_scope = "MARKET"
                raw_id = g("id")
                ent_id = str(raw_id) if raw_id is not None else f"UNIDENTIFIED_{idx}"
                if raw_id is None:
                    noise_flags.append("ID_MISSING")
                advertiser = str(g("name") or ent_id)
                obs_text = f"Annonceur {advertiser} (résultat {tool} #{idx + 1}, INDEX_COUNT : ni ventes ni rentabilité)"
                source_class = "INDEX_COUNT"
                source_url = "https://app.marketpulse.io/search"
            else:
                ent_scope = "MARKET"
                ent_id = str(r.get("id") or f"row_{idx}")
                advertiser = str(r.get("name") or ent_id)
                obs_text = str(r.get("summary") or f"Résultat {tool} #{idx}")
                source_class = "INDEX_COUNT"
                source_url = "https://app.marketpulse.io/search"

            if ent_id in seen_entities:
                noise_flags.append("DUPLICATE_ENTITY")
            seen_entities.add(ent_id)

            weight = (
                "EXCLUDED_FROM_DEMAND_SIGNALS"
                if ("DUPLICATE_ENTITY" in noise_flags or "DUPLICATE_CREATIVE" in noise_flags)
                else ("DEMOTED" if noise_flags else "USABLE")
            )
            ev = {
                "mission_id": call["mission_id"],
                "source_ref": f"TT-{call['call_id']}-{idx + 1:02d}",
                "tool_receipt": f"{receipt['request_id']}#{idx + 1}",
                "market_geo": country,
                "observed_at": _iso(),
                "source_class": source_class,
                "entity_scope": ent_scope,
                "entity_id": ent_id,
                "competitor_id": advertiser,
                "observation": obs_text,
                "source_url": source_url,
                "price": r.get("price"),
                "currency": r.get("currency"),
                "noise_flags": noise_flags,
                "signal_weight": weight,
                "provenance_mode": self.mode,
                "synthetic": self.mode == "REPLAY",
                "is_live": self.mode in ("MCP_SESSION", "FILE_DROP"),
            }
            if live:
                # Recherche non financière; sortie d'outil tiers, jamais preuve moteur FETCH_VERIFIED ni PASS de gate.
                del ev["price"], ev["currency"]
                if tool == "search_ads":
                    ev["shop_domain"] = domain or None
                ev["collection_method"] = f"MARKETPULSE_MCP:{tool}"
                ev["gate_eligible"] = False
                ev["field_status"] = dict(field_status)
                ev["field_paths"] = {role: fmap[role] for role in field_status if role in fmap}
                if tool == "search_advertisers":
                    ev["entity_kind"] = "ADVERTISER"
                ev.update({k: call.get(k) for k in ("lot_id", "market", "facet", "wave", "strategy") if call.get(k)})
                if call.get("linked_ad_ref"):
                    ev["linked_ad_ref"] = call["linked_ad_ref"]
                # Champs confirmés observés (valeurs scalaires uniquement), avec leur classe de fiabilité.
                observed = {role: resolve_row_field(r, role, fmap.get(role))[0] for role in FIELD_ROLES.get(tool, ())
                            if role in fmap and role not in ("total", "copy")}
                if tool in ("search_ads", "search_shops"):
                    observed["domain"] = domain or None
                ev["observed_fields"] = {k: v for k, v in observed.items() if v is not None}
                ev["field_classes"] = {k: FIELD_RELIABILITY.get(k, source_class) for k in ev["observed_fields"]}
                if intel is not None:
                    self._qualify_and_mark(ev, intel, copy_txt=copy_txt if tool == "search_ads" else "", tool=tool,
                                           in_call_duplicate="DUPLICATE_ENTITY" in noise_flags)
            evidence.append(ev)
        return evidence, links

    def reconcile_credits(self, raw_usage_now: dict | None = None) -> dict:
        if self.live:
            if raw_usage_now is not None:
                raise PermissionError("LIVE_BALANCE_ONLY_FROM_HOOK_CAPTURE")
            bal = self._read(self.p_balance, {}) or {}
            if bal.get("source") != HOOK_BALANCE_SOURCE:
                return {"status": "NO_HOOK_BALANCE"}
            raw_usage_now = {"requestId": bal["request_id"], "credits": {"totalRemaining": bal["credits_remaining"]}}
        rec = parse_receipt(raw_usage_now)
        if rec["kind"] != "USAGE":
            raise ValueError("NOT_A_USAGE_SNAPSHOT")
        calls = (
            [json.loads(x) for x in self.p_calls.read_text(encoding="utf-8").splitlines() if x.strip()]
            if self.p_calls.exists()
            else []
        )
        first_bal = next((c for c in calls if c.get("event") == "BALANCE"), None)
        if not first_bal:
            return {"status": "NO_BASELINE_BALANCE"}
        settled = (
            sum(
                json.loads(x).get("cost_units", 0)
                for x in self.p_ledger.read_text(encoding="utf-8").splitlines()
                if x.strip() and json.loads(x).get("event") == "SETTLED"
            )
            if self.p_ledger.exists()
            else 0
        )
        platform_spent = int(first_bal["credits_remaining"]) - int(rec["credits_remaining"])
        gap = platform_spent - settled
        status = "RECONCILED" if gap == 0 else ("UNSETTLED_SPEND_DETECTED" if gap > 0 else "LEDGER_EXCEEDS_PLATFORM")
        if self.live and status != "RECONCILED":
            with self._lock():
                self._halt(f"RECONCILE_{status}", gap=gap)
        return {
            "status": status,
            "initial_credits": first_bal["credits_remaining"],
            "current_credits": rec["credits_remaining"],
            "platform_spent": platform_spent,
            "ledger_settled": settled,
            "gap": gap,
            "pending_calls": sorted(self._read(self.p_pending, {}).keys()),
        }
