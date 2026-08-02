"""Variables d'environnement et valeurs par défaut."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RACINE = Path(__file__).resolve().parent.parent

# --- Discord ---------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
#: Serveurs sur lesquels synchroniser les commandes, séparés par des virgules.
#:
#: Une liste explicite plutôt que la synchronisation globale, pour trois
#: raisons : elle est immédiate (la propagation globale met jusqu'à une heure),
#: les commandes n'existent que sur les serveurs déclarés — un serveur où le bot
#: serait invité par ailleurs n'a aucune prise sur la configuration —, et vide,
#: elle retombe sur la synchro globale, donc un déploiement qui ne déclare rien
#: continue de fonctionner.
#:
#: `GUILD_ID` (singulier) reste lu en repli : il est déjà défini dans le `.env`
#: local et sur Render.
GUILD_ID = os.getenv("GUILD_ID", "")
GUILD_IDS = [
    serveur.strip()
    for serveur in os.getenv("GUILD_IDS", GUILD_ID).split(",")
    if serveur.strip()
]

# --- Base de données -------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- Serveur HTTP (Render impose $PORT) ------------------------------------
PORT = int(os.getenv("PORT", "10000"))
#: Jeton partagé avec cron-job.org pour protéger /tick.
TICK_TOKEN = os.getenv("TICK_TOKEN", "")

#: Secret partagé avec le site Vercel, qui protège `/api/*`.
#:
#: Distinct de `TICK_TOKEN` : le cron n'a besoin que de déclencher la
#: publication, alors que ce secret ouvre les réglages et le template. L'un
#: fuité ne doit pas donner l'autre.
#:
#: Vide, l'API refuse tout : c'est le comportement voulu si le site n'est pas
#: déployé.
API_SECRET = os.getenv("API_SECRET", "")

#: Origines autorisées à appeler `/api/*` depuis un navigateur, séparées par des
#: virgules. Normalement inutile : le site Vercel appelle le bot depuis son
#: serveur, jamais depuis le navigateur, ce qui évite CORS et garde `API_SECRET`
#: hors du code de la page. Sert de soupape si un appel direct devient
#: nécessaire.
API_ORIGINES = [
    origine.strip()
    for origine in os.getenv("API_ORIGINES", "").split(",")
    if origine.strip()
]

# --- Source des données ----------------------------------------------------
CSV_PATH = os.getenv("CSV_PATH", str(RACINE / "buildings_batiments_entreprise.csv"))

#: Clé de l'API Empire Immo. Dès qu'elle est renseignée, le bot lit l'export
#: en ligne au lieu du fichier local.
EMPIRE_API_KEY = os.getenv("EMPIRE_API_KEY", "")
#: URL de l'export, surchargeable si le monde change (M8 -> M9, par exemple).
EMPIRE_API_URL = os.getenv("EMPIRE_API_URL", "")
#: Ancien nom, conservé pour ne pas casser un `.env` existant.
CSV_URL = os.getenv("CSV_URL", "")

# --- Défauts de configuration (surchargeables par commande Discord) --------
#: Fourchette demandée : 100 TØ -> 6 PØ.
PRIX_MIN_DEFAUT = Decimal(os.getenv("PRIX_MIN", "1e14"))
PRIX_MAX_DEFAUT = Decimal(os.getenv("PRIX_MAX", "6e15"))
HEURE_DEFAUT = os.getenv("HEURE", "09:00")
FUSEAU_DEFAUT = os.getenv("FUSEAU", "Europe/Paris")
SALON_DEFAUT = os.getenv("SALON_ID", "")
ROLE_DEFAUT = os.getenv("ROLE_ID", "")
#: Salon où le bot raconte ce qu'il fait (publications et erreurs).
LOGS_SALON_DEFAUT = os.getenv("LOGS_SALON_ID", "")


def config_par_defaut() -> dict:
    """Valeurs de repli des clés jamais réglées. **Jamais écrites en base.**

    `prix_min`/`prix_max`/`salons` y survivent alors que les fourchettes les ont
    remplacés : ils servent encore à la migration d'une config plate à laquelle
    il manque un champ (voir `Store.fourchettes`). C'est aussi pourquoi toute
    écriture part de `Store._enregistree()` et non de `Store.config()` : les
    matérialiser en base donnerait à un bot neuf la signature d'une config à
    migrer, donc une fourchette `principale` que personne n'a demandée.
    """
    return {
        "prix_min": str(PRIX_MIN_DEFAUT),
        "prix_max": str(PRIX_MAX_DEFAUT),
        "heure": HEURE_DEFAUT,
        "fuseau": FUSEAU_DEFAUT,
        # Liste : le bot publie dans plusieurs salons (voir `Store.salons`).
        "salons": [SALON_DEFAUT] if SALON_DEFAUT else [],
        "role_id": ROLE_DEFAUT or None,
        "logs_salon_id": LOGS_SALON_DEFAUT or None,
    }
