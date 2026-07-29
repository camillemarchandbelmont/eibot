"""Tests des endpoints /health et /tick (sans Discord)."""

import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import settings
from src.web import JournalSansSecret, creer_app


class BotFactice:
    """Remplace le client Discord : seules deux méthodes sont utilisées."""

    def __init__(self, pret: bool = True, erreur: Exception | None = None):
        self.pret = pret
        self.erreur = erreur
        self.appels: list[bool] = []

    def is_ready(self) -> bool:
        return self.pret

    async def publier_si_lheure(self, forcer: bool = False) -> str:
        if self.erreur:
            raise self.erreur
        self.appels.append(forcer)
        return "publié (1 message(s))" if forcer else "rien à faire"


@pytest.fixture(autouse=True)
def jeton(monkeypatch):
    monkeypatch.setattr(settings, "TICK_TOKEN", "secret123")


async def _client(bot) -> TestClient:
    client = TestClient(TestServer(creer_app(bot)))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_health_repond_ok():
    client = await _client(BotFactice())
    reponse = await client.get("/health")
    assert reponse.status == 200
    assert "ok" in await reponse.text()
    await client.close()


@pytest.mark.asyncio
async def test_racine_sert_aussi_de_keepalive():
    client = await _client(BotFactice())
    assert (await client.get("/")).status == 200
    await client.close()


@pytest.mark.asyncio
async def test_health_signale_le_demarrage():
    client = await _client(BotFactice(pret=False))
    assert "démarrage" in await (await client.get("/health")).text()
    await client.close()


@pytest.mark.asyncio
async def test_tick_sans_jeton_refuse():
    client = await _client(BotFactice())
    assert (await client.get("/tick")).status == 403
    await client.close()


@pytest.mark.asyncio
async def test_tick_mauvais_jeton_refuse():
    client = await _client(BotFactice())
    assert (await client.get("/tick?token=faux")).status == 403
    await client.close()


@pytest.mark.asyncio
async def test_tick_sans_token_configure_refuse(monkeypatch):
    """Sans TICK_TOKEN, /tick ne doit pas être ouvert à tous."""
    monkeypatch.setattr(settings, "TICK_TOKEN", "")
    client = await _client(BotFactice())
    assert (await client.get("/tick?token=")).status == 403
    await client.close()


@pytest.mark.asyncio
async def test_tick_jeton_par_entete():
    client = await _client(BotFactice())
    reponse = await client.get("/tick", headers={"X-Tick-Token": "secret123"})
    assert reponse.status == 200
    await client.close()


@pytest.mark.asyncio
async def test_tick_delegue_au_bot():
    bot = BotFactice()
    client = await _client(bot)
    assert "rien à faire" in await (await client.get("/tick?token=secret123")).text()
    assert bot.appels == [False]
    await client.close()


@pytest.mark.asyncio
async def test_tick_forcer():
    bot = BotFactice()
    client = await _client(bot)
    reponse = await client.get("/tick?token=secret123&forcer=1")
    assert "publié" in await reponse.text()
    assert bot.appels == [True]
    await client.close()


@pytest.mark.asyncio
async def test_tick_accepte_post():
    """cron-job.org peut être configuré en POST."""
    client = await _client(BotFactice())
    assert (await client.post("/tick?token=secret123")).status == 200
    await client.close()


@pytest.mark.asyncio
async def test_tick_bot_pas_pret_ne_plante_pas():
    client = await _client(BotFactice(pret=False))
    reponse = await client.get("/tick?token=secret123")
    assert reponse.status == 200
    assert "pas encore connecté" in await reponse.text()
    await client.close()


def test_le_journal_dacces_ne_contient_pas_le_jeton(caplog):
    """Sur Render, ces lignes sont lisibles dans le dashboard.

    Le format par défaut d'aiohttp journalise la requête complète, donc
    `/tick?token=<secret>` en clair — un secret dans un log qu'on partage ou
    qu'on committe par erreur est un secret compromis.

    On teste le logger directement : les kwargs de `TestServer` ne parviennent
    pas au runner, donc passer par un vrai serveur ne prouverait rien.
    """
    class RequeteFactice:
        method = "GET"
        path = "/tick"                       # ce qu'on veut voir
        path_qs = "/tick?token=secret123"    # ce qu'on ne veut pas

    class ReponseFactice:
        status = 200
        body_length = 12

    logger = logging.getLogger("aiohttp.access")
    with caplog.at_level(logging.INFO, logger="aiohttp.access"):
        JournalSansSecret(logger, "").log(RequeteFactice(), ReponseFactice(), 0.05)

    journal = caplog.text
    assert journal.strip(), "aucune ligne journalisée : test sans valeur"
    assert "secret123" not in journal
    assert "/tick" in journal    # on garde une trace exploitable
    assert "200" in journal


@pytest.mark.asyncio
async def test_le_serveur_reel_masque_le_jeton(caplog, monkeypatch):
    """Garde-fou de bout en bout : la classe existe, encore faut-il la brancher.

    Passe par `demarrer()` — le vrai chemin de production — et non par
    `TestServer`, qui ignore `access_log_class`.
    """
    import socket

    import aiohttp

    from src.web import demarrer

    # Un port libre, choisi par l'OS puis relâché.
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]
    monkeypatch.setattr(settings, "PORT", port)

    runner = await demarrer(BotFactice())
    try:
        with caplog.at_level(logging.INFO, logger="aiohttp.access"):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{port}/tick?token=secret123"
                ) as reponse:
                    assert reponse.status == 200
    finally:
        await runner.cleanup()

    journal = caplog.text
    assert journal.strip(), "aucune ligne d'accès journalisée : test sans valeur"
    assert "secret123" not in journal
    assert "/tick" in journal


@pytest.mark.asyncio
async def test_erreur_de_publication_renvoie_500_sans_tuer_le_service():
    bot = BotFactice(erreur=RuntimeError("API Discord indisponible"))
    client = await _client(bot)
    reponse = await client.get("/tick?token=secret123")
    assert reponse.status == 500
    # Le service reste debout pour le ping suivant.
    assert (await client.get("/health")).status == 200
    await client.close()
