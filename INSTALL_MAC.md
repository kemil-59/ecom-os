# ECOM OS CLEAN B3 — Démarrage Mac (Apple Silicon / Intel)

**Version testable en REPLAY (sans frais).** Vérifiée sous Linux/Python 3.13 ; **pas encore exécutée sur un véritable Mac ni dans l'application LLM_Provider runtime**. Une recherche Web via LLM_Provider runtime dépend de ses outils réellement disponibles. Les captures externes sont *non vérifiées par la CLI*, et les décisions commerciales réelles ne sont pas activées.

## Étape 1 — Installer le dossier

1. Double-clique le ZIP `ECOM_OS_CLEAN_B3_MAC_AGENT_AUDITE_20261020.zip` (ne décompresse pas l'ancien ZIP B1/B2).
2. Place le dossier `ECOM_OS_CLEAN_B3_MAC_AGENT_FINAL` dans **Documents**. Il est indépendant de ton ancien projet.
3. Vérifie que tu as **Python 3.11 ou plus** : ouvre Terminal (⌘ Espace → « Terminal »), tape `python3 --version`.
4. Si tu as déjà l'application LLM_Provider avec l'onglet **Code**, ouvre le dossier comme projet. Sinon, pour la version terminal de LLM_Provider runtime, vérifie qu'elle est déjà installée avec `claude --version`. Rien dans ce projet ne l'installe ou ne se connecte à ton compte à ta place.

## Étape 2 — Démarrage le plus facile

Dans Finder, clic droit sur `DEMARRER_MAC.command` → **Ouvrir**. Ce fichier ne télécharge rien et ne publie rien. S'il est bloqué, ne désactive pas Gatekeeper globalement : utilise la méthode Terminal ci-dessous.

Le script propose : **1** Vérifier l'installation, **2** Lancer la démo fictive complète, **3** Démarrer LLM_Provider runtime si `claude` existe, **4** Quitter.

**Méthode Terminal si le double-clic échoue** : dépose le dossier du projet dans la fenêtre Terminal après avoir écrit `cd ` (un espace final), puis Entrée. Ensuite :

```sh
sh DEMARRER_MAC.command
```

## Étape 3 — Première phrase dans LLM_Provider runtime

> Lis le `CLAUDE.md` de ce dossier. Exécute `python3 ecom.py healthcheck`. Puis crée une mission de recherche REPLAY nommée RECHERCHE_01, marché FR, et génère son `research-kit`. Montre-moi tes 8 territoires de problèmes potentiels, les sources à rechercher et les preuves manquantes. Aucun achat ni action payante. Ne traite pas les données fictives de la démo comme des preuves réelles.

Pour tester **la vraie méthodologie sans données inventées**, demande ensuite :

> Recherche des problèmes clients actuels via les outils Web autorisés s'ils sont disponibles, fais examiner les contre-exemples par le sous-agent competitor-scout et conserve les captures non vérifiées dans RESEARCH_INBOX. Rapporte ce que tu as réellement observé et ce qui reste inaccessible. Ne saute aucune étape et ne publie rien.

## Étape 4 — Vérifier le programme à la main

```sh
python3 ecom.py healthcheck
python3 ecom.py test
python3 ecom.py demo-b3
```

`REPLAY_READY` et une suite de tests verte prouvent seulement que **les scénarios vérifiés localement** fonctionnent. Ils ne prouvent ni la fiabilité de tous les sites Web ni l'intégration native de LLM_Provider runtime.

## En cas de blocage

- `python3: command not found` → Python non installé, installation manuelle requise depuis une source officielle.
- `claude: command not found` → la version terminal de LLM_Provider runtime n'est pas installée. Tu peux utiliser l'onglet Code de LLM_Provider Desktop si tu l'as.
- `BLOCKED_CONNECTOR` ou `BLOCKED_SOURCE` → comportement normal : **aucun accès commercial LIVE** n'a été validé.
- Erreur `permission denied` sur le `.command` → utilise `sh DEMARRER_MAC.command`, **pas** `sudo`.
- Les dossiers `runs/` sont des missions locales; aucune suppression automatique de ces dossiers.

## Audit supplémentaire : isolation de la V024

Les 142 sources d'origine sont intégralement conservées **octet par octet**. Les 7 anciens fichiers de pilotage (`CLAUDE.md`, `AGENTS.md`, anciennes skills `.claude`) sont déplacés comme archives en `.txt` sous `reference_corpus/_LEGACY_INSTRUCTION_ARCHIVE/` pour éviter que LLM_Provider runtime charge les anciennes consignes. `healthcheck` vérifie leurs empreintes SHA-256 et bloque le lancement si un ancien routeur redevient actif. Les audits des anciennes étapes sont dans `docs/archives_audits/`.

**Dans LLM_Provider runtime :** ouvre le projet depuis sa racine, demande `python3 ecom.py healthcheck`, puis exécute `/context` pour contrôler les instructions chargées. La présence de règles LLM_Provider runtime issues de ton compte personnel reste possible et n'est pas contrôlée par ce ZIP. Ne désactive jamais les permissions pour contourner un blocage.
