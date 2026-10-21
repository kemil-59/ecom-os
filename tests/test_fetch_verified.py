"""Tests FETCH_VERIFIED — pages HTML SYNTHÉTIQUES (fixtures de test, aucune donnée réelle), téléchargeur injecté."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import autonomous_research as ar, autopilot, ledger, web_capture as wc  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from mission_helpers import seed_gate2a  # noqa: E402

FORUM = "https://www.forumconstruire.com/construire/topic-1-synthetique.php"
ZOO = "https://www.zooplus.fr/feedback/shop/synthetique/1"
BLOG = "https://www.consoglobe.com/article-synthetique-cg"
PAGES = {
    FORUM: "<p>alice92 Auteur du sujet Le 02/11/2024 à 10h01 Env. 3 messages Paris</p><p>La buée coule sur mes fenêtres chaque matin dans la chambre.</p>"
           "<p>bob Le 03/11/2024 à 11h00 Env. 1 message</p><p>Pareil chez moi, de la moisissure revient tous les hivers au plafond.</p>",
    ZOO: "<p>19/01/25 | Jade 3.0 This is a stars rating</p><p>La litière se retrouve partout dans l'escalier malgré les tapis.</p>"
         "<p>Avis d’autres pays</p><p>20/09/26 | Karl | Allemagne 3.0 This is a stars</p><p>Die Streu ist überall im Haus verteilt.</p>",
    BLOG: "<p>Michael dit : 14 août 2017 à 11h58</p><p>J'habite à Lyon et la condensation ruisselle sur mes fenêtres tous les jours.</p>",
}


def fake_fetch(url):
    if url.endswith("/robots.txt"):
        return 200, url, b"User-agent: *\nAllow: /\nUser-agent: ClaudeBot\nDisallow: /interdit\n"
    if url in PAGES:
        return 200, url, PAGES[url].encode()
    return 404, url, b""


class FetchVerifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = Path(self.tmp.name) / "MISSION_FV"
        (self.m / "AGENT_01").mkdir(parents=True)
        (self.m / "mission.json").write_text(json.dumps({"mission_id": "MISSION_FV", "target_sale_market": "FR", "mode": "FETCH_VERIFIED"}))

    def tearDown(self):
        self.tmp.cleanup()

    def v(self, **kw):
        return wc.verify(self.m, fetcher=fake_fetch, **kw)

    def test_01_quote_bound_to_author_and_geo_qualified(self):
        r = self.v(url=FORUM, quote="La buée coule sur mes fenêtres chaque matin", author="alice92", location_claim="Paris")
        self.assertEqual((r["status"], r["geo_basis"], r["market_geo"]), ("QUOTE_VERIFIED", "RESIDENCE_DECLARED", "FR"))
        self.assertNotIn("alice92", json.dumps(r))  # pseudo jamais stocké
        r2 = self.v(url=FORUM, quote="de la moisissure revient tous les hivers", author="bob")
        self.assertEqual(r2["geo_basis"], "LANGUAGE_ONLY")

    def test_02_rejections(self):
        # Pseudo revendiqué faux : la citation n'est jamais attribuée à « bob » ; elle reste une contribution non attribuée.
        mis = self.v(url=FORUM, quote="La buée coule sur mes fenêtres chaque matin", author="bob")
        self.assertEqual((mis["binding"], mis["author_claim_rejected"]), ("UNBOUND", "AUTHOR_MISMATCH"))
        self.assertTrue(mis["author_key"].startswith("unbound:"))
        self.assertEqual(self.v(url=FORUM, quote="phrase totalement inventée par le modèle", author="alice92")["status"], "QUOTE_NOT_FOUND")
        # Méthode active : plus de liste blanche ; une page inaccessible ne devient jamais une preuve.
        self.assertEqual(self.v(url="https://www.amazon.fr/x", quote="quinze caracteres au moins", author="a")["status"], "BLOCKED_HTTP_404")
        self.assertEqual(self.v(url="https://127.0.0.1/x", quote="quinze caracteres au moins", author="a")["status"], "UNSAFE_URL")
        self.assertEqual(self.v(url="https://www.zooplus.fr/interdit/1", quote="quinze caracteres au moins", author="a")["status"], "BLOCKED_ROBOTS")
        self.assertEqual(self.v(url=FORUM, quote="La buée coule", author="alice92", location_claim="Bruxelles")["status"], "QUOTE_TOO_SHORT")

    def test_03_foreign_section_and_market_site(self):
        self.assertEqual(self.v(url=ZOO, quote="La litière se retrouve partout dans l'escalier", author="Jade")["geo_basis"], "FR_MARKET_SITE")
        self.assertEqual(self.v(url=ZOO, quote="Die Streu ist überall im Haus verteilt.", author="Karl")["geo_basis"], "FOREIGN_SECTION")

    def test_04_ledger_requires_engine_receipt_and_explicit_fields(self):
        p = self.m / "AGENT_01" / "problem_ledger.jsonl"
        row = {"territory_id": "T", "probleme": "p", "phrase_exacte": "texte sans reçu moteur ici", "source_type": "FORUM_FR",
               "source_url": FORUM, "kind": "FRUSTRATION", "spontane": True, "market_geo": "FR",
               "geo_basis": "RESIDENCE_DECLARED", "author_key": "acct:x", "receipt_id": "forged"}
        with self.assertRaisesRegex(ValueError, "RECEIPT"):
            ledger.append(p, [row], mission_id="MISSION_FV", provenance_mode="FETCH_VERIFIED")
        with self.assertRaisesRegex(ValueError, "MISSING_EXPLICIT"):
            ledger.append(p, [{k: v for k, v in row.items() if k != "geo_basis"}], mission_id="M", provenance_mode="FETCH_VERIFIED")
        with self.assertRaisesRegex(ValueError, "LIVE"):
            ledger.append(p, [row], mission_id="M", provenance_mode="LIVE")

    def _cycle(self, captures, jobs, assessments=None):
        pr = self.m / "RESEARCH_INBOX" / "proposals"
        pr.mkdir(parents=True, exist_ok=True)
        seed_gate2a(self.m)   # méthode active : pas de GATE 3 sans GATE 2A
        (pr / "captures_1.json").write_text(json.dumps(captures))
        (pr / "jtbd.json").write_text(json.dumps({"jobs": jobs}))
        if assessments:
            (pr / "assessments.json").write_text(json.dumps(assessments))
        return ar.run(self.m, fetcher=fake_fetch)

    def test_05_end_to_end_exploratory_never_validated(self):
        caps = [
            {"url": FORUM, "quote": "La buée coule sur mes fenêtres chaque matin", "author": "alice92", "location_claim": "Paris",
             "kind": "FRUSTRATION", "territory_id": "T1", "probleme": "buée", "jtbd": "J1"},
            {"url": BLOG, "quote": "la condensation ruisselle sur mes fenêtres tous les jours", "author": "Michael",
             "location_claim": "Lyon", "kind": "FRUSTRATION", "territory_id": "T1", "probleme": "buée", "jtbd": "J1"},
            {"url": FORUM, "quote": "de la moisissure revient tous les hivers", "author": "bob", "kind": "FRUSTRATION",
             "territory_id": "T1", "probleme": "moisissure", "jtbd": "J2"},
            {"url": ZOO, "quote": "La litière se retrouve partout dans l'escalier", "author": "Jade", "kind": "FRUSTRATION",
             "territory_id": "T2", "probleme": "litière", "jtbd": "J3"},
            {"url": FORUM, "quote": "citation inexistante sur la page", "author": "bob", "kind": "FRUSTRATION", "jtbd": "J4"},
        ]
        jobs = [{"id": f"J{i}", "title": f"Job {i}"} for i in range(1, 6)]
        res = self._cycle(caps, jobs)
        rep = json.loads((self.m / "AGENT_01" / "research_report.json").read_text())
        self.assertFalse(rep["certified_live"])
        self.assertEqual(rep["receipts"]["verified"], 4)
        self.assertEqual(rep["gate_3"], "GATE_3_INCOMPLETE")  # J4/J5 sans preuve : pas de candidats fabriqués
        self.assertEqual(res["status"], "NEEDS_LLM_RESEARCH")
        self.assertFalse((self.m / "AGENT_01" / "candidate_registry.json").exists())

    def test_06_gate2b_two_persons_never_pass_and_stops_at_gate4(self):
        caps, jobs = [], []
        for j in range(1, 6):
            caps.append({"url": FORUM, "quote": "La buée coule sur mes fenêtres chaque matin", "author": "alice92",
                         "location_claim": "Paris", "kind": "FRUSTRATION", "territory_id": "T1", "probleme": "buée", "jtbd": f"J{j}"})
            jobs.append({"id": f"J{j}", "title": f"Job {j}"})
        caps += [{"url": BLOG, "quote": "la condensation ruisselle sur mes fenêtres tous les jours", "author": "Michael",
                  "location_claim": "Lyon", "kind": "FRUSTRATION", "territory_id": "T1", "probleme": "buée", "jtbd": "J1"},
                 {"url": ZOO, "quote": "La litière se retrouve partout dans l'escalier", "author": "Jade",
                  "kind": "FRUSTRATION", "territory_id": "T1", "probleme": "x", "jtbd": "J1"}]
        res = self._cycle(caps, jobs, {"J1": {"score_axes": {ax: {"score": 5, "evidence_refs": ["OBS-0001"], "justification": "proposition non fiable"}
                                                             for ax in ("persona_fit", "frequency", "intensity", "existing_spend", "dissatisfaction",
                                                                        "market_depth", "creative_demonstrability", "range_ltv")}}})
        rep = json.loads((self.m / "AGENT_01" / "research_report.json").read_text())
        j1 = next(s for s in rep["candidates"] if s["candidate_id"] == "J1")
        self.assertEqual(j1["gate2b"], "CONVERGENCE_HYPOTHESIS_DETECTED")   # 2 personnes : jamais un GATE 2B (méthode active)
        self.assertEqual(j1["score_evaluability"], "NON_EVALUABLE")  # axes anciens/sans preuve adaptée refusés
        self.assertEqual(j1["stage"], 5)                             # pas d'ADVANCE sans score ni récurrence
        j2 = next(s for s in rep["candidates"] if s["candidate_id"] == "J2")
        self.assertEqual(j2["gate2b"], "CONVERGENCE_HYPOTHESIS_DETECTED")
        self.assertEqual(res["log"]["autopilot"], "FETCH_CYCLE_DONE")
        # Un dossier modifié à la main est refusé par la CLI
        d = json.loads((self.m / "AGENT_01" / "dossiers.json").read_text())
        d["J1"]["economic_screen"] = {"status": "OK"}
        f = Path(self.tmp.name) / "forged.json"
        f.write_text(json.dumps(d))
        reg = autopilot.CandidateRegistry.load(self.m / "AGENT_01" / "candidate_registry.json")
        reg.candidates["J1"]["stage"] = 6
        out = autopilot.step(reg, json.loads((self.m / "AGENT_01" / "dossiers.json").read_text()),
                             ledger={r["observation_id"]: r for r in ledger.load_merged(self.m / "AGENT_01" / "problem_ledger.jsonl")},
                             provenance_mode="FETCH_VERIFIED")
        self.assertTrue(any(x.get("verdict") == "STOP_AT_GATE_4" for x in out["decisions"]))
        with self.assertRaisesRegex(ValueError, "LIVE_AUTOPILOT"):
            autopilot.step(reg, {}, provenance_mode="LIVE")

    def test_08_research_gate4_requires_significant_market_recurrence_is_not_a_veto(self):
        ok = {"candidate_id": "C", "research_mode": True, "gate2b": {"status": "PASS"},
              "score": {"score": 30, "status": "SCORED_WITH_EVIDENCE"},
              "demand": {"level": "SIGNIFICANT_MARKET_DOCUMENTED"}}
        self.assertEqual(autopilot.decide_gate4({**ok, "recurrence": {"status": "DOCUMENTED"}})["verdict"], "ADVANCE")
        # Un écran économique (même RED) est ignoré en recherche : jamais bloquant.
        self.assertEqual(autopilot.decide_gate4({**ok, "recurrence": {"status": "DOCUMENTED"},
                                                 "economic_screen": {"status": "RED_DEFICIT_AT_ZERO_COGS"}})["verdict"], "ADVANCE")
        # Méthode active (consigne propriétaire) : la récurrence est évaluée dans le score, jamais un veto.
        self.assertEqual(autopilot.decide_gate4({**ok, "recurrence": {"status": "ABSENT"}})["verdict"], "ADVANCE")
        # Sans marché significatif documenté, un bon score ne suffit jamais.
        self.assertEqual(autopilot.decide_gate4({**ok, "demand": {"level": "BROAD_DISTRIBUTED_SIGNAL"}})["verdict"], "RESEARCH")
        self.assertIn("PRICE_OBSERVED", ledger.FETCH_KINDS)   # prix publics observés autorisés (lecture seule)
        PAGES["https://www.zooplus.fr/feedback/shop/offre/1"] = ("<p>01/02/26 | Lea 5.0 This is a stars</p><p>[Cet avis a été "
            "recueilli en réponse à une offre.] Produit parfait contre les odeurs, je recommande.</p>")
        r = self.v(url="https://www.zooplus.fr/feedback/shop/offre/1", quote="Produit parfait contre les odeurs, je recommande.", author="Lea")
        self.assertTrue(r["solicited"])
        n = self.v(url=ZOO, quote="partout dans l'escalier", author=None, count_capture=True)
        self.assertEqual(n["status"], "COUNT_NOT_PARSED")

    def test_09_agent01_to_agent02_exploratory_chain(self):
        Z2 = "https://www.zooplus.fr/feedback/shop/synthetique/fontaine"
        PAGES[Z2] = ("<p>01/02/26 | Ana 1.0 This is a stars</p><p>Les filtres FontX sont introuvables et incompatibles avec ma fontaine.</p>"
                     "<p>02/02/26 | Ben 2.0 This is a stars</p><p>Encore des filtres FontX qui ne rentrent pas dans le bac. Je dois en racheter chaque mois.</p>"
                     "<p>03/02/26 | Cid 5.0 This is a stars</p><p>La pompe FontX marche très bien depuis un an.</p>")
        caps = [{"url": Z2, "quote": "Les filtres FontX sont introuvables et incompatibles avec ma fontaine.", "author": "Ana",
                 "kind": "SOLUTION_DEFECT", "jtbd": f"J{i}", "territory_id": "T1", "probleme": "filtres",
                 "incumbent": "FontX", "dimension": "filtres"} for i in range(1, 6)]
        caps += [{"url": Z2, "quote": "Je dois en racheter chaque mois.", "author": "Ben",
                  "kind": "REPEAT_PURCHASE", "jtbd": "J1", "territory_id": "T1", "probleme": "filtres", "incumbent": "FontX"},
                 {"url": Z2, "quote": "Encore des filtres FontX qui ne rentrent pas dans le bac.", "author": "Ben",
                  "kind": "SOLUTION_DEFECT", "jtbd": "J1", "territory_id": "T1", "probleme": "filtres", "incumbent": "FontX",
                  "dimension": "filtres"},
                 {"url": Z2, "quote": "La pompe FontX marche très bien depuis un an.", "author": "Cid", "kind": "SATISFACTION",
                  "jtbd": "J1", "territory_id": "T1", "probleme": "pompe", "incumbent": "FontX", "satisfied_dimension": "pompe"}]
        jobs = [{"id": "J1", "title": "Filtres", "recurrence_hypothesis": "CONSUMABLE"},
                {"id": "J2", "title": "Sans réachat", "recurrence_hypothesis": "NONE"}] + \
               [{"id": f"J{i}", "title": f"Job {i}", "recurrence_hypothesis": "REFILL"} for i in range(3, 6)]
        pr = self.m / "RESEARCH_INBOX" / "proposals"
        pr.mkdir(parents=True, exist_ok=True)
        (pr / "solutions.json").write_text(json.dumps({"J1": [
            {"id": "S1", "description": "filtres universels compatibles", "addresses_dimensions": ["filtres"], "recurrence_component": "filtres de rechange"},
            {"id": "S2", "description": "fontaine sans consommable", "addresses_dimensions": ["filtres"], "recurrence_component": ""},
            {"id": "S3", "description": "pompe silencieuse", "addresses_dimensions": ["bruit"], "recurrence_component": "pompes"}]}))
        self._cycle(caps, jobs)
        rep = json.loads((self.m / "AGENT_01" / "research_report.json").read_text())
        a02 = json.loads((self.m / "AGENT_02" / "competitive_dossier.json").read_text())
        self.assertEqual((a02["status"], a02["certified_live"], a02["gate6_audit"]), ("EXPLORATORY_NOT_VALIDATED", False, False))
        j1 = a02["candidates"]["J1"]
        self.assertEqual(j1["competitors"][0]["incumbent"], "FontX")
        self.assertEqual({g["dimension"]: g["status"] for g in j1["gaps"]}, {"filtres": "EXPLORATORY_GAP"})
        self.assertEqual({s["id"]: s["status"] for s in j1["solutions"]},
                         {"S1": "EXPLORATORY_SUPPORTED", "S2": "EXPLORATORY_SUPPORTED", "S3": "UNSUPPORTED_NO_VERIFIED_GAP"})
        self.assertFalse(j1["solutions"][1]["recurrence_component_present"])   # durable : évalué, pas exclu
        self.assertTrue(all(r.startswith("OBS-") for r in j1["solutions"][0]["gap_refs"]["filtres"]))
        self.assertIn("GATE_2B_NOT_VALIDATED", j1["uncertainties"])          # une seule famille : pas de PASS
        self.assertIn("J2", a02["candidates"])                                 # récurrence NONE : évaluée, pas exclue
        e1 = next(e for e in rep["exploratory_shortlist"] if e["candidate_id"] == "J1")
        self.assertEqual((e1["competitors"], e1["validation"]), (["FontX"], "EXPLORATORY_NOT_VALIDATED"))
        self.assertEqual(rep["official_shortlist_gate4"], [])
        # Le dossier exploratoire n'est jamais chargé comme audit Agent 02 du GATE 6.
        self.assertEqual(autopilot.load_agent02_audits(self.m / "AGENT_02" / "competitive_dossier.json"), {})
        # Prix publics observés autorisés (consigne du 2026-10-21) ; jamais coûts, marges, fournisseurs ni CAC.
        self.assertNotRegex(json.dumps(a02), "cogs|margin|supplier|\\bcac\\b")

    def test_07_cli_rejects_hand_made_dossiers(self):
        mdir = ROOT / "runs" / "TEST_FV_CLI_TMP"
        try:
            (mdir / "AGENT_01").mkdir(parents=True, exist_ok=True)
            (mdir / "mission.json").write_text(json.dumps({"mission_id": "TEST_FV_CLI_TMP", "target_sale_market": "FR", "mode": "FETCH_VERIFIED"}))
            f = mdir / "forged.json"
            f.write_text(json.dumps({"J1": {"candidate_id": "J1", "stage": 5, "economic_screen": {"status": "OK"}}}))
            cp = subprocess.run([sys.executable, str(ROOT / "ecom.py"), "auto", "step", "--mission", "TEST_FV_CLI_TMP", "--dossiers", str(f)],
                                capture_output=True, text=True)
            self.assertIn("FETCH_VERIFIED_DOSSIERS_MUST_COME_FROM_ENGINE_BUILDER", cp.stderr + cp.stdout)
            cp = subprocess.run([sys.executable, str(ROOT / "ecom.py"), "a01", "ledger-add", "--mission", "TEST_FV_CLI_TMP", "--file", str(f)],
                                capture_output=True, text=True)
            self.assertIn("FETCH_VERIFIED_INGESTION_ONLY_VIA_RESEARCH_RUN_RECEIPTS", cp.stderr + cp.stdout)
        finally:
            import shutil
            shutil.rmtree(mdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
