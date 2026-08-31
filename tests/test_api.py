"""Tests des routes JSON consommées par le site web.

Architecture de l'authentification, pour comprendre ce qui est testé ici :

    navigateur --(mot de passe, cookie signé)--> Next.js --(API_SECRET)--> bot

Le mot de passe et les comptes vivent **côté Vercel** : le bot ne voit jamais
que des appels serveur-à-serveur portant `API_SECRET`. Ce secret est donc la
seule chose qui protège les écritures, d'où l'insistance des tests ci-dessous
sur son absence et sur les valeurs approchantes.

Ces routes ne sont jamais appelées par un navigateur (Next.js sert de relais),
ce qui évite CORS et empêche le secret de se retrouver dans le code de la page.
"""

import json
from decimal import Decimal

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import settings
from src.db import Store
from src.source import CsvFileSource
from src.web import creer_app

CSV = (
    "# nom: Empire Immo - M8\n"
    "# mise_a_jour: 2026-07-29 12:00:07\n"
    "type,nom,niveau,valeur,loyer,charge,impot,promotion,"
    "construction,embellissement,reparation\n"
    'zones,"Mégapôle",0,173019538387120000000,10,1,1,17,0,0,0\n'
    'zones,"Technopôle",0,2710572934559948,10,1,1,17,0,0,0\n'
    'zones,"Zone portuaire",0,124467906332,10,1,1,17,0,0,0\n'
    'industriels,"Entrepôt",0,302620,1000,300,200,17,0,0,0\n'
    'zones,"Sans promo",0,5000000,10,1,1,0,0,0,0\n'
)

SECRET = "secret-de-test"

#: Espace insécable, celui que `format_money` place avant le symbole. Nommé
#: parce qu'il est invisible dans le code source et qu'un espace ordinaire à sa
#: place ferait échouer les comparaisons sans qu'on voie pourquoi.
NBSP = " "


class BotFactice:
    """Le minimum dont l'API a besoin, sans connexion Discord.

    `charger` reprend l'implémentation d'`EmpireBot` (fetch puis `parse_csv`)
    plutôt que de renvoyer des `Building` fabriqués : c'est le vrai chemin de
    données, y compris le parsing des entiers de 21 chiffres.
    """

    def __init__(self, store, source):
        self.store = store
        self.source = source
        self.pret = True
        self.publications = []

    def is_ready(self):
        return self.pret

    async def charger(self):
        from src.promos import parse_csv

        return parse_csv(await self.source.fetch())

    async def publier_si_lheure(self, forcer=False):
        self.publications.append(forcer)
        return "publié"


@pytest.fixture
async def client_pour():
    """Fabrique de clients HTTP branchés sur l'app réelle.

    Écrit à la main plutôt qu'avec la fixture `aiohttp_client` : celle-ci vient
    de `pytest-aiohttp`, dont on n'a pas besoin par ailleurs. Les clients sont
    fermés à la fin du test, sinon aiohttp signale des sockets en fuite.
    """
    ouverts = []

    async def fabriquer(bot):
        client = TestClient(TestServer(creer_app(bot)))
        await client.start_server()
        ouverts.append(client)
        return client

    yield fabriquer

    for client in ouverts:
        await client.close()


@pytest.fixture
def api(tmp_path, monkeypatch, client_pour):
    """Client HTTP + bot factice, avec un `API_SECRET` connu."""
    monkeypatch.setattr(settings, "API_SECRET", SECRET)
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")

    async def construire():
        store = Store(dsn="")
        await store.connect()
        bot = BotFactice(store, CsvFileSource(chemin))
        return await client_pour(bot), bot

    return construire


def _entetes(secret=SECRET):
    return {"X-Api-Secret": secret}


# --- Le secret partagé protège tout ----------------------------------------

async def test_sans_secret_refuse(api):
    client, _ = await api()
    for route in ("/api/etat", "/api/promos", "/api/config", "/api/template"):
        reponse = await client.get(route)
        assert reponse.status == 401, route


