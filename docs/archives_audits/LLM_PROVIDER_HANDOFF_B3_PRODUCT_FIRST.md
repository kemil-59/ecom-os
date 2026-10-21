# LLM_PROVIDER — Dernière interaction (B3), priorité: sortir un candidat produit crédible

Lire d'abord `docs/B2_PRODUCT_FIRST_REAUDIT.md`, `docs/B2_RESCUE_AUDIT.md` et `MODULE_MAP.md`.

**Source de vérité : cette B2 amendée, 52 tests réussis en REPLAY.** Ne repartir ni de la B1 ni du bootstrap LLM_Provider interrompu.

Objectif prioritaire B3 : construire le meilleur possible d'un **workflow Agent 01 → Agent 02** permettant, dans LLM_Provider runtime, de chercher, qualifier et auditer des problèmes clients/JTBD et sélectionner un candidat produit réellement défendable, sans inventer de preuves. L'utilisateur NE demande PAS une refonte de Shopify, Meta ou analytics.

Critères de réussite : collecte réelle vérifiable quand un moyen de collecte autorisé existe ; autrement blocage explicite ; registre multi-sources, déduplication personnes et preuves, scores /40 et /25 effectivement contraignants, 8 phases Agent 02, recherche ciblée de contre-exemples, filtre 14Q sourcé, sélection persistante avec historiques, sorties compréhensibles et chemins de reprise de mission. REPLAY n'est pas LIVE ; aucun connecteur payant non autorisé.

L'Agent 03 est seulement conservé et doit rester fonctionnel en REPLAY. Agents 04/05/06 : conserver fichiers, contrats et sécurité, mais **pas de nouvelle fonctionnalité**.

Livraison la plus compacte possible : patch B2→B3 non destructif, tests et procédure d'ouverture Mac/LLM_Provider runtime, pas de pseudo-code ; si pas de shell/ZIP dans l'environnement, le dire honnêtement. Préserver les 142 sources, les 52 tests et les journaux. Les limites LIVE / Mac non testé restent explicites.
