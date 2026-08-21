"""La publication quotidienne du tableau des frais.

Deux posts cohabitent désormais : les promotions et le tableau des filiales. Ce
qui est vérifié ici est surtout leur **indépendance** — chacun son heure, ses
salons, sa marque du jour, et la panne de l'un ne doit pas faire taire l'autre.
Une faute à cet endroit ne se remarquerait que le lendemain, en constatant
l'absence d'un post.
"""

from decimal import Decimal

import pytest

from src.bot import EmpireBot
from src.db import Store
from src.modules import frais as module_frais
from src.modules import promos as module_promos
from src.schedule import maintenant_local
from src.source import SourceError


class ServeurFactice:
    def __init__(self, serveur_id: int, nom: str = "Empire Immo"):
        self.id = serveur_id
        self.name = nom


#: Le serveur du tour complet. Les autres tests d'ici lisent la configuration
#: commune, où le serveur d'un salon n'entre pas en compte.
SERVEUR = ServeurFactice(111)


class SalonFactice:
    def __init__(
        self,
        salon_id: int,
        erreur: Exception | None = None,
        serveur: ServeurFactice | None = SERVEUR,
    ):
        self.id = salon_id
        self.name = f"salon-{salon_id}"
        self.guild = serveur
        self.mention = f"<#{salon_id}>"
        self.erreur = erreur
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        if self.erreur:
            raise self.erreur
        self.envois.append({"contenu": contenu, **options})

    @property
    def titres(self) -> list[str]:
        trouves = []
        for envoi in self.envois:
            embeds = envoi.get("embeds") or []
            if envoi.get("embed"):
                embeds = [*embeds, envoi["embed"]]
            trouves += [e.title for e in embeds if getattr(e, "title", None)]
        return trouves

    @property
    def textes(self) -> str:
        """Tout le texte reçu, espaces insécables normalisés."""
        parties = []
        for envoi in self.envois:
            parties.append(envoi.get("contenu") or "")
            embeds = [*(envoi.get("embeds") or [])]
            if envoi.get("embed"):
                embeds.append(envoi["embed"])
            for embed in embeds:
                parties += [embed.title or "", embed.description or ""]
                for champ in embed.fields:
                    parties += [champ.name or "", champ.value or ""]
                if embed.footer:
                    parties.append(embed.footer.text or "")
        return " ".join(parties).replace("\xa0", " ")


class JournalFactice:
    def __init__(self):
        self.publications: list[dict] = []
        self.erreurs: list[str] = []

    async def publication(self, promos, reussis, echecs):
        self.publications.append({"promos": promos, "reussis": reussis, "echecs": echecs})

    async def erreur(self, message):
        self.erreurs.append(message)


class SourceEnPanne:
    async def fetch(self) -> str:
        raise SourceError("API du jeu injoignable")


CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


class SourceFactice:
    async def fetch(self) -> str:
        return CSV


def _maintenant() -> str:
    """L'heure qu'il est, dans le fuseau par défaut.

    Pas « 00:00 » : `doit_publier` ne rattrape que pendant une heure, donc un
    horaire fixe rendrait le test vert ou rouge selon le moment de la journée.
    """
    return maintenant_local("Europe/Paris").strftime("%H:%M")


class BotDeTest(EmpireBot):
    """`EmpireBot` dont on choisit les serveurs.

    `guilds` est une propriété en lecture seule de `discord.Client` : la
    redéfinir dans une sous-classe est le seul moyen de la garnir sans se
    connecter à Discord.
    """

    @property
    def guilds(self):
        return self._serveurs


async def _bot(salons: dict[int, SalonFactice], source=None) -> BotDeTest:
    """Bot sans connexion Discord, comme dans `test_publication_fourchettes`."""
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(BotDeTest)
    bot.store = store
    bot.source = source or SourceFactice()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    bot._serveurs = [SERVEUR]
    # Le tour complet lit les publications des modules chargés : rien n'est écrit
    # en dur dans la boucle, donc rien à écrire en dur ici non plus.
    bot.modules = [module_promos.MODULE, module_frais.MODULE]
    return bot