async def test_mauvais_secret_refuse(api):
    client, _ = await api()
    reponse = await client.get("/api/etat", headers=_entetes("pas-le-bon"))
    assert reponse.status == 401


async def test_secret_non_configure_refuse_tout(api, monkeypatch):
    """Un `API_SECRET` vide ne doit pas ouvrir l'API à tous : sans lui, un
    appelant sans en-tête présenterait « » == « » et passerait."""
    client, _ = await api()
    monkeypatch.setattr(settings, "API_SECRET", "")

    assert (await client.get("/api/etat")).status == 401
    assert (await client.get("/api/etat", headers=_entetes(""))).status == 401


async def test_secret_partiel_refuse(api):
    """Ni préfixe ni sur-ensemble ne doivent passer : la comparaison est exacte.

    Pas de variante avec des espaces autour : HTTP les retire de la valeur d'un
    en-tête, ce ne serait donc pas un secret différent.
    """
    client, _ = await api()
    for tentative in (SECRET[:-1], SECRET + "x", SECRET.upper(), "secret_de_test"):
        reponse = await client.get("/api/etat", headers=_entetes(tentative))
        assert reponse.status == 401, tentative


async def test_ecritures_aussi_protegees(api):
    """Le vrai enjeu : sans secret, personne ne doit pouvoir toucher aux
    réglages ni publier dans le Discord."""
    client, bot = await api()

    assert (await client.patch("/api/config", json={"heure": "07:00"})).status == 401
    assert (await client.put("/api/template", json={"content": "x"})).status == 401
    assert (await client.post("/api/publier", json={})).status == 401

    assert bot.publications == []
    assert (await bot.store.config())["heure"] != "07:00"


# --- État -------------------------------------------------------------------

async def test_etat(api):
    client, _ = await api()
    reponse = await client.get("/api/etat", headers=_entetes())
    assert reponse.status == 200
    assert reponse.content_type == "application/json"

    corps = await reponse.json()
    assert corps["pret"] is True
    assert corps["stockage"] == "memoire"      # pas de DSN en test
    assert "fichier local" in corps["source"]


async def test_etat_ne_fuit_jamais_la_cle_dapi(monkeypatch, client_pour):
    """La clé d'API ne doit apparaître dans aucune réponse HTTP, pas plus que
    dans Discord ou les logs."""
    monkeypatch.setattr(settings, "API_SECRET", SECRET)
    from src.source import ApiSource

    store = Store(dsn="")
    await store.connect()
    bot = BotFactice(store, ApiSource(url="https://x/y.csv?key={api_key}",
                                     cle="CLE-SUPER-SECRETE"))
    client = await client_pour(bot)

    corps = await (await client.get("/api/etat", headers=_entetes())).text()
    assert "CLE-SUPER-SECRETE" not in corps
    assert "key=***" in corps


# --- Promotions -------------------------------------------------------------

async def test_promos_couvre_toutes_les_fourchettes_enregistrees(api):
    """Sans paramètre, l'union des bornes : comme `/promos` dans Discord.

    Interroger une seule fourchette obligerait le site à en désigner une, alors
    que la page sert à voir ce qui bouge dans tout ce qui est surveillé.
    """
    client, bot = await api()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("1e16"))
    await bot.store.ajouter_fourchette("petits", Decimal("1e11"), Decimal("1e12"))

    corps = await (await client.get("/api/promos", headers=_entetes())).json()

    noms = [p["nom"] for p in corps["promos"]]
    assert noms == ["Technopôle", "Zone portuaire"]   # tri décroissant
    assert corps["monde"] == "Empire Immo - M8"
    assert "Sans promo" not in noms


