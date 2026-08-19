"""La mécanique d'envoi, partagée par toutes les publications.

Elle était écrite deux fois — une pour les promotions, une pour le tableau des
frais — avec le même compte à rebours, la même boucle sur les salons, la même
gestion des pannes et la même marque de passage. Ici elle est unique et
paramétrée : c'est ce qui fait qu'un troisième post quotidien coûte une
déclaration.

Les publications d'essai ne connaissent ni Discord ni le jeu : elles rendent une
tournée écrite à la main, ce qui laisse voir la mécanique seule.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.db import Store
from src.modules import Envoi, Publication, Tournee
from src.tournee import faire_la_tournee


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


class BotFactice:
    """Le strict nécessaire : résoudre un salon et tenir un journal."""

    def __init__(self, salons: dict[int, SalonFactice]):
        self.salons = salons
        self.journalisees: list[dict] = []

    async def resoudre_salon(self, salon_id: str):
        salon = self.salons.get(int(salon_id))
        if salon is None:
            raise LookupError(f"salon {salon_id} introuvable")
        return salon

    async def journaliser_publication(self, promos, reussis, echecs):
        self.journalisees.append(
            {"compte": promos, "reussis": reussis, "echecs": echecs}
        )


def _instant(heure: str = "09:30") -> datetime:
    heures, minutes = (int(part) for part in heure.split(":"))
    return datetime(2026, 8, 19, heures, minutes, tzinfo=ZoneInfo("Europe/Paris"))


async def _magasin() -> Store:
    magasin = Store(dsn="")
    await magasin.connect()
    return magasin


def _publication(
    tournee: Tournee | Exception,
    appels: list[datetime] | None = None,
    **surcharges,
) -> Publication:
    """Une publication qui rend la tournée donnée — ou lève, si on lui en donne une.

    `appels` reçoit un instant par préparation : c'est ce qui permet de vérifier
    qu'on n'a **pas** préparé quand ce n'était pas l'heure, préparer coûtant un
    appel à l'API du jeu.
    """
    trace = appels if appels is not None else []

    async def preparer(bot, magasin, maintenant):
        trace.append(maintenant)
        if isinstance(tournee, Exception):
            raise tournee
        return tournee

    return Publication(
        cle=surcharges.pop("cle", "essai"),
        titre=surcharges.pop("titre", "l'essai"),
        preparer=preparer,
        **surcharges,
    )


def _tournee(*salons_par_envoi: tuple[str, list[str]], compte: int = 1) -> Tournee:
    envois = []
    for etiquette, salons in salons_par_envoi:
        async def envoyer(salon, etiquette=etiquette):
            await salon.send(f"contenu de {etiquette}")

        envois.append(
            Envoi(etiquette=etiquette, salons=tuple(salons), envoyer=envoyer)
        )
    return Tournee(envois=tuple(envois), compte=compte, resume=f"{compte} chose(s)")


# --- L'heure ---------------------------------------------------------------


async def test_hors_de_l_heure_rien_ne_part_et_rien_n_est_prepare():
    """Préparer coûte un appel à l'API du jeu : on ne prépare pas pour rien.

    C'est la raison d'ordre entre le compte à rebours et la préparation, et une
    inversion ne se verrait que sur la facture d'appels.
    """
    salons = {1: SalonFactice(1)}
    bot = BotFactice(salons)
    magasin = await _magasin()
    appels: list[datetime] = []
    publication = _publication(_tournee(("essai", ["1"])), appels)
    await magasin.set("publication:essai:heure", "21:00")

    resultat = await faire_la_tournee(publication, bot, magasin, _instant("09:30"))

    assert resultat == "rien à faire"
    assert appels == []
    assert salons[1].envois == []


async def test_forcer_passe_outre_l_heure():
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    await magasin.set("publication:essai:heure", "21:00")
    publication = _publication(_tournee(("essai", ["1"])))

    await faire_la_tournee(
        publication, BotFactice(salons), magasin, _instant("09:30"), forcer=True
    )

    assert salons[1].envois


async def test_a_l_heure_le_post_part():
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    await magasin.set("publication:essai:heure", "09:00")
    publication = _publication(_tournee(("essai", ["1"])))

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert salons[1].envois


async def test_deja_publie_aujourdhui_ne_republie_pas():
    """Le cron appelle toutes les cinq minutes : sans la marque, autant de posts."""
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    await magasin.set("publication:essai:heure", "09:00")
    await magasin.set("publication:essai:derniere", "2026-08-19")
    publication = _publication(_tournee(("essai", ["1"])))

    resultat = await faire_la_tournee(
        publication, BotFactice(salons), magasin, _instant("09:30")
    )

    assert resultat == "rien à faire"
    assert salons[1].envois == []


async def test_l_heure_par_defaut_sert_tant_que_rien_n_est_regle():
    """Un module neuf publie sans qu'on ait rien réglé.

    Sinon sa première journée serait muette, et l'absence de post se lirait
    comme un module qui ne marche pas.
    """
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    publication = _publication(_tournee(("essai", ["1"])), heure_par_defaut="09:00")

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert salons[1].envois


# --- Les lecteurs fournis par la publication -------------------------------


async def test_une_publication_peut_lire_son_heure_ailleurs():
    """Les deux publications historiques rangent leur heure dans la config.

    Le tiroir générique est le défaut, pas une obligation : les déménager
    demanderait une reprise de données qui n'a rien à voir avec ce chantier.
    """
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    await magasin.set("ailleurs", "09:00")

    async def lire_heure(magasin):
        return await magasin.get("ailleurs")

    publication = _publication(_tournee(("essai", ["1"])), lire_heure=lire_heure)

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert salons[1].envois


async def test_une_publication_peut_marquer_ailleurs():
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    marques: list[str] = []

    async def marquer(magasin, date):
        marques.append(date)

    publication = _publication(
        _tournee(("essai", ["1"])), heure_par_defaut="09:00", marquer=marquer
    )

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert marques == ["2026-08-19"]
    assert await magasin.get("publication:essai:derniere") is None


# --- Rien à envoyer --------------------------------------------------------


async def test_une_tournee_vide_rend_sa_raison():
    """« Rien » sans le pourquoi obligerait à deviner ce qu'il manque."""
    magasin = await _magasin()
    publication = _publication(
        Tournee(raison="aucun salon configuré (/essai salon ajouter)"),
        heure_par_defaut="09:00",
    )

    resultat = await faire_la_tournee(
        publication, BotFactice({}), magasin, _instant("09:30")
    )

    assert resultat == "aucun salon configuré (/essai salon ajouter)"


