"""Persistance de la configuration (Postgres free de Render).

Le disque de Render est éphémère : la fourchette, l'heure et le template
réglés par commande doivent survivre aux redéploiements, d'où Postgres.

Aucun bâtiment n'est stocké : le CSV reste la source de vérité, relu à
chaque exécution.

Sans `DATABASE_URL`, on retombe sur un stockage en mémoire pour pouvoir
lancer le bot en local sans base.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src import settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_state (
    cle    TEXT PRIMARY KEY,
    valeur JSONB NOT NULL,
    maj_le TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class Store:
    """Dictionnaire persistant clé -> JSON."""

    def __init__(self, dsn: str = ""):
        self.dsn = dsn
        self._pool = None
        self._memoire: dict[str, Any] = {}

    @property
    def persistant(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        if not self.dsn:
            log.warning(
                "DATABASE_URL absent : configuration gardée en mémoire, "
                "elle sera perdue au redémarrage."
            )
            return

        import asyncpg

        # Render fournit parfois 'postgres://' ; asyncpg accepte les deux, mais
        # exige SSL sur les bases managées.
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3)
        async with self._pool.acquire() as connexion:
            await connexion.execute(SCHEMA)
        log.info("Base connectée.")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get(self, cle: str, defaut: Any = None) -> Any:
        if self._pool is None:
            return self._memoire.get(cle, defaut)
        async with self._pool.acquire() as connexion:
            ligne = await connexion.fetchrow(
                "SELECT valeur FROM bot_state WHERE cle = $1", cle
            )
        if ligne is None:
            return defaut
        return json.loads(ligne["valeur"])

    async def set(self, cle: str, valeur: Any) -> None:
        if self._pool is None:
            self._memoire[cle] = valeur
            return
        async with self._pool.acquire() as connexion:
            await connexion.execute(
                """
                INSERT INTO bot_state (cle, valeur, maj_le)
                VALUES ($1, $2::jsonb, now())
                ON CONFLICT (cle) DO UPDATE
                    SET valeur = EXCLUDED.valeur, maj_le = now()
                """,
                cle,
                json.dumps(valeur),
            )

    # --- Accès métier ------------------------------------------------------

    async def config(self) -> dict:
        """Config courante, complétée par les défauts pour les clés absentes."""
        defauts = settings.config_par_defaut()
        enregistree = await self.get("config", {}) or {}
        return {**defauts, **enregistree}

    # --- Salons de publication ---------------------------------------------

    async def salons(self) -> list[str]:
        """Salons où publier, dans l'ordre d'ajout.

        Migre à la lecture l'ancien `salon_id` unique (config d'avant le
        multi-salon) : sans ça, une mise à jour du bot ferait taire un salon
        déjà configuré. `salons` fait foi dès qu'elle existe.
        """
        config = await self.config()
        liste = [str(salon) for salon in config.get("salons") or [] if salon]
        if liste:
            return liste

        # Liste vide : soit rien n'est configuré, soit une config d'avant la
        # migration. `_ecrire_salons` efface `salon_id`, donc un salon retiré
        # ne peut pas ressusciter par ici.
        ancien = config.get("salon_id")
        return [str(ancien)] if ancien else []

    async def _ecrire_salons(self, liste: list[str]) -> None:
        """Enregistre la liste et retire l'ancien champ unique.

        Écrit directement plutôt que par `maj_config`, qui ignore les `None` et
        ne pourrait donc pas effacer `salon_id` — un salon retiré reviendrait
        au redémarrage.
        """
        config = await self.config()
        config["salons"] = liste
        config.pop("salon_id", None)
        await self.set("config", config)

    async def ajouter_salon(self, salon_id: str) -> bool:
        """Ajoute un salon. Renvoie False s'il y était déjà."""
        liste = await self.salons()
        if str(salon_id) in liste:
            return False
        await self._ecrire_salons([*liste, str(salon_id)])
        return True

    async def retirer_salon(self, salon_id: str) -> bool:
        """Retire un salon. Renvoie False s'il n'y était pas."""
        liste = await self.salons()
        if str(salon_id) not in liste:
            return False
        await self._ecrire_salons([s for s in liste if s != str(salon_id)])
        return True

    # --- Salon de logs -----------------------------------------------------

    async def salon_logs(self) -> str | None:
        salon = (await self.config()).get("logs_salon_id")
        return str(salon) if salon else None

    async def desactiver_logs(self) -> None:
        """`maj_config` ignore les None : il faut écrire la config entière."""
        config = await self.config()
        config["logs_salon_id"] = None
        await self.set("config", config)

    # --- Membres autorisés à utiliser les commandes ------------------------

    async def autorises(self) -> list[str]:
        """Ids des membres autorisés en plus des administrateurs."""
        config = await self.config()
        return [str(membre) for membre in config.get("autorises") or [] if membre]

    async def _ecrire_autorises(self, liste: list[str]) -> None:
        """`maj_config` ignore les valeurs vides : une liste vidée ne serait
        jamais enregistrée, et un membre retiré reviendrait au redémarrage."""
        config = await self.config()
        config["autorises"] = liste
        await self.set("config", config)

    async def autoriser(self, membre_id: str) -> bool:
        """Autorise un membre. Renvoie False s'il l'était déjà."""
        liste = await self.autorises()
        if str(membre_id) in liste:
            return False
        await self._ecrire_autorises([*liste, str(membre_id)])
        return True

    async def retirer_autorise(self, membre_id: str) -> bool:
        """Retire un membre. Renvoie False s'il n'était pas autorisé."""
        liste = await self.autorises()
        if str(membre_id) not in liste:
            return False
        await self._ecrire_autorises([m for m in liste if m != str(membre_id)])
        return True

    async def maj_config(self, **champs: Any) -> dict:
        courante = await self.config()
        courante.update({cle: valeur for cle, valeur in champs.items() if valeur is not None})
        await self.set("config", courante)
        return courante

    async def template(self) -> dict:
        from src.template import TEMPLATE_DEFAUT

        return await self.get("template", None) or TEMPLATE_DEFAUT

    async def set_template(self, modele: dict) -> None:
        await self.set("template", modele)

    async def derniere_publication(self) -> str | None:
        return await self.get("derniere_publication", None)

    async def marquer_publie(self, date: str) -> None:
        await self.set("derniere_publication", date)

    async def oublier_publication(self) -> None:
        """Efface la marque du jour pour pouvoir retester le déclenchement."""
        await self.set("derniere_publication", None)
