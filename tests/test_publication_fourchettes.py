"""Tests de la publication par fourchette.

Chaque fourchette a ses propres bornes **et** ses propres salons : le salon
d'une fourchette ne doit recevoir que ce que ses bornes désignent.

Deux propriétés sont vérifiées ici parce qu'une faute y serait silencieuse :
l'export n'est lu **qu'une fois** quel que soit le nombre de fourchettes, et une
fourchette en panne n'empêche pas les suivantes de publier.
"""

from decimal import Decimal

import pytest

from src.bot import EmpireBot
from src.db import Store
from src.source import SourceError

#: Deux promos très éloignées, pour qu'une fourchette étroite en isole une seule.
CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
"""


class SourceComptee:
    """Source qui compte ses lectures.

    Le compteur est le cœur du test « une seule lecture » : vérifier par
    relecture du code laisserait passer une régression au premier
    réarrangement de la boucle.
    """

    def __init__(self, texte: str = CSV):
        self.texte = texte
        self.lectures = 0

    async def fetch(self) -> str:
        self.lectures += 1
        return self.texte


class SourceEnPanne:
    async def fetch(self) -> str:
        raise SourceError("API du jeu injoignable")


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

    @property
    def titres(self) -> list[str]:
        """Titres des embeds reçus, pour savoir *quoi* a été publié ici."""
        trouves = []
        for envoi in self.envois:
            for embed in envoi.get("embeds") or []:
                titre = getattr(embed, "title", None)
                if titre:
                    trouves.append(titre)
        return trouves


class JournalFactice:
    def __init__(self):
        self.publications: list[dict] = []
        self.erreurs: list[str] = []

    async def publication(self, promos, reussis, echecs):
        self.publications.append({"promos": promos, "reussis": reussis, "echecs": echecs})

    async def erreur(self, message):
        self.erreurs.append(message)


async def _bot(salons: dict[int, SalonFactice], source=None) -> EmpireBot:
    """Bot sans connexion Discord. Les fourchettes sont réglées par le test."""
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.source = source or SourceComptee()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    return bot


# --- Chaque fourchette vers ses salons --------------------------------------


async def test_chaque_fourchette_publie_dans_ses_salons():
    """Le cas demandé : fourchette 1 -> salons 1 et 2, fourchette 2 -> salon 3."""
    salons = {1: SalonFactice(1), 2: SalonFactice(2), 3: SalonFactice(3)}
    bot = await _bot(salons)

    # « grosses » ne contient que le Technopôle (2,71 PØ).
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "1")
    await bot.store.ajouter_salon_fourchette("grosses", "2")

    # « petits » ne contient que la Zone portuaire (124,47 GØ).
    await bot.store.ajouter_fourchette("petits", Decimal("1e11"), Decimal("1e12"))
    await bot.store.ajouter_salon_fourchette("petits", "3")

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois and salons[2].envois and salons[3].envois


async def test_un_salon_ne_recoit_que_les_promos_de_sa_fourchette():
    """La propriété qui fait l'intérêt de la fonctionnalité.

    Sans elle, tout marcherait « à peu près » : chaque salon recevrait un post,
    mais pas le bon — l'erreur la plus difficile à remarquer.
    """
    salons = {1: SalonFactice(1), 3: SalonFactice(3)}
    bot = await _bot(salons)

    # Repêchage désactivé de fait : chaque fourchette contient déjà 1 promo, et
    # CIBLE_MINIMUM vaut 2, donc chacune repêchera l'autre promo. On vérifie
    # donc le *rang 1*, la promo réellement dans la fourchette.
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "1")
    await bot.store.ajouter_fourchette("petits", Decimal("1e11"), Decimal("1e12"))
    await bot.store.ajouter_salon_fourchette("petits", "3")

    await bot.publier_si_lheure(forcer=True)

    assert "Technopôle" in salons[1].titres[0]
    assert "Zone portuaire" in salons[3].titres[0]


async def test_compte_rendu_compte_les_envois_et_non_les_salons():
    """Un salon servant deux fourchettes reçoit deux posts.

    « 2/2 salons » là où il n'y a qu'un salon ferait croire à un second salon
    configuré par erreur — et à l'inverse dédupliquer annoncerait un envoi de
    moins que ce qui est réellement parti.
    """
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "1")
    await bot.store.ajouter_fourchette("petits", Decimal("1e11"), Decimal("1e12"))
    await bot.store.ajouter_salon_fourchette("petits", "1")

    resultat = await bot.publier_si_lheure(forcer=True)

    assert len(salons[1].envois) == 2
    assert "2/2 envois" in resultat, resultat
    assert "salon" not in resultat, resultat


async def test_aucune_fourchette_ne_publie_rien():
    bot = await _bot({})

    resultat = await bot.publier_si_lheure(forcer=True)

    assert "fourchette" in resultat


async def test_fourchette_sans_salon_est_ignoree():
    """Elle est conservée en config, mais n'a nulle part où publier."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("orpheline", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_fourchette("servie", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("servie", "1")

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois


