"""Intelligence concurrentielle MarketPulse : filtres, qualification, nouveauté, pilotage adaptatif, arrêt, mode
autonome signé. Hors ligne ; aucun appel MCP. Toutes les lignes sont FICTIVES (forme des réponses réelles calibrées)."""
from __future__ import annotations
import copy
import datetime as dt
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import ecom
from engine import marketpulse_adapter as tt, tt_live_guard as guard
from tests.test_tt_live_guard import LiveBase, P, SCOPE

LEX_A = {"problem_terms": ["poux", "pou", "lentes"], "mechanism_terms": ["peigne", "lotion"], "avatar_terms": ["enfant", "école"],
         "substitute_terms": ["vinaigre"], "alternative_context_terms": ["poule", "poules"], "anchor_terms": ["enfant", "cheveux"]}
ADS_MAP = {"total": "pagination.total", "id": "id", "copy": "content.body", "landing": "content.landingPageUrl",
           "domain": "content.landingPageDomain", "advertiser": "advertiser.name", "reach": "metrics.reach",
           "active_ads": "advertiser.liveAdsCount", "media": "media.mediaUrl", "advertiser_id": "advertiser.id",
           "audience_country": "audience.mainCountry"}
ADV_MAP = {"total": "pagination.total", "id": "id", "name": "name", "active_ads": "advertising.activeAds"}
SHOP_MAP = {"total": "pagination.total", "domain": "domain", "name": "name", "products": "catalog.productsCount",
            "main_category": "catalog.mainCategory", "shop_country": "profile.countryCode"}


def ad(i, body, domain="ex-fictif.fr", adv="Marque Fictive", adv_id="p1", country="FR", live_ads=10):
    return {"id": f"facebook_{i}", "status": "active", "media": {"mediaUrl": f"https://medias.example/{i}.jpg"},
            "advertiser": {"id": adv_id, "name": adv, "liveAdsCount": live_ads},
            "content": {"body": body, "landingPageUrl": f"https://{domain}/products/x{i}", "landingPageDomain": domain},
            "metrics": {"reach": 5000 + i, "estimatedSpend": 99}, "audience": {"mainCountry": country}}


def adv(i, name, active=14):
    return {"id": i, "name": name, "advertising": {"activeAds": active, "reach30d": 0}}


def shop(domain, products=40, category="Health", country="FR"):
    return {"id": "s-" + domain, "domain": domain, "name": domain.split(".")[0],
            "catalog": {"productsCount": products, "mainCategory": category}, "profile": {"countryCode": country, "currency": "EUR"},
            "sales": {"estMonthlySales": 1}}


def payload(rows, cost, total=None):
    return {"structuredContent": {"requestId": "rq-" + str(len(rows)) + "-" + str(cost), "data": rows,
                                  "pagination": {"total": len(rows) if total is None else total}, "credits": {"costUnits": cost}}}


def base_plan(lots, budgets=None, scope=SCOPE):
    return {"schema": tt.PLAN_SCHEMA, "mission_id": "M7", "scope": scope, "market_geo": "FR", "locale": "fr",
            "deliverables_dir": "INTEL_OUT", "budgets": budgets or {"A": 200}, "domain_exclusions": ["amazon.fr"],
            "markets": {"A": {"label": "Marché fictif", "lexicon": LEX_A}}, "lots": lots}


PROBE = {"lot_id": "P1", "market": "A", "strategy": "S1", "facet": "PROBLEM", "budget": "A", "recipe": "A01_S5_AD_DENSITY_PROBE",
         "queries": ["poux", "lentes"]}
SAMPLE = {"lot_id": "S2", "market": "A", "strategy": "S2", "facet": "PROBLEM", "budget": "A", "recipe": "A01_S5_AD_SAMPLE_REACH",
          "queries_from": {"lot": "P1", "top_k": 1, "min_total": 1}}
ADVS = {"lot_id": "S3", "market": "A", "strategy": "S3", "facet": "PROBLEM", "budget": "A", "recipe": "A01_S5_NEW_ENTRANTS",
        "queries_from": {"lot": "P1", "top_k": 1, "min_total": 1}}
SHOPS = {"lot_id": "S4", "market": "A", "strategy": "S4", "facet": "DOMAIN", "budget": "A", "recipe": "A01_S5_LINKED_SHOP",
         "domains_from": {"markets": ["A"], "max": 3, "after_lots": ["S2"]}}


