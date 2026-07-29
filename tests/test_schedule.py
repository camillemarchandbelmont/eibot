"""Tests du déclenchement quotidien."""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.schedule import boucle_planning, doit_publier

PARIS = ZoneInfo("Europe/Paris")


def _le(annee, mois, jour, heure, minute):
    return datetime(annee, mois, jour, heure, minute, tzinfo=PARIS)


def test_avant_lheure_ne_publie_pas():
    assert not doit_publier(_le(2026, 7, 28, 8, 59), "09:00", None)


def test_pile_a_lheure_publie():
    assert doit_publier(_le(2026, 7, 28, 9, 0), "09:00", None)


def test_apres_lheure_publie_quand_meme():
    """Render dormait à 09:00 : le ping de 09:20 doit rattraper."""
    assert doit_publier(_le(2026, 7, 28, 9, 20), "09:00", None)


def test_rattrapage_borne_dans_la_fenetre():
    """Endormi une heure : on publie encore."""
    assert doit_publier(_le(2026, 7, 28, 9, 59), "09:00", None)


def test_rattrapage_expire_apres_la_fenetre():
    """Sept heures de retard : le post du jour est perdu, on ne le sort pas
    en pleine soirée — et surtout on ne brûle pas le quota du jour."""
    assert not doit_publier(_le(2026, 7, 28, 16, 32), "09:00", None)


def test_heure_reglee_dans_la_journee_ne_declenche_pas_le_passe():
    """Régler l'heure à 16:36 ne doit pas publier à 16:32."""
    assert not doit_publier(_le(2026, 7, 28, 16, 32), "16:36", None)
    assert doit_publier(_le(2026, 7, 28, 16, 36), "16:36", None)


def test_fenetre_personnalisable():
    assert doit_publier(_le(2026, 7, 28, 11, 0), "09:00", None, fenetre_minutes=180)
    assert not doit_publier(_le(2026, 7, 28, 13, 0), "09:00", None, fenetre_minutes=180)


def test_fenetre_illimitee():
    """`fenetre_minutes=0` restaure le rattrapage sans borne."""
    assert doit_publier(_le(2026, 7, 28, 23, 59), "09:00", None, fenetre_minutes=0)


def test_deja_publie_aujourdhui_ne_republie_pas():
    assert not doit_publier(_le(2026, 7, 28, 9, 5), "09:00", "2026-07-28")
    # Même dans la fenêtre de rattrapage.
    assert not doit_publier(_le(2026, 7, 28, 9, 30), "09:00", "2026-07-28")


def test_publie_a_nouveau_le_lendemain():
    assert doit_publier(_le(2026, 7, 29, 9, 0), "09:00", "2026-07-28")


def test_idempotence_sur_pings_repetes():
    """Deux pings rapprochés : un seul post."""
    premier = doit_publier(_le(2026, 7, 28, 9, 1), "09:00", None)
    second = doit_publier(_le(2026, 7, 28, 9, 2), "09:00", "2026-07-28")
    assert premier and not second


def test_minutes_prises_en_compte():
    assert not doit_publier(_le(2026, 7, 28, 9, 29), "09:30", None)
    assert doit_publier(_le(2026, 7, 28, 9, 30), "09:30", None)


def test_heure_invalide_retombe_sur_9h():
    assert doit_publier(_le(2026, 7, 28, 9, 0), "n'importe quoi", None)
    assert not doit_publier(_le(2026, 7, 28, 8, 0), "n'importe quoi", None)
    assert not doit_publier(_le(2026, 7, 28, 20, 0), "n'importe quoi", None)


def test_minuit():
    assert doit_publier(_le(2026, 7, 28, 0, 0), "00:00", None)
    assert not doit_publier(_le(2026, 7, 28, 0, 0), "00:01", None)


# --- Boucle de planification ------------------------------------------------

class Horloge:
    """Faux `asyncio.sleep` : compte les tours sans attendre."""

    def __init__(self, tours: int):
        self.restants = tours
        self.attentes: list[float] = []

    async def dormir(self, secondes: float) -> None:
        self.attentes.append(secondes)
        self.restants -= 1

    def arrete(self) -> bool:
        return self.restants <= 0


async def test_boucle_appelle_publier_a_chaque_tour():
    horloge = Horloge(3)
    appels = []

    async def publier():
        appels.append(1)
        return "rien à faire"

    await boucle_planning(publier, horloge.arrete, intervalle=60, dormir=horloge.dormir)
    assert len(appels) == 3


async def test_boucle_respecte_lintervalle():
    horloge = Horloge(2)

    async def publier():
        return "rien à faire"

    await boucle_planning(horloge and publier, horloge.arrete, intervalle=42, dormir=horloge.dormir)
    assert horloge.attentes == [42, 42]


async def test_boucle_survit_a_une_erreur():
    """Un échec réseau ne doit pas tuer la publication des jours suivants."""
    horloge = Horloge(3)
    appels = []

    async def publier():
        appels.append(1)
        raise RuntimeError("Discord injoignable")

    await boucle_planning(publier, horloge.arrete, intervalle=60, dormir=horloge.dormir)
    assert len(appels) == 3


async def test_boucle_sarrete_immediatement_si_deja_arretee():
    horloge = Horloge(0)
    appels = []

    async def publier():
        appels.append(1)
        return ""

    await boucle_planning(publier, horloge.arrete, intervalle=60, dormir=horloge.dormir)
    assert appels == []


async def test_boucle_ne_repete_pas_le_meme_message(caplog):
    """Sans salon configuré, la boucle ne doit pas noyer le log chaque minute."""
    import logging

    horloge = Horloge(4)

    async def publier():
        return "aucun salon configuré (/config salon)"

    with caplog.at_level(logging.INFO, logger="src.schedule"):
        await boucle_planning(
            publier, horloge.arrete, intervalle=60, dormir=horloge.dormir
        )

    concernes = [e for e in caplog.records if "aucun salon" in e.getMessage()]
    assert len(concernes) == 1


async def test_boucle_reparle_apres_un_changement_detat():
    horloge = Horloge(4)
    reponses = ["aucun salon configuré", "aucun salon configuré", "publié (1 message(s))"]

    async def publier():
        return reponses.pop(0) if reponses else "rien à faire"

    # Ne doit pas lever : le changement d'état est simplement re-journalisé.
    await boucle_planning(publier, horloge.arrete, intervalle=60, dormir=horloge.dormir)
