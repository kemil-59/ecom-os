"""MarketPulse MCP_SESSION : gouverneur + hook. Hors ligne; aucun appel MCP, réponses simulées."""
from __future__ import annotations
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import ecom
from engine import marketpulse_adapter as tt, tt_live_guard as guard

P = tt.MCP_TOOL_PREFIX
SCOPE = "RECHERCHE_07_TT_FR_V1"
# Noms de propriétés des schémas MCP réels (relevés le 2026-10-20) pour les clés utilisées par les recettes.
SCHEMA_KEYS = {
    "search_ads": {"query", "limit", "country", "status", "sort_by", "ad_language", "active_only", "ad_countries", "main_countries",
                   "trend_signal", "order", "page", "search_in", "keywords", "keyword_mode", "language", "ad_languages",
                   # relevés le 2026-10-20 dans le schéma MCP chargé (search_ads)
                   "max_ads_per_brand", "reach_period", "min_reach", "created_after"},
    "search_advertisers": {"query", "limit", "country", "sort_by", "order", "timeframe", "search_in", "keywords", "category_ids", "verbose"},
    "search_shops": {"query", "limit", "main_market_countries", "match_mode", "market_countries", "visitor_country", "sort_by", "order", "page",
                     "languages", "creation_country", "country"},
}
ENUMS = {("search_ads", "sort_by"): {"relevance", "reach", "longestRunning", "newest", "createdAt", "reachDelta30d"},
         ("search_ads", "reach_period"): {"total", "last24h", "last7d", "last30d"},
         ("search_ads", "status"): {"active", "inactive", "all"},
         ("search_advertisers", "sort_by"): {"relevance", "newest", "activeAds", "reach"},
         ("search_advertisers", "search_in"): {"auto", "brand", "domain", "ad_copy", "website_copy", "url"},
         ("search_shops", "match_mode"): {"contains", "exact"}}
# Réponse simulée : noms de champs inventés pour le test, confirmés par signature comme le ferait le propriétaire.
ADS_ROW = {"adKey": "A1", "body": "poux qui reviennent", "dest": "https://ex.fr/products/p", "page": "Ex", "estimatedSpend": 1234, "price": 9.9}
ADS_MAP = {"total": "meta.total", "id": "adKey", "copy": "body", "landing": "dest", "advertiser": "page"}


def ads_payload(total=37, rows=None):
    return {"structuredContent": {"requestId": "rq-" + uuid.uuid4().hex[:6], "meta": {"total": total},
                                  "data": [dict(ADS_ROW)] if rows is None else rows}}


class LiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = Path(tempfile.mkdtemp(prefix="tt_keys_"))
        for name in ("owner", "intruder"):
            subprocess.run([tt.OPENSSL, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(cls.keys / f"{name}.pem")],
                           check=True, capture_output=True)
            subprocess.run([tt.OPENSSL, "pkey", "-in", str(cls.keys / f"{name}.pem"), "-pubout", "-out", str(cls.keys / f"{name}.pub")],
                           check=True, capture_output=True)
        cls.pub = cls.keys / "owner.pub"
        cls.pin = tt.sha256_file(cls.pub)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.keys, ignore_errors=True)

    def setUp(self):
        self.runs = Path(tempfile.mkdtemp(prefix="tt_live_runs_"))
        self.mdir = self.runs / "M7"
        self.mdir.mkdir()
        (self.mdir / "mission.json").write_text(json.dumps({"mission_id": "M7", "mode": "FETCH_VERIFIED", "target_sale_market": "FR"}))
        self.tdir = self.mdir / "MARKETPULSE"
        self.a = self.adapter()

    def tearDown(self):
        shutil.rmtree(self.runs, ignore_errors=True)

    # ----- aides -----
    def adapter(self, **kw):
        return tt.MarketPulseAdapter(self.tdir, mode="MCP_SESSION", owner_pubkey=self.pub, owner_pubkey_sha256=self.pin, **kw)

    def sign(self, path, key="owner"):
        subprocess.run([tt.OPENSSL, "dgst", "-sha256", "-sign", str(self.keys / f"{key}.pem"), "-out", str(path.with_suffix(".sig")), str(path)],
                       check=True, capture_output=True)
        return path

    def approve(self, max_credits=500, key="owner", scope=SCOPE):
        return self.sign(self.a.prepare_live_approval(mission_id="M7", scope=scope, max_credits=max_credits, hours=24), key)

    def hook(self, event, name, tool_input=None, tool_response=None, tool_use_id=None, **extra):
        ev = {"tool_name": name, "tool_input": tool_input or {}, "tool_response": tool_response,
              "tool_use_id": tool_use_id or "toolu_" + uuid.uuid4().hex[:12], **extra}
        fn = {"pre": guard.pre_tool_use, "post": guard.post_tool_use, "failure": guard.post_tool_use_failure}[event]
        return fn(ev, self.runs, self.pub, self.pin)

    def balance(self, remaining, pre=True, post=True, tid=None):
        """check_credits réel : PreToolUse (horodate le début) puis PostToolUse (capture)."""
        tid = tid or "toolu_bal_" + uuid.uuid4().hex[:8]
        if pre:
            self.assertEqual(self.decision(self.hook("pre", P + "check_credits", tool_use_id=tid)), "allow")
        if post:
            resp = [{"type": "text", "text": json.dumps({"requestId": "bal-" + tid, "credits": {"balance": 0, "totalRemaining": remaining}})}]
            self.hook("post", P + "check_credits", {}, resp, tool_use_id=tid)
        return tid

    def current(self):
        return json.loads((self.tdir / "STATE" / "balance.json").read_text())["credits_remaining"]

    def plan(self, **kw):
        opts = dict(mission_id="M7", scope=SCOPE, recipe_id="A01_S5_AD_DENSITY_PROBE", country="FR", stage=5, terms=["poux"], locale="fr")
        opts.update(kw)
        return self.a.plan_call(**opts)

    def decision(self, out):
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def ready(self, remaining=9477):
        self.approve()
        self.balance(remaining)

    def dispatch(self, r, tid=None):
        return self.hook("pre", P + r["request"]["tool"], r["request"]["params"], tool_use_id=tid)

    def run_call(self, r, cost, response=None):
        tid = "toolu_" + uuid.uuid4().hex[:12]
        self.assertEqual(self.decision(self.dispatch(r, tid)), "ask")
        pre = self.current()
        if response is not False:
            resp = ads_payload() if response is None else response
            self.assertEqual(self.hook("post", P + r["request"]["tool"], r["request"]["params"], resp, tool_use_id=tid)["status"], "RECORDED")
        self.balance(pre - cost)
        return self.a.ingest_call(r["call_id"])

    def calibrate(self, tool="search_ads", response=None, cost=2):
        rid = {"search_ads": "TT_CALIBRATE_SEARCH_ADS", "search_advertisers": "TT_CALIBRATE_SEARCH_ADVERTISERS",
               "search_shops": "TT_CALIBRATE_SEARCH_SHOPS"}[tool]
        r = self.plan(recipe_id=rid)
        return self.run_call(r, cost=cost, response=response if response is not None else ads_payload())

    def confirm(self, tool="search_ads", mapping=None, key="owner"):
        return self.sign(self.a.prepare_field_map(mission_id="M7", tool=tool, mapping=mapping or ADS_MAP), key)

    def ready_ads(self):
        self.ready()
        self.assertEqual(self.calibrate()["status"], "CALIBRATION_CAPTURED")
        self.confirm()


class ApprovalTests(LiveBase):
    def test_unsigned_or_tampered_or_foreign_key_approval_is_ignored(self):
        p = self.a.prepare_live_approval(mission_id="M7", scope="S", max_credits=500)
        self.assertEqual(self.a.live_approvals(), [])
        p.unlink()
        p = self.approve(scope="S")
        self.assertEqual(len(self.a.live_approvals()), 1)
        doc = json.loads(p.read_text())
        doc["max_credits"] = 400
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        self.assertEqual(self.a.live_approvals(), [])
        self.approve(scope="S2", key="intruder")
        self.assertEqual(self.a.live_approvals(), [])

    def test_hard_caps_cannot_be_raised(self):
        with self.assertRaisesRegex(ValueError, "LIVE_MAX_CREDITS_OUT_OF_BOUNDS"):
            self.a.prepare_live_approval(mission_id="M7", scope="S", max_credits=501)
        with self.assertRaisesRegex(ValueError, "LIVE_TOOLS_OUTSIDE_CALIBRATED_SET"):
            self.a.prepare_live_approval(mission_id="M7", scope="S", max_credits=10, tools=["find_similar_shops"])
        self.assertEqual(self.adapter(reserve_floor=0).reserve_floor, 500)
        p = self.approve(scope="S")
        doc = json.loads(p.read_text())
        doc["max_credits"] = 5000
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        self.sign(p)
        self.assertEqual(self.a.live_approvals(), [])

    def test_replay_approval_still_refused_in_live(self):
        with self.assertRaisesRegex(PermissionError, "LIVE_CREDIT_APPROVAL_REQUIRES_INTERACTIVE_OWNER_CONFIRMATION"):
            self.a.record_replay_approval(scope="S", max_credits=10, owner_statement="owner said yes please")

    def test_cli_live_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            (runs / "R").mkdir()
            (runs / "R" / "mission.json").write_text(json.dumps({"mission_id": "R", "mode": "REPLAY"}))
            with patch.object(ecom, "_runs_dir", return_value=runs):
                with self.assertRaisesRegex(SystemExit, "LIVE_MARKETPULSE_REQUIRES_FETCH_VERIFIED_MISSION"):
                    ecom.main(["tt", "status", "--mission", "R", "--mode", "MCP_SESSION"])
                with self.assertRaisesRegex(SystemExit, "LIVE_MARKETPULSE_DISABLED"):
                    ecom.main(["tt", "capabilities", "--mission", "R", "--mode", "LIVE"])
                (runs / "R" / "mission.json").write_text(json.dumps({"mission_id": "R", "mode": "FETCH_VERIFIED"}))
                with patch.object(sys, "stdin", io.StringIO("")):
                    with self.assertRaisesRegex(SystemExit, "APPROVE_LIVE_MUST_BE_RUN_BY_OWNER"):
                        ecom.main(["tt", "approve-live", "--mission", "R", "--mode", "MCP_SESSION", "--scope", "S", "--max-credits", "10"])
                    with self.assertRaisesRegex(SystemExit, "CONFIRM_FIELDS_MUST_BE_RUN_BY_OWNER"):
                        ecom.main(["tt", "confirm-fields", "--mission", "R", "--mode", "MCP_SESSION", "--tool", "search_ads", "--map", "id=x"])

    def test_balance_and_response_only_from_hook(self):
        with self.assertRaisesRegex(PermissionError, "LIVE_BALANCE_ONLY_FROM_HOOK_CAPTURE"):
            self.a.ingest_balance({"requestId": "x", "credits": {"totalRemaining": 99999}})
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        with self.assertRaisesRegex(PermissionError, "LIVE_RESPONSE_ONLY_FROM_HOOK_CAPTURE"):
            self.a.ingest_call(r["call_id"], {"requestId": "x", "credits": {"costUnits": 0}, "data": []})


