"""Tests hors réseau des signaux Stage 1, preuves micro, contre-signaux et hold MarketPulse."""
import unittest
from engine import research_depth


def obs(n, territory="T01", kind="FRUSTRATION", family="FORUM_FR", author=None, *,
        geo="RESIDENCE_DECLARED", spontaneous=True, market="FR", mode="FETCH_VERIFIED"):
    return {"observation_id": f"OBS-{n:04d}", "territory_id": territory, "kind": kind,
            "source_type": family, "author_key": author or f"acct-{n}", "market_geo": market,
            "geo_basis": geo, "spontane": spontaneous, "provenance_mode": mode}


def micro(pain=None, counter=None, territory="T01", **kw):
    return {"id": "MICRO-01", "territory_id": territory, "who": "parents en studio",
            "moment": "réveil", "trigger": "condensation", "specific_friction": "joints mouillés",
            "attempted_solution": "essuyage", "unresolved_reason": "revient chaque matin",
            "pain_refs": pain or [], "counter_refs": counter or [], **kw}


class ResearchDepthStage12Tests(unittest.TestCase):
    def setUp(self):
        self.rows = [obs(1, author="a"), obs(2, family="BLOG_COMMENT_FR", author="b"),
                     obs(3, family="FORUM_FR", author="c"),
                     obs(4, kind="SATISFACTION", author="d")]

    def test_cross_source_broad_and_specific_micro(self):
        r = research_depth.analyze(self.rows, {"micro_problems": [micro(["OBS-0001", "OBS-0002"], ["OBS-0004"])]})
        self.assertEqual(r["stage1"]["T01"]["distinct_pain_accounts"], 3)
        self.assertEqual(r["stage1"]["T01"]["status"], "CROSS_SOURCE_SIGNAL_NOT_POPULATION_VALIDATION")
        self.assertEqual(r["stage2"][0]["status"], "CROSS_SOURCE_MICRO_SIGNAL_NOT_GATE2B")
        self.assertEqual(r["stage2"][0]["counterevidence_refs"], ["OBS-0004"])
        self.assertFalse(r["population_prevalence_measured"])
        self.assertEqual(r["ready_for_owner_review"], ["MICRO-01"])
        self.assertIn("BLOCKED", r["marketpulse"])
        self.assertFalse(research_depth.work_orders(r))

    def test_identical_author_and_same_family_not_three_sources(self):
        rows = [obs(1, author="a"), obs(2, author="a"), obs(3, family="REDDIT_FR", author="b")]
        r = research_depth.analyze(rows, {"micro_problems": [micro(["OBS-0001", "OBS-0002"])]})
        self.assertEqual(r["stage1"]["T01"]["distinct_pain_accounts"], 2)
        self.assertEqual(r["stage1"]["T01"]["pain_families"], ["FORUM_QA"])
        self.assertEqual(r["stage2"][0]["status"], "BLOCKED_STAGE1_BREADTH")
        self.assertEqual(research_depth.work_orders(r)[0]["task"], "STAGE1_CROSS_SOURCE_DISCOVERY")

    def test_solicited_language_only_search_or_index_not_proof(self):
        rows = [obs(1, spontaneous=False), obs(2, geo="LANGUAGE_ONLY"),
                obs(3, kind="INDEX_COUNT"), obs(4, family="GOOGLE_FR_PAA")]
        r = research_depth.analyze(rows, {"micro_problems": [micro(["OBS-0001", "OBS-0002", "OBS-0003", "OBS-0004"])]})
        self.assertEqual(r["stage1"]["T01"]["distinct_pain_accounts"], 0)
        self.assertEqual(r["stage2"][0]["qualified_pain_refs"], [])
        self.assertEqual(r["stage2"][0]["status"], "BLOCKED_STAGE1_BREADTH")

    def test_wrong_territory_or_imaginary_obs_fails(self):
        r = research_depth.analyze(self.rows, {"micro_problems": [micro(["OBS-0002", "OBS-9999"], territory="T99")]})
        self.assertEqual(r["stage2"][0]["status"], "INVALID_EVIDENCE_REFS")
        self.assertIn("OBS-9999", r["stage2"][0]["invalid_refs"])
        self.assertIn("OBS-0002", r["stage2"][0]["wrong_territory_refs"])

    def test_no_hypothesis_promoted_without_micro_context(self):
        m = micro(["OBS-0001", "OBS-0002"])
        m["trigger"] = ""
        r = research_depth.analyze(self.rows, {"micro_problems": [m]})
        self.assertEqual(r["stage2"][0]["status"], "INCOMPLETE_MICRO_CONTEXT")
        self.assertEqual(r["ready_for_owner_review"], [])

    def test_no_mock_or_foreign_sources_promoted(self):
        rows = [obs(1, mode="REPLAY"), obs(2, market="US"), obs(3)]
        r = research_depth.analyze(rows, None)
        self.assertEqual(r["stage1"]["T01"]["distinct_pain_accounts"], 1)
        self.assertEqual(r["stage2"], [])

    def test_no_duplicates_in_micro_proposals(self):
        with self.assertRaisesRegex(ValueError, "MICRO_PROBLEM_ID_MISSING_OR_DUPLICATE"):
            research_depth.analyze(self.rows, {"micro_problems": [micro(), micro()]})


if __name__ == "__main__":
    unittest.main()

class Stage12RunIntegrationTests(unittest.TestCase):
    def test_run_writes_depth_report_without_fake_broad_validation(self):
        import json
        import tempfile
        from pathlib import Path
        from engine import autonomous_research
        try:
            from test_fetch_verified import FORUM, BLOG, fake_fetch
        except ImportError:
            self.skipTest("Fixture d'intégration test_fetch_verified non disponible")
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp) / "DEPTH_TEST"
            (m / "AGENT_01").mkdir(parents=True)
            (m / "mission.json").write_text(json.dumps({"mission_id": m.name, "mode": "FETCH_VERIFIED",
                                                      "target_sale_market": "FR"}))
            proposals = m / "RESEARCH_INBOX" / "proposals"
            proposals.mkdir(parents=True)
            proposals.joinpath("captures_01.json").write_text(json.dumps([
                {"url": FORUM, "quote": "La buée coule sur mes fenêtres chaque matin", "author": "alice92",
                 "location_claim": "Paris", "kind": "FRUSTRATION", "territory_id": "T01", "probleme": "buée", "jtbd": "J01"},
                {"url": BLOG, "quote": "la condensation ruisselle sur mes fenêtres tous les jours", "author": "Michael",
                 "location_claim": "Lyon", "kind": "FRUSTRATION", "territory_id": "T01", "probleme": "buée", "jtbd": "J01"}]))
            proposals.joinpath("micro_problems.json").write_text(json.dumps({"micro_problems": [micro(
                ["OBS-0001", "OBS-0002"])]}))
            autonomous_research.run(m, fetcher=fake_fetch)
            depth = json.loads((m / "AGENT_01" / "STAGE12_DEPTH_REPORT.json").read_text())
            rep = json.loads((m / "AGENT_01" / "research_report.json").read_text())
            self.assertEqual(depth["stage1"]["T01"]["distinct_pain_accounts"], 2)
            self.assertEqual(depth["stage2"][0]["status"], "BLOCKED_STAGE1_BREADTH")
            self.assertEqual(rep["stage12_depth"], depth)
            self.assertFalse(depth["commercially_approved"])