class FilterSelectionTests(unittest.TestCase):
    def test_new_recipes_use_real_schema_parameters_with_purpose(self):
        g = tt.build_live_params("A01_S5_AD_SAMPLE_GROWTH", country="FR", limit=8, terms=["poux"], locale="fr", linked_domain=None)
        self.assertEqual((g["sort_by"], g["reach_period"], g["min_reach"], g["max_ads_per_brand"]), ("reachDelta30d", "last30d", 1, 2))
        r = tt.build_live_params("A01_S5_AD_RECENT_LAUNCHES", country="FR", limit=8, terms=["poux"], locale="fr", linked_domain=None,
                                 today=dt.date(2026, 10, 20))
        self.assertEqual(r["created_after"], "2026-07-22")
        self.assertNotIn("page", r)
        self.assertEqual(tt.build_live_params("A01_S5_AD_SAMPLE_REACH", country="FR", limit=8, terms=["poux"], locale="fr",
                                              linked_domain=None, page=2)["page"], 2)
        with self.assertRaisesRegex(ValueError, "PAGINATION_UNSUPPORTED_BY_SCHEMA"):
            tt.build_live_params("A01_S5_NEW_ENTRANTS", country="FR", limit=5, terms=["poux"], locale="fr", linked_domain=None, page=2)
        e = tt.build_live_params("A01_S5_ESTABLISHED_ADVERTISERS", country="FR", limit=5, terms=["poux"], locale="fr", linked_domain=None)
        self.assertEqual((e["sort_by"], e["search_in"]), ("activeAds", "ad_copy"))
        s = tt.build_live_params("A01_S5_LINKED_SHOP", country="FR", limit=1, terms=[], locale="fr", linked_domain="ex.fr")
        self.assertEqual(s, {"query": "ex.fr", "limit": 1, "match_mode": "exact"})

    def test_plan_validation_refuses_unusable_or_unsafe_lots(self):
        def errs(lot, **kw):
            return tt.validate_plan(base_plan([dict(PROBE), lot] if lot else [dict(PROBE)], **kw))
        self.assertEqual(errs(None), [])
        self.assertIn("PLAN_RECIPE_NOT_LIVE_USABLE", str(errs({**SAMPLE, "lot_id": "X", "recipe": "A02_FR_BASELINE_SHOPS", "strategy": "S4"})))
        self.assertIn("PLAN_FOREIGN_RECIPE_FORBIDDEN", str(errs({**SAMPLE, "lot_id": "X", "recipe": "A01_S5_FOREIGN_PRELIM_ADS"})))
        self.assertIn("PLAN_RECIPE_NOT_LIVE_USABLE", str(errs({**SAMPLE, "lot_id": "X", "recipe": "A01_S7_SOLUTION_PRODUCTS"})))
        self.assertIn("PLAN_PAGES_ABOVE_RECIPE_MAX", str(errs({**SAMPLE, "lot_id": "X", "pages": 9})))
        self.assertIn("PLAN_LATER_PAGE_REQUIRES_PRODUCTIVITY_CONDITION", str(errs({**SAMPLE, "lot_id": "X", "start_page": 2})))
        self.assertIn("PLAN_LINKED_SHOP_REQUIRES_DOMAINS_FROM", str(errs({**SHOPS, "lot_id": "X", "domains_from": None, "queries": ["ex.fr"]})))
        self.assertIn("PLAN_LOT_REFERENCE_MUST_PRECEDE", str(errs({**SAMPLE, "queries_from": {"lot": "ZZ"}})))
        self.assertIn("PLAN_BUDGETS_ABOVE_500", str(errs(None, budgets={"A": 501})))
        self.assertIn("PLAN_LOT_STRATEGY_MISMATCH", str(errs({**SAMPLE, "lot_id": "X", "strategy": "S1"})))
        bad = base_plan([dict(PROBE)])
        bad["deliverables_dir"] = "MARKETPULSE/STATE"
        self.assertIn("PLAN_DELIVERABLES_DIR_OUTSIDE_MISSION_OR_IN_ENGINE_STATE", tt.validate_plan(bad))
        bad = base_plan([dict(PROBE)])
        bad["markets"]["A"]["lexicon"] = {"problem_terms": []}
        self.assertIn("PLAN_LEXICON_INVALID:A", tt.validate_plan(bad))


