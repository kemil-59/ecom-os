"""Méthode active de recherche (Agents 01/02) — tests hors réseau.

Les pages des tests 1 à 13 sont SYNTHÉTIQUES : elles vérifient le calcul et la logique, jamais une demande réelle.
Le test de bout en bout rejoue les pages RÉELLES de RECHERCHE_08 (instantanés sha256, tests/fixtures/r08).
"""
import gzip
import json
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ecom
from engine import (autonomous_research as ar, autopilot, competitor_intel as ci, demand, discovery, ledger, persona,
                    research_depth, scoring, web_capture as wc)

FX = Path(__file__).parent / "fixtures" / "r08"
TH = demand.load_thresholds()
PROFILE = persona.load_profile("PERSONA_A_FR")
NEW = "https://www.forum-inconnu-exemple.fr/discussion/42"          # domaine absent de tout registre
REV = "https://boutique-dtc-exemple.fr/produit/creme"
CAP = "https://www.site-protege-exemple.fr/page"
PAGES = {
    NEW: "<html lang='fr'><div class='post'><span class='author'>marie_l</span> · 12/03/2025<p>Depuis des mois mes talons se fendillent malgré la crème, c'est douloureux.</p></div>"
         "<div class='post'><span class='author'>jules84</span> · 14/03/2025<p>Chez moi aussi les talons craquent dès que je marche pieds nus.</p></div>"
         "<div class='post'><p>Sans auteur visible : la crème ne tient pas la journée sur mes pieds.</p></div></html>",
    REV: "<html lang='fr'><script type='application/ld+json'>{\"@type\":\"Product\",\"review\":[{\"@type\":\"Review\","
         "\"author\":{\"@type\":\"Person\",\"name\":\"Claire\"},\"datePublished\":\"2025-06-01\","
         "\"reviewBody\":\"Trop grasse, elle tache les chaussettes et ne répare rien.\"}]}</script>"
         "<p>Crème talons 29,90 € — livraison offerte dès 2 tubes</p></html>",
    CAP: "<html><title>Just a moment...</title><p>Vérifiez que vous êtes humain (captcha)</p></html>",
}


def fetch(url):
    if url.endswith("/robots.txt"):
        if "interdit-exemple" in url:
            return 200, url, b"User-agent: *\nDisallow: /\n"
        return 404, url, b""
    if url in PAGES:
        return 200, url, PAGES[url].encode()
    return 404, url, b""


def fixture_fetcher(url):
    if url.endswith("/robots.txt"):
        return 404, url, b""
    idx = json.loads((FX / "index.json").read_text())
    if url not in idx:
        return 404, url, b""
    return 200, url, gzip.decompress((FX / "pages" / idx[url]).read_bytes())


def mission(root, name, cfg=None):
    m = Path(root) / name
    (m / "AGENT_01").mkdir(parents=True)
    (m / "RESEARCH_INBOX" / "proposals").mkdir(parents=True)
    (m / "mission.json").write_text(json.dumps({"mission_id": name, "target_sale_market": "FR", "mode": "FETCH_VERIFIED",
                                                **(cfg or {})}))
    return m


def obs(n, *, dom="exemple.fr", kind="FRUSTRATION", st="WEB_FORUM", author=None, text=None, geo="LANGUAGE_ONLY",
        year=2025, cands=("C1",), nature="USER_CONTRIBUTION_MARKUP", **kw):
    return {"observation_id": f"OBS-{n:05d}", "territory_id": "T1", "kind": kind, "source_type": st,
            "author_key": author or f"acct:{n}", "author_site_key": f"site:{dom}:{author or n}", "market_geo": "UNKNOWN",
            "geo_basis": geo, "spontane": True, "provenance_mode": "FETCH_VERIFIED", "registrable_domain": dom,
            "phrase_exacte": text or f"mes talons se fendillent encore message numéro {n} vraiment",
            "source_url": f"https://www.{dom}/t/{n % 7}", "source_date": f"01/03/{year}", "candidate_ids": list(cands),
            "contribution_nature": nature, **kw}


def broad_rows(n_authors, n_domains, *, years=(2023, 2024, 2025), fr_q=0, start=1, kinds=("WEB_FORUM", "WEB_REVIEW", "WEB_COMMENT")):
    rows = []
    for i in range(start, start + n_authors):
        rows.append(obs(i, dom=f"site{i % n_domains}.fr", st=kinds[i % len(kinds)], year=years[i % len(years)],
                        geo="RESIDENCE_DECLARED" if i - start < fr_q else "LANGUAGE_ONLY",
                        text=f"talons fendillés auteur {i} raconte sa situation précise numéro {i * 7}"))
    return rows


def micro(mid="M1", refs=(), terms=("talons",), **kw):
    return {"id": mid, "territory_id": "T1", "who": "x", "moment": "x", "trigger": "x", "specific_friction": "x",
            "attempted_solution": "x", "unresolved_reason": "x", "pain_refs": list(refs), "counter_refs": [],
            "signature_terms": list(terms), **kw}


V3 = lambda mode="DISCOVERED", **kw: {"thresholds": TH, "persona_mode": mode, **kw}


