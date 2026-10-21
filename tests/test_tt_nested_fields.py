"""Champs imbriqués MarketPulse (chemins pointés) : extraction typée, absents, financiers exclus, REPLAY inchangé.
Hors ligne; aucun appel MCP. Lignes simulées reprenant la FORME de la réponse search_ads réelle (valeurs fictives)."""
from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path

from engine import marketpulse_adapter as tt
from tests.test_tt_live_guard import LiveBase

NESTED_ROW = {
    "id": "facebook_000001", "status": "active", "createdAt": "2026-10-01T00:00:00.000Z",
    "media": {"type": "image", "thumbnailUrl": None, "mediaUrl": "https://medias.example/ad.jpg"},
    "advertiser": {"id": "p1", "name": "Marque Fictive", "liveAdsCount": 10},
    "content": {"title": None, "body": "Des poux ? Lotion fictive.", "landingPageUrl": "https://fictif.fr/products/lotion",
                "landingPageDomain": "fictif.fr"},
    "metrics": {"reach": 5516, "estimatedSpend": 50, "duplicates": 1},
}
NESTED_MAP = {"total": "pagination.total", "id": "id", "copy": "content.body", "landing": "content.landingPageUrl",
              "domain": "content.landingPageDomain", "advertiser": "advertiser.name", "reach": "metrics.reach",
              "active_ads": "advertiser.liveAdsCount", "media": "media.mediaUrl"}


def nested_payload(rows=None, total=2416, cost=2):
    return {"structuredContent": {"requestId": "rq-nested", "data": [copy.deepcopy(NESTED_ROW)] if rows is None else rows,
                                  "pagination": {"total": total, "isTotalApproximate": False},
                                  "credits": {"costUnits": cost}}}


def row_with(**changes):
    r = copy.deepcopy(NESTED_ROW)
    for path, value in changes.items():
        cur = r
        parts = path.split("__")
        for p in parts[:-1]:
            cur = cur[p]
        if value is KeyError:
            cur.pop(parts[-1], None)
        else:
            cur[parts[-1]] = value
    return r


class ResolveRowFieldTests(unittest.TestCase):
    def test_statuses(self):
        r = {"a": {"b": "x", "n": 3, "flag": True, "obj": {"k": 1}, "lst": [1], "empty": "", "nul": None}, "top": 7}
        self.assertEqual(tt.resolve_row_field(r, "copy", "a.b"), ("x", tt.FIELD_OK))
        self.assertEqual(tt.resolve_row_field(r, "id", "top"), (7, tt.FIELD_OK))
        self.assertEqual(tt.resolve_row_field(r, "reach", "a.n"), (3, tt.FIELD_OK))
        for path in ("a.zz", "a.b.c", "zz.b", "a.empty", "a.nul"):
            self.assertEqual(tt.resolve_row_field(r, "copy", path), (None, tt.FIELD_MISSING), path)
        for role, path in (("copy", "a.obj"), ("copy", "a.lst"), ("copy", "a.n"), ("reach", "a.b"), ("reach", "a.flag"), ("id", "a.flag")):
            self.assertEqual(tt.resolve_row_field(r, role, path), (None, tt.FIELD_TYPE_MISMATCH), (role, path))
        self.assertEqual(tt.resolve_row_field(r, "copy", None), (None, tt.FIELD_NOT_MAPPED))
        self.assertEqual(tt.resolve_row_field({"metrics": {"estimatedSpend": 5}}, "reach", "metrics.estimatedSpend"),
                         (None, tt.FIELD_FINANCIAL_FORBIDDEN))

    def test_financial_path_detection_covers_nested_segments(self):
        for p in ("metrics.estimatedSpend", "shop.revenue30d", "offer.price", "x.unitsSold", "currency"):
            self.assertTrue(tt.is_financial_path(p), p)
        for p in NESTED_MAP.values():
            self.assertFalse(tt.is_financial_path(p), p)


