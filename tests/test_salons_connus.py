"""Noms de salons et de serveurs, mémorisés pour le site.

Le site n'a pas accès à Discord : il ne connaît ni les noms de salons, ni ceux
des serveurs. Avec un seul serveur, afficher `123456` était austère ; avec deux,
c'est ambigu — rien ne dit d'où vient le salon.

Le bot connaît les deux au moment du réglage. Il les écrit, si bien que
`/api/config` ne dépend pas de l'état de la connexion Discord.
"""

from decimal import Decimal

from src.db import Store


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def test_rien_de_connu_par_defaut():
    store = await _store()
    assert await store.salons_connus() == {}
    assert await store.serveurs() == {}


async def test_memorise_le_salon_et_son_serveur():
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    assert await store.salons_connus() == {
        "1": {"nom": "promos", "serveur": "111"}
    }
    assert await store.serveurs() == {"111": "Empire Immo"}


async def test_nom_rafraichi_quand_le_salon_est_renomme():
    """Sinon le site afficherait indéfiniment l'ancien nom."""
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("1", "bonnes-affaires", "111", "Empire Immo SA")

    assert (await store.salons_connus())["1"]["nom"] == "bonnes-affaires"
    assert (await store.serveurs())["111"] == "Empire Immo SA"


async def test_deux_salons_du_meme_serveur_partagent_son_nom():
    """Le nom du serveur est stocké une fois : deux copies divergeraient."""
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("2", "annonces", "111", "Empire Immo")

    assert len(await store.serveurs()) == 1
    assert len(await store.salons_connus()) == 2


async def test_oublie_les_salons_plus_attaches_a_aucune_fourchette():
    """Sinon la table grossit indéfiniment avec des salons dont plus personne
    ne parle."""
    store = await _store()
    await store.ajouter_fourchette("a", Decimal("0"), Decimal("1e15"))
    await store.ajouter_salon_fourchette("a", "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("2", "vieux", "111", "Empire Immo")

    efface = await store.oublier_salons_orphelins()

    assert efface == 1
    assert list(await store.salons_connus()) == ["1"]


async def test_oublie_aussi_le_serveur_devenu_inutile():
    """Un serveur dont plus aucun salon ne dépend n'a plus à être nommé."""
    store = await _store()
    await store.ajouter_fourchette("a", Decimal("0"), Decimal("1e15"))
    await store.ajouter_salon_fourchette("a", "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("9", "autre", "222", "Second serveur")

    await store.oublier_salons_orphelins()

    assert await store.serveurs() == {"111": "Empire Immo"}


async def test_oublier_ne_touche_pas_un_salon_servant_deux_fourchettes():
    """Retiré d'une seule fourchette, il reste servi par l'autre."""
    store = await _store()
    for nom in ("a", "b"):
        await store.ajouter_fourchette(nom, Decimal("0"), Decimal("1e15"))
        await store.ajouter_salon_fourchette(nom, "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    await store.retirer_salon_fourchette("a", "1")
    await store.oublier_salons_orphelins()

    assert list(await store.salons_connus()) == ["1"]


async def test_oublier_sans_rien_a_faire_renvoie_zero():
    """Et n'écrit pas en base pour rien."""
    store = await _store()
    assert await store.oublier_salons_orphelins() == 0


class ServeurFactice:
    def __init__(self, serveur_id: int, nom: str):
        self.id = serveur_id
        self.name = nom


class SalonFactice:
    def __init__(self, salon_id: int, nom: str, serveur: ServeurFactice):
        self.id = salon_id
        self.name = nom
        self.guild = serveur
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})


async def test_resoudre_salon_memorise_son_nom():
    """Le rafraîchissement : chaque résolution met le nom à jour.

    C'est ce qui corrige un salon renommé au premier post suivant, sans
    intervention.
    """
    from src.bot import EmpireBot

    salon = SalonFactice(1, "bonnes-affaires", ServeurFactice(111, "Empire Immo"))
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.get_channel = {1: salon}.get

    await bot.resoudre_salon("1")

    assert (await store.salons_connus())["1"]["nom"] == "bonnes-affaires"