class CollectionTests(unittest.TestCase):
    def test_01_unknown_public_site_verified_without_any_activation(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "M1")
            self.assertNotIn("www.forum-inconnu-exemple.fr", wc.ADAPTERS)
            r = wc.verify(m, url=NEW, quote="mes talons se fendillent malgré la crème", author="marie_l", fetcher=fetch, source_kind="FORUM")
            self.assertEqual((r["status"], r["binding"], r["source_type"], r["contribution_nature"]),
                             ("QUOTE_VERIFIED", "COMMENT_BLOCK", "WEB_FORUM", "USER_CONTRIBUTION_MARKUP"))
            self.assertEqual(r["testimony_date"], "12/03/2025")
            s = wc.verify(m, url=REV, quote="elle tache les chaussettes et ne répare rien", author="Claire", fetcher=fetch)
            self.assertEqual((s["status"], s["binding"], s["source_type"], s["testimony_date"]),
                             ("QUOTE_VERIFIED", "STRUCTURED", "WEB_REVIEW", "2025-06-01"))
            PAGES["https://avis-traduits-exemple.fr/p"] = ("<p>Noemi · 01/02/2025</p><p>Crème trop légère pour mes talons très secs."
                                                          "</p><p>Publié à l'origine sur exemple.it Traduit de l'italien</p>")
            tr = wc.verify(m, url="https://avis-traduits-exemple.fr/p", quote="Crème trop légère pour mes talons très secs", author="Noemi", fetcher=fetch)
            self.assertEqual(tr["geo_basis"], "SYNDICATED_OR_FOREIGN")              # traduction : jamais demande FR
            u = wc.verify(m, url=NEW, quote="la crème ne tient pas la journée sur mes pieds", author="inconnue", fetcher=fetch)
            self.assertEqual((u["binding"], u["author_bound"]), ("UNBOUND", False))
            self.assertTrue(u["author_key"].startswith("unbound:"))   # contribution, jamais une personne identifiée

    def test_02_inaccessible_source_never_becomes_verified(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "M2")
            v = lambda **k: wc.verify(m, fetcher=fetch, **k)["status"]
            self.assertEqual(v(url="https://www.interdit-exemple.fr/x", quote="une citation assez longue ici", author="a"), "BLOCKED_ROBOTS")
            self.assertEqual(v(url=CAP, quote="une citation assez longue ici", author="a"), "BLOCKED_CAPTCHA")
            self.assertEqual(v(url="https://www.absent-exemple.fr/x", quote="une citation assez longue ici", author="a"), "BLOCKED_HTTP_404")
            # Résumé de moteur de recherche : citation absente de la page réellement téléchargée.
            self.assertEqual(v(url=NEW, quote="résumé inventé par un moteur de recherche", author="marie_l"), "QUOTE_NOT_FOUND")
            self.assertEqual(v(url="https://localhost/x", quote="une citation assez longue ici", author="a"), "UNSAFE_URL")
            with mock.patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
                self.assertFalse(wc._host_is_public("intranet-exemple.fr"))     # résolution vers un hôte privé
                self.assertEqual(wc.http_fetch("https://intranet-exemple.fr/x")[0], 0)
            caps = [{"url": CAP, "quote": "une citation assez longue ici", "author": "a", "kind": "FRUSTRATION",
                     "territory_id": "T1", "probleme": "p", "jtbd": "J1"}]
            (m / "RESEARCH_INBOX/proposals/captures_1.json").write_text(json.dumps(caps))
            ar.run(m, fetcher=fetch)
            reg = [json.loads(x) for x in (m / "RESEARCH_INBOX/discovery_registry.jsonl").read_text().splitlines()]
            self.assertEqual((reg[0]["status"], reg[0]["access"]), ("DISCOVERED_UNVERIFIED", "BLOCKED_CAPTCHA"))
            self.assertEqual(ledger.load(m / "AGENT_01/problem_ledger.jsonl"), [])

    def test_03_sectors_never_selected_from_a_site_list(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "M3")
            secs = ["beaute_soin", "sante_bien_etre", "mode_vetements", "voyages_mobilite", "sport_activite",
                    "tech_numerique", "parentalite_enfants", "budget_consommation"]
            ts = [{"territory_id": f"T{i}", "name": f"T{i}", "sector": secs[i], "micro_contexts": [{"moment": "a", "place": "b", "friction": "c"}],
                   "discovery_refs": [f"https://www.communaute{i}-exemple.fr/fil"]} for i in range(8)]
            discovery.record(m, [{"url": x["discovery_refs"][0], "territory_id": x["territory_id"]} for x in ts], "t")
            self.assertEqual(discovery.check_territory_discovery(ts, m), [])
            with mock.patch.object(wc, "ADAPTERS", {}):
                self.assertEqual(discovery.check_territory_discovery(ts, m), [])        # indépendant des adaptateurs
            ts[0]["sector"] = ts[1]["sector"] = ts[2]["sector"] = "maison_entretien"
            self.assertTrue(any(i.startswith("TERRITORY_SECTOR_OVERREPRESENTED") for i in discovery.check_territory_discovery(ts, m)))