async def test_une_tournee_vide_ne_marque_pas_la_journee():
    """Sinon régler le salon à 09:05 ne donnerait un post que le lendemain."""
    magasin = await _magasin()
    publication = _publication(Tournee(raison="aucun salon"), heure_par_defaut="09:00")

    await faire_la_tournee(publication, BotFactice({}), magasin, _instant("09:30"))

    assert await magasin.get("publication:essai:derniere") is None


# --- Les envois ------------------------------------------------------------


async def test_chaque_envoi_part_dans_chacun_de_ses_salons():
    salons = {1: SalonFactice(1), 2: SalonFactice(2), 3: SalonFactice(3)}
    magasin = await _magasin()
    publication = _publication(
        _tournee(("petite", ["1", "2"]), ("grande", ["3"])),
        heure_par_defaut="09:00",
    )

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert salons[1].envois[0]["contenu"] == "contenu de petite"
    assert salons[2].envois[0]["contenu"] == "contenu de petite"
    assert salons[3].envois[0]["contenu"] == "contenu de grande"


async def test_un_salon_qui_sert_deux_envois_recoit_les_deux():
    """Un salon abonné à deux fourchettes reçoit deux posts, et non un seul."""
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    publication = _publication(
        _tournee(("petite", ["1"]), ("grande", ["1"])), heure_par_defaut="09:00"
    )

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert len(salons[1].envois) == 2


async def test_un_salon_casse_ne_prive_pas_les_autres():
    salons = {
        1: SalonFactice(1, erreur=PermissionError("droits manquants")),
        2: SalonFactice(2),
    }
    magasin = await _magasin()
    publication = _publication(
        _tournee(("essai", ["1", "2"])), heure_par_defaut="09:00"
    )

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert salons[2].envois


