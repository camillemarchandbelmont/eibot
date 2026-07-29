"""Point d'entrée : bot Discord et serveur HTTP dans la même boucle asyncio."""

from __future__ import annotations

import asyncio
import logging

from src import settings
from src.bot import EmpireBot, creer_source
from src.db import Store
from src.web import demarrer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("empire")


async def principal() -> None:
    if not settings.DISCORD_TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN manquant. Copie .env.example vers .env et renseigne-le."
        )

    store = Store(settings.DATABASE_URL)
    await store.connect()

    bot = EmpireBot(store, creer_source())
    runner = await demarrer(bot)

    try:
        await bot.start(settings.DISCORD_TOKEN)
    finally:
        await runner.cleanup()
        await store.close()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        log.info("Arrêt demandé.")