class QualificationTests(unittest.TestCase):
    def q(self, tool="search_ads", **fields):
        return tt.qualify_row(fields, LEX_A, tool=tool, source_class="DIRECT_OBSERVATION", geo_semantics="AD_AUDIENCE",
                              market_geo="FR", linked_ref=fields.pop("linked", None) if "linked" in fields else None)

    def test_relevant_off_topic_alternative_and_word_boundaries(self):
        self.assertEqual(self.q(copy="Des poux ? Lotion pour enfant", domain="ex.fr")["verdict"], "RELEVANT")
        off = self.q(copy="Huile botanique et canne de soutien double poignée", domain="ex.fr")
        self.assertEqual(off["verdict"], "OFF_TOPIC_CANDIDATE")
        self.assertTrue(off["isolated"])
        self.assertEqual(self.q(copy="Pour vos sols, une solution")["verdict"], "OFF_TOPIC_CANDIDATE")  # « pour » ≠ « pou »
        self.assertEqual(self.q(copy="Poux rouges des poules : traitement")["verdict"], "ALTERNATIVE_CONTEXT")
        amb = self.q(copy="Poux des poules ou de votre enfant")  # ancre + autre usage : jamais confirmé
        self.assertEqual((amb["verdict"], amb["isolated"]), ("AMBIGUOUS_ALTERNATIVE_CONTEXT", False))
        self.assertEqual(self.q(copy="Peigne fin pour enfant")["verdict"], "AMBIGUOUS_NO_PROBLEM_TERM")
        self.assertIn("SUBSTITUTE_MENTIONED:vinaigre", self.q(copy="Poux : oubliez le vinaigre")["counter_signals"])
        self.assertEqual(self.q()["verdict"], "UNVERIFIED_NO_TEXT")

    def test_regression_out_of_market_audience_and_other_avatar_never_relevant(self):
        # Défaut 1a : audience observée BE pour un marché FR → isolée, plus jamais RELEVANT.
        be = self.q(copy="Des poux ? Lotion enfant", audience_country="BE", domain="ex.be")
        self.assertEqual((be["verdict"], be["isolated"]), ("OUT_OF_MARKET_AUDIENCE", True))
        self.assertNotIn(be["verdict"], tt.RELEVANT_VERDICTS)
        self.assertEqual(self.q(copy="Des poux ? Lotion enfant", audience_country="fr")["verdict"], "RELEVANT")
        # Défaut 1b : couches pour bébé dans le marché adulte B (lexique réel du plan RECHERCHE_07).
        plan = json.loads((Path(__file__).resolve().parent.parent / "runs" / "RECHERCHE_07" / tt.PLAN_FILENAME).read_text()) \
            if (Path(__file__).resolve().parent.parent / "runs" / "RECHERCHE_07" / tt.PLAN_FILENAME).is_file() else None
        lex_b = plan["markets"]["B"]["lexicon"] if plan else {
            "problem_terms": ["incontinence", "fuites"], "mechanism_terms": ["couches"], "alternative_context_terms": ["bébé"],
            "anchor_terms": ["incontinence", "adulte"]}
        qb = lambda copy: tt.qualify_row({"copy": copy}, lex_b, tool="search_ads", source_class="DIRECT_OBSERVATION",
                                         geo_semantics="AD_AUDIENCE", market_geo="FR")
        baby = qb("Couches bébé anti-fuites : fini l'incontinence nocturne de votre bébé")
        self.assertEqual(baby["verdict"], "AMBIGUOUS_ALTERNATIVE_CONTEXT")
        self.assertNotIn(baby["verdict"], tt.RELEVANT_VERDICTS)
        self.assertEqual(qb("Couches bébé : zéro fuites la nuit")["verdict"], "ALTERNATIVE_CONTEXT")
        self.assertEqual(qb("Culotte absorbante adulte contre les fuites urinaires la nuit")["verdict"], "RELEVANT")
        # Pays d'origine d'une boutique ≠ pays de ses clients : une boutique liée reste pertinente.
        s = tt.qualify_row({"domain": "ex.fr", "name": "ex", "products": 40, "shop_country": "CN"}, LEX_A, tool="search_shops",
                           source_class="THIRD_PARTY_ESTIMATE", geo_semantics="SHOP_TRAFFIC_MAIN", market_geo="FR", linked_ref="TT-X-01")
        self.assertEqual((s["verdict"], s["isolated"]), ("RELEVANT_VIA_LINKED_AD", False))
        self.assertIn("SHOP_ORIGIN_CN_NOT_CUSTOMER_MARKET", s["counter_signals"])

    def test_geo_specialization_and_links_never_invented(self):
        f = self.q(copy="Anti poux", audience_country="BE")
        self.assertIn("AUDIENCE_NOT_FR", f["counter_signals"])
        self.assertTrue(self.q(copy="Anti poux")["geo"].startswith("REQUEST_FILTER_ONLY"))
        self.assertEqual(self.q(copy="Anti poux", domain="stop-poux.fr")["specialization"], "SPECIALIZED_CANDIDATE")
        self.assertEqual(self.q(copy="Anti poux", active_ads=450)["specialization"], "GENERALIST")
        a = tt.qualify_row({"name": "Mouvewell page"}, LEX_A, tool="search_advertisers", source_class="INDEX_COUNT",
                           geo_semantics="AD_AUDIENCE", market_geo="FR")
        self.assertEqual(a["verdict"], "UNVERIFIED_NO_TEXT")
        s = tt.qualify_row({"name": "whatchic", "domain": "whatchic.com", "products": 4077, "shop_country": "CN"}, LEX_A,
                           tool="search_shops", source_class="THIRD_PARTY_ESTIMATE", geo_semantics="SHOP_TRAFFIC_MAIN", market_geo="FR")
        self.assertEqual(s["verdict"], "GENERALIST_UNLINKED")
        self.assertTrue(s["isolated"])
        self.assertIn("SHOP_ORIGIN_CN_NOT_CUSTOMER_MARKET", s["counter_signals"])
        self.assertEqual(s["link_chain"]["ad"], "MISSING")
        chain = self.q(copy="poux", landing="https://ex.fr/pages/info", domain="ex.fr")["link_chain"]
        self.assertEqual((chain["shop"], chain["product"]), ("NOT_LOOKED_UP", "MISSING"))