async def test_promos_union_ne_se_reduit_pas_a_la_premiere_fourchette(api):
    """Les deux bornes viennent de fourchettes différentes.

    Sans ça, ne consulter que la première passerait le test précédent, où elle
    est la plus large — et la page perdrait silencieusement les promotions des
    autres fourchettes.
    """
    client, bot = await api()
    await bot.store.ajouter_fourchette("petits", Decimal("1e11"), Decimal("1e12"))
    await bot.store.ajouter_fourchette("grosses", Decimal("1e15"), Decimal("1e16"))

    corps = await (await client.get("/api/promos", headers=_entetes())).json()

    dedans = {p["nom"] for p in corps["promos"] if p["dans_fourchette"]}
    assert dedans == {"Technopôle", "Zone portuaire"}


async def test_promos_sans_fourchette_configuree_explique(api):
    """Un bot neuf : une liste vide ferait croire à une absence de promotions."""
    client, _ = await api()

    reponse = await client.get("/api/promos", headers=_entetes())

    assert reponse.status == 400
    assert "fourchette" in (await reponse.json())["erreur"].lower()


async def test_promos_fourchette_en_parametres(api):
    """La page peut simuler une autre fourchette sans modifier la config."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e11"), Decimal("1e16"))

    corps = await (await client.get(
        "/api/promos?min=0&max=1000000", headers=_entetes()
    )).json()

    dedans = [p["nom"] for p in corps["promos"] if p["dans_fourchette"]]
    assert dedans == ["Entrepôt"]
    # La config n'a pas bougé.
    fourchette = (await bot.store.fourchettes())[0]
    assert Decimal(fourchette["prix_min"]) == Decimal("1e11")


async def test_promos_signale_les_repechees(api):
    """`find_promos` complète une fourchette trop pauvre avec les promos les
    plus proches — même comportement que dans Discord. Le site doit pouvoir les
    distinguer, sinon il afficherait un bâtiment hors budget sans le dire."""
    client, _ = await api()

    corps = await (await client.get(
        "/api/promos?min=0&max=1000000", headers=_entetes()
    )).json()

    assert len(corps["promos"]) == 2, "une seule promo dans la fourchette : une repêchée"
    repechee = next(p for p in corps["promos"] if not p["dans_fourchette"])
    assert repechee["nom"] == "Zone portuaire"
    # De combien elle dépasse, pour que le site puisse l'écrire.
    assert Decimal(repechee["ecart_brut"]) > 0


async def test_promos_ecarte_les_types_exclus(api):
    """Le site doit montrer ce que le bot publierait, filtres compris.

    Sans le filtre, la page listerait des promotions qui ne sortent dans aucun
    salon : on croirait le bot en panne, alors qu'il obéit à un réglage que la
    page ne montre pas. La configuration lue est la **commune**, la seule dont le
    site parle, faute de dire de quel serveur il s'agit.
    """
    client, bot = await api()
    await bot.store.exclure_type("industriels")

    corps = await (await client.get(
        "/api/promos?min=0&max=1000000", headers=_entetes()
    )).json()

    assert "Entrepôt" not in [p["nom"] for p in corps["promos"]]


async def test_apercu_ecarte_les_types_exclus(api):
    """Le même filtre sur l'autre appel, sans quoi l'aperçu du site montrerait un
    post que la publication ne produira pas."""
    client, bot = await api()
    await bot.store.exclure_type("industriels")

    corps = await (await client.post(
        "/api/apercu", json={"min": "0", "max": "1000000"}, headers=_entetes()
    )).json()

    assert "Entrepôt" not in str(corps)


async def test_promos_respecte_le_plafond_des_fourchettes(api):
    """Sans bornes, la route couvre l'union des fourchettes : elle doit donc
    respecter leur plafond, sinon la page promettrait un post plus long que celui
    qui sortira le soir."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    await bot.store.regler_plafond_fourchette("tout", 2)

    corps = await (await client.get("/api/promos", headers=_entetes())).json()

    assert len(corps["promos"]) == 2


