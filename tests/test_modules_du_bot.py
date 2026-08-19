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

from src.bot import EmpireBot
from src.db import Store
from src.modules import Module, decouvrir
from src.modules import filiales as module_filiales
from src.modules import promos as module_promos

from tests.test_commandes_fourchettes import SourceFactice


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


def test_le_bot_balaie_le_dossier_a_son_demarrage():
    """Le bot doit retenir ce qu'il a trouvé, et ce qu'il a refusé.

    Retenu et non redécouvert à la demande : le balayage importe des fichiers,
    donc exécute du code, et le refaire à chaque commande multiplierait les
    occasions de tomber. La liste des refusés est gardée pour être dite dans le
    salon de logs — un module absent du menu sans explication enverrait chercher
    la panne du côté de Discord.
    """
    bot = EmpireBot(Store(dsn=""), SourceFactice())

    assert {module.nom for module in bot.modules} >= {"promos", "filiales"}
    assert bot.modules_refuses == {}


class JournalFactice:
    def __init__(self):
        self.erreurs: list[str] = []

    async def erreur(self, message: str) -> None:
        self.erreurs.append(message)


async def test_un_module_refuse_est_nomme_dans_le_salon_de_logs():
    """Le fautif et sa raison, là où on les lit.

    Un module écarté disparaît du menu sans bruit : sans ce signalement, on
    chercherait la panne du côté de Discord ou de la synchronisation des
    commandes, et non du fichier qu'on vient de pousser.
    """
    bot = EmpireBot(Store(dsn=""), SourceFactice())
    bot.journal = JournalFactice()
    bot.modules_refuses = {"bonjour": "ImportError : pas de module nommé pandas"}

    await bot.signaler_les_modules_refuses()

    assert len(bot.journal.erreurs) == 1
    assert "bonjour" in bot.journal.erreurs[0]
    assert "pandas" in bot.journal.erreurs[0]


async def test_sans_module_refuse_le_salon_de_logs_reste_muet():
    """Un « 0 module refusé » à chaque démarrage apprendrait à ne plus lire le
    salon de logs, et le vrai signalement passerait avec le reste."""
    bot = EmpireBot(Store(dsn=""), SourceFactice())
    bot.journal = JournalFactice()

    await bot.signaler_les_modules_refuses()

    assert bot.journal.erreurs == []


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


async def test_les_promotions_ecrivent_leur_heure_dans_la_config():
    """Écrite là où elle est lue, sinon `/fourchette heure` confirmerait pour rien."""
    publication = _publication(module_promos.MODULE, "promos")
    magasin = await _magasin()

    await publication.ecrire_heure(magasin, "18:45")

    assert (await magasin.config())["heure"] == "18:45"
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
    # Nommée, et pas seulement comptée : l'aperçu doit dire *laquelle* ne partira
    # pas, sans quoi il faudrait la déduire de la liste des salons.
    assert tournee.ecartes == (("petite", "aucun salon"),)


# --- Le tableau des frais --------------------------------------------------


async def test_le_tableau_lit_son_heure_a_lui():
    """Distincte de celle des promotions : les deux posts ne sortent pas ensemble."""
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()
    await magasin.maj_config(filiales_heure="21:15")

    assert await publication.lire_heure(magasin) == "21:15"
    assert await publication.lire_heure(magasin) != (await magasin.config())["heure"]


async def test_le_tableau_ecrit_son_heure_sans_toucher_a_celle_des_promotions():
    """Deux posts, deux horaires : régler l'un en déplaçant l'autre serait une
    surprise découverte le lendemain."""
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()
    avant = (await magasin.config())["heure"]

    await publication.ecrire_heure(magasin, "21:15")

    assert await publication.lire_heure(magasin) == "21:15"
    assert (await magasin.config())["heure"] == avant


async def test_le_tableau_range_ses_salons_dans_son_ancienne_liste():
    """Les salons déjà réglés doivent rester ceux que la publication utilise.

    Rangés dans le tiroir générique, le tableau du soir partirait dans le vide
    alors que `/filiales salon ajouter` aurait répondu « ✅ ».
    """
    publication = _publication(module_filiales.MODULE, "filiales")
    magasin = await _magasin()

    assert await publication.ajouter_salon(magasin, "4242") is True
    assert await publication.ajouter_salon(magasin, "4242") is False
    assert await magasin.salons_filiales() == ["4242"]
    assert await publication.lire_salons(magasin) == ["4242"]

    assert await publication.retirer_salon(magasin, "4242") is True
    assert await publication.retirer_salon(magasin, "4242") is False
    assert await magasin.salons_filiales() == []


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