class IntelBase(LiveBase):
    def install_plan(self, lots, **kw):
        (self.mdir / tt.PLAN_FILENAME).write_text(json.dumps(base_plan(lots, **kw), ensure_ascii=False, indent=2))

    def calibrated(self):
        self.ready()
        self.calibrate("search_ads", response=payload([ad(0, "poux calibrage")], 2, total=1), cost=2)
        self.confirm("search_ads", ADS_MAP)
        self.calibrate("search_advertisers", response=payload([adv("c0", "Cal")], 3, total=1), cost=3)
        self.confirm("search_advertisers", ADV_MAP)
        self.calibrate("search_shops", response=payload([shop("cal.fr")], 4, total=1), cost=4)
        self.confirm("search_shops", SHOP_MAP)

    def step(self, rows, unit, total=None):
        r = self.a.next_call()
        self.assertEqual(r["status"], "AWAITING_EXECUTION", r)
        out = self.run_call(r, cost=unit * len(rows), response=payload(rows, unit * len(rows), total))
        self.assertEqual(out["status"], "INGESTED", out)
        return r, out


class AdaptiveInvestigationTests(IntelBase):
    def test_full_chain_ad_advertiser_domain_shop_with_dedup_and_reports(self):
        self.calibrated()
        self.install_plan([dict(PROBE), dict(SAMPLE), dict(ADVS), dict(SHOPS)])
        r, out = self.step([ad(1, "Des poux ? Lotion enfant", "stop-poux.fr", "StopPoux", "pA")], 2, total=120)
        self.assertEqual(r["lot"]["terms"], ["poux"])
        self.assertEqual(out["evidence"][0]["source_class"], "INDEX_COUNT")
        self.assertEqual(out["evidence"][1]["qualification"]["verdict"], "RELEVANT")
        r, out = self.step([ad(2, "Huile botanique et canne", "mouve-fictif.fr", "Mouve", "pM")], 2, total=30)
        self.assertEqual(out["evidence"][1]["signal_weight"], "EXCLUDED_OFF_TOPIC")
        # S2 choisit la formulation dont le premier résultat est pertinent (« poux »), pas la seule plus volumineuse.
        r, out = self.step([ad(1, "Des poux ? Lotion enfant", "stop-poux.fr", "StopPoux", "pA"),
                            ad(3, "Peigne anti poux école", "peigne-fictif.fr", "PeigneCo", "pB"),
                            ad(4, "Canapé douillet", "deco-fictif.fr", "Deco", "pD"),
                            ad(5, "Lentes et poux : vinaigre ou lotion ?", "amazon.fr", "Revendeur", "pR")], 2)
        self.assertEqual(r["request"]["params"]["query"], "poux")
        self.assertEqual(r["request"]["params"]["max_ads_per_brand"], 2)
        ev = {e["entity_id"]: e for e in out["evidence"]}
        self.assertTrue(ev["facebook_1"]["novelty"].startswith("SEEN_BEFORE:"))
        self.assertEqual(ev["facebook_3"]["novelty"], "NEW")
        self.assertEqual(ev["facebook_4"]["qualification"]["verdict"], "OFF_TOPIC_CANDIDATE")
        y = [json.loads(x) for x in (self.tdir / "STATE" / "yield.jsonl").read_text().splitlines()][-1]
        self.assertEqual((y["rows"], y["new_relevant"], y["isolated"], y["duplicates"]), (4, 2, 1, 1))
        # S3 : annonceur relié par identifiant à une annonce pertinente ; l'autre reste non vérifié.
        r, out = self.step([adv("pB", "PeigneCo"), adv("zz", "Mouvewell page")], 3)
        q = {e["entity_id"]: e["qualification"]["verdict"] for e in out["evidence"] if e.get("entity_kind") == "ADVERTISER"}
        self.assertEqual(q, {"pB": "RELEVANT_VIA_LINKED_AD", "zz": "UNVERIFIED_NO_TEXT"})
        # S4 : uniquement domaines d'annonces pertinentes, exclusions appliquées, jamais le domaine hors sujet.
        r, out = self.step([shop("stop-poux.fr")], 4)
        self.assertEqual(r["request"]["params"], {"query": "stop-poux.fr", "limit": 1, "match_mode": "exact"})
        self.assertEqual(out["evidence"][-1]["qualification"]["verdict"], "RELEVANT_VIA_LINKED_AD")
        r, out = self.step([shop("peigne-fictif.fr", products=4077, category="Apparel", country="CN")], 4)
        sq = out["evidence"][-1]["qualification"]
        self.assertEqual(sq["specialization"], "GENERALIST")
        self.assertIn("SHOP_ORIGIN_CN_NOT_CUSTOMER_MARKET", sq["counter_signals"])
        st = self.a.plan_status()
        self.assertEqual(st["status"], "PLAN_COMPLETE", st["states"])
        self.assertEqual(st["states"]["S4"]["calls"], 2)  # deco-fictif.fr (hors sujet) et amazon.fr (exclu) jamais recherchés
        rep = self.a.build_reports()
        self.assertEqual(rep["status"], "REPORTS_WRITTEN")
        d = Path(rep["dir"])
        carto = (d / "11_CARTOGRAPHIE_ACTEURS.md").read_text()
        self.assertIn("stop-poux.fr", carto)
        self.assertIn("RELEVANT_VIA_LINKED_AD", carto)
        comp = (d / "14_RAPPORT_COMPARATIF_MARCHES.md").read_text()
        self.assertIn("NON RENDUE", comp)
        allout = "".join(p.read_text() for p in d.iterdir())
        for forbidden in ("estimatedSpend", "estMonthlySales", "EUR", "price"):
            self.assertNotIn(forbidden, allout)
        self.assertIn("vinaigre", (d / "13_LACUNES_SUBSTITUTS_CONTRE_PREUVES.md").read_text())
        self.assertEqual(self.a.reconcile_credits()["status"], "RECONCILED")

    def test_stop_rules_pagination_conditions_and_no_quota_spend(self):
        self.calibrated()
        lots = [{**PROBE, "queries": ["poux"]},
                {**SAMPLE, "queries_from": None, "queries": ["poux a", "poux b", "poux c"]},
                {**SAMPLE, "lot_id": "PG", "queries_from": None, "queries": ["poux page"], "pages": 2},
                {**SAMPLE, "lot_id": "V2", "wave": "V2_RELAX_ONE", "queries_from": {"lot": "PG", "same_queries": True}, "start_page": 3,
                 "condition": {"lot": "PG", "min_new_relevant": 50}}]
        self.install_plan(lots)
        self.step([ad(1, "poux")], 2, total=5)
        self.step([ad(10, "Canapé"), ad(11, "Chemise")], 2)          # « poux a » : 0 nouveau pertinent
        self.step([ad(10, "Canapé")], 2)                               # « poux b » : 0 → arrêt
        st = self.a.plan_status()["states"]
        self.assertEqual(st["S2"]["status"], "STOPPED_DIMINISHING_RETURNS")
        r, _ = self.step([ad(20, "poux lotion", "a1.fr", "A1", "a1"), ad(21, "poux peigne", "a2.fr", "A2", "a2")], 2)  # page 1 non pleine
        self.assertNotIn("page", r["request"]["params"])
        st = self.a.plan_status()
        self.assertEqual(st["states"]["PG"]["status"], "DONE")         # page 2 jamais demandée (page 1 < limit)
        self.assertEqual(st["states"]["V2"]["status"], "CONDITION_NOT_MET")
        self.assertEqual(st["status"], "PLAN_COMPLETE")
        self.assertEqual(self.a.next_call()["status"], "PLAN_COMPLETE")
        self.assertFalse(any("poux c" in json.dumps(json.loads(p.read_text())) for p in (self.tdir / "OUTBOX").glob("*.json")))

    def test_off_topic_ratio_stops_lot_and_no_linked_domain_means_no_shop_call(self):
        self.calibrated()
        self.install_plan([{**PROBE, "queries": ["poux"]},
                           {**SAMPLE, "queries_from": None, "queries": ["poux x", "poux y"]},
                           dict(SHOPS)], )
        self.step([ad(1, "Canapé")], 2, total=3)
        self.step([ad(i, "Chemise rayée") for i in range(30, 36)] + [ad(36, "poux", "z.fr", "Z", "z")], 2)
        st = self.a.plan_status()
        self.assertEqual(st["states"]["S2"]["status"], "STOPPED_OFF_TOPIC")
        self.assertEqual(st["next"]["linked_domain"], "z.fr")  # seul le domaine de l'annonce pertinente est proposé
        # Formulation déjà interrogée : réponse en cache, 0 crédit, rendement nul enregistré (le lot avance).
        self.install_plan([{**PROBE, "lot_id": "Q1", "queries": ["poux"]},
                           {**SAMPLE, "lot_id": "Q2", "queries_from": {"lot": "Q1", "top_k": 1}}])
        self.assertEqual(self.a.plan_status()["states"]["Q2"]["status"], "WAITING_FOR_LOT")
        r = self.a.next_call()
        self.assertEqual((r["status"], r["credits_spent"]), ("CACHE_HIT", 0))
        st = self.a.plan_status()
        self.assertEqual(st["states"]["Q1"]["status"], "DONE")
        self.assertEqual(st["states"]["Q2"]["status"], "NO_QUALIFIED_QUERY")  # premier résultat hors sujet : pas d'échantillon
        self.assertEqual(st["status"], "PLAN_COMPLETE")

    def test_regression_out_of_market_and_ambiguous_ads_never_trigger_shop_lookup(self):
        self.calibrated()
        self.install_plan([{**SAMPLE, "queries_from": None, "queries": ["poux"]}, dict(SHOPS)])
        self.step([ad(1, "Des poux ? Lotion enfant", "be-fictif.be", "BE", "b1", country="BE"),
                   ad(2, "Poux des poules ou de votre enfant", "amb-fictif.fr", "Amb", "b2"),
                   ad(3, "Des poux ? Peigne enfant", "fr-fictif.fr", "FR", "b3")], 2)
        st = self.a.plan_status()
        self.assertEqual(st["states"]["S2"]["new_relevant"], 1)
        self.assertEqual(st["next"]["linked_domain"], "fr-fictif.fr")
        self.step([shop("fr-fictif.fr")], 4)
        self.assertEqual(self.a.plan_status()["states"]["S4"]["status"], "DONE")  # be/amb jamais recherchés

    def test_regression_v2_page_requires_full_and_productive_previous_page(self):
        # Défaut 2 : A2 limit=8, page 1 = 4 pertinents sur 4 → V2A1 (start_page=2) ne doit pas être READY.
        self.calibrated()
        a2 = {**SAMPLE, "lot_id": "A2", "queries_from": None, "queries": ["poux"]}
        v2 = {**SAMPLE, "lot_id": "V2A1", "wave": "V2_RELAX_ONE", "queries_from": {"lot": "A2", "same_queries": True},
              "start_page": 2, "condition": {"lot": "A2", "min_new_relevant": 4}}
        self.install_plan([a2, v2])
        self.step([ad(i, f"Des poux ? Lotion {i}", f"d{i}.fr", f"M{i}", f"m{i}") for i in range(1, 5)], 2)
        st = self.a.plan_status()
        self.assertEqual(st["states"]["A2"]["new_relevant"], 4)
        self.assertNotEqual(st["states"]["V2A1"]["status"], "READY")
        self.assertEqual(st["status"], "PLAN_COMPLETE")
        # Page 1 pleine (8/8) et productive → la page 2 du lot V2 devient possible.
        self.install_plan([{**a2, "lot_id": "B2", "queries": ["poux lentes"]},
                           {**v2, "lot_id": "V2B1", "queries_from": {"lot": "B2", "same_queries": True}, "condition": {"lot": "B2", "min_new_relevant": 4}}])
        self.step([ad(i, f"Des poux ? Peigne {i}", f"e{i}.fr", f"N{i}", f"n{i}") for i in range(10, 18)], 2)
        st = self.a.plan_status()
        self.assertEqual((st["states"]["V2B1"]["status"], st["next"]["page"]), ("READY", 2))
        r, _ = self.step([ad(30, "Des poux ? Spray")], 2)
        self.assertEqual(r["request"]["params"]["page"], 2)

    def test_budget_envelope_exhausted_before_overspend(self):
        self.calibrated()
        self.install_plan([{**SAMPLE, "queries_from": None, "queries": ["poux a", "poux b"]}], budgets={"A": 19})
        self.step([ad(1, "poux lotion", "a.fr", "A", "a"), ad(2, "poux peigne", "b.fr", "B", "b")], 2)
        self.assertEqual(self.a.plan_status()["states"]["S2"]["status"], "BUDGET_EXHAUSTED")  # 4 réglés + 16 max > 19
        self.assertEqual(self.a.next_call()["status"], "PLAN_COMPLETE")