# --- Le tableau part dans ses salons ----------------------------------------


def _un_tableau(salon: SalonFactice) -> bool:
    """Un tableau des frais est-il arrivé dans ce salon ?

    Sur le titre, sans le comparer mot pour mot : sa mise en forme (emoji,
    libellé) appartient à `test_publication_filiales.py`, et un test d'envoi
    n'a pas à casser quand elle change.
    """
    return any("frais" in (titre or "").lower() for titre in salon.titres)


async def test_le_tableau_est_publie_dans_les_salons_des_filiales():
    salons = {1: SalonFactice(1), 2: SalonFactice(2)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.ajouter_salon_filiales("2")
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert _un_tableau(salons[1])
    assert _un_tableau(salons[2])


async def test_le_tableau_porte_les_frais_et_le_total():
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")
    await bot.store.enregistrer_filiale("MARINE", Decimal(2000), "2026-08-11")

    await bot.publier_filiales_si_lheure(forcer=True)

    texte = salons[1].textes
    assert "ARMEE" in texte and "MARINE" in texte
    assert "210 Ø" in texte


async def test_le_tableau_n_est_pas_publie_dans_les_salons_des_promotions():
    """Deux posts, deux destinations : le tableau des frais n'a rien à faire
    dans le salon des promotions, qui peut être public."""
    salons = {1: SalonFactice(1), 9: SalonFactice(9)}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "9")
    await bot.store.ajouter_salon_filiales("1")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert salons[9].envois == []


async def test_sans_salon_configure_rien_n_est_publie():
    bot = await _bot({})
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")

    resultat = await bot.publier_filiales_si_lheure(forcer=True)

    assert "salon" in resultat
    assert await bot.store.derniere_publication_filiales() is None


async def test_le_tableau_sort_meme_sans_filiale_enregistree():
    """L'absence de post ne se distinguerait pas d'une panne du bot : le tableau
    vide dit au moins que le bot a tourné, et rappelle comment le remplir."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert "aucune" in salons[1].textes.lower()


# --- Une fois par jour, à son heure -----------------------------------------


async def test_rien_avant_l_heure_du_tableau():
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    # 23:59 : impossible d'y être déjà, quelle que soit l'heure du test.
    await bot.store.maj_config(filiales_heure="23:59")

    resultat = await bot.publier_filiales_si_lheure()

    assert resultat == "rien à faire"
    assert salons[1].envois == []


async def test_un_second_passage_le_meme_jour_ne_republie_pas():
    """C'est ce qui permet au cron d'appeler `/tick` toutes les cinq minutes."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.maj_config(filiales_heure=_maintenant())

    await bot.publier_filiales_si_lheure()
    await bot.publier_filiales_si_lheure()

    assert len(salons[1].envois) == 1


async def test_l_heure_du_tableau_est_la_sienne_pas_celle_des_promotions():
    """Régler `heure` (les promotions) ne doit pas déclencher le tableau."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.maj_config(heure="00:00", filiales_heure="23:59")

    assert await bot.publier_filiales_si_lheure() == "rien à faire"


async def test_la_marque_du_jour_est_propre_au_tableau():
    """Partagée, la publication des promotions consommerait le quota du tableau
    et l'un des deux posts ne sortirait jamais."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.maj_config(filiales_heure=_maintenant())

    await bot.publier_filiales_si_lheure()

    assert await bot.store.derniere_publication_filiales() is not None
    assert await bot.store.derniere_publication() is None


# --- Isolation des pannes ---------------------------------------------------