class AccumulationTests(unittest.TestCase):
    def test_04_testimonies_from_many_sites_join_the_same_micro(self):
        rows = [obs(i, dom=f"site{i}.fr", text=f"mes talons craquent encore témoignage {i} depuis l'hiver") for i in range(1, 13)]
        r = research_depth.analyze(rows, {"micro_problems": [micro(refs=[x["observation_id"] for x in rows], terms=["talons"])]}, v2=V3())
        m = r["stage2"][0]
        self.assertEqual(len(m["matched_pain_refs"]), 12)
        self.assertEqual(len(m["demand"]["indicators"]["6_distribution"]["domains"]), 12)

    def test_05_neighbouring_problems_are_not_merged(self):
        rows = [obs(1, text="mes talons se fendillent tout l'hiver"), obs(2, text="la crème laisse des traces grasses sur le sol")]
        r = research_depth.analyze(rows, {"micro_problems": [micro("TALONS", ["OBS-00001", "OBS-00002"], ["talons", "fendill"]),
                                                             micro("TRACES", ["OBS-00002"], ["traces grasses"])]}, v2=V3())
        a, b = r["stage2"]
        self.assertEqual((a["matched_pain_refs"], a["refs_signature_mismatch"]), (["OBS-00001"], ["OBS-00002"]))
        self.assertEqual(b["matched_pain_refs"], ["OBS-00002"])
        self.assertEqual(a["shared_refs_with_other_micros"], [])

    def test_06_people_authors_messages_threads_volumes_counted_separately(self):
        rows = [obs(i, author=f"a{i % 3}", dom="x.fr") for i in range(1, 10)]
        vol = [obs(99, kind="INDEX_COUNT", count=4800)]
        ind = demand.assess(rows, volume_rows=vol, thresholds=TH)["indicators"]
        self.assertEqual(ind["3_contributions"], 9)
        self.assertEqual(ind["4_distinct_authors_interval"][0], 3)
        self.assertEqual(ind["2_relevant_pages"], 7)
        self.assertEqual(demand.assess(rows, volume_rows=vol, thresholds=TH)["volume_indicators_not_persons"][0]["count"], 4800)

    def test_07_duplicates_and_syndication_not_double_counted(self):
        same = "cette crème pour talons ne tient pas la journée même en mettant des chaussettes la nuit"
        rows = [obs(1, author="h", dom="a.fr"), obs(2, author="h", dom="b.fr"),            # même pseudo, deux sites
                obs(3, author="x", text=same), obs(4, author="y", text=same + " vraiment"),  # copie
                obs(5, author="z", geo="SYNDICATED_OR_FOREIGN"),                             # syndiqué
                obs(6, author="unbound:abc")]                                                # non rattachable
        a = demand.assess(rows, thresholds=TH)
        lo, hi = a["indicators"]["4_distinct_authors_interval"]
        self.assertEqual(lo, 2)
        self.assertGreater(hi, lo)
        self.assertEqual(a["indicators"]["5_uniqueness_uncertainty"]["same_handle_on_several_sites"], 1)
        self.assertEqual(a["indicators"]["5_uniqueness_uncertainty"]["unbound_contributions"], 1)
        self.assertIn("EXCLUDED_SOLICITED_FOREIGN_OR_SYNDICATED:1", a["biases"])

    def test_08_distribution_between_independent_sources(self):
        rows = broad_rows(30, 3)
        ind = demand.assess(rows, thresholds=TH)["indicators"]
        self.assertEqual(sorted(ind["6_distribution"]["domains"].values()), [10, 10, 10])
        self.assertAlmostEqual(ind["6_distribution"]["hhi"], 0.333, places=2)
        self.assertEqual(len(ind["6_distribution"]["source_kinds"]), 3)

    def test_09_large_volume_is_not_commercial_validation(self):
        t0 = time.time()
        one_site = [obs(i, dom="unique.fr", text=f"talons abîmés témoignage {i} unique site message {i * 3}") for i in range(1, 5001)]
        a = demand.assess(one_site, thresholds=TH)
        self.assertEqual(a["indicators"]["4_distinct_authors_interval"][0], 5000)
        self.assertEqual(a["level"], "HYPOTHESIS_DETECTED")                                  # un seul domaine
        one_year = broad_rows(3000, 30, years=(2025,), fr_q=100)
        self.assertNotIn(demand.assess(one_year, thresholds=TH)["level"], ("BROAD_DISTRIBUTED_SIGNAL", "SIGNIFICANT_MARKET_DOCUMENTED"))
        self.assertLess(time.time() - t0, 20)


class PersonaTests(unittest.TestCase):
    def test_10_market_studied_without_imposed_persona_segment_discovered(self):
        mid = "TEST_METHOD_INIT"
        try:
            ecom.main(["init", "--mission", mid, "--mode", "FETCH_VERIFIED"])
            cfg = json.loads((ecom.ROOT / "runs" / mid / "mission.json").read_text())
            self.assertEqual((cfg["persona"], cfg["persona_mode"]), (None, "DISCOVERED"))
            self.assertEqual(persona.resolve(cfg)["mode"], "DISCOVERED")
        finally:
            shutil.rmtree(ecom.ROOT / "runs" / mid, ignore_errors=True)
        rows = [obs(i, declared_attributes={"gender": "F", "roles": ["parent"], "age_band": "35-44"} if i <= 12 else {})
                for i in range(1, 31)]
        seg = persona.segment_profile(rows, TH)
        self.assertEqual(seg["testimonies_declaring"], 12)
        self.assertIn({"dimension": "roles", "value": "parent", "testimonies": 12, "share_of_declaring": 1.0,
                       "status": "EMERGENT_SEGMENT_HYPOTHESIS"}, seg["emergent_segments"])
        self.assertEqual(persona.extract_attributes("je suis maman de deux ados et j'ai 47 ans"),
                         {"age": 47, "age_band": "45-54", "gender": "F", "roles": ["parent"]})
        self.assertEqual(persona.extract_attributes("marie42"), {})                       # jamais depuis un pseudo

    def test_11_explicit_persona_a_mission_keeps_its_targeting(self):
        mid = "TEST_METHOD_PERSONA_A"
        try:
            ecom.main(["init", "--mission", mid, "--mode", "FETCH_VERIFIED", "--persona", "PERSONA_A_FR"])
            cfg = json.loads((ecom.ROOT / "runs" / mid / "mission.json").read_text())
            self.assertEqual((cfg["persona"], cfg["persona_mode"]), ("PERSONA_A_FR", "PROFILE"))
            with self.assertRaises(SystemExit):
                ecom.main(["init", "--mission", mid + "_B", "--mode", "FETCH_VERIFIED", "--persona", "texte libre"])
        finally:
            shutil.rmtree(ecom.ROOT / "runs" / mid, ignore_errors=True)
        self.assertIn("PERSONA_LINK_MISSING:T1", persona.check_territories([{"territory_id": "T1", "name": "x"}], PROFILE))
        out = [obs(i, persona_basis=persona.OUT) for i in range(1, 8)]
        self.assertEqual(persona.fit_status(out, TH, "PROFILE")["status"], "PERSONA_OUT_OF_TARGET")
        d = {"candidate_id": "C1", "research_mode": True, "persona_mode": "PROFILE", "persona_fit": {"status": "PERSONA_OUT_OF_TARGET"}}
        self.assertEqual(autopilot.decide_gate4(d)["verdict"], "REJECT")
        self.assertEqual(persona.fit_status([], TH, "PROFILE", applicable_segment_statistic=True)["status"], "PERSONA_FIT_DEMONSTRATED")