class AutonomousModeTests(IntelBase):
    def setUp(self):
        super().setUp()
        self.calibrated()
        self.install_plan([{**PROBE, "queries": ["poux", "lentes"]}])

    def autonomous(self, max_credits=500):
        return self.sign(self.a.prepare_live_approval(mission_id="M7", scope=SCOPE, max_credits=max_credits, hours=4, autonomous=True))

    def perm(self, params, tool="search_ads"):
        return guard.permission_request({"tool_name": P + tool, "tool_input": params}, self.runs, self.pub, self.pin)

    def test_signed_autonomous_call_is_allowed_once_without_manual_confirmation(self):
        self.autonomous()
        r = self.a.next_call()
        params = r["request"]["params"]
        self.assertIsNone(self.perm(params))  # pas encore AUTHORIZED par PreToolUse
        tid = "toolu_auto_1"
        pre = self.dispatch(r, tid)
        self.assertEqual(self.decision(pre), "ask")  # la règle de confirmation n'est jamais levée par PreToolUse
        self.assertIn("AUTONOME", pre["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.perm(params)
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertIsNone(self.perm(params))  # une seule fois
        self.assertIsNone(self.perm({**params, "limit": 20}))  # paramètres différents
        pre_bal = self.current()
        self.assertEqual(self.hook("post", P + "search_ads", params, payload([ad(1, "poux")], 2, total=3), tool_use_id=tid)["status"], "RECORDED")
        self.balance(pre_bal - 2)
        self.assertEqual(self.a.ingest_call(r["call_id"])["status"], "INGESTED")
        self.assertEqual(self.a.reconcile_credits()["status"], "RECONCILED")

    def test_offline_autonomous_loop_response_balance_ingest_reconcile_then_next_call(self):
        self.autonomous()
        for i, q in enumerate(("poux", "lentes"), start=1):
            r = self.a.next_call()
            self.assertEqual((r["status"], r["request"]["params"]["query"]), ("AWAITING_EXECUTION", q))
            tid = f"toolu_loop_{i}"
            self.assertEqual(self.decision(self.dispatch(r, tid)), "ask")
            self.assertEqual(self.perm(r["request"]["params"])["hookSpecificOutput"]["decision"]["behavior"], "allow")
            before = self.current()
            self.assertEqual(self.hook("post", P + "search_ads", r["request"]["params"],
                                       payload([ad(100 + i, f"Des {q} ? Lotion")], 2, total=9), tool_use_id=tid)["status"], "RECORDED")
            # Sans nouvelle lecture du solde après la réponse : règlement refusé, appel suivant bloqué.
            with self.assertRaisesRegex(PermissionError, "LIVE_POST_RESPONSE_BALANCE_REQUIRED"):
                self.a.ingest_call(r["call_id"])
            again = self.a.next_call()  # aucune seconde réservation : la même, non réglée, est renvoyée
            self.assertEqual((again.get("call_id"), again.get("idempotent_reuse")), (r["call_id"], True))
            self.assertEqual(len(self.a.live_status()["pending"]), 1)
            other = self.a.plan_call(mission_id="M7", scope=SCOPE, recipe_id="A01_S5_AD_DENSITY_PROBE", terms=["autre"], lot_id="P1")
            self.assertEqual(other["status"], "BLOCKED")
            self.assertEqual(len(self.a.live_status()["pending"]), 1)
            self.balance(before - 2)
            self.assertEqual(self.a.ingest_call(r["call_id"])["status"], "INGESTED")
            self.assertEqual(self.a.reconcile_credits()["status"], "RECONCILED")
        self.assertEqual(self.a.next_call()["status"], "PLAN_COMPLETE")
        self.assertEqual(self.a.live_status()["settled_by_scope"][SCOPE], 9 + 4)  # 9 de calibrage + 2 appels

    def test_out_of_plan_or_changed_plan_or_manual_approval_falls_back_to_human(self):
        self.autonomous()
        blocked = self.plan(recipe_id="A01_S5_AD_DENSITY_PROBE", terms=["autre chose"], lot_id="P1")
        self.assertIn("AUTONOMOUS_CALL_NOT_NEXT_IN_SIGNED_PLAN", str(blocked["reasons"]))
        self.assertIn("AUTONOMOUS_CALL_NOT_NEXT_IN_SIGNED_PLAN", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")["reasons"]))
        r = self.a.next_call()
        self.dispatch(r)
        plan = json.loads((self.mdir / tt.PLAN_FILENAME).read_text())
        plan["lots"][0]["queries"].append("nouvelle formulation")
        (self.mdir / tt.PLAN_FILENAME).write_text(json.dumps(plan))
        self.assertIsNone(self.perm(r["request"]["params"]))  # plan modifié après signature : confirmation humaine
        self.assertIn("AUTONOMOUS_PLAN_CHANGED", str(self.a.next_call().get("reasons") or self.a.plan_call(
            mission_id="M7", scope=SCOPE, recipe_id="A01_S5_AD_DENSITY_PROBE", terms=["lentes"], lot_id="P1")["reasons"]))

    def test_manual_approval_never_autonomous_and_other_tools_never_allowed(self):
        r = self.a.next_call()  # approbation non autonome (ready)
        self.dispatch(r)
        self.assertIsNone(self.perm(r["request"]["params"]))
        for name in ("search_products", "find_similar_shops", "add_board_item", "check_credits"):
            self.assertIsNone(self.perm({}, tool=name))
        self.assertIsNone(guard.permission_request({"tool_name": "mcp__claude_ai_Shopify__create-product", "tool_input": {}},
                                                   self.runs, self.pub, self.pin))
        self.assertEqual(self.decision(self.hook("pre", P + "search_products")), "deny")

    def test_window_halt_and_budget_bounds(self):
        with self.assertRaisesRegex(ValueError, "AUTONOMOUS_PLAN_BUDGETS_ABOVE_APPROVAL"):
            self.autonomous(max_credits=100)
        self.autonomous()
        r = self.a.next_call()
        self.dispatch(r)
        later = tt._now() + dt.timedelta(seconds=tt.PERMISSION_WINDOW_SECONDS + 5)
        with patch.object(tt, "_now", return_value=later):
            self.assertIsNone(self.perm(r["request"]["params"]))
        with self.a._lock():
            self.a._halt("TEST_HALT")
        self.assertIsNone(self.perm(r["request"]["params"]))

    def test_reserve_floor_kept_in_autonomous_mode(self):
        self.autonomous()
        self.balance(501)
        self.assertIn("RESERVE_FLOOR_BREACH", str(self.a.next_call().get("reasons")))

    def test_guard_permission_event_fails_closed_to_human_prompt(self):
        for stdin in ("pas du json", json.dumps({"tool_name": P + "search_ads", "tool_input": {"query": "x"}})):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = guard.main(["permission", "--pubkey", str(self.pub), "--pubkey-sha256", self.pin, "--runs-dir", str(self.runs)],
                                stdin=io.StringIO(stdin))
            self.assertEqual((rc, buf.getvalue()), (0, ""))
        self.assertIsNone(guard.permission_request({"tool_name": P + "search_ads", "tool_input": {}}, self.runs, self.pub, "0" * 64))


class SettingsAndCliTests(unittest.TestCase):
    def test_settings_wire_permission_hook_and_keep_ask_rules(self):
        conf = json.loads((ecom.ROOT / ".claude" / "settings.json").read_text())
        if "PermissionRequest" not in conf.get("hooks", {}):
            self.skipTest("réglages du paquet d'intelligence pas encore installés")
        cmd = conf["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.startswith("/usr/bin/python3 -I ") and " permission " in cmd and "--pubkey-sha256 " in cmd)
        self.assertEqual(conf["hooks"]["PermissionRequest"][0]["matcher"], "mcp__claude_ai_Marketpulse__.*")
        self.assertEqual(sorted(conf["permissions"]["ask"]), sorted(P + t for t in tt.LIVE_TOOLS))
        allow, deny = conf["permissions"]["allow"], conf["permissions"]["deny"]
        self.assertFalse(any(a.startswith(P + "search_") for a in allow))
        for c in ("next", "wave", "report"):
            self.assertIn(f"Bash(python3 ecom.py tt {c} --mission RECHERCHE_07 --mode MCP_SESSION)", allow)
        self.assertIn("Bash(*approve-live*)", deny)

    def test_replay_plan_call_accepts_page_and_lot_without_live_rules(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a = tt.MarketPulseAdapter(Path(tmp) / "TT", mode="REPLAY")
            a.ingest_balance({"requestId": "b", "credits": {"totalRemaining": 9000}})
            a.record_replay_approval(scope="S", max_credits=50, owner_statement="simulation REPLAY de test")
            r = a.plan_call(mission_id="R", scope="S", recipe_id="A01_S5_AD_SAMPLE_REACH", terms=["poux"], page=2, lot_id="L")
            self.assertEqual(r["status"], "AWAITING_EXECUTION")
            self.assertIn("PAGE_OUTSIDE", str(a.plan_call(mission_id="R", scope="S", recipe_id="A01_S5_AD_DENSITY_PROBE",
                                                          terms=["poux"], page=2)["reasons"]))


class RecherchePlanFileTests(unittest.TestCase):
    def test_recherche_07_plan_is_valid_and_within_500(self):
        p = ecom.ROOT / "runs" / "RECHERCHE_07" / tt.PLAN_FILENAME
        if not p.is_file():
            self.skipTest("plan RECHERCHE_07 non installé dans cette copie")
        plan = json.loads(p.read_text())
        self.assertEqual(tt.validate_plan(plan), [])
        self.assertLessEqual(sum(plan["budgets"].values()), 500)
        self.assertEqual(plan["market_geo"], "FR")
        recipes = {l["recipe"] for l in plan["lots"]}
        self.assertNotIn("A02_FR_BASELINE_SHOPS", recipes)
        for l in plan["lots"]:
            if tt.RECIPES[l["recipe"]]["tool"] == "search_shops":
                self.assertIn("domains_from", l)
        src = (ecom.ROOT / "engine" / "marketpulse_adapter.py").read_text()
        for market_word in ("poux", "incontinence"):
            self.assertNotIn(market_word, src.split("class MarketPulseAdapter")[1])  # aucun marché codé en dur dans le moteur
