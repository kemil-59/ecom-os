"""Aide de test : GATE 2A valide selon la méthode active (territoires issus de la DISCOVERY, secteurs variés).

Les URL sont des sources découvertes FICTIVES enregistrées comme DISCOVERED_UNVERIFIED : elles ne constituent jamais
une preuve et servent seulement à franchir la porte structurelle pour tester la logique en aval.
"""
import json
from pathlib import Path

from engine import discovery

SECTORS = ["beaute_soin", "sante_bien_etre", "maison_entretien", "cuisine_alimentation", "animaux",
           "voyages_mobilite", "sport_activite", "organisation_rangement"]
DOMAINS = ["exemple-forum-a.fr", "exemple-avis-b.fr", "exemple-blog-c.fr", "exemple-qr-d.fr", "exemple-commu-e.fr"]


def seed_gate2a(mdir: Path, n: int = 8) -> list[dict]:
    mdir = Path(mdir)
    terr = []
    for i in range(1, n + 1):
        url = f"https://www.{DOMAINS[i % len(DOMAINS)]}/discussion-{i}"
        terr.append({"territory_id": f"T{i}", "name": f"Territoire synthétique {i}", "sector": SECTORS[(i - 1) % len(SECTORS)],
                     "micro_contexts": [{"moment": "soir", "place": "domicile", "friction": "friction synthétique"}],
                     "discovery_refs": [url]})
        discovery.record(mdir, [{"url": url, "territory_id": f"T{i}", "signal": "synthétique"}], "TEST_FIXTURE")
    pr = mdir / "RESEARCH_INBOX" / "proposals"
    pr.mkdir(parents=True, exist_ok=True)
    (pr / "territories.json").write_text(json.dumps(terr), encoding="utf-8")
    return terr