async def test_promos_avec_bornes_nest_pas_plafonnee(api):
    """Comme `/promos chercher min: max:` : des bornes données à la main posent
    une autre question que « qu'est-ce qui va sortir ? », et couper le résultat
    cacherait des promotions qu'on vient de demander explicitement."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    await bot.store.regler_plafond_fourchette("tout", 2)

    # 1 Q (quintillion) : au-dessus du plus gros bâtiment de l'export, donc les
    # bornes ne retirent rien et le compte ne parle que du plafond.
    corps = await (await client.get(
        "/api/promos?min=0&max=1Q", headers=_entetes()
    )).json()

    assert len(corps["promos"]) == 4


async def test_apercu_respecte_le_plafond_des_fourchettes(api):
    """Le même plafond sur l'autre appel : un aperçu plus long que le post est
    exactement ce que l'aperçu doit empêcher."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    await bot.store.regler_plafond_fourchette("tout", 2)

    corps = await (await client.post("/api/apercu", json={}, headers=_entetes())).json()

    # `corps["promos"]` est le rendu complet (monde, date, liste) : l'aperçu y
    # ajoute les messages Discord, d'où un niveau de plus que `/api/promos`.
    assert len(corps["promos"]["promos"]) == 2


async def test_promos_respecte_les_tranches_des_fourchettes(api):
    """Mêmes raisons que le plafond, à l'échelle d'une plage de prix : sans
    bornes, la route décrit le post du soir, et une liste plus longue que lui
    ferait douter du bot le lendemain."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    # 1 TØ : au-dessus de la Zone portuaire et de l'Entrepôt, sous les deux
    # autres. La tranche ne peut donc en garder qu'un des deux.
    await bot.store.regler_tranche_fourchette(
        "tout", Decimal(0), Decimal("1e12"), 1
    )

    corps = await (await client.get("/api/promos", headers=_entetes())).json()

    assert len(corps["promos"]) == 3


async def test_promos_avec_bornes_nest_pas_tranchee(api):
    """Le témoin de la précédente, et la même règle que le plafond : des bornes
    données à la main sont une recherche libre, où couper cacherait des
    promotions qu'on vient de demander explicitement."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    await bot.store.regler_tranche_fourchette(
        "tout", Decimal(0), Decimal("1e12"), 1
    )

    corps = await (await client.get(
        "/api/promos?min=0&max=1Q", headers=_entetes()
    )).json()

    assert len(corps["promos"]) == 4


async def test_apercu_respecte_les_tranches_des_fourchettes(api):
    """Un aperçu plus long que le post est exactement ce que l'aperçu doit
    empêcher."""
    client, bot = await api()
    await bot.store.ajouter_fourchette("tout", Decimal(0), Decimal("1e30"))
    await bot.store.regler_tranche_fourchette(
        "tout", Decimal(0), Decimal("1e12"), 1
    )

    corps = await (await client.post("/api/apercu", json={}, headers=_entetes())).json()

    assert len(corps["promos"]["promos"]) == 3


async def test_promos_accepte_la_notation_du_jeu(api):
    """« 50 6P » doit être lu comme dans Discord : une seule grammaire de
    saisie pour les deux façades. Lu comme 506 PØ, il exclut le Mégapôle."""
    client, _ = await api()
    corps = await (await client.get(
        "/api/promos?min=1P&max=50%206P", headers=_entetes()
    )).json()
    dedans = [p["nom"] for p in corps["promos"] if p["dans_fourchette"]]
    assert dedans == ["Technopôle"]


async def test_promos_montant_illisible_explique(api):
    """Le message doit dire quel champ et pourquoi : le site l'affiche tel quel,
    sans le reformuler."""
    client, _ = await api()
    reponse = await client.get("/api/promos?min=abc", headers=_entetes())

    assert reponse.status == 400
    erreur = (await reponse.json())["erreur"]
    assert "minimum" in erreur.lower()
    assert "K (mille)" in erreur      # les symboles valides, comme dans Discord


async def test_promos_prix_intacts(api):
    """Le Mégapôle fait 21 chiffres : c'est le test qui compte."""
    client, _ = await api()
    corps = await (await client.get(
        "/api/promos?min=0&max=1Z", headers=_entetes()
    )).json()

    megapole = next(p for p in corps["promos"] if p["nom"] == "Mégapôle")
    assert megapole["prix_brut"] == "173019538387120000000"
    assert megapole["prix"] == f"173.02{NBSP}EØ"