class Agent02Tests(unittest.TestCase):
    def _research(self, n_fr=5):
        return {"queries": [{"q": f"q{i}", "intent": it} for i, it in enumerate(
                    ["FR_COMPETITORS", "SATISFACTION", "NEGATIVE_REVIEWS", "SUBSTITUTES", "INVALIDATION"][:n_fr])]
                + [{"q": "us", "intent": "INTERNATIONAL", "market": "US"}],
                "invalidation_attempts": [{"hypothesis": "déjà résolu", "method": "avis 5★", "result": "NOT_INVALIDATED"},
                                          {"hypothesis": "geste gratuit", "method": "forums", "result": "INCONCLUSIVE"}],
                "competitors": [{"name": "Marque X", "type": "DIRECT", "evidence_refs": ["OBS-00010"]},
                                {"name": "US Brand", "type": "DIRECT", "market": "US"}]}

    def test_12_competitors_public_prices_and_counter_evidence(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "M12")
            p = wc.verify(m, url=REV, quote="Crème talons 29,90 €", author=None, fetcher=fetch, capture_kind="PRICE_OBSERVED")
            self.assertEqual((p["status"], p["amount"], p["kind_hint"]), ("QUOTE_VERIFIED", 29.9, "PRICE_OBSERVED"))
            self.assertEqual(wc.verify(m, url=REV, quote="Crème talons 19,90 €", author=None, fetcher=fetch,
                                       capture_kind="PRICE_OBSERVED")["status"], "QUOTE_NOT_FOUND")   # prix inventé refusé
        rows = [obs(1, author="a", dimension="fissures"), obs(2, author="b", dimension="fissures"),
                obs(10, kind="PRICE_OBSERVED", amount=29.9, incumbent="Marque X"),
                obs(11, kind="OFFER_OBSERVED", incumbent="Marque X")]
        self.assertEqual(ci.agent02_v2_assessment(rows, {"candidate_id": "C1"}, None, TH)["verdict"], "INSUFFICIENT_COUNTER_RESEARCH")
        r = ci.agent02_v2_assessment(rows, {"candidate_id": "C1"}, self._research(), TH)
        self.assertEqual(r["verdict"], "DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN")
        self.assertEqual((r["public_price_range"], r["public_prices"][0]["url"]), ([29.9, 29.9], rows[2]["source_url"]))
        self.assertEqual(r["competitors"][1]["role"], "INSPIRATION_ONLY_NOT_FR_DEMAND")
        sat = rows + [obs(20 + i, kind="SATISFACTION", author=f"s{i}") for i in range(3)]
        self.assertEqual(ci.agent02_v2_assessment(sat, {"candidate_id": "C1"}, self._research(), TH)["verdict"], "ALREADY_WELL_SOLVED")
        none = ci.agent02_v2_assessment(rows, {"candidate_id": "C1"}, {**self._research(), "competitors": []}, TH)
        self.assertEqual(none["verdict"], "COMPETITION_UNKNOWN_SEARCH_INSUFFICIENT")       # jamais « marché vide »
        self.assertIn("FR_QUERIES_BELOW_5", ci.agent02_v2_assessment(rows, {"candidate_id": "C1"}, self._research(3), TH)["counter_research_issues"])


class GateTests(unittest.TestCase):
    def test_13_no_gate_passed_by_score_or_few_testimonies(self):
        few = [obs(i, author=f"a{i}", dom=f"s{i}.fr") for i in range(1, 4)]
        self.assertNotEqual(demand.gate_2b_convergence(few, "C1", thresholds=TH)["status"], "PASS")
        idx = {r["observation_id"]: r for r in few}
        axes = {ax: {"score": 5, "evidence_refs": list(idx), "justification": "trois témoignages seulement"}
                for ax in ("frequency", "intensity", "dissatisfaction")}
        s = scoring.score_with_evidence_v2(axes, ref_index=idx, candidate_id="C1", thresholds=TH, level="HYPOTHESIS_DETECTED")
        self.assertEqual(s["caps"]["frequency"], 1)
        ok = {"candidate_id": "C", "research_mode": True, "gate2b": {"status": "PASS"}, "score": {"score": 40, "status": "SCORED_WITH_EVIDENCE"}}
        for lvl in ("HYPOTHESIS_DETECTED", "PROBLEM_CORROBORATED", "BROAD_DISTRIBUTED_SIGNAL"):
            self.assertEqual(autopilot.decide_gate4({**ok, "demand": {"level": lvl}})["verdict"], "RESEARCH")
        # Chemin positif (synthétique) : signal large, distribué, persistant, ≥ 1000 auteurs sur ≥ 20 domaines.
        rows = broad_rows(1200, 25, fr_q=80)
        a = demand.assess(rows, thresholds=TH)
        self.assertEqual((a["level"], a["evidence_paths"]["testimony"], a["evidence_paths"]["statistic"]),
                         ("SIGNIFICANT_MARKET_DOCUMENTED", True, False))
        res = {"C1": {**Agent02Tests()._research(), "no_public_price_found": True}}
        r = research_depth.analyze(rows, {"micro_problems": [micro(refs=[x["observation_id"] for x in rows], candidate_id="C1")]},
                                   v2=V3(competitive_research=res))
        self.assertEqual(r["stage2"][0]["agent02"]["verdict"], "NO_CREDIBLE_ADVANTAGE_FOUND")    # aucune dimension en défaut
        self.assertEqual(r["stage2"][0]["verdict"], "REJECTED")                                   # grand marché ≠ opportunité
        rows2 = [{**x, "dimension": "fissures"} for x in rows]
        r = research_depth.analyze(rows2, {"micro_problems": [micro(refs=[x["observation_id"] for x in rows2], candidate_id="C1")]},
                                   v2=V3(competitive_research=res))
        self.assertEqual(r["stage2"][0]["verdict"], "COMMERCIAL_OPPORTUNITY_TO_DEEPEN")
        self.assertEqual(r["owner_checkpoint"]["opportunities"], ["M1"])

    def test_13b_saturated_investigation_concludes_insufficient(self):
        inv = {}
        rows = [obs(i, author=f"a{i}", dom=f"s{i % 4}.fr", st=["WEB_FORUM", "WEB_REVIEW"][i % 2]) for i in range(1, 13)]
        for k in range(5):
            batch = rows + [obs(100 + j, author=f"a{1 + j}", dom="s1.fr") for j in range(k + 1)]   # nouveaux messages, mêmes auteurs
            r = research_depth.analyze(batch, {"micro_problems": [micro(refs=[x["observation_id"] for x in batch])]}, v2=V3(investigation=inv))
        self.assertTrue(r["stage2"][0]["investigation"]["saturated"])
        self.assertEqual(r["stage2"][0]["verdict"], "INSUFFICIENT_EVIDENCE")


