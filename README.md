# ⚡ Ecom OS — Open-Source AI E-Commerce Operating System

![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![Tests](https://img.shields.io/badge/Tests-239_Passing-brightgreen.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**Ecom OS** is an open-source, zero-dependency Python framework for evidence-driven e-commerce research, validation, and autonomous workflows.

Instead of relying on unverified LLM outputs or fragmented SaaS plugins, Ecom OS pairs autonomous research agents with a deterministic verification engine. Every LLM proposal is validated against live DOM captures, cryptographic hashes, and strict JSON schemas before execution.

## 🚀 Key Features

* **Evidence-Driven Engine:** Deterministic verification (`web_capture.py`, `scoring.py`) ensuring LLM outputs are backed by verifiable web quotes and data.
* **Contract & Schema Validation:** Strict policy and threshold enforcement via standardized JSON contracts.
* **Autonomous Research & VOC Mining:** Built-in pipelines for product discovery, competitor intelligence, and persona modeling.
* **Zero-Dependency Core:** Pure Python 3.11+ architecture backed by a comprehensive 239-unit-test suite.

## 🛠️ Architecture Overview

* `/engine` — Core verification engine, autonomous research pipelines, web capture, and scoring modules.
* `/contracts` — JSON schemas, research policies, source registries, and validation thresholds.
* `/research` — Playbooks and target persona definitions.
* `/tests` — Comprehensive unit and integration test suite (239 passing tests).
* `ecom.py` — Unified CLI entrypoint for running pipelines, audits, and test suites (`python3 ecom.py test`).

## ⚡ Quick Start

```bash
# Run the verification test suite (239 tests)
python3 ecom.py test