async def test_erreur_inattendue_ne_fuit_pas_la_cle_dapi(monkeypatch, client_pour):
    """Une exception imprévue ne doit livrer que son *type*, jamais son message.

    Le cas réel : `aiohttp` met l'URL complète dans ses exceptions, et cette URL
    porte `?key=<clé>`. Recopier `str(erreur)` dans la réponse HTTP publierait
    donc la clé à qui a déclenché la panne. Vaut pour toute la chaîne, pas
    seulement pour les erreurs de source déjà nettoyées par `src/source.py`.
    """
    monkeypatch.setattr(settings, "API_SECRET", SECRET)
    from src.source import DataSource

    class Explose(DataSource):
        async def fetch(self):
            # Imite une exception d'aiohttp, qui embarque l'URL appelée.
            raise RuntimeError(
                "Cannot connect to host monde8.empireimmo.com "
                "(url=https://monde8.empireimmo.com/api/x.csv?key=CLE-SUPER-SECRETE)"
            )

    store = Store(dsn="")
    await store.connect()
    client = await client_pour(BotFactice(store, Explose()))

    # Bornes explicites : sans elles la requête s'arrêterait sur « aucune
    # fourchette configurée », et la source ne serait jamais appelée — donc
    # l'exception qui porte la clé ne serait jamais levée.
    reponse = await client.get("/api/promos?min=0&max=1Z", headers=_entetes())
    corps = await reponse.text()

    assert reponse.status == 500
    assert "CLE-SUPER-SECRETE" not in corps
    assert "empireimmo.com" not in corps
    # Le type suffit à diagnostiquer, et ne contient jamais de secret.
    assert "RuntimeError" in corps


async def test_promos_source_en_panne(monkeypatch, client_pour):
    """Une source injoignable doit donner un message lisible, pas une 500
    opaque — et surtout pas la clé d'API."""
    monkeypatch.setattr(settings, "API_SECRET", SECRET)
    from src.source import DataSource, SourceError

    class Cassee(DataSource):
        async def fetch(self):
            raise SourceError("API injoignable (ClientError).")

    store = Store(dsn="")
    await store.connect()
    client = await client_pour(BotFactice(store, Cassee()))

    # Bornes explicites : la panne de source est ce qu'on teste, pas l'absence
    # de fourchette, qui répondrait 400 avant d'appeler la source.
    reponse = await client.get("/api/promos?min=0&max=1Z", headers=_entetes())
    assert reponse.status == 502
    assert "injoignable" in (await reponse.json())["erreur"]


# --- Configuration ----------------------------------------------------------

async def test_config_lecture(api):
    client, bot = await api()
    await bot.store.maj_config(heure="09:30")
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")

    corps = await (await client.get("/api/config", headers=_entetes())).json()

    assert corps["heure"] == "09:30"
    fourchette = corps["fourchettes"][0]
    assert fourchette["nom"] == "grosses"
    assert fourchette["prix_min_brut"] == "100000000000000"
    assert fourchette["salons"] == ["111"]


async def test_config_lecture_sans_fourchette(api):
    client, _ = await api()
    corps = await (await client.get("/api/config", headers=_entetes())).json()
    assert corps["fourchettes"] == []


async def test_config_ecriture(api):
    client, bot = await api()

    reponse = await client.patch(
        "/api/config", headers=_entetes(), json={"heure": "07:15"}
    )
    assert reponse.status == 200

    assert (await bot.store.config())["heure"] == "07:15"
    # La réponse renvoie l'état après écriture, pour éviter un second aller-retour.
    assert (await reponse.json())["heure"] == "07:15"