STUDY = "https://institut-exemple.fr/etude/pieds-2024"
STUDY_HTML = ("<html lang='fr'><h1>Baromètre santé des pieds</h1><p>Enquête réalisée en France en 2024 auprès de 2 015 adultes"
              " (échantillon représentatif, méthode des quotas).</p><p>Parmi les femmes de 45 à 60 ans, 31 % déclarent souffrir"
              " de talons fendillés au moins une fois par mois.</p></html>")


def study_fetch(url):
    if url == STUDY:
        return 200, url, STUDY_HTML.encode()
    if url.endswith("/rapport.pdf") or url == "https://institut-exemple.fr/rapport":
        return 200, url, b"%PDF-1.7 binaire"
    return fetch(url)


class FrenchMarketAndStudyTests(unittest.TestCase):
    def test_17_unknown_geography_majority_never_certifies_fr_market(self):
        rows = broad_rows(1200, 25, fr_q=60)
        rows = [{**r, "registrable_domain": r["registrable_domain"].replace(".fr", ".com"),
                 "source_url": r["source_url"].replace(".fr/", ".com/")} for r in rows]
        a = demand.assess(rows, thresholds=TH)
        self.assertEqual(a["fr_market"]["status"], "FR_SCALE_NOT_DEMONSTRATED")
        self.assertNotEqual(a["level"], "SIGNIFICANT_MARKET_DOCUMENTED")
        self.assertIn("LARGE_SIGNAL_BUT_FR_SCALE_NOT_DEMONSTRATED", a["biases"])
        self.assertEqual(a["fr_market"]["testimonies_by_geography"]["UNKNOWN_COUNTRY"], 1140)
        self.assertEqual(a["fr_market"]["testimonies_by_geography"]["FR_RESIDENCE_DECLARED"], 60)

    def test_18_fr_merchant_site_author_is_not_a_proven_resident(self):
        rows = [obs(i, author=f"m{i}", st="RETAILER_REVIEW_FR", geo="FR_MARKET_SITE", dom="auchan.fr") for i in range(1, 30)]
        a = demand.assess(rows, thresholds=TH)
        self.assertEqual(a["fr_market"]["fr_residence_declared_testimonies"], 0)
        self.assertEqual(a["fr_qualified_authors"], 0)
        self.assertEqual(a["fr_market"]["testimonies_by_geography"]["FR_MARKET_SITE_NO_RESIDENCE"], 29)
        intl = demand.assess([obs(1, geo="SYNDICATED_OR_FOREIGN")], thresholds=TH)
        self.assertEqual(intl["fr_market"]["testimonies_by_geography"]["INTERNATIONAL"], 1)   # signal conservé, hors FR

    def test_19_many_domains_are_not_independent_publications(self):
        same = "les talons fendillés reviennent chaque hiver malgré toutes les crèmes que j'achète en pharmacie"
        rows = [obs(i, author=f"syn{i}", dom=f"revendeur{i}.fr", text=same) for i in range(1, 13)]
        a = demand.assess(rows, thresholds=TH)
        self.assertEqual((a["independence"]["raw_domains"], a["independence"]["independent_domains"]), (12, 1))
        self.assertEqual(a["level"], "HYPOTHESIS_DETECTED")

    def test_20_study_verified_on_html_page_with_all_fields(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "MS")
            ok = dict(url=STUDY, figure_quote="31 % déclarent souffrir de talons fendillés", date_claim="2024",
                      country_claim="France", population_claim="femmes de 45 à 60 ans", link_terms=["talons fendillés"],
                      denominator_claim="2 015 adultes", fetcher=study_fetch)
            r = wc.verify_study(m, **ok)
            self.assertEqual((r["status"], r["country"]), ("VERIFIED", "FR"))
            self.assertEqual(wc.verify_study(m, **{**ok, "population_claim": "hommes de 18 à 25 ans"})["status"], "PARTIALLY_VERIFIED")
            self.assertEqual(wc.verify_study(m, **{**ok, "link_terms": ["ongles incarnés"]})["missing"], ["micro_link"])
            self.assertEqual(wc.verify_study(m, **{**ok, "figure_quote": "45 % des Français"})["status"], "FIGURE_NOT_FOUND")
            self.assertEqual(wc.verify_study(m, **{**ok, "url": "https://institut-exemple.fr/rapport.pdf"})["status"], "UNSUPPORTED_FORMAT_PDF")
            self.assertEqual(wc.verify_study(m, **{**ok, "url": "https://institut-exemple.fr/rapport"})["status"], "UNSUPPORTED_FORMAT_PDF")
            self.assertEqual(wc.verify_study(m, **{**ok, "country_claim": "Belgique"})["status"], "PARTIALLY_VERIFIED")

    def test_21_proposed_verified_flag_never_passes_a_gate(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "MS2")
            pr = m / "RESEARCH_INBOX/proposals"
            (pr / "micro_problems.json").write_text(json.dumps({"micro_problems": [micro(terms=["talons fendillés"])]}))
            (pr / "studies.json").write_text(json.dumps({"studies": [
                {"id": "S_FAKE", "url": STUDY, "figure_quote": "62 % des Français", "date": "2024", "country": "France",
                 "population": "adultes", "scope": "MICRO_PROBLEM", "applies_to": ["M1"], "verification": "VERIFIED"},
                {"id": "S_OK", "url": STUDY, "figure_quote": "31 % déclarent souffrir de talons fendillés", "date": "2024",
                 "country": "France", "population": "femmes de 45 à 60 ans", "scope": "MICRO_PROBLEM", "applies_to": ["M1"]}]}))
            st = {s["id"]: s for s in ar._studies(m, fetcher=study_fetch)}
            self.assertEqual(st["S_FAKE"]["verification"], "FIGURE_NOT_FOUND")      # statut proposé ignoré
            self.assertEqual((st["S_OK"]["verification"], st["S_OK"]["country"]), ("VERIFIED", "FR"))
            fr_rows = [obs(i, author=f"f{i}", dom=f"forum{i % 4}.fr", st=["WEB_FORUM", "WEB_REVIEW"][i % 2]) for i in range(1, 13)]
            fake_only = demand.assess(fr_rows, studies=[st["S_FAKE"], {"id": "X", "verification": "VERIFIED", "country": "FR",
                                                                      "scope": "MICRO_PROBLEM", "applies_to": ["M1"]}],
                                      scope_id="M1", thresholds=TH)
            self.assertEqual(fake_only["applicable_statistics"], [])                # sans reçu moteur : jamais applicable
            self.assertNotEqual(fake_only["level"], "SIGNIFICANT_MARKET_DOCUMENTED")
            real = demand.assess(fr_rows, studies=[st["S_OK"]], scope_id="M1", thresholds=TH)
            self.assertEqual((real["level"], real["fr_market"]["status"]), ("SIGNIFICANT_MARKET_DOCUMENTED", "FR_SCALE_DEMONSTRATED_BY_STATISTIC"))
            unknown = [{**r, "registrable_domain": r["registrable_domain"].replace(".fr", ".com")} for r in fr_rows]
            self.assertNotEqual(demand.assess(unknown, studies=[st["S_OK"]], scope_id="M1", thresholds=TH)["level"],
                                "SIGNIFICANT_MARKET_DOCUMENTED")                    # statistique FR + aucun ancrage FR : refusé


