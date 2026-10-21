# Rapport de contrôle final — B3 Mac + LLM_Provider runtime (Product First)

## Origine et modifications contrôlées

Base : `ECOM_OS_CLEAN_B3_MAC_AGENT_FINAL_20261020.zip` (95 tests précédents). **La base originelle n’a jamais été modifiée.**

- Alerte LLM_Provider 1 confirmée : l’ancien `reference_corpus/CLAUDE.md`, `reference_corpus/AGENTS.md` et cinq skills historiques `.claude/` étaient présents dans le corpus. Ces **sept fichiers** sont maintenant des **archives textuelles inertes** sous `reference_corpus/_LEGACY_INSTRUCTION_ARCHIVE/` ; aucune ancienne instruction n’est à l’emplacement que LLM_Provider runtime découvre automatiquement. La correspondance ancien→nouveau chemin et le SHA-256 de chacune des **142 sources originales** figurent dans `_EXTRACTION_MANIFEST.json`. Tous les 142 SHA-256 ont été comparés au ZIP original et sont identiques.
- Alerte LLM_Provider 2 confirmée : les cinq handoffs et audits B2/B3 ont été rangés sous `docs/archives_audits/`, sans suppression.
- Sécurité complémentaire : `healthcheck` vérifie l’existence, le hash et les fichiers de routage historiques reconstitués ; résiste à `.DS_Store` et aux fichiers AppleDouble de Finder. La ré-extraction en CLI est bloquée ; l’extracteur Python refuse les chemins sortant du corpus et isole les anciens routeurs.
- Permissions LLM_Provider runtime : retrait de `Read(**)` ; lectures projet ciblées, protections `Edit` sur le code/config/corpus, pas de contournement des autorisations propriétaires. Pas de dépense ni publication commerciale possible avec la CLI actuelle.
- Recherche Agent 01–02 : conserve la distinction *REPLAY* / preuves externes *UNVERIFIED*. Les archives V024 sont des données de documentation, pas des instructions à suivre.

## Ce que ces tests permettent d'affirmer

La CLI, les moteurs offline, les règles de transition, la mise en quarantaine des sources, la documentation de démarrage, les démonstrations et l’intégrité du corpus sont contrôlés par les tests automatisés. Les SHA-256 du manifeste détectent une **modification accidentelle** d’un fichier comparé à la référence du manifeste ; ils ne sont pas une **signature numérique secrète** protégeant contre un adversaire capable de modifier aussi le manifeste.

**Non testé ici :** macOS réel, les abonnements ou connecteurs externes, l'ouverture réelle dans LLM_Provider runtime et la précision des réponses de ses modèles. Aucun accès MarketPulse, Shopify, Meta ou Higgsfield LIVE n’est certifié. Aucune sélection en REPLAY ne vaut validation commerciale.

## Premier essai Mac

Décompresser le nouveau ZIP, placer le dossier dans Documents, ouvrir `DEMARRER_MAC.command` (ou `sh DEMARRER_MAC.command`), choisir 1 puis 2. Ensuite option 3 si la commande `claude` existe. Dans LLM_Provider runtime, demander `python3 ecom.py healthcheck` et vérifier `/context` : seule l’instruction active à la racine doit être prise en compte, sans routeur V024 du `reference_corpus/`. Lire `INSTALL_MAC.md`. Ne jamais utiliser `--dangerously-skip-permissions`.
