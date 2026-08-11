"""Serveur HTTP minimal.

Deux rôles, imposés par l'hébergement gratuit de Render :
  - `/health` : cible du ping de cron-job.org, qui empêche l'endormissement ;
  - `/tick`   : déclencheur de la publication, appelé par le même cron.

Le service dormant à l'heure prévue ne poserait donc pas problème : le
premier ping suivant rattrape la publication (voir `schedule.doit_publier`).
"""

from __future__ import annotations

import logging

from aiohttp import web

from src import settings

log = logging.getLogger(__name__)

class JournalSansSecret(web.AbstractAccessLogger):
    """Journal d'accès amputé de la query string.

    Le format par défaut d'aiohttp journalise la requête complète, donc
    `/tick?token=<secret>` en clair — visible dans le dashboard Render comme
    dans `bot.log`. Méthode, chemin et statut suffisent au débogage ; la query
    ne contient de toute façon que `token` et `forcer`.
    """

    def log(self, request, response, time: float) -> None:
        self.logger.info(
            "%s %s → %s (%.0f ms)",
            request.method,
            request.path,          # `.path` exclut la query, `.path_qs` l'inclut
            response.status,
            time * 1000,
        )


def _autorise(requete: web.Request) -> bool:
    """Vérifie le jeton partagé avec cron-job.org."""
    if not settings.TICK_TOKEN:
        # Aucun jeton configuré : on n'expose pas /tick à l'aveugle.
        return False
    fourni = requete.query.get("token") or requete.headers.get("X-Tick-Token", "")
    return fourni == settings.TICK_TOKEN


def creer_app(bot) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        etat = "prêt" if bot.is_ready() else "démarrage"
        return web.Response(text=f"ok ({etat})")

    async def tick(requete: web.Request) -> web.Response:
        if not _autorise(requete):
            return web.Response(status=403, text="jeton invalide ou TICK_TOKEN absent")
        if not bot.is_ready():
            return web.Response(text="bot pas encore connecté")

        forcer = requete.query.get("forcer") == "1"
        try:
            # Le tour complet : les promotions **et** le tableau des frais. Ne
            # déclencher que les promotions laisserait le tableau muet sur
            # Render, où le service dort entre deux appels du cron.
            resultat = await bot.publier_tout(forcer=forcer)
        except Exception as erreur:  # on répond 500 mais on garde le service vivant
            log.exception("Échec de la publication")
            return web.Response(status=500, text=f"erreur : {erreur}")
        return web.Response(text=resultat)

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/tick", tick)
    app.router.add_post("/tick", tick)

    # `/api/*` : les routes du site web, protégées par leur propre secret.
    # Importé ici et non en tête de module pour garder `web.py` chargeable même
    # si `api.py` casse : le keepalive de Render ne doit pas dépendre du site.
    from src.api import enregistrer_routes

    enregistrer_routes(app, bot)
    return app


async def demarrer(bot) -> web.AppRunner:
    """Lance le serveur sur le port imposé par Render."""
    runner = web.AppRunner(creer_app(bot), access_log_class=JournalSansSecret)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.PORT)
    await site.start()
    log.info("Serveur HTTP sur le port %s", settings.PORT)
    return runner