class HookBarrierTests(LiveBase):
    def test_default_deny_for_everything_not_allowlisted(self):
        self.ready()
        self.assertEqual(self.decision(self.hook("pre", "mcp__claude_ai_Shopify__create-product")), "deny")
        for t in ("add_board_item", "create_board", "add_to_brandtracker", "find_similar_shops", "scan_ad", "find_winning_products", "lookup"):
            self.assertEqual(self.decision(self.hook("pre", P + t)), "deny", t)
        self.assertEqual(self.decision(self.hook("pre", P + "check_credits")), "allow")
        self.assertEqual(self.decision(self.hook("pre", P + "search_ads", {"query": "poux", "limit": 1})), "deny")
        self.assertIsNone(self.hook("pre", "Read"))

    def test_exact_params_ask_once_then_deny(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        params = r["request"]["params"]
        self.assertEqual(self.decision(self.hook("pre", P + "search_ads", {**params, "limit": 20})), "deny")
        self.assertEqual(self.decision(self.hook("pre", P + "search_ads", {**params, "min_reach": 0})), "deny")
        self.assertEqual(self.decision(self.hook("pre", P + "search_shops", params)), "deny")
        out = self.dispatch(r)
        self.assertEqual(self.decision(out), "ask")  # jamais "allow" pour un outil payant
        self.assertIn(r["call_id"], out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.decision(self.dispatch(r)), "deny")

    def test_concurrent_dispatch_single_winner(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.decision(self.dispatch(r)))) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results.count("ask"), 1)
        self.assertEqual(results.count("deny"), 7)

    def test_pin_mismatch_denies_paid_calls(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        out = guard.pre_tool_use({"tool_name": P + "search_ads", "tool_input": r["request"]["params"], "tool_use_id": "t"}, self.runs, self.pub, "0" * 64)
        self.assertEqual(self.decision(out), "deny")

    def test_guard_fail_closed_on_bad_input_and_subprocess(self):
        buf = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(buf):
            guard.main(["pre", "--runs-dir", str(self.runs)], stdin=io.StringIO("not json"))
        self.assertEqual(json.loads(buf.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")
        script = Path(guard.__file__)
        for ev, payload, rc in (("pre", {"tool_name": P + "create_board", "tool_input": {}}, 0), ("failure", "not json", 2)):
            cmd = f'/usr/bin/python3 -I "{script}" {ev} --runs-dir "{self.runs}" --pubkey "{self.pub}" --pubkey-sha256 {self.pin} || exit 2'
            res = subprocess.run(["sh", "-c", cmd], input=payload if isinstance(payload, str) else json.dumps(payload), capture_output=True, text=True)
            self.assertEqual(res.returncode, rc, res.stderr)
            if ev == "pre":
                self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        broken = subprocess.run(["sh", "-c", '/usr/bin/python3 -I /nonexistent/guard.py pre || exit 2'], input="{}", capture_output=True, text=True)
        self.assertEqual(broken.returncode, 2)

    def test_stale_or_untimed_balance_requires_refresh(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        bal = json.loads((self.tdir / "STATE" / "balance.json").read_text())
        bal["observed_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat()
        (self.tdir / "STATE" / "balance.json").write_text(json.dumps(bal))
        self.assertIn("LIVE_BALANCE_REFRESH_REQUIRED", self.dispatch(r)["hookSpecificOutput"]["permissionDecisionReason"])
        self.balance(9477, pre=False)  # capture sans PreToolUse : début de lecture inconnu
        self.assertIn("LIVE_BALANCE_REFRESH_REQUIRED", self.dispatch(r)["hookSpecificOutput"]["permissionDecisionReason"])
        self.balance(9477)
        self.assertEqual(self.decision(self.dispatch(r)), "ask")


class BudgetTests(LiveBase):
    def test_one_outstanding_call_and_dedup(self):
        self.ready()
        r1 = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        self.assertTrue(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")["idempotent_reuse"])
        self.assertIn("LIVE_ONE_OUTSTANDING_CALL", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["reasons"]))
        self.a.cancel_call(r1["call_id"])
        self.assertEqual(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["status"], "AWAITING_EXECUTION")

    def test_limit_cannot_exceed_recipe_and_uncalibrated_refused(self):
        self.ready()
        self.assertIn("LIVE_LIMIT_ABOVE_RECIPE", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", limit=20)["reasons"]))
        self.assertIn("LIVE_REQUIRES_CALIBRATED_RECIPE", str(self.plan(recipe_id=None, tool="search_ads")["reasons"]))
        self.assertIn("LIVE_TOOL_NOT_ALLOWED", str(self.plan(recipe_id="A01_S7_SOLUTION_PRODUCTS", stage=7)["reasons"]))

    def test_reserve_floor_kept(self):
        self.approve()
        self.balance(503)
        self.assertEqual(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")["status"], "AWAITING_EXECUTION")  # 503-2 >= 500
        self.a.cancel_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")["call_id"])
        self.assertIn("RESERVE_FLOOR_BREACH", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_SHOPS")["reasons"]))  # 503-4 < 500

    def test_reserve_floor_blocks_large_call(self):
        self.ready_ads()
        self.balance(519)
        self.assertIn("RESERVE_FLOOR_BREACH", str(self.plan(recipe_id="A02_FR_BASELINE_ADS")["reasons"]))  # 519-20 < 500

    def test_ceiling_and_platform_guard(self):
        self.approve(max_credits=4)
        self.balance(9477)
        self.run_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS"), cost=2)
        self.run_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"]), cost=2)
        self.assertIn("SCOPE_CREDIT_CEILING_EXCEEDED", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["anti-poux"])["reasons"]))

    def test_external_debit_counts_against_budget(self):
        self.approve(max_credits=10)
        self.balance(9477)
        self.a.cancel_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")["call_id"])
        self.balance(9469)  # 8 crédits débités hors moteur
        self.assertIn("LIVE_PLATFORM_SPEND_CEILING_EXCEEDED", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_SHOPS")["reasons"]))


class SettlementTests(LiveBase):
    def test_normal_settlement_no_financial_fields_and_index_count(self):
        self.ready_ads()
        r = self.plan()
        out = self.run_call(r, cost=2)
        self.assertEqual(out["status"], "INGESTED")
        self.assertEqual(out["credits_spent"], 2)
        total, ad = out["evidence"]
        self.assertEqual((total["source_class"], total["index_count"]), ("INDEX_COUNT", 37))
        self.assertEqual((ad["entity_id"], ad["shop_domain"]), ("A1", "ex.fr"))
        self.assertNotIn("price", ad)
        self.assertFalse(ad["gate_eligible"])
        inbox = (self.tdir / "INBOX" / f"{r['call_id']}.response.json").read_text()
        self.assertNotIn("estimatedSpend", inbox)
        self.assertNotIn("price", inbox)
        self.assertEqual(self.a.reconcile_credits()["status"], "RECONCILED")

    def test_settled_reservation_cannot_be_reused(self):
        self.ready_ads()
        r = self.plan()
        self.run_call(r, cost=2)
        self.balance(self.current())
        self.assertEqual(self.decision(self.dispatch(r)), "deny")  # mêmes paramètres : plus de réservation
        self.assertEqual(self.plan()["status"], "CACHE_HIT")

    def test_unexpected_response_halts_until_new_signed_approval(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        out = self.run_call(r, cost=0, response=[{"type": "text", "text": "## markdown only"}])
        self.assertEqual(out["status"], "UNEXPECTED_RESPONSE_SHAPE")
        self.assertTrue(out["halted"])
        self.assertIn("LIVE_HALTED", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["reasons"]))
        self.approve()
        self.balance(9477)
        self.assertIn("TOOL_FROZEN", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["reasons"]))

    def test_cost_overrun_halts_and_freezes(self):
        self.ready()
        out = self.run_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS"), cost=5)
        self.assertIn("COST_MODEL_VIOLATION", out["alert"])
        self.assertTrue(out["halted"])
        self.assertIn("search_ads", json.loads((self.tdir / "STATE" / "frozen_tools.json").read_text()))

    def test_receipt_mismatch_halts_and_settles_higher_cost(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADVERTISERS")
        out = self.run_call(r, cost=1, response={"requestId": "x", "credits": {"costUnits": 2}, "data": []})
        self.assertTrue(out["halted"])
        self.assertIn("COST_RECEIPT_MISMATCH", out["alert"])
        self.assertEqual(out["credits_spent"], 2)

    def test_unmatched_paid_response_halts(self):
        self.ready()
        self.assertEqual(self.hook("post", P + "search_ads", {"query": "x"}, {"data": []})["status"], "HALTED_UNMATCHED")
        self.assertIsNotNone(self.a.halt_state())


class PostResponseBalanceTests(LiveBase):
    """Correction 1 : le solde de règlement doit avoir été DEMANDÉ après la réponse payante capturée."""

    def test_balance_after_authorization_but_before_response_is_rejected(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        tid = "toolu_paid"
        self.assertEqual(self.decision(self.dispatch(r, tid)), "ask")
        self.balance(9477)  # lu après l'autorisation, avant la réponse (débit pas encore visible)
        self.hook("post", P + "search_ads", r["request"]["params"], ads_payload(), tool_use_id=tid)
        with self.assertRaisesRegex(PermissionError, "LIVE_POST_RESPONSE_BALANCE_REQUIRED"):
            self.a.ingest_call(r["call_id"])
        self.assertIn(r["call_id"], json.loads((self.tdir / "STATE" / "pending.json").read_text()))  # réservation conservée
        self.assertEqual(json.loads((self.tdir / "STATE" / "reservations.json").read_text())[
            json.loads((self.tdir / "STATE" / "pending.json").read_text())[r["call_id"]]["reservation_id"]]["max_credits"], 2)
        self.assertIn("LIVE_ONE_OUTSTANDING_CALL", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["reasons"]))
        self.balance(9475)  # lecture démarrée après la réponse
        out = self.a.ingest_call(r["call_id"])
        self.assertEqual((out["status"], out["credits_spent"]), ("CALIBRATION_CAPTURED", 2))

    def test_concurrent_balance_read_started_before_response_is_rejected(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        self.assertEqual(self.decision(self.dispatch(r, "toolu_paid")), "ask")
        early = self.balance(0, pre=True, post=False)  # lecture lancée en parallèle, avant la réponse
        self.hook("post", P + "search_ads", r["request"]["params"], ads_payload(), tool_use_id="toolu_paid")
        self.balance(9477, pre=False, tid=early)  # sa capture arrive après la réponse
        with self.assertRaisesRegex(PermissionError, "LIVE_POST_RESPONSE_BALANCE_REQUIRED"):
            self.a.ingest_call(r["call_id"])


class InterruptionTests(LiveBase):
    """Refus, échec, interruption : aucun délai ne libère une réservation ; résolution signée cohérente avec le solde."""

    def later(self, minutes):
        real = tt._now()
        return patch.object(tt, "_now", lambda: real + dt.timedelta(minutes=minutes))

    def authorize(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        self.assertEqual(self.decision(self.dispatch(r, "toolu_paid")), "ask")
        return r

    def resolve(self, r, decision, charged=0, key="owner"):
        p = self.a.prepare_call_resolution(mission_id="M7", call_id=r["call_id"], decision=decision, charged_credits=charged,
                                           justification="Historique de consommation MarketPulse vérifié par le propriétaire")
        return self.sign(p, key)

    def assert_blocked(self, r):
        self.assertIn("LIVE_ONE_OUTSTANDING_CALL", str(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["reasons"]))
        self.assertIn("OPEN_CALL_NOT_SETTLED", self.dispatch(r)["hookSpecificOutput"]["permissionDecisionReason"])
        with self.assertRaisesRegex(PermissionError, "LIVE_CALL_ALREADY_DISPATCHED"):
            self.a.cancel_call(r["call_id"])

    def test_manual_denial_no_timeout_release(self):
        r = self.authorize()  # refus manuel : aucun hook
        for minutes in (0, 11, 60 * 24 * 30):  # même après 30 jours de solde inchangé
            with self.later(minutes):
                self.balance(9477)
                with self.assertRaisesRegex(PermissionError, "LIVE_INDETERMINATE_CALL_REQUIRES_SIGNED_OWNER_RESOLUTION"):
                    self.a.ingest_call(r["call_id"])
                if minutes < 60 * 24:
                    self.assert_blocked(r)
                else:  # approbation expirée entre-temps : blocage maintenu
                    self.assertEqual(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])["status"], "BLOCKED")
                    self.assertEqual(self.decision(self.dispatch(r)), "deny")
        self.assertIn(r["call_id"], json.loads((self.tdir / "STATE" / "pending.json").read_text()))

    def test_resolution_must_be_signed_by_owner_and_followed_by_balance(self):
        r = self.authorize()
        self.balance(9477)
        self.resolve(r, "NO_CHARGE", key="intruder")
        with self.assertRaisesRegex(PermissionError, "REQUIRES_SIGNED_OWNER_RESOLUTION"):
            self.a.ingest_call(r["call_id"])
        self.resolve(r, "NO_CHARGE")
        with self.assertRaisesRegex(PermissionError, "LIVE_POST_RESOLUTION_BALANCE_REQUIRED"):
            self.a.ingest_call(r["call_id"])  # solde lu avant la résolution
        self.balance(9477)
        out = self.a.ingest_call(r["call_id"])
        self.assertEqual((out["status"], out["credits_spent"], out["halted"]), ("RESOLVED_NO_CHARGE", 0, False))

    def test_no_charge_resolution_contradicted_by_debit_is_refused(self):
        r = self.authorize()
        self.resolve(r, "NO_CHARGE")
        self.balance(9475)
        with self.assertRaisesRegex(PermissionError, "LIVE_RESOLUTION_CONTRADICTS_OBSERVED_DEBIT"):
            self.a.ingest_call(r["call_id"])
        self.assertIn(r["call_id"], json.loads((self.tdir / "STATE" / "pending.json").read_text()))

    def test_late_billing_after_no_charge_resolution_halts(self):
        r = self.authorize()
        self.resolve(r, "NO_CHARGE")
        self.balance(9477)
        self.a.ingest_call(r["call_id"])
        self.balance(9475)  # débit tardif de l'appel refusé
        r2 = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])
        self.assertIn("UNSETTLED_SPEND_DETECTED", self.dispatch(r2)["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.a.halt_state()["reason"], "UNSETTLED_SPEND_DETECTED")
        self.assertEqual(self.a.reconcile_credits()["status"], "UNSETTLED_SPEND_DETECTED")

    def test_late_billing_after_normal_settlement_halts(self):
        self.ready()
        out = self.run_call(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS"), cost=0)  # débit pas encore visible
        self.assertEqual(out["credits_spent"], 0)
        self.balance(self.current() - 2)  # débit tardif
        r2 = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS", terms=["lentes"])
        self.assertEqual(self.decision(self.dispatch(r2)), "deny")
        self.assertEqual(self.a.halt_state()["reason"], "UNSETTLED_SPEND_DETECTED")

    def test_mcp_failure_kept_until_charged_resolution(self):
        r = self.authorize()
        self.assertEqual(self.hook("failure", P + "search_ads", r["request"]["params"], tool_use_id="toolu_paid",
                                   error="timeout", is_interrupt=False)["status"], "RECORDED_FAILURE")
        self.assertEqual(json.loads((self.tdir / "STATE" / "pending.json").read_text())[r["call_id"]]["status"], "FAILED")
        self.assert_blocked(r)
        with self.later(120):
            self.balance(9475)
            with self.assertRaisesRegex(PermissionError, "REQUIRES_SIGNED_OWNER_RESOLUTION"):
                self.a.ingest_call(r["call_id"])
        self.resolve(r, "CHARGED", charged=1)
        self.balance(9475)
        out = self.a.ingest_call(r["call_id"])  # coût = max(débit observé 2, déclaré 1)
        self.assertEqual((out["status"], out["credits_spent"], out["halted"]), ("RESOLVED_CHARGED_WITHOUT_RESPONSE", 2, True))

    def test_resolution_only_for_open_call_without_response(self):
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        with self.assertRaisesRegex(PermissionError, "RESOLUTION_ONLY_FOR_AUTHORIZED_OR_FAILED"):
            self.resolve(r, "NO_CHARGE")  # jamais envoyée : utiliser cancel
        self.dispatch(r, "toolu_paid")
        self.hook("post", P + "search_ads", r["request"]["params"], ads_payload(), tool_use_id="toolu_paid")
        with self.assertRaisesRegex(PermissionError, "RESOLUTION_ONLY_FOR_AUTHORIZED_OR_FAILED"):
            self.resolve(r, "NO_CHARGE")  # réponse capturée : règlement par solde post-réponse

    def test_resolution_parameters_validated(self):
        r = self.authorize()
        for decision, charged in (("CHARGED", 0), ("NO_CHARGE", 2), ("MAYBE", 0)):
            with self.assertRaises(ValueError):
                self.a.prepare_call_resolution(mission_id="M7", call_id=r["call_id"], decision=decision, charged_credits=charged,
                                               justification="Historique de consommation vérifié")
        with self.assertRaisesRegex(ValueError, "JUSTIFICATION_TOO_SHORT"):
            self.a.prepare_call_resolution(mission_id="M7", call_id=r["call_id"], decision="NO_CHARGE", justification="ok")

    def test_failure_hook_subprocess_and_wrong_tool_use_id(self):
        r = self.authorize()
        self.assertEqual(self.hook("failure", P + "search_ads", r["request"]["params"], tool_use_id="toolu_other")["status"], "NO_MATCH")
        ev = {"tool_name": P + "search_ads", "tool_input": r["request"]["params"], "tool_use_id": "toolu_paid", "error": "x", "is_interrupt": True}
        res = subprocess.run(["/usr/bin/python3", "-I", str(Path(guard.__file__)), "failure", "--runs-dir", str(self.runs), "--pubkey", str(self.pub),
                              "--pubkey-sha256", self.pin], input=json.dumps(ev), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(json.loads((self.tdir / "STATE" / "pending.json").read_text())[r["call_id"]]["failure"]["is_interrupt"])

    def test_late_response_after_failure_is_still_captured(self):
        r = self.authorize()
        self.hook("failure", P + "search_ads", r["request"]["params"], tool_use_id="toolu_paid", error="x")
        self.assertEqual(self.hook("post", P + "search_ads", r["request"]["params"], ads_payload(), tool_use_id="toolu_paid")["status"], "RECORDED")

    def test_cli_resolve_call_owner_terminal_only(self):
        with patch.object(ecom, "_runs_dir", return_value=self.runs), patch.object(sys, "stdin", io.StringIO("")):
            with self.assertRaisesRegex(SystemExit, "RESOLVE_CALL_MUST_BE_RUN_BY_OWNER"):
                ecom.main(["tt", "resolve-call", "--mission", "M7", "--mode", "MCP_SESSION", "--call", "TTC-x",
                           "--decision", "NO_CHARGE", "--justification", "x" * 30])


class FieldMapAndFilterTests(LiveBase):
    """Correction 5 : aucun champ deviné ; lots dépendants bloqués avant confirmation signée du format réel."""

    def test_new_entrants_search_in_ad_copy(self):
        p = tt.build_live_params("A01_S5_NEW_ENTRANTS", country="FR", limit=5, terms=["poux qui reviennent"], locale="fr", linked_domain=None)
        self.assertEqual((p["search_in"], p["sort_by"], p["country"]), ("ad_copy", "newest", "FR"))

    def test_dependent_recipes_blocked_until_signed_field_map(self):
        self.ready()
        for rid in ("A01_S5_AD_DENSITY_PROBE", "A02_FR_COUNTER_PROBE", "A01_S5_AD_SAMPLE_REACH", "A01_S5_NEW_ENTRANTS"):
            self.assertIn("LIVE_FIELD_MAP_NOT_CONFIRMED", str(self.plan(recipe_id=rid)["reasons"]), rid)
        # Recherche texte de boutiques désactivée en LIVE (hors cible au calibrage) : bloquée avant toute dépense.
        self.assertIn("LIVE_RECIPE_DISABLED", str(self.plan(recipe_id="A02_FR_BASELINE_SHOPS")["reasons"]))
        with self.assertRaisesRegex(PermissionError, "CALIBRATION_REQUIRED_BEFORE_FIELD_MAP"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping=ADS_MAP)
        self.calibrate()
        self.confirm(key="intruder")
        self.assertIn("LIVE_FIELD_MAP_NOT_CONFIRMED", str(self.plan()["reasons"]))
        self.confirm()
        self.assertEqual(self.plan()["status"], "AWAITING_EXECUTION")

    def test_field_map_rejects_unobserved_keys_and_non_integer_total(self):
        self.ready()
        self.calibrate()
        with self.assertRaisesRegex(ValueError, "ROW_KEY_NOT_OBSERVED_IN_CALIBRATION"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"id": "adId"})
        with self.assertRaisesRegex(ValueError, "TOTAL_PATH_NOT_INTEGER"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"total": "meta.nope"})
        summary = self.a.calibration_summary("search_ads")
        self.assertIn("meta.total", summary["payload_paths"])
        self.assertIn("adKey", summary["row_keys"])
        self.assertNotIn("price", summary["row_keys"])

    def test_density_blocked_if_total_role_not_confirmed(self):
        self.ready()
        self.calibrate()
        self.confirm(mapping={k: v for k, v in ADS_MAP.items() if k != "total"})
        self.assertIn("LIVE_FIELD_MAP_MISSING_ROLES:search_ads:total", str(self.plan()["reasons"]))
        self.assertEqual(self.plan(recipe_id="A01_S5_AD_SAMPLE_REACH")["status"], "AWAITING_EXECUTION")

    def test_linked_shop_requires_ingested_ad_with_that_domain(self):
        self.ready_ads()
        out = self.run_call(self.plan(recipe_id="A01_S5_AD_SAMPLE_REACH"), cost=2)
        ref = out["evidence"][0]["source_ref"]
        self.calibrate("search_shops", response={"structuredContent": {"requestId": "s", "meta": {"total": 1}, "data": [{"host": "ex.fr"}]}}, cost=4)
        self.confirm("search_shops", {"domain": "host"})
        self.assertIn("LINKED_AD_REF_NOT_FOUND", str(self.plan(recipe_id="A01_S5_LINKED_SHOP", linked_domain="autre.fr", linked_ad_ref=ref)["reasons"]))
        self.assertIn("LINKED_AD_REF_NOT_FOUND", str(self.plan(recipe_id="A01_S5_LINKED_SHOP", linked_domain="ex.fr", linked_ad_ref="TT-X-01")["reasons"]))
        r = self.plan(recipe_id="A01_S5_LINKED_SHOP", linked_domain="https://www.ex.fr/x", linked_ad_ref=ref)
        self.assertEqual(r["request"]["params"], {"query": "ex.fr", "limit": 1, "match_mode": "exact"})


class ModeSwitchAndTamperTests(LiveBase):
    def test_non_live_adapter_refused_on_live_state(self):
        self.ready()
        self.dispatch(self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS"))
        for mode in ("REPLAY", "DISCONNECTED", "FILE_DROP"):
            with self.assertRaisesRegex(PermissionError, "LIVE_STATE_REQUIRES_MCP_SESSION"):
                tt.MarketPulseAdapter(self.tdir, mode=mode)

    def test_cli_duplicate_mode_cannot_downgrade_fetch_verified_mission(self):
        fake = self.runs / "fake.json"
        fake.write_text(json.dumps({"requestId": "x", "credits": {"costUnits": 0, "creditsRemaining": 1}, "data": []}))
        with patch.object(ecom, "_runs_dir", return_value=self.runs):
            for mode in ("REPLAY", "DISCONNECTED"):
                with self.assertRaisesRegex(SystemExit, "FETCH_VERIFIED_MISSION_MARKETPULSE_ONLY_MCP_SESSION"):
                    ecom.main(["tt", "ingest", "--mission", "M7", "--mode", "MCP_SESSION", "--mode", mode, "--call", "TTC-x", "--file", str(fake)])

    def test_settings_keep_protections_and_wire_all_hooks(self):
        conf = json.loads((ecom.ROOT / ".claude" / "settings.json").read_text())
        if "hooks" not in conf:
            self.skipTest("réglages LIVE pas encore installés (étape 4 de l'installation ; revérifiés aux étapes 6 et 7)")
        deny, allow = conf["permissions"]["deny"], conf["permissions"]["allow"]
        for entry in ("Edit(/engine/**)", "Edit(/ecom.py)", "Edit(/.claude/**)", "Edit(/CLAUDE.md)", "Edit(/reference_corpus/**)",
                      "Edit(/runs/*/MARKETPULSE/**)", "Bash(*approve-live*)", "Bash(*confirm-fields*)", "Bash(*owner-keygen*)",
                      "Bash(*MARKETPULSE/STATE*)", "Bash(*resolve-call*)", "Bash(python3 ecom.py auto authorize *)", "Bash(*--confirm-owner-said-yes*)"):
            self.assertIn(entry, deny)
        self.assertEqual(sum(1 for d in deny if d.startswith(P)), 41)
        self.assertFalse(any(a.startswith(P + "search_") for a in allow))
        self.assertEqual(sorted(conf["permissions"]["ask"]), sorted(P + t for t in tt.LIVE_TOOLS))
        self.assertEqual(conf["permissions"]["disableBypassPermissionsMode"], "disable")
        for ev, arg in (("PreToolUse", " pre "), ("PostToolUse", " post "), ("PostToolUseFailure", " failure ")):
            cmd = conf["hooks"][ev][0]["hooks"][0]["command"]
            self.assertTrue(cmd.startswith("/usr/bin/python3 -I ") and arg in cmd and cmd.endswith("|| exit 2"), ev)

    def test_forged_state_limits_are_documented_not_claimed(self):
        # Un solde fabriqué sans horodatage de lecture est refusé ; un état entièrement fabriqué ne peut être détecté
        # que par les permissions (Edit/Bash refusés sur MARKETPULSE/STATE) : limite assumée.
        self.ready()
        r = self.plan(recipe_id="TT_CALIBRATE_SEARCH_ADS")
        bal = json.loads((self.tdir / "STATE" / "balance.json").read_text())
        bal.pop("requested_at")
        (self.tdir / "STATE" / "balance.json").write_text(json.dumps(bal))
        self.assertIn("LIVE_BALANCE_REFRESH_REQUIRED", self.dispatch(r)["hookSpecificOutput"]["permissionDecisionReason"])


class SchemaMappingTests(unittest.TestCase):
    def test_live_params_match_real_schemas(self):
        for rid, spec in tt.RECIPES.items():
            if spec["tool"] not in tt.LIVE_TOOLS:
                continue
            p = tt.build_live_params(rid, country="FR", limit=spec["limit"], terms=["poux"], locale="fr",
                                     linked_domain="ex.fr" if spec.get("requires_linked_domain") else None)
            self.assertNotIn("locale", p, rid)
            self.assertLessEqual(set(p), SCHEMA_KEYS[spec["tool"]], rid)
            for (tool, key), allowed in ENUMS.items():
                if tool == spec["tool"] and key in p:
                    self.assertIn(p[key], allowed, rid)
            if spec["tool"] == "search_ads":
                self.assertIn("sort_by", p, rid)
            if spec["tool"] == "search_shops":
                self.assertNotIn("country", p, rid)
            if spec["tool"] == "search_advertisers":
                self.assertEqual(p.get("search_in"), "ad_copy", rid)

    def test_write_tools_of_real_connector_forbidden(self):
        for t in ("add_board_item", "archive_board", "update_board", "create_ad_share_link", "remove_board_item", "restore_board"):
            self.assertIn(t, tt.FORBIDDEN_WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