async def test_un_salon_casse_ne_prive_pas_les_autres():
    salons = {1: SalonFactice(1, erreur=RuntimeError("interdit")), 2: SalonFactice(2)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.ajouter_salon_filiales("2")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert salons[2].envois


async def test_le_tableau_ne_depend_pas_de_l_export_du_jeu():
    """Les relevés sont saisis à la main : une API du jeu en panne ne doit pas
    empêcher le tableau de sortir."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons, source=SourceEnPanne())
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert salons[1].envois


async def test_un_echec_total_ne_marque_pas_le_jour():
    """Sinon la panne d'un instant annulerait le tableau de toute la journée."""
    salons = {1: SalonFactice(1, erreur=RuntimeError("interdit"))}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")

    resultat = await bot.publier_filiales_si_lheure(forcer=True)

    assert "échec" in resultat
    assert await bot.store.derniere_publication_filiales() is None


async def test_les_envois_sont_journalises():
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons)
    await bot.store.ajouter_salon_filiales("1")
    await bot.store.enregistrer_filiale("ARMEE", Decimal(1000), "2026-08-11")

    await bot.publier_filiales_si_lheure(forcer=True)

    assert bot.journal.publications


# --- Le tour complet, appelé par /tick --------------------------------------
#
# Le tour lit la configuration **du serveur**, une par entreprise : ce qui est
# éprouvé ici reste l'indépendance des deux publications entre elles. Le
# cloisonnement lui-même est dans `test_publication_par_serveur.py`.


async def test_le_tour_publie_les_promotions_et_le_tableau():
    salons = {1: SalonFactice(1), 9: SalonFactice(9)}
    bot = await _bot(salons)
    magasin = bot.store.pour(SERVEUR.id)
    await magasin.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await magasin.ajouter_salon_fourchette("grosses", "9")
    await magasin.ajouter_salon_filiales("1")
    await magasin.maj_config(heure=_maintenant(), filiales_heure=_maintenant())

    resultat = await bot.publier_tout()

    assert salons[9].envois and salons[1].envois
    # Les deux comptes rendus, sinon `/tick` ne dirait que la moitié de ce qui
    # s'est passé.
    assert "publié" in resultat
    assert "tableau des frais" in resultat.lower()


async def test_une_panne_des_promotions_laisse_sortir_le_tableau():
    """L'export du jeu est la partie fragile ; les relevés saisis à la main ne
    doivent pas en dépendre."""
    salons = {1: SalonFactice(1)}
    bot = await _bot(salons, source=SourceEnPanne())
    magasin = bot.store.pour(SERVEUR.id)
    await magasin.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await magasin.ajouter_salon_fourchette("grosses", "1")
    await magasin.ajouter_salon_filiales("1")
    await magasin.maj_config(heure=_maintenant(), filiales_heure=_maintenant())

    resultat = await bot.publier_tout()

    assert _un_tableau(salons[1])
    # La panne reste visible : avalée en silence, on croirait tout normal.
    assert "injoignable" in resultat or "SourceError" in resultat


async def test_tick_declenche_le_tour_complet():
    """Sans ça le tableau ne sortirait jamais sur Render : le service dort, et
    seul le cron externe peut le réveiller."""
    from aiohttp.test_utils import TestClient, TestServer

    from src import settings
    from src.web import creer_app

    class BotFactice:
        def __init__(self):
            self.tours = 0

        def is_ready(self):
            return True

        async def publier_tout(self, forcer: bool = False) -> str:
            self.tours += 1
            return "publié"

    jeton = settings.TICK_TOKEN
    settings.TICK_TOKEN = "secret123"
    try:
        bot = BotFactice()
        client = TestClient(TestServer(creer_app(bot)))
        await client.start_server()
        try:
            assert (await client.get("/tick?token=secret123")).status == 200
        finally:
            await client.close()
    finally:
        settings.TICK_TOKEN = jeton

    assert bot.tours == 1


async def test_une_panne_du_tableau_laisse_sortir_les_promotions():
    salons = {9: SalonFactice(9)}
    bot = await _bot(salons)
    magasin = bot.store.pour(SERVEUR.id)
    await magasin.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("6e15"))
    await magasin.ajouter_salon_fourchette("grosses", "9")
    await magasin.maj_config(heure=_maintenant(), filiales_heure=_maintenant())

    # Salon des filiales introuvable : `get_channel` renvoie None et l'envoi
    # lèvera.
    await magasin.ajouter_salon_filiales("404")

    resultat = await bot.publier_tout()

    assert salons[9].envois
    assert "publié" in resultat
