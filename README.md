# AngleBot

Bot Discord (discord.py) qui surveille des **flux RSS/Atom** et des **comptes sociaux**
(Bluesky, TikTok, Instagram, X, Threads, YouTube) et publie une notification dans un
salon des qu'une nouvelle publication apparait. Il attribue aussi un role automatique
aux nouveaux membres.

- Token lu depuis un fichier `.env`
- Sources declarees dans `config.yaml` **ou** ajoutees a chaud avec `/veille ajouter`
- Deduplication persistee sur disque : aucun doublon, meme apres un redemarrage
- Un salon et un role a mentionner configurables par source
- Embeds riches (titre, extrait, image, auteur, date, compteurs Bluesky)
- Role donne automatiquement a chaque arrivee, avec diagnostic integre

## 1. Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # puis renseignez le token
```

## 2. Creer le bot Discord

1. <https://discord.com/developers/applications> → **New Application**
2. Onglet **Bot** → **Reset Token** → copiez-le dans `.env` (`DISCORD_TOKEN=...`)
3. Onglet **Bot → Privileged Gateway Intents** : activez **Server Members Intent**
   (obligatoire pour le role automatique ; inutile si vous desactivez `autorole`).
4. Onglet **OAuth2 → URL Generator** : scopes `bot` + `applications.commands`,
   permissions `Send Messages`, `Embed Links`, `Read Message History`
   et `Manage Roles` (pour le role automatique).
   Ouvrez l'URL generee pour inviter le bot.
5. Recuperez l'ID du salon de notification (clic droit sur le salon → *Copier l'identifiant*,
   le mode developpeur doit etre actif) et mettez-le dans `DISCORD_DEFAULT_CHANNEL_ID`.

`DISCORD_GUILD_ID` est optionnel : renseigne, les commandes `/` apparaissent
instantanement sur ce serveur (sinon la propagation globale peut prendre ~1 h).

## 3. Declarer les sources

Dans `config.yaml` :

```yaml
poll_interval_seconds: 300      # frequence de verification
announce_on_first_run: false    # true = notifier aussi les publications deja en ligne
max_items_per_check: 5          # anti-spam si une source publie en rafale

sources:
  - name: "Blog de l'equipe"
    type: rss
    url: "https://example.com/feed.xml"
    channel_id: 123456789012345678   # optionnel (defaut : salon global)
    role_id: 123456789012345678      # optionnel : role mentionne

  - name: "Bluesky - @moi"
    type: bluesky
    handle: "moi.bsky.social"
    include_reposts: false           # optionnel
    include_replies: false           # optionnel

  - type: tiktok
    handle: "monpseudo"

  - type: youtube
    channel: "UCxxxxxxxxxxxxxxxxxxxxxx"   # identifiant de chaine (commence par UC)
```

| `type` | Champ attendu | Methode de collecte |
| --- | --- | --- |
| `rss` / `atom` | `url` | requete HTTP directe |
| `bluesky` | `handle` | API publique `public.api.bsky.app` (sans compte) |
| `youtube` | `channel` (`UC...`), `playlist` ou `url` | flux Atom officiel YouTube |
| `tiktok` | `handle` | passerelle RSSHub |
| `instagram` | `handle` | passerelle RSSHub |
| `threads` | `handle` | passerelle RSSHub |
| `x` / `twitter` | `handle` | passerelle RSSHub (cookies de session requis) |

Toute source accepte aussi `url:` directement, ce qui court-circuite la passerelle
(pratique pour un RSS-Bridge ou n'importe quel autre pont).

Puis :

```bash
python main.py
```

## 4. Commandes Discord

| Commande | Effet |
| --- | --- |
| `/veille liste` | sources surveillees, salon cible, derniere erreur |
| `/veille ajouter type: cible: [salon] [role] [nom]` | teste puis enregistre une source |
| `/veille retirer source:` | retire une source ajoutee via Discord (autocompletion) |
| `/veille verifier` | force une verification immediate |
| `/veille apercu type: cible:` | teste une source et affiche sa derniere publication sans rien enregistrer |
| `/veille etat` | sources, erreurs, latence, prochaine verification |

`ajouter`, `retirer` et `verifier` sont reservees aux membres ayant **Gerer le serveur**
(ajustable dans *Parametres du serveur → Integrations*). Les reponses sont ephemeres.

Une source ajoutee via `/veille ajouter` est enregistree dans `data/subscriptions.json`
et prise en compte immediatement, sans redemarrage. Ses publications deja en ligne sont
memorisees comme point de reference : seules les suivantes declenchent une notification.

## 5. Role automatique des nouveaux membres

```yaml
autorole:
  enabled: true
  role_id: 1546623728895656016
  include_bots: false        # true = les bots qui rejoignent recoivent aussi le role
  wait_for_screening: true   # attendre la validation du reglement avant d'attribuer