async def test_aucune_fourchette_avec_salon_ne_publie_rien():
    """Le compte rendu doit dire quoi faire, pas annoncer un échec.

    Une fourchette sans salon publiée quand même produirait « échec dans les 0
    salon(s) » — un message qui contient bien le mot « salon », mais qui fait
    chercher une panne là où il manque seulement un réglage.
    """
    bot = await _bot({})
    await bot.store.ajouter_fourchette("orpheline", Decimal("0"), Decimal("6e15"))

    resultat = await bot.publier_si_lheure(forcer=True)

    assert "/fourchette salon ajouter" in resultat
    assert "échec" not in resultat


# --- L'export n'est lu qu'une fois ------------------------------------------


async def test_export_lu_une_seule_fois_pour_trois_fourchettes():
    """Sinon N fourchettes = N appels à l'API du jeu, pour les mêmes données."""
    salons = {1: SalonFactice(1), 2: SalonFactice(2), 3: SalonFactice(3)}
    source = SourceComptee()
    bot = await _bot(salons, source=source)

    for index, nom in enumerate(("a", "b", "c"), start=1):
        await bot.store.ajouter_fourchette(nom, Decimal("0"), Decimal("6e15"))
        await bot.store.ajouter_salon_fourchette(nom, str(index))

    await bot.publier_si_lheure(forcer=True)

    assert source.lectures == 1


# --- Isolation des pannes ---------------------------------------------------


async def test_une_fourchette_en_panne_ne_prive_pas_les_suivantes():
    """Le salon de « a » refuse l'envoi ; « b » doit publier quand même."""
    salons = {
        1: SalonFactice(1, erreur=RuntimeError("403 Forbidden")),
        2: SalonFactice(2),
    }
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_fourchette("b", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("b", "2")

    await bot.publier_si_lheure(forcer=True)

    assert not salons[1].envois
    assert salons[2].envois


async def test_un_salon_casse_ne_prive_pas_les_autres_de_sa_fourchette():
    """L'isolation par salon d'avant les fourchettes, conservée."""
    salons = {
        1: SalonFactice(1),
        2: SalonFactice(2, erreur=RuntimeError("403")),
        3: SalonFactice(3),
    }
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    for salon_id in ("1", "2", "3"):
        await bot.store.ajouter_salon_fourchette("a", salon_id)

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois and salons[3].envois
    assert not salons[2].envois


async def test_marque_publie_si_au_moins_un_salon_a_recu():
    """Sinon le passage suivant reposterait là où ça avait marché."""
    salons = {1: SalonFactice(1), 2: SalonFactice(2, erreur=RuntimeError("403"))}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_fourchette("b", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("b", "2")

    await bot.publier_si_lheure(forcer=True)

    assert await bot.store.derniere_publication() is not None


async def test_ne_marque_pas_publie_si_tout_echoue():
    """La journée doit rester à publier : le prochain passage réessaie."""
    salons = {1: SalonFactice(1, erreur=RuntimeError("403"))}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")

    await bot.publier_si_lheure(forcer=True)

    assert await bot.store.derniere_publication() is None


async def test_source_en_panne_ne_marque_rien_et_leve():
    """La panne doit remonter *avant* d'avoir touché à Discord.

    Sinon une panne de l'API à 09:00 annulerait la publication de la journée
    entière, la marque du jour ayant été posée.
    """
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons, source=SourceEnPanne())
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")

    with pytest.raises(SourceError):
        await bot.publier_si_lheure(forcer=True)

    assert not salons[1].envois
    assert await bot.store.derniere_publication() is None


# --- Migration vue depuis la publication ------------------------------------


async def test_config_plate_publie_encore():
    """Le scénario du déploiement : la prod a une config plate, elle doit poster.

    C'est le test qui garantit qu'une mise à jour du bot ne fait pas taire un
    salon déjà configuré.
    """
    salons = {7: SalonFactice(7)}
    bot = await _bot(salons)
    await bot.store.set(
        "config", {"prix_min": "0", "prix_max": "6e15", "salons": ["7"]}
    )

    await bot.publier_si_lheure(forcer=True)

    assert salons[7].envois
