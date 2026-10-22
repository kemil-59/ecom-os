# ⚡ Ecom OS — Open-Source AI E-Commerce Operating System

![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![Tests](https://img.shields.io/badge/Tests-239_Passing-brightgreen.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**Ecom OS** is an open-source, zero-dependency Python framework for evidence-driven e-commerce research, validation, and autonomous workflows.

Instead of relying on unverified LLM outputs or fragmented SaaS plugins, Ecom OS pairs autonomous research agents with a deterministic verification engine. Every LLM proposal is validated against live DOM captures, cryptographic hashes, and strict JSON schemas before execution.

## 🚀 Key Features

* **Evidence-Driven Engine:** Deterministic verification (`web_capture.py`, `scoring.py`) ensuring LLM outputs are backed by verifiable web quotes and data.
* **Contract & Schema Validation:** Strict policy and threshold enforcement via standardized JSON contracts (`contracts/`).
* **Autonomous Research & VOC Mining:** Built-in pipelines for product discovery, competitor intelligence, and persona modeling.
* **Zero-Dependency Core:** Pure Python 3.11+ architecture backed by a comprehensive 239-unit-test suite.

## 🛠️ Architecture Overview

* `/engine` — Core verification engine, autonomous research pipelines, web capture, and scoring modules.
* `/contracts` — JSON schemas, research policies, source registries, and validation thresholds.
* `/research` — Playbooks and target persona definitions.
* `/tests` — Comprehensive unit and integration test suite (239 passing tests).
* `ecom.py` — Unified CLI entrypoint for running pipelines, audits, and test suites (`python3 ecom.py test`).

## ⚡ Quick Start

Run the verification test suite (239 passing tests, zero external dependencies required):

    python3 ecom.py test

Run the autonomous research & validation pipeline:

    python3 ecom.py --help

## 🤝 Contributing & Roadmap

We recently transitioned Ecom OS from an internal production system into a public open-source repository to empower independent developers and merchants.

**Current Roadmap:**
- [ ] Port autonomous agent adapters (`autonomous_research.py`, `downstream_agents.py`) to native OpenAI / Codex tool-calling schemas.
- [ ] Implement automated PR security checks and SSRF/scraper auditing via OpenAI Codex Security.
- [ ] Expand English documentation and community plugin templates.

Pull requests, bug reports, and feature discussions are welcome in the **Issues** tab!

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
