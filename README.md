# Ecom OS

**An open-source Python framework for evidence-driven e-commerce product research, validation contracts and AI-agent orchestration.**

Ecom OS turns an LLM agent into a disciplined research analyst. The agent explores the public web and *proposes* findings. A deterministic Python engine then downloads every page itself, checks each quote against the page, scores the depth of the evidence and decides, through explicit gates, whether a consumer problem is a real market opportunity. Opinions from the LLM never count as proof.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Why Ecom OS?

Most AI "product research" tools summarize search results and present them as validation. Ecom OS works the other way round:

- **Untrusted proposer, trusted verifier.** The LLM writes proposal files. Only the engine records evidence, after a verified fetch with a SHA-256 receipt.
- **Generic, any-site verification.** There is no domain whitelist. The engine enforces:
  - HTTPS and a public host (DNS check);
  - `robots.txt` compliance;
  - no cross-host redirects;
  - login and CAPTCHA detection, never bypassed.
- **Strict evidence classes.** Direct observation, platform-reported data, third-party estimates, index counts, inferences and simulated data are always kept apart.
- **Multi-source accumulation.** A micro-problem only advances when distinct authors, independent domains and varied source types converge.
- **Adversarial counter-research.** A dedicated competitor agent tries to *refute* every opportunity. It looks at existing solutions, positive and negative reviews, substitutes and public prices.
- **Zero runtime dependencies.** The engine uses the Python standard library only.

## Architecture

```
ecom.py                 CLI entry point (missions, research cycles, agents, gates, tests)
engine/                 Deterministic decision engine
contracts/              Machine-readable policies, thresholds and schemas
research/               Research playbook and persona profiles
.claude/                Agent-runtime configuration: rules, skills, sub-agents, hooks
docs/                   Methodology, security model, integration notes
tests/                  unittest suite
```

### `engine/` — key modules

| Module | Responsibility |
|---|---|
| `web_capture.py` | Fetches and verifies any public page. Quote matching, author binding (`ADAPTER` / `STRUCTURED` / `UNBOUND`), contribution-nature detection (user content vs. editorial), price/offer observation, study verification. |
| `discovery.py` | Registry of discovered sources. Diversity rules for research territories (gate 2A). |
| `ledger.py` | Append-only evidence ledger with author/text deduplication. |
| `demand.py` | Ten depth indicators. Testimony and statistic evidence paths. Evidence levels from `HYPOTHESIS_DETECTED` up to `SIGNIFICANT_MARKET_DOCUMENTED`. |
| `research_depth.py` | Micro-problem signatures, multi-source accumulation, saturation tracking, final verdicts. |
| `persona.py` | Segments discovered from verified self-declarations (default), or an imposed profile on request. |
| `geo_fr.py` | French geography resolution (departments, regions, cities). |
| `competitor_intel.py` | Competitor agent: counter-research requirements, public prices, verdicts such as `ALREADY_WELL_SOLVED` or `DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN`. |
| `scoring.py` | 8-axis /40 score, each axis capped by the evidence level. |
| `autonomous_research.py` | One research cycle: process proposals, run gates, build reports and adaptive work orders. |
| `autopilot.py` | Candidate state machine across gates. |
| `marketpulse_adapter.py`, `tt_live_guard.py` | Optional market-intelligence connector. Read-only, budget-governed, signature-gated, enforced by agent-runtime hooks. |
| `creative_center.py`, `downstream_agents.py` | Creative-brief and downstream agents (offline / simulation only). |

### `contracts/`

| File | Content |
|---|---|
| `research_policy.json` | What may and may not be collected. |
| `research_thresholds.json` | Calibration parameters for every evidence level and gate. |
| `schemas.json` | Data contracts for proposals and records. |
| `source_registry.json` | Optional high-confidence site adapters. They are never a whitelist. |

## Evidence levels

| Level | Minimum (default calibration) |
|---|---|
| `HYPOTHESIS_DETECTED` | First verified signal |
| `PROBLEM_CORROBORATED` | ≥ 10 distinct testimonies, ≥ 3 domains, ≥ 2 source types |
| `BROAD_DISTRIBUTED_SIGNAL` | ≥ 100 testimonies, ≥ 10 domains, dominant domain ≤ 35 %, ≥ 2 years |
| `SIGNIFICANT_MARKET_DOCUMENTED` | ≥ 1,000 testimonies with national scale demonstrated, **or** an engine-verified national statistic plus ≥ 10 locally anchored testimonies |
| `COMMERCIAL_OPPORTUNITY_TO_DEEPEN` | Significant market **and** plausible differentiation after full counter-research |

`INSUFFICIENT_EVIDENCE` and `REJECTED` are first-class outcomes. All thresholds live in `contracts/research_thresholds.json`.

## Quick start

Requirements: **Python 3.11+**. Nothing else to install.

```bash
git clone https://github.com/<your-account>/ecom-os.git
cd ecom-os

# Fictional end-to-end demo (offline, REPLAY mode)
python3 ecom.py demo-b3

# Run the test suite
python3 ecom.py test
```

The suite contains **239 tests** (standard `unittest`). A few tests need optional local assets: a document corpus, a knowledge index, recorded page fixtures or an owner key. These tests are skipped automatically when the assets are absent, and become active as soon as they are present. On a fresh clone, the expected result is `OK` with these skips reported.

### Run a real research mission

```bash
python3 ecom.py init --mission MY_MISSION --market FR --mode FETCH_VERIFIED
# The LLM agent writes proposal files into runs/MY_MISSION/RESEARCH_INBOX/proposals/
python3 ecom.py research run --mission MY_MISSION
```

Each cycle produces the following files under `runs/MY_MISSION/`:
- `AGENT_01/STAGE12_DEPTH_REPORT.json` — depth indicators, segments, counter-research and verdicts;
- `AGENT_02/competitive_dossier.json` — the competitor agent's dossier;
- `RESEARCH_INBOX/WORK_ORDERS.json` — adaptive work orders for the next cycle.

The research method is described in `research/RESEARCH_PLAYBOOK.md` and `docs/METHODE_RECHERCHE_ACTIVE.md` (French).

### Useful commands

| Command | Purpose |
|---|---|
| `python3 ecom.py healthcheck` | Environment and configuration checks |
| `python3 ecom.py research run --mission ID` | One autonomous research cycle |
| `python3 ecom.py decision-dossier --mission ID` | Consolidated decision dossier |
| `python3 ecom.py auto status --mission ID` | Candidate state machine status |
| `python3 ecom.py sources` | Optional site adapters |
| `python3 ecom.py --help` | Full command list |

## Configuration

Copy `.env.example` to `.env` if you use the optional market-intelligence connector. `ECOM_OWNER_PUBKEY_SHA256` holds the fingerprint of *your own* owner public key. When it is empty, every live call is refused.

## Safety model

- No purchase, publication, supplier contact or paid call without an explicit, independent authorization chain.
- Paid connectors are read-only. They are capped by a signed, time-limited budget and guarded by pre- and post-call hooks.
- Simulated (REPLAY) data can never validate a real mission.
- The engine never infers sales, revenue or ad performance from proxy counts.

## Contributing

Issues and pull requests are welcome. Please run `python3 ecom.py test` before submitting, and keep the engine dependency-free.

## License

[MIT](LICENSE)