class ProblemBeforeIdentityTests(unittest.TestCase):
    def test_22_anonymous_testimonies_measure_the_problem(self):
        rows = [obs(i, author=f"unbound:{i}", dom=f"commu{i % 12}.com", st=["WEB_FORUM", "WEB_REVIEW", "WEB_COMMENT"][i % 3],
                    year=(2023, 2024, 2025)[i % 3], text=f"talons fendillés témoignage anonyme {i} contexte précis numéro {i * 11}")
                for i in range(1, 151)]
        a = demand.assess(rows, thresholds=TH)
        self.assertEqual(a["indicators"]["4_distinct_authors_interval"][0], 0)       # aucun auteur identifiable…
        self.assertEqual(a["distinct_testimonies"], 150)                            # …mais 150 témoignages distincts
        self.assertEqual(a["level"], "BROAD_DISTRIBUTED_SIGNAL")
        copies = [obs(1000 + i, author=f"unbound:c{i}", dom=f"copie{i}.com",
                      text="la même phrase recopiée partout sur les talons qui craquent tout l'hiver") for i in range(40)]
        self.assertEqual(demand.distinct_testimonies(copies), 1)                    # copies dédupliquées

    def test_23_no_pseudo_required_and_uncertain_attribution_never_invented(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "M23")
            r = wc.verify(m, url=NEW, quote="la crème ne tient pas la journée sur mes pieds", author=None, fetcher=fetch)
            self.assertEqual((r["status"], r["binding"]), ("QUOTE_VERIFIED", "UNBOUND"))
            w = wc.verify(m, url=NEW, quote="mes talons se fendillent malgré la crème", author="jules84", fetcher=fetch)
            self.assertEqual(w["binding"], "UNBOUND")          # « jules84 » n'est pas affiché avant cette citation : non attribuée
            caps = [{"url": NEW, "quote": "la crème ne tient pas la journée sur mes pieds", "kind": "FRUSTRATION",
                     "territory_id": "T1", "probleme": "p", "jtbd": "J1", "source_kind": "FORUM"}]
            (m / "RESEARCH_INBOX/proposals/captures_1.json").write_text(json.dumps(caps))
            ar.run(m, fetcher=fetch)
            rows = ledger.load(m / "AGENT_01/problem_ledger.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["author_key"].startswith("unbound:"))

    def test_24_declared_inferred_study_unknown_kept_separate(self):
        self.assertEqual(persona.infer_attributes("Je suis très déçue, mon mari aussi")["gender"],
                         {"value": "F", "confidence": "MEDIUM", "cue": "accord grammatical féminin"})
        self.assertEqual(persona.infer_attributes("je suis déçu de cette crème")["gender"]["value"], "M")
        self.assertEqual(persona.infer_attributes("Marie, 48 ans selon son pseudo"), {})   # rien depuis un prénom
        rows = [obs(i, inferred_attributes={"gender": {"value": "F", "confidence": "MEDIUM", "cue": "x"}}) for i in range(1, 13)]
        rows += [obs(50, declared_attributes={"age": 47, "age_band": "45-54"})]
        seg = persona.segment_profile(rows, TH, studies=[{"id": "S", "scope": "SEGMENT", "verification": "VERIFIED",
                                                          "engine_receipt_id": "STU-1", "applies_to": ["M1"], "population": "femmes 45-60"}], scope_id="M1")
        self.assertEqual(seg["emergent_segments"], [])                              # l'inféré ne crée pas de segment déclaré
        self.assertEqual(seg["layers"]["2_inferred_probable"]["gender"]["F"], {"LOW": 0, "MEDIUM": 12})
        self.assertEqual(seg["inferred_segment_hypotheses"][0]["status"], "INFERRED_SEGMENT_HYPOTHESIS_NOT_INDIVIDUAL_PROOF")
        self.assertEqual(seg["layers"]["3_established_by_studies"][0]["id"], "S")
        self.assertEqual(seg["layers"]["1_declared"]["age_band"], {"45-54": 1})
        self.assertEqual(persona.fit_status(rows, TH, "PROFILE")["status"], "PERSONA_FIT_UNPROVEN")   # inférence ≠ preuve

    def test_25_single_gate_2b_logic_in_the_real_pipeline(self):
        rows = [obs(i, author=f"a{i}", dom=f"s{i % 4}.fr", st=["WEB_FORUM", "WEB_REVIEW"][i % 2]) for i in range(1, 13)]
        conv = demand.gate_2b_convergence(rows, "C1", thresholds=TH)
        via_ledger = ledger.gate_2b(rows, "C1", tier=ledger.FETCH_VERIFIED)
        self.assertEqual((via_ledger["status"], via_ledger["validation"]), (conv["status"], conv["validation"]))
        self.assertTrue(ar._evidence(rows, "C1", "FR")["gate2b"]["validation"].startswith("CONVERGENCE_"))
        src = (ecom.ROOT / "engine/autonomous_research.py").read_text(encoding="utf-8")
        self.assertNotIn("ledger.gate_2b(", src)


AB = "https://www.page-alice-bob-exemple.fr/sujet"
AB_BLOCKS = "https://www.forum-blocs-exemple.fr/sujet"
AB_LD = "https://www.avis-ambigus-exemple.fr/produit"
QUOTE_AB = "Ce produit me donne des démangeaisons tous les soirs"
REGRESSION_PAGES = {
    AB: f"<html><p>Alice</p><p>Bob</p><p>{QUOTE_AB}.</p></html>",
    AB_BLOCKS: (f"<html><div class='comment'><span class='username'>Alice</span><p>{QUOTE_AB}.</p></div>"
                "<div class='comment'><span class='username'>Bob</span><p>Moi je n'ai eu aucun souci avec ce produit.</p></div></html>"),
    AB_LD: ("<html><script type='application/ld+json'>{\"@type\":\"Product\",\"review\":["
            f"{{\"@type\":\"Review\",\"author\":\"Alice\",\"reviewBody\":\"{QUOTE_AB}\"}},"
            f"{{\"@type\":\"Review\",\"author\":\"Bob\",\"reviewBody\":\"{QUOTE_AB}\"}}]}}</script></html>"),
    "https://www.microdonnees-exemple.fr/p": ("<html><div itemscope itemtype='https://schema.org/Review'><span itemprop='author'>Claire</span>"
                                             "<p itemprop='reviewBody'>Trop grasse, elle tache mes chaussettes chaque soir.</p></div></html>"),
    "https://www.faq-exemple.fr/p": ("<html><script type='application/ld+json'>{\"@type\":\"FAQPage\",\"mainEntity\":[{\"@type\":\"Question\","
                                    "\"name\":\"Pourquoi ?\",\"acceptedAnswer\":{\"@type\":\"Answer\",\"text\":\"Les talons secs se fendillent souvent en hiver.\"}}]}"
                                    "</script></html>"),
    "https://www.forum-anonyme-exemple.fr/t": ("<html><div class='message'><p>Mes talons craquent malgré trois crèmes différentes.</p></div>"
                                              "<div class='message'><p>Pareil chez moi depuis des années, rien ne marche.</p></div></html>"),
}
for i in range(12):
    REGRESSION_PAGES[f"https://www.boutique{i % 4}-exemple.fr/produit-{i}"] = [
        f"<html><h1>Crème miracle {i}</h1><p>Nos clientes ont les talons qui craquent ? Notre crème {i} répare tout en une nuit.</p></html>",
        f"<html><script type='application/ld+json'>{{\"@type\":\"Product\",\"description\":\"Nos clientes ont les talons qui craquent ? Notre crème {i} répare tout en une nuit.\"}}</script></html>",
        f"<html><div class='testimonials'><p>Nos clientes ont les talons qui craquent ? Notre crème {i} répare tout en une nuit.</p></div></html>",
    ][i % 3]


def regression_fetch(url):
    if url.endswith("/robots.txt"):
        return 404, url, b""
    return (200, url, REGRESSION_PAGES[url].encode()) if url in REGRESSION_PAGES else (404, url, b"")


class RegressionAttributionAndNatureTests(unittest.TestCase):
    def test_26_proximity_never_proves_authorship_alice_and_bob(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "R26")
            v = lambda url, a: wc.verify(m, url=url, quote=QUOTE_AB, author=a, fetcher=regression_fetch)
            alice, bob = v(AB, "Alice"), v(AB, "Bob")                         # défaut reproduit : les deux étaient PROXIMITY
            self.assertEqual((alice["binding"], bob["binding"]), ("UNBOUND", "UNBOUND"))
            self.assertEqual(alice["author_claim_rejected"], "NO_RELIABLE_AUTHOR_BLOCK")
            self.assertTrue(alice["author_key"].startswith("unbound:") and bob["author_key"].startswith("unbound:"))
            ba, bb = v(AB_BLOCKS, "Alice"), v(AB_BLOCKS, "Bob")               # bloc de commentaire : paternité unique
            self.assertEqual((ba["binding"], bb["binding"]), ("COMMENT_BLOCK", "UNBOUND"))
            self.assertEqual(bb["author_claim_rejected"], "AUTHOR_NOT_IN_CONTRIBUTION_BLOCK")
            la, lb = v(AB_LD, "Alice"), v(AB_LD, "Bob")                       # même avis signé par deux auteurs : ambigu
            self.assertEqual((la["binding"], lb["binding"]), ("UNBOUND", "UNBOUND"))
            self.assertEqual(la["author_claim_rejected"], "AMBIGUOUS_AUTHORSHIP")
            md = wc.verify(m, url="https://www.microdonnees-exemple.fr/p", quote="elle tache mes chaussettes chaque soir",
                           author="Claire", fetcher=regression_fetch)
            self.assertEqual((md["binding"], md["contribution_nature"]), ("STRUCTURED", "USER_CONTRIBUTION_STRUCTURED"))

    def test_27_commercial_pages_never_become_testimonies_nor_pass_gate_2b(self):
        """Défaut reproduit : 12 pages commerciales sur 4 domaines, proposées comme REVIEW/FORUM → PROBLEM_CORROBORATED et GATE 2B PASS."""
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "R27")
            from mission_helpers import seed_gate2a
            seed_gate2a(m)
            caps = [{"url": f"https://www.boutique{i % 4}-exemple.fr/produit-{i}",
                     "quote": f"Nos clientes ont les talons qui craquent ? Notre crème {i} répare tout en une nuit",
                     "author": f"cliente{i}", "source_kind": ["REVIEW", "FORUM"][i % 2], "kind": "FRUSTRATION",
                     "territory_id": "T1", "probleme": "talons", "jtbd": "J1"} for i in range(12)]
            pr = m / "RESEARCH_INBOX/proposals"
            (pr / "captures_1.json").write_text(json.dumps(caps))
            (pr / "jtbd.json").write_text(json.dumps({"jobs": [{"id": f"J{i}", "title": f"Job {i}"} for i in range(1, 6)]}))
            ar.run(m, fetcher=regression_fetch)
            rows = ledger.load_merged(m / "AGENT_01/problem_ledger.jsonl")
            self.assertEqual(len(rows), 12)                                    # citations présentes : conservées comme information
            self.assertEqual({r["contribution_nature"] for r in rows}, {"UNCERTAIN_NATURE", "EDITORIAL_OR_MARKETING"})
            self.assertTrue(all(r["source_type"] in ("WEB_OTHER", "WEB_NEWS") for r in rows))   # source_kind proposé ignoré
            self.assertTrue(all(r["author_key"].startswith("unbound:") for r in rows))
            g = demand.gate_2b_convergence(rows, "J1", thresholds=TH)
            self.assertNotEqual(g["status"], "PASS")
            a = demand.assess(rows, thresholds=TH)
            self.assertEqual((a["level"], a["indicators"]["3_contributions"]), ("HYPOTHESIS_DETECTED", 0))
            self.assertEqual(sum(a["market_information_not_testimonies"].values()), 12)

    def test_28_anonymous_user_contributions_stay_admissible_editorial_faq_does_not(self):
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "R28")
            anon = wc.verify(m, url="https://www.forum-anonyme-exemple.fr/t", quote="Mes talons craquent malgré trois crèmes différentes",
                             author=None, fetcher=regression_fetch)
            self.assertEqual((anon["binding"], anon["contribution_nature"]), ("UNBOUND", "USER_CONTRIBUTION_MARKUP"))
            self.assertTrue(demand.is_user_contribution(anon))
            faq = wc.verify(m, url="https://www.faq-exemple.fr/p", quote="Les talons secs se fendillent souvent en hiver",
                            author=None, fetcher=regression_fetch)
            self.assertEqual(faq["contribution_nature"], "EDITORIAL_OR_MARKETING")   # FAQ du site ≠ témoignage
            self.assertFalse(demand.is_user_contribution(faq))


