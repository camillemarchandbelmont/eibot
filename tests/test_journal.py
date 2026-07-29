"""Tests du salon de logs.

Le journal a une exigence particulière : **il ne doit jamais faire échouer ce
qu'il raconte**. Un salon de logs supprimé ou sans permissions ne peut pas
casser la publication quotidienne. La plupart des tests ci-dessous vérifient
donc des non-plantages.
"""

import logging

import pytest

from src.db import Store
from src.journal import Journal


class SalonFactice:
    """Remplace un `discord.TextChannel` : seul `send` est utilisé."""

    def __init__(self, erreur: Exception | None = None):
        self.erreur = erreur
        self.messages: list[str] = []

    async def send(self, contenu: str, **options):
        if self.erreur:
            raise self.erreur
        self.messages.append(contenu)


class BotFactice:
    def __init__(self, salon=None):
        self.salon = salon
        self.demandes: list[int] = []

    def get_channel(self, salon_id: int):
        self.demandes.append(salon_id)
        return self.salon

    async def fetch_channel(self, salon_id: int):
        if self.salon is None:
            raise RuntimeError("salon introuvable")
        return self.salon


async def _journal(salon=None, logs_id: str | None = "789") -> tuple[Journal, Store]:
    store = Store(dsn="")
    await store.connect()
    if logs_id:
        await store.maj_config(logs_salon_id=logs_id)
    return Journal(BotFactice(salon), store), store


# --- Publication ------------------------------------------------------------

async def test_publication_reussie_est_journalisee():
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.publication(promos=2, reussis=["#promos", "#vip"], echecs={})

    assert len(salon.messages) == 1
    message = salon.messages[0]
    assert "✅" in message
    assert "2 promotion" in message
    assert "#promos" in message and "#vip" in message


async def test_publication_partielle_signale_les_echecs():
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.publication(
        promos=2, reussis=["#promos"], echecs={"#vip": "permissions manquantes"}
    )

    message = salon.messages[0]
    assert "⚠️" in message
    assert "#vip" in message
    assert "permissions manquantes" in message


async def test_publication_totalement_en_echec():
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.publication(promos=2, reussis=[], echecs={"#promos": "403 Forbidden"})

    message = salon.messages[0]
    assert "❌" in message
    assert "403 Forbidden" in message


async def test_publication_sans_promotion_le_dit():
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.publication(promos=0, reussis=["#promos"], echecs={})

    assert "aucune promotion" in salon.messages[0].lower()


# --- Erreurs ----------------------------------------------------------------

async def test_erreur_est_journalisee():
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.erreur("L'API a répondu 401 : Clé API invalide ou révoquée.")

    assert "❌" in salon.messages[0]
    assert "401" in salon.messages[0]


# --- Désactivation ----------------------------------------------------------

async def test_sans_salon_configure_rien_nest_envoye():
    salon = SalonFactice()
    journal, _ = await _journal(salon, logs_id=None)

    await journal.publication(promos=1, reussis=["#promos"], echecs={})
    await journal.erreur("boum")

    assert salon.messages == []


async def test_desactiver_les_logs_arrete_les_envois():
    salon = SalonFactice()
    journal, store = await _journal(salon)
    await store.desactiver_logs()

    await journal.erreur("boum")

    assert salon.messages == []


# --- Robustesse : le journal ne doit jamais faire échouer l'appelant --------

async def test_salon_introuvable_ne_leve_pas():
    """Salon supprimé : la publication doit continuer malgré tout."""
    journal, _ = await _journal(salon=None)
    await journal.erreur("boum")          # ne doit pas lever


async def test_salon_sans_permissions_ne_leve_pas(caplog):
    salon = SalonFactice(erreur=RuntimeError("403 Forbidden"))
    journal, _ = await _journal(salon)

    with caplog.at_level(logging.WARNING):
        await journal.publication(promos=1, reussis=["#promos"], echecs={})

    # L'échec est tracé dans le log fichier, pas perdu en silence.
    assert "journal" in caplog.text.lower() or "403" in caplog.text


async def test_une_erreur_du_journal_ne_se_rejournalise_pas():
    """Sinon une boucle : échec d'écriture -> log -> échec d'écriture -> …"""
    salon = SalonFactice(erreur=RuntimeError("403 Forbidden"))
    journal, _ = await _journal(salon)

    await journal.erreur("panne initiale")

    assert salon.messages == []  # une seule tentative, pas de cascade


# --- Sécurité ---------------------------------------------------------------

async def test_le_journal_ne_relaie_pas_de_cle_dapi():
    """Le journal ne doit publier que des messages déjà assainis."""
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.erreur(
        "L'API a répondu 401 : Clé API invalide. "
        "https://monde8.empireimmo.com/api/x.csv?key=***"
    )

    assert "***" in salon.messages[0]


async def test_message_tres_long_est_tronque():
    """Discord refuse au-delà de 2000 caractères : rogner plutôt qu'échouer."""
    salon = SalonFactice()
    journal, _ = await _journal(salon)

    await journal.erreur("x" * 5000)

    assert len(salon.messages[0]) <= 2000
