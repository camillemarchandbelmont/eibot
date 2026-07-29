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
#: Optionnel : synchronise les commandes sur un seul serveur (immédiat au lieu
#: d'attendre la propagation globale). Pratique en développement.
GUILD_ID = os.getenv("GUILD_ID", "")

# --- Base de données -------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- Serveur HTTP (Render impose $PORT) ------------------------------------
PORT = int(os.getenv("PORT", "10000"))
#: Jeton partagé avec cron-job.org pour protéger /tick.
TICK_TOKEN = os.getenv("TICK_TOKEN", "")

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
    """Config initiale, écrite en base au premier démarrage."""
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