class IntegrityTests(unittest.TestCase):
    ACTIVE = [".claude/skills/ecom-os/SKILL.md", ".claude/skills/agent01-research/SKILL.md", ".claude/skills/agent02-competitor/SKILL.md",
              ".claude/agents/voc-miner.md", ".claude/agents/competitor-scout.md", ".claude/rules/01_evidence_and_truth.md",
              ".claude/rules/02_autonomy_and_gates.md", "research/RESEARCH_PLAYBOOK.md", "CLAUDE.md"]
    FORBIDDEN = ["--persona OUVERT", "persona ouvert pendant", "récurrence d'achat obligatoire", "récurrence d'ACHAT obligatoire",
                 "NOT_ALLOWLISTED", "liste `engine/web_capture.DOMAINS` détermine", "PERSONA_A_FR par défaut",
                 "--open-persona-explicit", "aucune collecte de prix", "Seuls les hôtes ACTIFS"]

    def test_14_old_rules_cannot_regain_priority(self):
        for f in self.ACTIVE:
            txt = (ecom.ROOT / f).read_text(encoding="utf-8")
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, txt, f"{f} contient une règle remplacée : {bad}")
        self.assertIsNone(wc.check_url("https://www.nimporte-quel-site-public.fr/page"))
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "old.json"
            p.write_text(json.dumps({"schema": "ECOM_OS_RESEARCH_THRESHOLDS_V1", "demand": {}}))
            with self.assertRaises(ValueError):
                demand.load_thresholds(p)                                    # anciens seuils 15/40 refusés
        r = research_depth.analyze([obs(1), obs(2)], {"micro_problems": [micro(refs=["OBS-00001", "OBS-00002"])]}, v2=V3())
        self.assertNotIn("ready_for_owner_review", r)                        # ancien signal à 2 comptes hors circuit
        for m in sorted((ecom.ROOT / "runs").glob("RECHERCHE_0*")):
            self.assertEqual(persona.resolve(json.loads((m / "mission.json").read_text()))["mode"], "DISCOVERED")
        pol = json.loads((ecom.ROOT / "contracts/research_policy.json").read_text())
        self.assertTrue(pol["observable_prices_allowed"])
        self.assertFalse(pol["purchase_recurrence_hard_filter_at_gate4"])

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / ".claude/ecom_owner_pubkey.pem").exists(), "Requires the owner's public key")
    def test_15_marketpulse_protections_and_hooks_unchanged(self):
        st = json.loads((ecom.ROOT / ".claude/settings.json").read_text())
        self.assertEqual(sorted(st["hooks"]), ["PermissionRequest", "PostToolUse", "PostToolUseFailure", "PreToolUse"])
        self.assertTrue(all("tt_live_guard.py" in h["hooks"][0]["command"] for v in st["hooks"].values() for h in v))
        self.assertIn("mcp__claude_ai_Marketpulse__search_ads", st["permissions"]["ask"])
        self.assertIn("Bash(*approve-live*)", st["permissions"]["deny"])
        self.assertEqual(st["permissions"]["disableBypassPermissionsMode"], "disable")
        self.assertTrue((ecom.ROOT / ".claude/ecom_owner_pubkey.pem").exists())

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "tests/fixtures/r08").exists(), "Requires external page fixtures")
    def test_16_end_to_end_real_r08_pages_under_active_method(self):
        """Pages réelles RECHERCHE_08, aucune donnée ajoutée : la méthode active ne promeut rien."""
        with tempfile.TemporaryDirectory() as t:
            m = mission(t, "R08_REPLAYED")
            for f in (FX / "proposals").iterdir():
                shutil.copy(f, m / "RESEARCH_INBOX/proposals" / f.name)
            for _ in range(2):
                res = ar.run(m, fetcher=fixture_fetcher)
            depth = json.loads((m / "AGENT_01/STAGE12_DEPTH_REPORT.json").read_text())
            rep = json.loads((m / "AGENT_01/research_report.json").read_text())
            self.assertEqual(rep["receipts"]["verified"], rep["receipts"]["total"])
            self.assertEqual(res["log"]["gate_2a"], "GATE_2A_INCOMPLETE")         # territoires sans sources découvertes ni secteurs
            self.assertEqual(res["log"]["gate_3"], "GATE_3_BLOCKED_BY_GATE_2A")
            self.assertEqual(depth["owner_checkpoint"]["opportunities"], [])
            self.assertTrue(all(x["verdict"] == "HYPOTHESIS_DETECTED" for x in depth["stage2"]))   # signature absente / < 10 auteurs
            self.assertEqual(depth["marketpulse"], "BLOCKED_NO_OPPORTUNITY_MEETS_EVIDENCE_LEVEL")


if __name__ == "__main__":
    unittest.main()