class NestedFieldMapTests(LiveBase):
    def calibrate_nested(self):
        self.ready()
        self.assertEqual(self.calibrate(response=nested_payload())["status"], "CALIBRATION_CAPTURED")

    def test_calibration_summary_lists_nested_paths_without_financial(self):
        self.calibrate_nested()
        s = self.a.calibration_summary("search_ads")
        for p in ("content.body", "advertiser.name", "metrics.reach", "media.mediaUrl"):
            self.assertIn(p, s["row_paths"])
        self.assertNotIn("metrics.estimatedSpend", s["row_paths"])
        self.assertEqual(s["row_keys"], sorted(NESTED_ROW))

    def test_nested_map_accepted_and_extracted_with_provenance(self):
        self.calibrate_nested()
        self.confirm(mapping=NESTED_MAP)
        out = self.run_call(self.plan(recipe_id="A01_S5_AD_SAMPLE_REACH"), cost=2, response=nested_payload())
        self.assertEqual(out["status"], "INGESTED")
        ev = out["evidence"][0]
        self.assertEqual(ev["entity_id"], "facebook_000001")
        self.assertEqual(ev["competitor_id"], "Marque Fictive")
        self.assertIn("Des poux ? Lotion fictive.", ev["observation"])
        self.assertEqual(ev["shop_domain"], "fictif.fr")
        self.assertEqual(ev["source_url"], "https://fictif.fr/products/lotion")
        self.assertEqual(set(ev["field_status"].values()), {tt.FIELD_OK})
        self.assertEqual(ev["field_paths"]["copy"], "content.body")
        self.assertEqual(ev["source_class"], "DIRECT_OBSERVATION")
        self.assertFalse(ev["gate_eligible"])
        self.assertNotIn("price", ev)
        link = out["entity_links"][0]
        self.assertEqual((link["ad_id"], link["advertiser"], link["shop_domain"], link["landing_url"]),
                         ("facebook_000001", "Marque Fictive", "fictif.fr", "https://fictif.fr/products/lotion"))
        self.assertFalse(link["sku_sales_proven"])

    def test_direct_id_and_pagination_total_still_work(self):
        self.calibrate_nested()
        self.confirm(mapping={"total": "pagination.total", "id": "id"})
        out = self.run_call(self.plan(recipe_id="A01_S5_AD_DENSITY_PROBE"), cost=2, response=nested_payload(total=2416))
        self.assertEqual(out["evidence"][0]["source_class"], "INDEX_COUNT")
        self.assertEqual(out["evidence"][0]["index_count"], 2416)
        self.assertEqual(out["evidence"][1]["entity_id"], "facebook_000001")
        self.assertEqual(out["evidence"][1]["field_status"], {"id": tt.FIELD_OK, "copy": tt.FIELD_NOT_MAPPED,
                                                              "landing": tt.FIELD_NOT_MAPPED, "domain": tt.FIELD_NOT_MAPPED,
                                                              "advertiser": tt.FIELD_NOT_MAPPED, "reach": tt.FIELD_NOT_MAPPED,
                                                              "active_ads": tt.FIELD_NOT_MAPPED, "media": tt.FIELD_NOT_MAPPED})

    def test_absent_and_wrong_type_fields_marked_never_invented(self):
        self.calibrate_nested()
        self.confirm(mapping=NESTED_MAP)
        rows = [row_with(content__landingPageUrl=KeyError, content__landingPageDomain=KeyError, metrics__reach="5516",
                         advertiser__name={"fr": "objet"}),
                row_with(id="facebook_000002", content__body={"nested": "objet"}, media__mediaUrl=None)]
        out = self.run_call(self.plan(recipe_id="A01_S5_AD_SAMPLE_REACH"), cost=4, response=nested_payload(rows=rows, cost=4))
        first, second = out["evidence"]
        self.assertEqual(first["field_status"]["landing"], tt.FIELD_MISSING)
        self.assertEqual(first["field_status"]["domain"], tt.FIELD_MISSING)
        self.assertEqual(first["field_status"]["reach"], tt.FIELD_TYPE_MISMATCH)
        self.assertEqual(first["field_status"]["advertiser"], tt.FIELD_TYPE_MISMATCH)
        self.assertIsNone(first["shop_domain"])
        self.assertEqual(first["competitor_id"], "UNKNOWN_ADVERTISER")
        self.assertEqual(first["source_url"], "https://medias.example/ad.jpg")
        self.assertIsNone(out["entity_links"][0]["landing_url"])
        self.assertEqual(second["field_status"]["copy"], tt.FIELD_TYPE_MISMATCH)
        self.assertEqual(second["field_status"]["media"], tt.FIELD_MISSING)
        self.assertNotIn("{", second["observation"])
        self.assertNotIn("objet", json.dumps(out["evidence"], ensure_ascii=False))

    def test_non_scalar_or_wrong_type_or_unobserved_path_refused_at_confirmation(self):
        self.calibrate_nested()
        with self.assertRaisesRegex(ValueError, "ROW_FIELD_TYPE_MISMATCH_IN_CALIBRATION:copy=content"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"copy": "content"})
        with self.assertRaisesRegex(ValueError, "ROW_FIELD_TYPE_MISMATCH_IN_CALIBRATION:reach=content.body"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"reach": "content.body"})
        with self.assertRaisesRegex(ValueError, "ROW_KEY_NOT_OBSERVED_IN_CALIBRATION:copy=content.title"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"copy": "content.title"})
        with self.assertRaisesRegex(ValueError, "ROW_KEY_NOT_OBSERVED_IN_CALIBRATION:copy=content.nope"):
            self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"copy": "content.nope"})

    def test_financial_fields_forbidden_even_nested(self):
        self.calibrate_nested()
        stored = json.dumps(self.a.calibration_payload("search_ads"))
        self.assertNotIn("estimatedSpend", stored)
        for path in ("metrics.estimatedSpend", "content.price"):
            with self.assertRaisesRegex(ValueError, "FINANCIAL_FIELD_FORBIDDEN"):
                self.a.prepare_field_map(mission_id="M7", tool="search_ads", mapping={"reach": path})
        # Un document signé contenant un chemin financier (écrit hors prepare_field_map) est ignoré.
        p = self.tdir / "STATE" / "field_maps" / "search_ads.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": tt.FIELD_MAP_SCHEMA, "mission_id": "M7", "tool": "search_ads",
                                 "mapping": {"id": "id", "reach": "metrics.estimatedSpend"}}, sort_keys=True))
        self.sign(p)
        self.assertIsNone(self.a.field_map("search_ads", "M7"))


class ReplayCompatibilityTests(unittest.TestCase):
    def test_replay_normalization_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = tt.MarketPulseAdapter(Path(tmp) / "TT", mode="REPLAY")
            a.ingest_balance({"requestId": "b1", "credits": {"totalRemaining": 9000}})
            a.record_replay_approval(scope="S", max_credits=50, owner_statement="simulation REPLAY de test")
            r = a.plan_call(mission_id="R", scope="S", recipe_id="A01_S5_AD_SAMPLE_REACH", terms=["poux"])
            self.assertEqual(r["status"], "AWAITING_EXECUTION")
            out = a.ingest_call(r["call_id"], {"requestId": "rq", "credits": {"costUnits": 2}, "data": [
                {"ad_id": "X1", "ad_copy": "poux encore", "landing_url": "https://ex.fr/products/p", "advertiser_name": "Ex",
                 "eu_reach": 500}]})
            ev = out["evidence"][0]
            self.assertEqual((ev["entity_id"], ev["competitor_id"], ev["source_url"]), ("X1", "Ex", "https://ex.fr/products/p"))
            self.assertTrue(ev["synthetic"])
            self.assertNotIn("field_status", ev)
            self.assertIn("price", ev)


if __name__ == "__main__":
    unittest.main()