async def test_config_ecriture_partielle(api):
    """Un PATCH ne touche que les champs fournis : régler l'heure ne doit pas
    écraser le fuseau."""
    client, bot = await api()
    await bot.store.maj_config(heure="09:00", fuseau="Europe/Paris")

    await client.patch("/api/config", headers=_entetes(), json={"heure": "10:30"})

    config = await bot.store.config()
    assert config["heure"] == "10:30"
    assert config["fuseau"] == "Europe/Paris"


async def test_config_refuse_les_fourchettes_depuis_le_site(api):
    """Elles désignent des salons dont le site ne peut vérifier ni l'existence,
    ni les permissions du bot : elles restent réglées dans Discord.

    Le refus doit dire où aller, sinon la page paraît cassée.
    """
    client, bot = await api()
    avant = await bot.store.fourchettes()

    reponse = await client.patch(
        "/api/config", headers=_entetes(),
        json={"fourchettes": [{"nom": "pirate", "prix_min": "0", "prix_max": "1",
                               "salons": ["999"]}]},
    )

    assert reponse.status == 400
    erreur = (await reponse.json())["erreur"]
    assert "Discord" in erreur
    assert await bot.store.fourchettes() == avant


async def test_config_refuse_les_anciens_champs_de_prix(api):
    """`prix_min` à la racine n'existe plus : l'accepter écrirait une clé morte
    que plus rien ne lit, en laissant croire au site que c'est enregistré."""
    client, _ = await api()

    reponse = await client.patch(
        "/api/config", headers=_entetes(), json={"prix_min": "100T"}
    )

    assert reponse.status == 400


async def test_config_refuse_une_heure_invalide(api):
    client, bot = await api()
    for heure in ("25:00", "9h", "09:60", ""):
        reponse = await client.patch(
            "/api/config", headers=_entetes(), json={"heure": heure}
        )
        assert reponse.status == 400, heure
    assert (await bot.store.config())["heure"] != "25:00"


async def test_config_refuse_un_fuseau_inconnu(api):
    client, _ = await api()
    reponse = await client.patch(
        "/api/config", headers=_entetes(), json={"fuseau": "Mars/Olympus"}
    )
    assert reponse.status == 400


async def test_config_ignore_les_champs_inconnus(api):
    """Le site ne doit pas pouvoir écrire n'importe quelle clé en base : une
    liste blanche évite qu'un bug de la page corrompe la config."""
    client, bot = await api()

    reponse = await client.patch(
        "/api/config", headers=_entetes(),
        json={"heure": "08:00", "autorises": ["666"], "salons": ["999"]},
    )
    assert reponse.status == 400

    config = await bot.store.config()
    assert "666" not in (config.get("autorises") or [])
    assert config["heure"] != "08:00"


# --- Template ---------------------------------------------------------------

async def test_template_lecture(api):
    client, _ = await api()
    corps = await (await client.get("/api/template", headers=_entetes())).json()
    assert "embeds" in corps["template"]
    # Le site affiche la liste des placeholders à côté de l'éditeur.
    assert "prix" in corps["placeholders"]


async def test_template_ecriture(api):
    client, bot = await api()
    modele = {"embeds": [{"title": "{nom} à {prix}"}]}

    reponse = await client.put("/api/template", headers=_entetes(),
                               json={"template": modele})
    assert reponse.status == 200
    assert await bot.store.template() == modele


async def test_template_invalide_refuse(api):
    client, bot = await api()
    avant = await bot.store.template()

    reponse = await client.put(
        "/api/template", headers=_entetes(),
        json={"template": {"embeds": [{"title": "a"}, {"title": "b"}]}},
    )

    assert reponse.status == 400
    assert "seul" in (await reponse.json())["erreur"]
    assert await bot.store.template() == avant


async def test_template_signale_les_fautes_de_frappe(api):
    """`{prixx}` est accepté (le template reste valide) mais signalé, comme le
    fait `/template charger` dans Discord."""
    client, _ = await api()
    reponse = await client.put(
        "/api/template", headers=_entetes(),
        json={"template": {"embeds": [{"title": "{prixx}"}]}},
    )
    assert reponse.status == 200
    assert (await reponse.json())["inconnus"] == ["prixx"]


