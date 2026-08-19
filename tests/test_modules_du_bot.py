"""Les modules livrés avec le bot, vus par le contrat.

Ce que les autres fichiers de test éprouvent, c'est le résultat : le post part,
au bon endroit, à la bonne heure. Ici on éprouve la **déclaration** : que les
fonctionnalités du bot sont bien des modules ordinaires, sans passe-droit. C'est
la seule preuve que le contrat suffit — un module écrit plus tard n'aura rien de
plus à sa disposition.

Point de vigilance particulier : les deux publications historiques rangent leur
heure et leur trace de passage là où elles les rangeaient avant les modules. Le
déménagement du mécanisme ne doit **déplacer aucune donnée** ; les tests qui
suivent le vérrouillent clé par clé, parce qu'une reprise oubliée se traduirait
par un bot qui republie tout un jour à 09:00.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.db import Store
from src.modules import Module, decouvrir
from src.modules import filiales as module_filiales
from src.modules import promos as module_promos


async def _magasin() -> Store:
    magasin = Store(dsn="")
    await magasin.connect()
    return magasin


def _instant() -> datetime:
    return datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Europe/Paris"))


def _publication(module: Module, cle: str):
    for publication in module.publications:
        if publication.cle == cle:
            return publication
    raise AssertionError(f"{module.nom} ne déclare pas la publication « {cle} »")


# --- Le dossier ------------------------------------------------------------


def test_les_publications_du_bot_sont_declarees_par_des_modules():
    """Aucune n'est câblée à part : c'est ce qui prouve que le contrat suffit.

    Le balayage du vrai dossier, et non une liste écrite ici : un module posé et
    cassé apparaîtrait dans `refuses`, ce qu'aucun autre test ne verrait.
    """
    charges, refuses = decouvrir()

    assert refuses == {}
    assert {module.nom for module in charges} >= {"promos", "filiales"}
    assert [p.cle for module in charges for p in module.publications] == [
        "promos",
        "filiales",
    ]


# --- Les promotions --------------------------------------------------------


async def test_les_promotions_lisent_leur_heure_dans_la_config():
    """Leur heure y vit depuis avant les modules ; la déplacer serait une reprise.

    Sans cette lecture, le bot retomberait sur le tiroir générique vide, donc sur
    09:00 — et republierait à contretemps le jour du déploiement.
    """
    publication = _publication(module_promos.MODULE, "promos")
    magasin = await _magasin()
    await magasin.maj_config(heure="18:45")

    assert await publication.lire_heure(magasin) == "18:45"


async def test_les_promotions_relisent_et_marquent_leur_ancienne_trace():
    publication = _publication(module_promos.MODULE, "promos")
    magasin = await _magasin()

    await publication.marquer(magasin, "2026-08-19")

    assert await magasin.derniere_publication() == "2026-08-19"
    assert await publication.lire_derniere(magasin) == "2026-08-19"


async def test_sans_fourchette_les_promotions_disent_laquelle_creer():
    publication = _publication(module_promos.MODULE, "promos")
    magasin = await _magasin()

    tournee = await publication.preparer(None, magasin, None)

    assert tournee.envois == ()
    assert "aucune fourchette" in tournee.raison


async def test_une_fourchette_sans_salon_ne_donne_rien_a_envoyer():
    """Le message parle de salon, cette fois : la fourchette existe."""
    publication = _publication(module_promos.MODULE, "promos")
    magasin = await _magasin()
    await magasin.ajouter_fourchette("petite", Decimal("1e15"), Decimal("2e15"))

    tournee = await publication.preparer(None, magasin, None)

    assert tournee.envois == ()
    assert "aucun salon" in tournee.raison


# --- Le tableau des frais --------------------------------------------------


async def test_le_tableau_lit_son_heure_a_lui():
    """Distincte de celle des promotions : les deux posts ne sortent pas ensemble."""
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()
    await magasin.maj_config(filiales_heure="21:15")

    assert await publication.lire_heure(magasin) == "21:15"
    assert await publication.lire_heure(magasin) != (await magasin.config())["heure"]


async def test_le_tableau_relit_et_marque_son_ancienne_trace():
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()

    await publication.marquer(magasin, "2026-08-19")

    assert await magasin.derniere_publication_filiales() == "2026-08-19"
    assert await publication.lire_derniere(magasin) == "2026-08-19"
    # La trace de l'autre publication est intacte : une panne des promotions ne
    # doit pas annuler la journée du tableau, ni l'inverse.
    assert await magasin.derniere_publication() is None


async def test_sans_salon_le_tableau_dit_lequel_ajouter():
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()

    tournee = await publication.preparer(None, magasin, None)

    assert tournee.envois == ()
    assert "salon" in tournee.raison


async def test_le_tableau_part_meme_sans_aucune_filiale():
    """L'absence de post ne se distinguerait pas d'une panne du bot.

    L'embed vide, lui, dit comment le remplir. C'est ce qui distingue cette
    publication des promotions, qui ne partent pas sans fourchette.
    """
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()
    await magasin.ajouter_salon_filiales("1")
    maintenant = _instant()

    tournee = await publication.preparer(None, magasin, maintenant)

    assert [envoi.salons for envoi in tournee.envois] == [("1",)]
    assert tournee.compte == 0
