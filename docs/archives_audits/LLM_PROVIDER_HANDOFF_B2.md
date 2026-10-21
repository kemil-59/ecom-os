# B1 → LLM_Provider B2 — État réellement vérifié

Ce dossier est une **base de développement REPLAY**, non une application LIVE certifiée.

## Acquis
- Sources officielles V4 extraites dans reference_corpus (142 fichiers).
- Agent 01, Agent 02, Autopilot : scénario REPLAY exécutable.
- Suite de tests locale lancée avant livraison (voir logs). Aucun connecteur payant actif.

## Correctifs de sécurité supplémentaires avant transmission
- `ecom.py auto authorize` et `auto operational` désactivés au niveau CLI dans B1 : aucune authentification utilisateur réelle disponible.
- `ecom.py auto step` et `a02 audit` bloqués pour les missions `LIVE` en B1.
- Une contre-recherche ne peut plus annoncer `COUNTEREXAMPLE_FOUND` ni une absence sur recherches échantillonnées par des références inexistantes.
- Un nouveau candidat ne peut plus être importé comme déjà sélectionné, ni sauter les gates via `stage`; un registre prétendant une sélection sans décision Gate 7 cohérente est bloqué.
- Permissions LLM_Provider runtime en liste d'autorisation restreinte, sans prétendre garantir l'isolation d'un interpréteur Python général.

## Obligations pour B2
- Implémenter des autorisations humaines prouvables, indépendantes des sorties LLM_Provider, avec confirmation réellement interactive avant tout connecteur payant.
- Réserver un mode `LIVE` aux collectes avec reçus de connecteurs et vérifications des sources; jamais débloquer par `--allow-synthetic`.
- Audits Agent 02 : vérifier la pertinence du contre-exemple *sur la caractéristique précise*, pas seulement l'existence de l'URL; contrôler origine VoC vs SHOP et la vérité des observations.
- Corriger le contrôle de scoring et les transitions sur les chemins d'import non canoniques; empêcher l'utilisateur d'injecter `stage` ou `PASS` en entrée pour sauter les gates.
- Ajouter des tests indépendants pour chaque nouvelle intégration et ne pas annoncer fonctionnels les connecteurs sans essai.
- Relier mission, candidat, solution, preuve et version d'audit; checksums simples ≠ signatures de confiance.
- Aucun remboursement, achat, dépense ou publication.

Ne modifie pas l'ancien dépôt ECOM OS. Ce paquet est uniquement une entrée de développement pour construire B2 dans un **nouveau dossier**.