```

Trois conditions, toutes verifiables d'un coup avec `/autorole etat` :

1. **Server Members Intent** actif dans le portail developpeur (sinon le bot refuse de
   demarrer avec un message explicite) ;
2. permission **Gerer les roles** accordee au bot ;
3. le role du bot **au-dessus** du role attribue dans *Parametres du serveur → Roles*.
   C'est l'oubli le plus frequent : Discord interdit d'attribuer un role situe au meme
   niveau ou plus haut que le sien.

Un role gere par une integration (booster Nitro, role d'un autre bot) et `@everyone` ne
sont pas attribuables : Discord le refuse, `/autorole etat` le signale.

Si votre serveur utilise l'**ecran de regles**, le role est pose au moment ou le membre
valide le reglement, pas a la connexion (`wait_for_screening: true`). Mettez `false` pour
l'attribuer immediatement, avant validation.

| Commande | Effet |
| --- | --- |
| `/autorole etat` | role vise, intent, hierarchie, permissions, compteur d'attributions |
| `/autorole rattraper` | donne le role aux membres deja presents qui ne l'ont pas (500 max par appel) |

Reservees aux membres ayant **Gerer les roles**. Un membre arrive pendant que le bot est
hors ligne ne recoit rien : `/autorole rattraper` sert a combler ces trous.

## 6. A savoir sur les plateformes

RSS, Bluesky et YouTube fonctionnent sans dependance externe.

**TikTok, Instagram, X et Threads n'offrent aucune API publique gratuite** pour suivre un
compte. AngleBot passe donc par une passerelle qui convertit le profil en flux RSS, par
defaut l'instance publique [RSSHub](https://docs.rsshub.app). Consequences :

- l'instance publique `rsshub.app` est souvent saturee ou limitee (erreurs 429/503) ;
- Instagram et X exigent generalement des cookies de session cote passerelle ;
- ces plateformes changent regulierement leurs protections : une route peut cesser de
  fonctionner du jour au lendemain.

Pour une veille fiable sur ces reseaux, utilisez le `docker-compose.yml` fourni : il
lance **votre propre instance RSSHub** et y branche le bot automatiquement
(voir [section 7](#7-deploiement-docker-bot--rsshub)). Sinon, fournissez `url:` avec
le pont de votre choix. `/veille apercu` verifie immediatement si une route repond.

## 7. Deploiement Docker (bot + RSSHub)

`docker-compose.yml` demarre trois services : le bot, une instance **RSSHub privee**
et le Redis qui lui sert de cache. Le bot vise `http://rsshub:1200` par le reseau
interne — la valeur `RSSHUB_BASE` du `.env` est ecrasee, vous n'avez rien a y changer.
C'est ce qui debloque TikTok, Instagram, X et Threads, que l'instance publique refuse.

```bash
cp .env.example .env          # token, salon, role : a renseigner
cp rsshub.env.example rsshub.env   # optionnel : identifiants Instagram / X
docker compose up -d
docker compose logs -f anglebot
```

- `config.yaml` est monte en lecture seule : modifiez-le puis
  `docker compose restart anglebot`, sans reconstruire l'image.
- L'etat de deduplication vit dans le volume `anglebot-data`. Le conserver evite de
  re-annoncer d'anciennes publications ; l'inspecter :
  `docker compose exec anglebot cat /app/data/state.json`.
- RSSHub n'ecoute que sur `127.0.0.1:1200`, pour tester une route a la main :
  `curl "http://127.0.0.1:1200/tiktok/user/@anglegauche"`.
- L'image `chromium-bundled` embarque le navigateur exige par TikTok et Threads :
  comptez ~1,5 Go de RAM pour l'ensemble.
- Instagram et X demandent en plus des identifiants dans `rsshub.env`
  (`IG_USERNAME`/`IG_PASSWORD` ou `IG_COOKIE`, et `TWITTER_AUTH_TOKEN`) — noms
  verifies dans les sources de RSSHub, voir <https://docs.rsshub.app/deploy/config>.

Verification hors ligne avant de deployer, sans contacter Discord :

```bash
python scripts/smoke_test.py
```

## 8. Mise a jour automatique (GitHub Actions)

`.github/workflows/deploy.yml` enchaine trois etapes a chaque push sur `main` :
verification hors ligne, construction de l'image sur `ghcr.io`, puis deploiement SSH
qui envoie `docker-compose.yml` et relance les conteneurs sur le tag du commit.

A configurer une fois dans *Settings → Secrets and variables → Actions* :

