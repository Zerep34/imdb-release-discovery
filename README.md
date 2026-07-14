# Sorties de la semaine → Telegram

Récupère les **sorties de la semaine** (films, séries, animation) au **cinéma** et sur les
**plateformes de streaming** via l'API **TMDB**, puis publie un récapitulatif formaté sur un
**canal Telegram**. Cross-platform : Windows, macOS, Linux.

## Avant de commencer (prérequis)

1. **Clé API TMDB** : compte sur [themoviedb.org](https://www.themoviedb.org) → *Paramètres*
   → *API* → clé **v3**. Doc : https://developer.themoviedb.org/reference/intro/getting-started
2. **Bot Telegram** : parler à [`@BotFather`](https://t.me/BotFather) → `/newbot` → récupérer
   le **token** (`123456:AA...`).
3. **Canal Telegram** : créer le canal, y ajouter le bot **comme administrateur**.
4. **chat_id du canal** :
   - canal **public** : `@nom_du_canal`
   - canal **privé** : l'ID numérique `-100…`, obtenu via `getUpdates`
     (`https://api.telegram.org/bot<token>/getUpdates` après avoir posté dans le canal) ou via
     un bot comme `@userinfobot`.

## Installation

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
cp config.example.json config.json   # puis éditer config.json
```

Les secrets peuvent aussi être fournis par variables d'environnement (prioritaires sur le
fichier) : `TMDB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OMDB_API_KEY`.
Ne **committez jamais** `config.json`.

## Sécurité des clés

Ce dépôt ne contient **aucune clé** : le code lit tout depuis `config.json` (ignoré par git via
`.gitignore`) ou les variables d'environnement. Seul `config.example.json` est versionné, avec
des valeurs factices.

- Copie `config.example.json` → `config.json` et mets-y **tes** clés (jamais dans le code).
- Ou exporte les variables d'environnement (idéal en CI / conteneur).
- Vérifie avant de pousser : `git status` ne doit **pas** lister `config.json`.

Aucune clé n'est écrite dans les logs.

## Configuration (`config.json`)

| Clé | Description |
|-----|-------------|
| `tmdb_api_key` | Clé API TMDB v3 |
| `telegram_bot_token` | Token du bot |
| `telegram_chat_id` | `@canal` ou ID numérique `-100…` |
| `omdb_api_key` | Clé [OMDb](https://www.omdbapi.com/apikey.aspx) pour afficher la note **Rotten Tomatoes** (🍅). `""` = désactivé |
| `language` | Langue TMDB, ex. `fr-FR` |
| `regions` | Codes ISO 3166-1 (`["FR","US"]`). Vide ou `["ALL"]` = monde |
| `platforms` | Noms de plateformes, résolus **dynamiquement** en IDs TMDB par région |
| `include_cinema` | Inclure les sorties cinéma |
| `include_returning_seasons` | `true` = inclut les **nouvelles saisons** (S2/S3…) en plus des nouvelles séries. Détecte les premières de saison via `/tv/{id}` (1 appel par candidat en cours de diffusion, appels parallélisés) et annote le titre « — Saison N ». `false` = uniquement les nouvelles séries (S1) |
| `categories` | Sous-ensemble de `films`, `series`, `animation`, `animation_series` |
| `min_vote_count` | Nombre de votes minimum (peu utile pour des sorties fraîches, souvent 0 vote) |
| `min_popularity` | Popularité TMDB minimum — écarte les petites productions / films confidentiels. `0` = tout garder ; ~10 filtre l'essentiel du bruit, montez si trop de titres |
| `max_items_per_section` | Nombre max de titres par section |
| `max_pages` | Pages TMDB récupérées par requête (défaut 2) |
| `style` | Rendu du message : `card` = un message par sortie avec **carte de lien** (affiche + note + genres, dépliée par Telegram depuis IMDb/TMDB) ; `text` = un récap texte groupé |
| `trailers` | `true` = ajoute un bouton **🎞 BA** (trailer YouTube via TMDB). 1 appel TMDB/titre. `false` = désactivé |
| `cinema_label` | Texte du bouton séances (ex. `Séances`, `UGC`, `Mon ciné`) |
| `cinema_search_url` | Gabarit d'URL de recherche séances pour les **sorties ciné** ; `{query}` = titre encodé. Défaut Allociné. `""` = pas de bouton. Ex. UGC : `https://www.ugc.fr/rechercher.html?q={query}` |
| `radarr_url` | URL de ton Radarr (ex. `http://localhost:7878`). Ajoute un lien **➕ Radarr** aux films, ouvrant la page d'ajout préremplie. `""` = pas de lien |
| `sonarr_url` | URL de ton Sonarr (ex. `http://localhost:8989`). Ajoute un lien **➕ Sonarr** aux séries. `""` = pas de lien |
| `use_history` | `true` = mémorise les titres postés et ne les reposte pas (anti-répétition d'une semaine sur l'autre) |
| `history_file` | Chemin du fichier d'état JSON (défaut `sent_history.json`, ignoré par git) |
| `timezone` | Fuseau pour le calcul de la semaine, ex. `Europe/Paris` |

## Utilisation

```bash
python releases_to_telegram.py --check       # valide TMDB + Telegram, puis quitte
python releases_to_telegram.py --dry-run      # affiche le message SANS envoyer
python releases_to_telegram.py                # envoie sur le canal
```

Options :

| Option | Rôle |
|--------|------|
| `--config PATH` | Fichier de config (défaut `./config.json`) |
| `--regions FR,US` | Surcharge les régions |
| `--platforms "Netflix,Max"` | Surcharge les plateformes |
| `--week current\|next\|last` | Fenêtre visée (défaut `current`) |
| `--text` | Force le mode texte groupé (au lieu du mode carte) |
| `--ignore-history` | Ne filtre pas les titres déjà postés (backfill / test) |
| `--dry-run` | Affiche sans envoyer |
| `--check` | Valide les identifiants puis quitte (code ≠ 0 si invalide) |
| `--verbose` | Logs détaillés |

**Codes de sortie** : `0` succès · `1` erreur de config/identifiants · `2` erreur réseau/API.

## Semaine ciblée

Fenêtre de **7 jours ancrée sur `week_start_day`** (`weekday()` : lundi=0 … mercredi=2 …
dimanche=6), dans le fuseau `timezone`. Défaut **mercredi=2** → fenêtre **mercredi → mardi**
(jour des sorties ciné en France). `current` = fenêtre du jour, `next`/`last` = suivante/précédente.
Mettre `"week_start_day": 0` pour un classique lundi → dimanche.

## Notes Rotten Tomatoes (🍅)

TMDB n'expose **pas** Rotten Tomatoes. On passe par **OMDb** : clé gratuite sur
[omdbapi.com](https://www.omdbapi.com/apikey.aspx) → `omdb_api_key` (ou env `OMDB_API_KEY`).
Chaîne : TMDB `external_ids` → `imdb_id` → OMDb → Tomatometer.

Limites : le score n'apparaît que si OMDb l'a (surtout **films**, peu de **séries**), et les
sorties très fraîches n'ont souvent **pas encore** de note RT. Coût : 1 requête OMDb par titre
(gratuit = 1000/jour). Sans clé, le 🍅 est simplement omis.

## Liens Radarr / Sonarr

Renseigne `radarr_url` et/ou `sonarr_url`. Chaque titre du message porte alors un lien
**➕ Radarr** (films) ou **➕ Sonarr** (séries) qui ouvre la **page d'ajout préremplie** de
ton instance — aucun ajout automatique, tu valides d'un clic.

- Films → `<radarr_url>/add/new?term=tmdb:<id>` (correspondance exacte par tmdbId).
- Séries → `<sonarr_url>/add/new?term=tvdb:<id>` ; Sonarr indexant par **tvdbId**, l'ID est
  résolu via TMDB `/tv/<id>/external_ids`. Sans tvdbId trouvé, repli sur une recherche par titre.

Les URLs doivent être **joignables depuis l'appareil qui ouvre Telegram** (ex. `localhost`
ne marche que si tu cliques depuis la machine qui héberge *arr ; sinon mets l'IP/DNS).

## ⚠️ Limite connue (streaming)

TMDB **n'expose pas** la date exacte d'ajout d'un titre sur une plateforme. On approxime
« nouveau sur le streaming cette semaine » par la **date de sortie / première diffusion**
filtrée sur la fenêtre, combinée à la **disponibilité** du titre sur la plateforme
(`with_watch_providers`, `flatrate`). C'est le meilleur proxy gratuit — à accepter comme tel.
Les IDs de plateformes varient selon la région et sont donc résolus dynamiquement (jamais
codés en dur).

## Planification

L'outil ne s'auto-planifie pas. Exemples cross-platform ci-dessous.

### macOS / Linux — `cron`

```cron
# Chaque lundi à 08:00
0 8 * * 1 cd /chemin/sorties-telegram && /usr/bin/python3 releases_to_telegram.py >> run.log 2>&1
```

### macOS — `launchd` (alternative propre)

Fichier `~/Library/LaunchAgents/com.sorties.telegram.plist` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sorties.telegram</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/chemin/sorties-telegram/releases_to_telegram.py</string>
  </array>
  <key>WorkingDirectory</key><string>/chemin/sorties-telegram</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/chemin/sorties-telegram/run.log</string>
  <key>StandardErrorPath</key><string>/chemin/sorties-telegram/run.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.sorties.telegram.plist
```

### Windows — Planificateur de tâches (`schtasks`)

```bat
schtasks /Create /SC WEEKLY /D MON /ST 08:00 /TN "SortiesTelegram" ^
  /TR "python C:\chemin\sorties-telegram\releases_to_telegram.py"
```

Option : un `.bat` d'amorçage qui active le venv puis lance le script —
`run.bat` :

```bat
@echo off
cd /d C:\chemin\sorties-telegram
call .venv\Scripts\activate
python releases_to_telegram.py >> run.log 2>&1
```

Puis pointer `schtasks ... /TR "C:\chemin\sorties-telegram\run.bat"`.

## Tests

```bash
python -m pytest -q          # ou : python -m unittest discover -s tests
```

Tests sans réseau : fenêtre de dates, dédoublonnage, formatage (classement genre 16,
découpage < 4096 sans casser de balise HTML, cas « aucune sortie »).

## Structure

```
releases_to_telegram.py   point d'entrée CLI + fenêtre de dates + dédoublonnage
tmdb.py                   client TMDB (requêtes, retries, parsing)
telegram_client.py        envoi Telegram (retries, 429)
formatter.py              classement + mise en forme HTML découpée
```
