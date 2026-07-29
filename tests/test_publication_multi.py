"""Tests de la publication vers plusieurs salons.

Décision de conception vérifiée ici : un salon en panne (supprimé, permissions
manquantes) ne prive pas les autres. La journée est marquée publiée dès qu'un
seul envoi a réussi ; si tous échouent, rien n'est marqué et le passage
suivant réessaie.
"""

import pytest

from src.bot import EmpireBot
from src.db import Store
from src.source import SourceError

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
"""


class SourceFactice:
    def __init__(self, texte: str = CSV):
        self.texte = texte

    async def fetch(self) -> str:
        return self.texte


class SalonFactice:
    def __init__(self, salon_id: int, erreur: Exception | None = None):
        self.id = salon_id
        self.mention = f"<#{salon_id}>"
        self.erreur = erreur
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        if self.erreur:
            raise self.erreur
        self.envois.append({"contenu": contenu, **options})


class JournalFactice:
    def __init__(self):
        self.publications: list[dict] = []
        self.erreurs: list[str] = []

    async def publication(self, promos, reussis, echecs):
        self.publications.append({"promos": promos, "reussis": reussis, "echecs": echecs})

    async def erreur(self, message):
        self.erreurs.append(message)


async def _bot(salons: dict[int, SalonFactice], source=None) -> EmpireBot:
    """Un bot sans connexion Discord, avec des salons factices."""
    store = Store(dsn="")
    await store.connect()
    for salon_id in salons:
        await store.ajouter_salon(str(salon_id))

    bot = object.__new__(EmpireBot)   # sans se connecter à Discord
    bot.store = store
    bot.source = source or SourceFactice()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    return bot


# --- Diffusion --------------------------------------------------------------

async def test_publie_dans_tous_les_salons():
    salons = {1: SalonFactice(1), 2: SalonFactice(2), 3: SalonFactice(3)}
    bot = await _bot(salons)

    resultat = await bot.publier_si_lheure(forcer=True)

    assert all(salon.envois for salon in salons.values())
    assert "3" in resultat


async def test_un_seul_salon_fonctionne_comme_avant():
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)

    await bot.publier_si_lheure(forcer=True)

    assert len(salons[1].envois) == 1


async def test_aucun_salon_configure_ne_publie_rien():
    bot = await _bot({})
    resultat = await bot.publier_si_lheure(forcer=True)
    assert "aucun salon" in resultat


# --- Échec partiel ----------------------------------------------------------

async def test_un_salon_en_panne_ne_prive_pas_les_autres():
    salons = {
        1: SalonFactice(1),
        2: SalonFactice(2, erreur=RuntimeError("403 Forbidden")),
        3: SalonFactice(3),
    }
    bot = await _bot(salons)

    resultat = await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois and salons[3].envois
    assert not salons[2].envois
    assert "2/3" in resultat or "2 salon" in resultat


async def test_echec_partiel_marque_quand_meme_le_jour_publie():
    """Sinon le passage suivant reposterait dans les salons déjà servis."""
    salons = {1: SalonFactice(1), 2: SalonFactice(2, erreur=RuntimeError("403"))}
    bot = await _bot(salons)

    await bot.publier_si_lheure(forcer=True)

    assert await bot.store.derniere_publication() is not None


async def test_echec_total_ne_marque_pas_le_jour_publie():
    """Aucun salon servi : le prochain passage doit réessayer."""
    salons = {
        1: SalonFactice(1, erreur=RuntimeError("403")),
        2: SalonFactice(2, erreur=RuntimeError("403")),
    }
    bot = await _bot(salons)

    resultat = await bot.publier_si_lheure(forcer=True)

    assert await bot.store.derniere_publication() is None
    assert "échec" in resultat.lower() or "aucun" in resultat.lower()


async def test_salon_supprime_est_traite_comme_un_echec():
    """`get_channel` renvoie None et `fetch_channel` lève."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon("999")   # salon qui n'existe plus

    async def fetch_channel(salon_id):
        raise RuntimeError("Unknown Channel")

    bot.fetch_channel = fetch_channel

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois                       # l'autre a bien reçu
    assert await bot.store.derniere_publication() is not None


