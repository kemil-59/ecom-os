"""Suite de tests automatiques ECOM_OS_CLEAN_B1.

Teste les exigences fonctionnelles et les 10 garde-fous de sécurité de l'audit V4 :
1. Parcours REPLAY complet Agent 01 -> Agent 02 -> PROPOSE_FOR_COMMERCIAL_SELECTION.
2. Interdiction d'AUDIT_COMPLETE Agent 02 avec 0 preuve ou 0 gap.
3. Rejet d'un `agent02_status` saisi manuellement par LLM_Provider sans rapport signé.
4. Rejet d'un audit Agent 02 devenu obsolète après modification matérielle de la solution.
5. Isolation stricte `scope_key` : l'autorisation pour le fournisseur A ne s'applique pas au fournisseur B.
6. Isolation des refus : le refus pour le fournisseur A ne bloque pas le fournisseur B.
7. Blocage de `resolve_authorization` avec un `scope_key` inexistant.
8. Invalidation automatique d'un `PASS` opérationnel après modification matérielle du SKU/solution.
9. Blocage de `operational_result` si les préconditions (Gate 7 + autorisations) ne sont pas remplies,
   et absence de statuts contradictoires après un échec opérationnel (`passed=False`).
10. Paramétrage ouvert du marché cible (ex. `AE` au lieu de `FR`) et séparation `LIVE` vs `REPLAY`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import ecom
from engine import autopilot, competitor_intel, corpus_extractor, ledger, scoring


class EcomOsCleanB1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ecom_b1_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_replay_end_to_end_pipeline(self):
        res = ecom.build_replay_fixture(self.tmp / "MISSION_REPLAY_01")
        self.assertEqual(res["gate2b"], "PASS")
        self.assertEqual(res["market_score_40"], 33.5)
        self.assertEqual(res["agent02_status"], "READY_FOR_AGENT01_REVIEW_NOT_APPROVED")
        self.assertEqual(res["autopilot_action"], "PROPOSE_FOR_COMMERCIAL_SELECTION")
        self.assertEqual(res["selected_candidate"], "JTBD-DRAIN-01")
        self.assertEqual(res["selected_solution"], "SOL-DIATOMITE-SPOUT")
        self.assertEqual(len(res["owner_authorizations_needed"]), 2)

    def test_02_agent02_never_completes_without_evidence_or_gaps(self):
        handoff = {
            "mission_id": "M-EMPTY",
            "target_sale_market": "FR",
            "jtbd": [{"jtbd_id": "J1", "problem_statement": "Problème test"}],
            "home_market_sources": [{"source_ref": "S1", "market_geo": "FR"}],
        }
        r_no_ev = competitor_intel.run_audit(handoff, [], [], [], {}, allow_synthetic=True)
        self.assertEqual(r_no_ev["status"], "REJECTED_EVIDENCE")

        ev = [{
            "mission_id": "M-EMPTY",
            "source_ref": "EV-1",
            "market_geo": "FR",
            "observed_at": "2026-10-19T10:00:00+00:00",
            "source_class": "DIRECT_OBSERVATION",
            "entity_scope": "SHOP",
            "observation": "Concurrent observé",
            "source_url": "https://example.fr/shop",
        }]
        r_no_gaps = competitor_intel.run_audit(handoff, ev, [], [], {}, allow_synthetic=True)
        self.assertEqual(r_no_gaps["status"], "NEEDS_GAP_RESEARCH")

    def test_03_unauthenticated_manual_agent02_status_is_ignored(self):
        mdir = self.tmp / "M_FAKE_A02"
        ecom.build_replay_fixture(mdir)
        dossiers = json.loads((mdir / "AGENT_01" / "dossiers.json").read_text(encoding="utf-8"))
        # LLM_Provider tente d'injecter manuellement agent02_status sans rapport Agent 02
        for s in dossiers["JTBD-DRAIN-01"]["solutions"]:
            s["agent02_status"] = "AUDITED_READY"
        dossiers["JTBD-DRAIN-01"]["stage"] = 7
        dec = autopilot.decide_gate6(
            {
                **dossiers["JTBD-DRAIN-01"],
                "_ledger": ledger.load_by_id(mdir / "AGENT_01" / "problem_ledger.jsonl"),
                "_agent02_audits": {},  # Aucun audit signé fourni
            },
            rounds_exhausted=True,
        )
        self.assertEqual(dec["verdict"], "PARK_PROMISING")
        self.assertIn("AGENT02_AUDIT_MISSING", dec["reasons"])

    def test_04_stale_agent02_audit_rejected_when_solution_changes(self):
        mdir = self.tmp / "M_STALE_A02"
        ecom.build_replay_fixture(mdir)
        dossiers = json.loads((mdir / "AGENT_01" / "dossiers.json").read_text(encoding="utf-8"))
        audits = autopilot.load_agent02_audits(mdir / "AGENT_02" / "competitor_audit_report.json")
        # Modification matérielle du mécanisme de la solution après l'audit Agent 02
        dossiers["JTBD-DRAIN-01"]["solutions"][0]["mechanism"] = "Tout autre mécanisme plastique non audité"
        dossiers["JTBD-DRAIN-01"]["stage"] = 7
        dec = autopilot.decide_gate6(
            {
                **dossiers["JTBD-DRAIN-01"],
                "_ledger": ledger.load_by_id(mdir / "AGENT_01" / "problem_ledger.jsonl"),
                "_agent02_audits": audits,
            },
            rounds_exhausted=False,
        )
        self.assertEqual(dec["verdict"], "RESEARCH")
        self.assertIn("STALE_AGENT02_AUDIT_SOLUTION_CHANGED", dec["reasons"])

    def test_05_authorization_scope_isolation_and_unknown_scope_rejection(self):
        reg = autopilot.CandidateRegistry(mission_id="M-SCOPE")
        reg.add("C1", "Candidat 1", stage=5)
        item_a = {"id": "C1:quote", "solution_id": "S1", "provider": "SUPPLIER_A", "cost_class": "PAID_MARKETPULSE", "max_amount": 20, "decisive": True}
        item_b = {"id": "C1:quote", "solution_id": "S1", "provider": "SUPPLIER_B", "cost_class": "PAID_MARKETPULSE", "max_amount": 20, "decisive": True}
        scope_a = autopilot.compute_scope_key("M-SCOPE", "C1", item_a)
        scope_b = autopilot.compute_scope_key("M-SCOPE", "C1", item_b)
        self.assertNotEqual(scope_a, scope_b)

        reg.record("C1", {"gate": "GATE_4", "verdict": "REQUEST_AUTHORIZATION", "reasons": ["PAID"], "authorization": [{**item_a, "scope_key": scope_a}, {**item_b, "scope_key": scope_b}]}, "fp1")

        # 1. Tenter de résoudre avec un scope_key inexistant doit lever KeyError
        with self.assertRaises(KeyError):
            reg.resolve_authorization("C1", "C1:quote", "SCOPE-NONEXISTENT", granted=True)

        # 2. Autoriser A ne doit jamais autoriser B
        reg.resolve_authorization("C1", "C1:quote", scope_a, granted=True)
        plan_after_a = autopilot.plan_information(
            [item_a, item_b],
            mission_id="M-SCOPE",
            candidate_id="C1",
            granted_scopes=set(reg.candidates["C1"]["granted_scopes"]),
            declined_scopes=set(reg.candidates["C1"]["declined_scopes"]),
        )
        self.assertEqual(len(plan_after_a["free"]), 1)
        self.assertEqual(plan_after_a["free"][0]["provider"], "SUPPLIER_A")
        self.assertEqual(len(plan_after_a["paid"]), 1)
        self.assertEqual(plan_after_a["paid"][0]["provider"], "SUPPLIER_B")

        # 3. Refuser B ne doit jamais annuler A
        reg.resolve_authorization("C1", "C1:quote", scope_b, granted=False)
        plan_after_b_declined = autopilot.plan_information(
            [item_a, item_b],
            mission_id="M-SCOPE",
            candidate_id="C1",
            granted_scopes=set(reg.candidates["C1"]["granted_scopes"]),
            declined_scopes=set(reg.candidates["C1"]["declined_scopes"]),
        )
        self.assertEqual(len(plan_after_b_declined["free"]), 1)
        self.assertEqual(plan_after_b_declined["free"][0]["provider"], "SUPPLIER_A")
        self.assertEqual(len(plan_after_b_declined["declined"]), 1)
        self.assertEqual(plan_after_b_declined["declined"][0]["provider"], "SUPPLIER_B")

    def test_06_operational_validation_preconditions_failure_and_material_change(self):
        mdir = self.tmp / "M_OP"
        res = ecom.build_replay_fixture(mdir)
        rpath = mdir / "AGENT_01" / "candidate_registry.json"
        reg = autopilot.CandidateRegistry.load(rpath, "M_OP")
        cid = res["selected_candidate"]
        sol_id = res["selected_solution"]
        mat_fp = res["selected_material_fingerprint"]

        # 1. Impossible de valider les étapes 9-10 tant que les autorisations opérationnelles ne sont pas GRANTED
        with self.assertRaises(ValueError):
            reg.operational_result(cid, solution_id=sol_id, material_fingerprint=mat_fp, passed=True, reason="Devis OK", refs=["QUOTE-01"])

        # 2. Le propriétaire accorde les 2 autorisations opérationnelles avec leurs scope_keys exacts
        for req in list(reg.data["authorization_requests"]):
            reg.resolve_authorization(cid, req["id"], req["scope_key"], granted=True)

        # 3. Validation opérationnelle PASS
        op_pass = reg.operational_result(
            cid, solution_id=sol_id, material_fingerprint=mat_fp, passed=True, reason="Devis et échantillon validés", refs=["QUOTE-01", "SAMPLE-01"]
        )
        self.assertEqual(op_pass["operational_validation"], "PASSED_READY_FOR_G1")

        # 4. Modification matérielle du produit après le PASS -> invalidation automatique du PASS
        dossiers = json.loads((mdir / "AGENT_01" / "dossiers.json").read_text(encoding="utf-8"))
        dossiers[cid]["solutions"][0]["sku_spec"]["materials"] = ["cheap_plastic_changed"]
        inv = reg.check_material_integrity(cid, dossiers[cid])
        self.assertEqual(inv, "STALE_PASS_INVALIDATED_MATERIAL_CHANGE")
        self.assertEqual(reg.candidates[cid]["operational_validation"], "INVALIDATED_AFTER_MATERIAL_CHANGE")
        self.assertEqual(reg.candidates[cid]["status"], "ACTIVE")

    def test_07_operational_failure_cleanly_rejects_and_advances_backlog(self):
        mdir = self.tmp / "M_OP_FAIL"
        res = ecom.build_replay_fixture(mdir)
        rpath = mdir / "AGENT_01" / "candidate_registry.json"
        reg = autopilot.CandidateRegistry.load(rpath, "M_OP_FAIL")
        cid = res["selected_candidate"]
        sol_id = res["selected_solution"]
        mat_fp = res["selected_material_fingerprint"]

        for req in list(reg.data["authorization_requests"]):
            reg.resolve_authorization(cid, req["id"], req["scope_key"], granted=True)

        op_fail = reg.operational_result(
            cid, solution_id=sol_id, material_fingerprint=mat_fp, passed=False, reason="Fuite constatée sur échantillon réel", refs=["SAMPLE-FAIL-01"]
        )
        self.assertEqual(op_fail["status"], "REJECTED")
        self.assertEqual(op_fail["operational_validation"], "FAILED")
        self.assertIsNone(reg.candidates[cid]["selected_solution_id"])

    def test_08_configurable_market_and_live_vs_replay_guard(self):
        lpath = self.tmp / "ae_ledger.jsonl"
        ledger.append(
            lpath,
            [
                {"territory_id": "T1", "probleme": "AC dry skin", "phrase_exacte": "AC dries my skin every night", "source_type": "AMAZON_FR_REVIEW", "source_url": "https://www.amazon.ae/dp/B01", "market_geo": "AE", "author_key": "u1", "candidate_ids": ["C-AE"]},
                {"territory_id": "T1", "probleme": "AC dry skin", "phrase_exacte": "Same issue in Dubai apartments", "source_type": "FORUM_FR", "source_url": "https://forum.example.ae/t/1", "market_geo": "AE", "author_key": "u2", "candidate_ids": ["C-AE"]},
                {"territory_id": "T1", "probleme": "AC dry skin", "phrase_exacte": "Humidifier leaks after a week", "source_type": "YOUTUBE_COMMENT", "source_url": "https://www.youtube.com/watch?v=ae1", "market_geo": "AE", "author_key": "u3", "candidate_ids": ["C-AE"]},
            ],
            mission_id="M-AE",
            target_market="AE",
            provenance_mode="REPLAY",
        )
        rows = ledger.load(lpath)
        # En mode REPLAY autorisé sur le marché AE -> PASS
        g_replay = ledger.gate_2b(rows, "C-AE", target_market="AE", allow_replay=True)
        self.assertEqual(g_replay["status"], "PASS")
        # En mode LIVE strict -> les observations REPLAY sont exclues -> FAIL
        g_live = ledger.gate_2b(rows, "C-AE", target_market="AE", allow_replay=False)
        self.assertEqual(g_live["status"], "FAIL")

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "knowledge/nectar_index.json").exists(), "Requires external knowledge index")
    def test_09_knowledge_index_lookup(self):
        res = corpus_extractor.query_knowledge(
            ecom.ROOT / "knowledge" / "nectar_index.json", ecom.ROOT / "reference_corpus", "Smooche"
        )
        self.assertTrue(len(res["index_matches"]) >= 1)


if __name__ == "__main__":
    unittest.main()