| Nom | Type | Role |
| --- | --- | --- |
| `SSH_HOST` | secret | adresse du serveur |
| `SSH_USER` | secret | utilisateur SSH, membre du groupe `docker` |
| `SSH_KEY` | secret | cle privee de deploiement (`ssh-keygen -t ed25519`) |
| `SSH_KNOWN_HOSTS` | secret | sortie de `ssh-keyscan votre-serveur` — sans lui, la cle du serveur est acceptee sans verification |
| `SSH_PORT` | variable | port SSH, defaut `22` |
| `DEPLOY_DIR` | variable | dossier sur le serveur, defaut `/opt/anglebot` |
| `DEPLOY_ENABLED` | variable | **`true` pour activer le deploiement**. Absente, le workflow se contente de verifier et de construire l'image |

Cote serveur, une seule preparation manuelle :

```bash
sudo mkdir -p /opt/anglebot && cd /opt/anglebot
# .env et config.yaml sont la propriete du serveur : le workflow n'y touche jamais
sudo nano .env          # token, salon, role
sudo nano config.yaml   # sources surveillees
```

Le deploiement echoue volontairement si `.env` est absent du dossier, plutot que de
demarrer un bot sans token.

L'image publiee sur GHCR est **privee par defaut**. Rendez le package public
(*Package settings → Change visibility*), ou authentifiez le serveur une fois :

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u ItsKandar --password-stdin
```

Revenir en arriere apres un deploiement casse :

```bash
cd /opt/anglebot
ANGLEBOT_TAG=<sha-du-commit-precedent> docker compose up -d
```

Un `docker compose up -d` sans `ANGLEBOT_TAG` reprend le tag `latest`, c'est-a-dire
le dernier build de `main`.

## 9. Fonctionnement interne

```
main.py               point d'entree : .env, logs, demarrage
anglebot/config.py    lecture .env + config.yaml (les ${VAR} y sont resolus)
anglebot/bot.py       client Discord, boucle de veille, publication
anglebot/commands.py  commandes /veille
anglebot/autorole.py  role automatique des nouveaux membres + /autorole
anglebot/notifier.py  construction des embeds et envoi
anglebot/state.py     data/state.json : identifiants deja annonces (250 par source)
anglebot/subscriptions.py  data/subscriptions.json : sources ajoutees via Discord
anglebot/sources/     rss.py, bluesky.py, social.py + registre des types
scripts/smoke_test.py verification hors ligne (config, cogs, commandes)
Dockerfile            image du bot (python:3.14-slim, utilisateur non-root)
docker-compose.yml    bot + RSSHub privee + Redis
```

Les publications sont reclassees par date avant traitement : certains flux (dont des
CMS maison) listent leurs entrees du plus ancien au plus recent, ce qui inverserait
l'ordre des notifications. Faute de dates dans le flux, l'ordre d'origine est conserve.

Les sources sont interrogees 5 par 5, chaque envoi est espace d'une seconde, et une
source en panne est journalisee sans interrompre les autres. Les nouveautes sont publiees
du plus ancien au plus recent ; au-dela de `max_items_per_check`, le surplus le plus
ancien est marque comme vu sans notification.

Supprimer `data/state.json` remet la deduplication a zero : au prochain demarrage, tout
est re-memorise silencieusement (ou re-annonce si `announce_on_first_run: true`).

## 10. Depannage

| Symptome | Piste |
| --- | --- |
| `Token Discord refuse` | token invalide ou regenere : recopiez-le dans `.env` |
| Les commandes `/` n'apparaissent pas | scope `applications.commands` manquant a l'invitation, ou attendez la propagation / renseignez `DISCORD_GUILD_ID` |
| `Salon ... introuvable` | mauvais ID, ou le bot ne voit pas le salon |
| `Droits insuffisants` | accordez *Envoyer des messages* et *Integrer des liens* au bot dans ce salon |
| `HTTP 403/404` sur TikTok/Instagram/X | l'instance publique refuse ces routes : passez par le `docker compose` fourni |
| RSSHub repond mais Instagram/X echouent | identifiants manquants dans `rsshub.env` |
| Le deploiement s'arrete sur `.env absent` | creez `/opt/anglebot/.env` sur le serveur |
| `denied` au `docker compose pull` | package GHCR prive : rendez-le public ou `docker login ghcr.io` |
| `403` au push de l'image en CI | *Settings → Actions → General → Workflow permissions* : passez a **Read and write** |
| Notifications en double | `data/state.json` non persiste (verifiez le volume si vous conteneurisez) |
| Le bot refuse de demarrer (`Server Members`) | activez l'intent dans le portail, ou `autorole.enabled: false` |
| Le role automatique n'est pas donne | lancez `/autorole etat` : hierarchie, permission ou intent |

Logs plus verbeux : `LOG_LEVEL=DEBUG` dans `.env`.

### Lancement en service (systemd)

```ini
[Unit]
Description=AngleBot
After=network-online.target

[Service]
WorkingDirectory=/chemin/vers/AngleBot
ExecStart=/chemin/vers/AngleBot/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
