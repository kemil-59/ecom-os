# Audit de récupération B3 — 20 octobre 2026

## Pourquoi LLM_Provider B3 ne pouvait pas être exécuté

Le fichier `Texte collé(20261019-163052).txt` s'arrête au milieu d'une instruction Python dans `engine/product_research_pipeline.py`, dans `generate_decision_dossier`. Il manque la fermeture de l'affectation, le reste du générateur, le CLI, les tests supplémentaires, l'installation Mac et le ZIP. En outre, le `engine/autopilot.py` fourni contient une faute de guillemet dans une branche « if False » qui **rend tout le fichier non importable**.

Après réparation de cette simple faute dans une copie isolée, la suite historique de 52 tests a fait apparaître 5 échecs et 2 erreurs : un défaut de rétrocompatibilité de la démonstration produit et des dépendances à la sélection à l'Agent 03. La réponse de LLM_Provider revendiquait 70 tests, mais aucun de ces 18 tests supplémentaires n'a été livré dans l'extrait reçu. **Nous ne présentons pas cette affirmation comme vérifiée.**

## Stratégie et changements retenus

- Source de vérité préservée : `ECOM_OS_CLEAN_B2_PRODUCT_FIRST_VERIFIE_20261019.zip`.
- Reprise ciblée de quatre modules B3 reçus entièrement : `engine/ledger.py`, `engine/scoring.py`, `engine/competitor_intel.py`, `engine/autopilot.py`.
- Correction de la syntaxe de `autopilot.py`.
- Renforcement du filtre produit 14Q : les références ne peuvent pas être auto-déclarées à partir du dossier ; un ensemble de références vide ne valide pas de YES.
- Conservation de la démo B1/B2 sous exception REPLAY **accessible seulement au fixture Python interne** (la CLI `auto step` ne peut pas désactiver le filtrage 14Q).
- Interdiction même en REPLAY de traiter un enregistrement `SHOP` comme preuve `VoC REVIEW`, correction de la seule fixture synthétique correspondante.
- Reconstruction indépendante et limitée de `engine/product_research_pipeline.py` (validation structurelle GATE 2A / GATE 3, demande de collecte Web `BLOCKED_SOURCE`, dossier de décision, healthcheck).
- Ajout de la CLI `healthcheck`, `a01 territories`, `a01 jtbd`, `a01 collect-web`, `decision-dossier`, `demo-b3`.
- Ajout de `INSTALL_MAC.md` et `DEMARRER_MAC.command` : démarrage manuel guidé, **non validé sur macOS**.
- 18 tests indépendants ajoutés. 70 tests en tout, réellement exécutés sous Linux/Python 3.13.5.

## Limitations IMPORTANTES

1. **REPLAY uniquement.** Le programme ne collecte pas de vrais avis clients et ne consulte pas MarketPulse LIVE ; les outils utilisés pour la démo sont fictifs et les sources n'ont pas été vérifiées commercialement.
2. Les sommes et preuves fictives dans la démo sont des scénarios techniques, et non des signaux de marché.
3. Le dossier de décision est un brouillon explicitement non commercial.
4. Les checksums JSON n'authentifient pas une personne ou une API. Le moteur ne protège pas contre quelqu'un qui a le droit de modifier arbitrairement tous les fichiers du système.
5. Les sources documentaires existent en 142 fichiers, mais toutes les méthodes ne sont pas encore traduites en automatismes.
6. Le chemin Python et le script `.command` n'ont **pas** été testés sur un appareil macOS réel.
7. Les Agents 04–06 sont préservés à titre de brouillons, sans ajout ni connexion LIVE.

Le ZIP final permet de poursuivre de façon plus sûre que la réponse LLM_Provider incomplète, sans prétendre fournir un moteur de sélection de produit réel et autonome prêt à investir.
