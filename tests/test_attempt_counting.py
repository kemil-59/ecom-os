"""Comptage des tentatives (FETCH_VERIFIED) — pages HTML SYNTHÉTIQUES, téléchargeur injecté, zéro réseau.

Règles testées :
- une nouvelle preuve vérifiée rattachée au candidat = progrès, jamais une tentative ;
- seule une recherche réellement effectuée (capture visant le candidat) sans nouvelle preuve compte ;
- des cycles sans activité arrêtent proprement (IDLE_AWAITING_NEW_RESEARCH) sans tentative ni mise en réserve ;
- un candidat en réserve est rouvert par une nouvelle preuve vérifiée, jamais par un changement de fichier.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from engine import autonomous_research as ar  # noqa: E402
from test_fetch_verified import FORUM, PAGES, fake_fetch  # noqa: E402
from mission_helpers import seed_gate2a  # noqa: E402

Z3 = "https://www.zooplus.fr/feedback/shop/synthetique/attempts"
PAGES[Z3] = "".join(f"<p>0{i}/03/26 | U{i} 1.0 This is a stars</p><p>Le produit numéro {i} ne règle toujours pas le problème chez moi.</p>"
                    for i in range(1, 6))
QA = "La buée coule sur mes fenêtres chaque matin"
JOBS = {"jobs": [{"id": f"J{i}", "title": f"Job {i}", "recurrence_hypothesis": "CONSUMABLE"} for i in range(1, 6)]}


def cap(quote, author, jtbd, url=Z3, **kw):
    return {"url": url, "quote": quote, "author": author, "kind": "FRUSTRATION", "jtbd": jtbd,
            "territory_id": "T1", "probleme": "p", **kw}


class AttemptCountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = Path(self.tmp.name) / "M_ATTEMPTS"
        (self.m / "AGENT_01").mkdir(parents=True)
        (self.m / "mission.json").write_text(json.dumps({"mission_id": "M_ATTEMPTS", "target_sale_market": "FR",
                                                         "mode": "FETCH_VERIFIED"}))
        self.pr = self.m / "RESEARCH_INBOX" / "proposals"
        self.pr.mkdir(parents=True)
        seed_gate2a(self.m)   # méthode active : pas de GATE 3 sans GATE 2A (territoires issus de la DISCOVERY)
        self.n = 0
        (self.pr / "jtbd.json").write_text(json.dumps(JOBS))
        self.cycle([cap(QA, "alice92", f"J{i}", url=FORUM, location_claim="Paris") for i in range(1, 6)])

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, captures=None, assessments=None):
        if captures is not None:
            self.n += 1
            (self.pr / f"captures_{self.n}.json").write_text(json.dumps(captures))
        if assessments is not None:
            (self.pr / "assessments.json").write_text(json.dumps(assessments))
        return ar.run(self.m, fetcher=fake_fetch)

    def cand(self, cid="J1"):
        return json.loads((self.m / "AGENT_01" / "candidate_registry.json").read_text())["candidates"][cid]

    def test_new_evidence_each_cycle_never_parks(self):
        for i in range(1, 5):
            self.cycle([cap(f"Le produit numéro {i} ne règle toujours pas le problème chez moi.", f"U{i}", "J1")])
            self.assertEqual(self.cand()["status"], "ACTIVE")
        self.assertEqual(self.cand()["attempts"], {})

    def test_documented_research_without_new_evidence_parks_after_max_attempts(self):
        self.cycle([cap("citation absente de la page synthétique", "U1", "J1")])
        self.assertEqual(self.cand()["status"], "ACTIVE")
        self.cycle([cap(QA, "alice92", "J1", url=FORUM, location_claim="Paris")])   # doublon : aucune preuve nouvelle
        self.assertEqual(self.cand()["status"], "PARKED_INSUFFICIENT_EVIDENCE")
        self.assertEqual(self.cand("J2")["status"], "ACTIVE")                      # non recherché : non pénalisé
        self.assertEqual(self.cand("J2")["attempts"], {})

    def test_idle_cycles_stop_cleanly_without_attempts_or_parking(self):
        statuses = [self.cycle()["status"] for _ in range(5)]
        self.assertEqual(statuses[-1], "IDLE_AWAITING_NEW_RESEARCH")
        for i in range(1, 6):
            c = self.cand(f"J{i}")
            self.assertEqual((c["status"], c["attempts"]), ("ACTIVE", {}))

    def test_research_done_or_file_change_is_not_evidence_nor_attempt(self):
        self.cycle(assessments={"J1": {"research_done": ["J1:gate2b", "J1:score_evidence"]}})
        self.cycle(assessments={"J1": {"research_done": ["J1:gate2b"], "score_axes": {}}})
        self.assertEqual((self.cand()["status"], self.cand()["attempts"]), ("ACTIVE", {}))

    def test_parked_candidate_reopens_only_on_new_verified_evidence(self):
        self.cycle([cap("citation absente de la page synthétique", "U1", "J1")])
        self.cycle([cap("autre citation absente de la page", "U2", "J1")])
        self.assertEqual(self.cand()["status"], "PARKED_INSUFFICIENT_EVIDENCE")
        self.cycle(assessments={"J1": {"research_done": ["J1:gate2b"]}})            # fichier modifié : pas une preuve
        self.assertEqual(self.cand()["status"], "PARKED_INSUFFICIENT_EVIDENCE")
        self.cycle([cap("encore une citation absente", "U3", "J1")])               # échec : pas une preuve
        self.assertEqual(self.cand()["status"], "PARKED_INSUFFICIENT_EVIDENCE")
        self.cycle([cap("Le produit numéro 4 ne règle toujours pas le problème chez moi.", "U4", "J1")])
        self.assertEqual(self.cand()["status"], "ACTIVE")
        self.assertTrue(any(e.get("verdict") == "REOPEN_NEW_VERIFIED_EVIDENCE" and e.get("candidate_id") == "J1"
                            for e in json.loads((self.m / "AGENT_01" / "candidate_registry.json").read_text())["events"]))


if __name__ == "__main__":
    unittest.main()