# --- Aperçu -----------------------------------------------------------------

async def test_apercu_rend_le_post_sans_publier(api):
    client, bot = await api()

    reponse = await client.post(
        "/api/apercu", headers=_entetes(),
        json={"template": {"embeds": [{"title": "🏷️ {nom}", "description": "{prix}"}]},
              "min": "0", "max": "1Z"},
    )

    assert reponse.status == 200
    corps = await reponse.json()
    assert corps["messages"], "l'aperçu doit contenir au moins un message"
    rendu = json.dumps(corps["messages"], ensure_ascii=False)
    assert "Mégapôle" in rendu and f"173.02{NBSP}EØ" in rendu
    # Rien n'a été publié ni enregistré.
    assert bot.publications == []


async def test_apercu_utilise_le_template_enregistre_par_defaut(api):
    client, bot = await api()
    await bot.store.set_template({"embeds": [{"title": "ENREGISTRÉ {nom}"}]})
    # Sans fourchette il n'y a pas de « post du jour » à prévisualiser : la
    # route refuse, et ce n'est pas ce cas-là qu'on teste ici.
    await bot.store.ajouter_fourchette("grosses", Decimal("0"), Decimal("1e21"))

    corps = await (await client.post(
        "/api/apercu", headers=_entetes(), json={}
    )).json()

    assert "ENREGISTRÉ" in json.dumps(corps["messages"], ensure_ascii=False)


async def test_apercu_nenregistre_pas_le_template_essaye(api):
    """On doit pouvoir tester un template sans l'imposer à la publication du
    lendemain."""
    client, bot = await api()
    avant = await bot.store.template()

    await client.post("/api/apercu", headers=_entetes(),
                      json={"template": {"embeds": [{"title": "essai"}]}})

    assert await bot.store.template() == avant


# --- Publication à la demande ----------------------------------------------

async def test_publier_declenche_la_publication(api):
    client, bot = await api()
    reponse = await client.post("/api/publier", headers=_entetes(), json={})

    assert reponse.status == 200
    assert bot.publications == [True]     # forcé : le bouton du site est explicite


async def test_publier_quand_le_bot_est_deconnecte(api):
    client, bot = await api()
    bot.pret = False

    reponse = await client.post("/api/publier", headers=_entetes(), json={})
    assert reponse.status == 503
    assert bot.publications == []


# --- Contrat général --------------------------------------------------------

async def test_reponses_toujours_en_json(api):
    """Y compris les erreurs : le site fait `await res.json()` sans regarder le
    statut, et une erreur en `text/plain` lui donnerait une exception de
    parsing au lieu du message."""
    client, _ = await api()

    for reponse in (
        await client.get("/api/etat"),                                  # 401
        await client.get("/api/promos?min=abc", headers=_entetes()),    # 400
        await client.get("/api/etat", headers=_entetes()),              # 200
    ):
        assert reponse.content_type == "application/json", reponse.status


async def test_les_routes_du_cron_restent_intactes(api):
    """L'ajout de l'API ne doit pas casser `/health` ni `/tick`, dont dépend le
    keepalive de Render."""
    client, _ = await api()

    sante = await client.get("/health")
    assert sante.status == 200
    assert "ok" in await sante.text()

    # `/tick` garde son propre jeton : `API_SECRET` ne doit pas y donner accès.
    assert (await client.get("/tick", headers=_entetes())).status == 403


async def test_api_secret_absent_des_journaux(api, caplog):
    """`JournalSansSecret` masque la query string, mais le secret voyage en
    en-tête : on vérifie qu'il ne ressort pas non plus dans les logs
    applicatifs (message d'erreur, exception…)."""
    client, _ = await api()
    with caplog.at_level("DEBUG"):
        await client.get("/api/etat", headers=_entetes())
        await client.get("/api/etat", headers=_entetes("mauvais"))

    assert SECRET not in caplog.text