# --- Journal ----------------------------------------------------------------

async def test_le_journal_recoit_le_compte_rendu():
    salons = {1: SalonFactice(1), 2: SalonFactice(2, erreur=RuntimeError("403 Forbidden"))}
    bot = await _bot(salons)

    await bot.publier_si_lheure(forcer=True)

    rapport = bot.journal.publications[0]
    assert rapport["promos"] == 2
    assert len(rapport["reussis"]) == 1
    assert len(rapport["echecs"]) == 1
    assert "403 Forbidden" in str(rapport["echecs"])


async def test_une_panne_dapi_est_journalisee():
    class SourceEnPanne:
        async def fetch(self):
            raise SourceError("L'API a répondu 401 : Clé API invalide.")

    bot = await _bot({1: SalonFactice(1)}, source=SourceEnPanne())

    with pytest.raises(SourceError):
        await bot.publier_si_lheure(forcer=True)

    assert "401" in bot.journal.erreurs[0]
    assert await bot.store.derniere_publication() is None


async def test_une_erreur_inattendue_est_journalisee():
    """Une panne non prévue ne doit pas rester invisible dans Discord."""
    class SourceCassee:
        async def fetch(self):
            raise ValueError("CSV corrompu")

    bot = await _bot({1: SalonFactice(1)}, source=SourceCassee())

    with pytest.raises(ValueError):
        await bot.publier_si_lheure(forcer=True)

    assert bot.journal.erreurs, "l'erreur doit apparaître dans le salon de logs"
    assert "CSV corrompu" in bot.journal.erreurs[0]
    assert await bot.store.derniere_publication() is None


async def test_un_journal_en_panne_ne_casse_pas_la_publication():
    """Le journal est un observateur : il ne doit jamais bloquer l'essentiel."""
    class JournalEnPanne:
        async def publication(self, *args, **kwargs):
            raise RuntimeError("salon de logs supprimé")

        async def erreur(self, message):
            raise RuntimeError("salon de logs supprimé")

    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    bot.journal = JournalEnPanne()

    resultat = await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois
    assert "publié" in resultat.lower()


# --- Mention de rôle --------------------------------------------------------

async def test_le_role_est_mentionne_dans_chaque_salon():
    salons = {1: SalonFactice(1), 2: SalonFactice(2)}
    bot = await _bot(salons)
    await bot.store.maj_config(role_id="4242")

    await bot.publier_si_lheure(forcer=True)

    for salon in salons.values():
        # `envoyer` passe le contenu en kwarg `content`.
        assert "<@&4242>" in salon.envois[0]["content"]


# --- Promos repêchées -------------------------------------------------------

async def test_aucune_note_globale_sous_le_message():
    """Le repêchage se lit dans l'embed concerné, pas dans une note à part.

    Une note sous le message répétait ce que `{hors_fourchette}` dit déjà,
    bâtiment par bâtiment.
    """
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    # Fourchette vide de promos : les deux du CSV sont repêchées.
    await bot.store.maj_config(prix_min="1e30", prix_max="2e30")

    await bot.publier_si_lheure(forcer=True)

    envoi = salons[1].envois[0]
    assert "Trop peu de promotions" not in (envoi.get("content") or "")


# --- Cas « aucune promotion » ----------------------------------------------

async def test_message_de_repli_diffuse_partout():
    """Sans promo, le message « aucune promotion » va dans tous les salons."""
    sans_promo = CSV.replace(",17,", ",0,")
    salons = {1: SalonFactice(1), 2: SalonFactice(2)}
    bot = await _bot(salons, source=SourceFactice(sans_promo))

    await bot.publier_si_lheure(forcer=True)

    for salon in salons.values():
        assert "Aucune promotion" in salon.envois[0]["contenu"]
    assert bot.journal.publications[0]["promos"] == 0