async def test_un_seul_succes_suffit_a_marquer_la_journee():
    """Sinon le passage suivant reposterait là où ça avait marché."""
    salons = {1: SalonFactice(1, erreur=PermissionError("non")), 2: SalonFactice(2)}
    magasin = await _magasin()
    publication = _publication(
        _tournee(("essai", ["1", "2"])), heure_par_defaut="09:00"
    )

    await faire_la_tournee(publication, BotFactice(salons), magasin, _instant("09:30"))

    assert await magasin.get("publication:essai:derniere") == "2026-08-19"


async def test_tous_les_salons_en_panne_ne_marquent_rien():
    """Le passage suivant doit réessayer : rien n'est parti."""
    salons = {1: SalonFactice(1, erreur=PermissionError("non"))}
    magasin = await _magasin()
    publication = _publication(_tournee(("essai", ["1"])), heure_par_defaut="09:00")

    resultat = await faire_la_tournee(
        publication, BotFactice(salons), magasin, _instant("09:30")
    )

    assert await magasin.get("publication:essai:derniere") is None
    assert "échec" in resultat
    assert "l'essai" in resultat


async def test_un_salon_introuvable_compte_comme_un_echec():
    """Un salon supprimé lève à la résolution, avant même l'envoi."""
    magasin = await _magasin()
    publication = _publication(_tournee(("essai", ["404"])), heure_par_defaut="09:00")
    bot = BotFactice({})

    resultat = await faire_la_tournee(publication, bot, magasin, _instant("09:30"))

    assert "échec" in resultat
    assert bot.journalisees[0]["echecs"]


# --- La panne de préparation --------------------------------------------


async def test_une_preparation_en_panne_remonte_sans_marquer():
    """L'export du jeu en panne à 09:00 ne doit pas annuler la journée.

    L'exception remonte pour que l'appelant l'isole des autres publications ;
    marquer avant de savoir si l'on peut publier condamnerait le rattrapage.
    """
    magasin = await _magasin()
    publication = _publication(
        RuntimeError("API du jeu injoignable"), heure_par_defaut="09:00"
    )

    with pytest.raises(RuntimeError):
        await faire_la_tournee(
            publication, BotFactice({}), magasin, _instant("09:30")
        )

    assert await magasin.get("publication:essai:derniere") is None


# --- Le journal ------------------------------------------------------------


async def test_le_journal_recoit_le_compte_les_reussis_et_les_echecs():
    salons = {1: SalonFactice(1), 2: SalonFactice(2, erreur=PermissionError("non"))}
    magasin = await _magasin()
    bot = BotFactice(salons)
    publication = _publication(
        _tournee(("petite", ["1", "2"]), compte=7), heure_par_defaut="09:00"
    )

    await faire_la_tournee(publication, bot, magasin, _instant("09:30"))

    journalisee = bot.journalisees[0]
    assert journalisee["compte"] == 7
    assert journalisee["reussis"] == ["<#1> (petite)"]
    assert list(journalisee["echecs"]) == ["<#2> (petite)"]


async def test_l_etiquette_distingue_deux_envois_dans_le_meme_salon():
    """« <#1> a échoué » serait ambigu pour un salon servant deux fourchettes."""
    salons = {1: SalonFactice(1)}
    magasin = await _magasin()
    bot = BotFactice(salons)
    publication = _publication(
        _tournee(("petite", ["1"]), ("grande", ["1"])), heure_par_defaut="09:00"
    )

    await faire_la_tournee(publication, bot, magasin, _instant("09:30"))

    assert bot.journalisees[0]["reussis"] == ["<#1> (petite)", "<#1> (grande)"]


# Le journal en panne n'est pas éprouvé ici : le moteur passe par
# `journaliser_publication`, où la garde vit — un observateur ne doit jamais
# bloquer ce qu'il observe. La doubler dans le moteur ferait deux gardes à
# maintenir. C'est `test_publication_multi.py` qui l'éprouve, sur le vrai bot.


# --- Le compte rendu -------------------------------------------------------


async def test_le_compte_rendu_nomme_la_publication_et_compte_les_envois():
    salons = {1: SalonFactice(1), 2: SalonFactice(2)}
    magasin = await _magasin()
    publication = _publication(
        _tournee(("petite", ["1", "2"]), compte=3), heure_par_defaut="09:00"
    )

    resultat = await faire_la_tournee(
        publication, BotFactice(salons), magasin, _instant("09:30")
    )

    assert "l'essai" in resultat
    assert "2/2" in resultat
    assert "3 chose(s)" in resultat
