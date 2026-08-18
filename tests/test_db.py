"""Tests du Store en mode mémoire (sans Postgres)."""

import sys
from decimal import Decimal

import pytest

from src.db import Store
from src.template import TEMPLATE_DEFAUT


@pytest.fixture
def store():
    return Store(dsn="")


@pytest.mark.asyncio
async def test_sans_dsn_pas_persistant(store):
    await store.connect()
    assert not store.persistant


@pytest.mark.asyncio
async def test_config_par_defaut_est_la_fourchette_100T_6P(store):
    config = await store.config()
    assert Decimal(config["prix_min"]) == Decimal("1e14")
    assert Decimal(config["prix_max"]) == Decimal("6e15")
    assert config["fuseau"] == "Europe/Paris"


@pytest.mark.asyncio
async def test_maj_config_conserve_les_autres_champs(store):
    await store.maj_config(heure="20:30")
    config = await store.config()
    assert config["heure"] == "20:30"
    # La fourchette n'a pas bougé.
    assert Decimal(config["prix_min"]) == Decimal("1e14")


@pytest.mark.asyncio
async def test_maj_config_ignore_les_none(store):
    await store.maj_config(heure="20:30")
    await store.maj_config(heure=None, fuseau="Europe/Lisbon")
    config = await store.config()
    assert config["heure"] == "20:30"
    assert config["fuseau"] == "Europe/Lisbon"


@pytest.mark.asyncio
async def test_template_par_defaut_si_aucun_charge(store):
    assert await store.template() == TEMPLATE_DEFAUT


@pytest.mark.asyncio
async def test_template_personnalise_remplace_le_defaut(store):
    mien = {"embeds": [{"title": "{nom}"}]}
    await store.set_template(mien)
    assert await store.template() == mien


@pytest.mark.asyncio
async def test_idempotence_de_la_publication(store):
    assert await store.derniere_publication() is None
    await store.marquer_publie("2026-07-28")
    assert await store.derniere_publication() == "2026-07-28"


@pytest.mark.asyncio
async def test_oublier_publication_permet_de_retester(store):
    """Sans ça, impossible de retester le déclenchement le même jour."""
    await store.marquer_publie("2026-07-28")
    await store.oublier_publication()
    assert await store.derniere_publication() is None


@pytest.mark.asyncio
async def test_oublier_publication_sans_publication_prealable(store):
    await store.oublier_publication()
    assert await store.derniere_publication() is None


# --- Liste de salons --------------------------------------------------------

@pytest.mark.asyncio
async def test_aucun_salon_par_defaut(store):
    assert await store.salons() == []


@pytest.mark.asyncio
async def test_ajouter_un_salon(store):
    assert await store.ajouter_salon("123") is True
    assert await store.salons() == ["123"]


@pytest.mark.asyncio
async def test_ajouter_deux_fois_le_meme_salon_est_sans_effet(store):
    await store.ajouter_salon("123")
    assert await store.ajouter_salon("123") is False
    assert await store.salons() == ["123"]


@pytest.mark.asyncio
async def test_ordre_dajout_conserve(store):
    """L'ordre d'affichage doit être stable, pas dépendant d'un set."""
    for salon in ("300", "100", "200"):
        await store.ajouter_salon(salon)
    assert await store.salons() == ["300", "100", "200"]


@pytest.mark.asyncio
async def test_retirer_un_salon(store):
    await store.ajouter_salon("123")
    await store.ajouter_salon("456")
    assert await store.retirer_salon("123") is True
    assert await store.salons() == ["456"]


@pytest.mark.asyncio
async def test_retirer_un_salon_absent_ne_plante_pas(store):
    assert await store.retirer_salon("999") is False


# --- Migration de l'ancien `salon_id` unique --------------------------------

@pytest.mark.asyncio
async def test_ancien_salon_id_devient_une_liste(store):
    """La config en base d'avant le multi-salon doit rester utilisable."""
    await store.set("config", {"salon_id": "123"})
    assert await store.salons() == ["123"]


@pytest.mark.asyncio
async def test_ancien_salon_id_ignore_si_liste_presente(store):
    """Une fois migré, `salons` fait foi — sinon l'ancien salon ressusciterait."""
    await store.set("config", {"salon_id": "123", "salons": ["456"]})
    assert await store.salons() == ["456"]


@pytest.mark.asyncio
async def test_retirer_le_dernier_salon_migre_ne_le_fait_pas_revenir(store):
    """Le piège : `salon_id` doit être effacé, pas seulement ignoré."""
    await store.set("config", {"salon_id": "123"})
    await store.retirer_salon("123")
    assert await store.salons() == []
    assert not (await store.config()).get("salon_id")


@pytest.mark.asyncio
async def test_salon_id_vide_ne_cree_pas_de_salon_fantome(store):
    await store.set("config", {"salon_id": None})
    assert await store.salons() == []
    await store.set("config", {"salon_id": ""})
    assert await store.salons() == []


# --- Salon de logs ----------------------------------------------------------

@pytest.mark.asyncio
async def test_aucun_salon_de_logs_par_defaut(store):
    assert await store.salon_logs() is None


@pytest.mark.asyncio
async def test_definir_puis_desactiver_le_salon_de_logs(store):
    await store.maj_config(logs_salon_id="789")
    assert await store.salon_logs() == "789"
    await store.desactiver_logs()
    assert await store.salon_logs() is None


# --- Membres autorisés ------------------------------------------------------

@pytest.mark.asyncio
async def test_aucun_membre_autorise_par_defaut(store):
    """Seuls les administrateurs, jusqu'à ce qu'on en ajoute."""
    assert await store.autorises() == []


@pytest.mark.asyncio
async def test_ajouter_puis_retirer_un_membre(store):
    assert await store.autoriser("111") is True
    assert await store.autorises() == ["111"]
    assert await store.retirer_autorise("111") is True
    assert await store.autorises() == []


@pytest.mark.asyncio
async def test_ajouter_deux_fois_le_meme_membre(store):
    await store.autoriser("111")
    assert await store.autoriser("111") is False
    assert await store.autorises() == ["111"]


@pytest.mark.asyncio
async def test_retirer_un_membre_absent(store):
    assert await store.retirer_autorise("111") is False


@pytest.mark.asyncio
async def test_ordre_dajout_conserve(store):
    for membre in ("333", "111", "222"):
        await store.autoriser(membre)
    assert await store.autorises() == ["333", "111", "222"]


@pytest.mark.asyncio
async def test_ids_normalises_en_texte(store):
    """Discord donne des int ; JSONB les rendrait tels quels et la comparaison
    avec `str(interaction.user.id)` échouerait silencieusement."""
    await store.autoriser(111)
    assert await store.autorises() == ["111"]


# --- tout() : l'état entier, pour le copier d'une base à l'autre ------------


@pytest.mark.asyncio
async def test_tout_rend_l_etat_entier(store):
    """Un déménagement de base doit pouvoir lire ce qu'il y a, sans le savoir.

    Clé par clé avec une liste écrite en dur, la clé ajoutée après serait
    oubliée en silence — et ne manquerait qu'une fois l'ancienne base éteinte.
    """
    await store.set("config", {"heure": "09:00"})
    await store.set("filiales", [{"nom": "A"}])

    assert await store.tout() == {
        "config": {"heure": "09:00"},
        "filiales": [{"nom": "A"}],
    }


@pytest.mark.asyncio
async def test_tout_sur_une_base_neuve_est_vide(store):
    """Vide et non les défauts d'usine : `tout` dit ce qui est **enregistré**.

    Les défauts recopiés dans la cible lui inventeraient une config plate, que
    `Store` prend justement pour la signature d'un bot à migrer.
    """
    assert await store.tout() == {}


# --- Connexion à une base managée -------------------------------------------


class _ConnexionFactice:
    def __init__(self):
        self.executes: list[str] = []
        #: Ce que la table est censée contenir, telle que Postgres la rendrait :
        #: `valeur` en texte JSON, comme le fait la colonne JSONB via asyncpg.
        self.lignes: list[dict] = []

    async def execute(self, sql, *args):
        self.executes.append(sql)

    async def fetch(self, sql, *args):
        return list(self.lignes)


class _PoolFactice:
    def __init__(self):
        self.connexion = _ConnexionFactice()

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.connexion

    async def __aexit__(self, *_):
        return False

    async def close(self):
        pass


class _AsyncpgFactice:
    """Le module `asyncpg` vu par `Store.connect`, pour lire ses arguments.

    `connect` fait son `import asyncpg` dans le corps de la fonction : un faux
    posé dans `sys.modules` est donc celui qu'elle trouvera.
    """

    def __init__(self):
        self.kwargs: dict = {}
        self.pool = _PoolFactice()

    async def create_pool(self, dsn, **kwargs):
        self.kwargs = {"dsn": dsn, **kwargs}
        return self.pool


@pytest.fixture
def asyncpg_factice(monkeypatch):
    faux = _AsyncpgFactice()
    monkeypatch.setitem(sys.modules, "asyncpg", faux)
    return faux


@pytest.mark.asyncio
async def test_connect_desactive_le_cache_de_prepared_statements(asyncpg_factice):
    """Le pooler en mode transaction ne partage pas les prepared statements.

    asyncpg en prépare un pour chaque requête paramétrée : contre un pooler en
    mode transaction, la deuxième requête échoue sur un statement que la
    connexion reprise ne connaît pas. Le défaut ne se verrait pas au démarrage
    mais au premier `/config`, et seulement en production.

    Zéro coûte une préparation par requête — quelques-unes par ping de cron,
    donc rien de mesurable — contre une panne entière si la chaîne de connexion
    pointe le port 6543.
    """
    store = Store(dsn="postgresql://qui:que@ou.example:6543/postgres")

    await store.connect()

    assert asyncpg_factice.kwargs["statement_cache_size"] == 0


@pytest.mark.asyncio
async def test_connect_ferme_la_table_a_la_cle_publique(asyncpg_factice):
    """Supabase publie chaque table de `public` en HTTPS avec la clé anonyme.

    Cette clé est publique par conception. Sans RLS, `bot_state` — salons,
    membres autorisés, template — serait lisible par quiconque a l'URL du
    projet. Le propriétaire de la table échappe à RLS, donc le bot continue de
    lire et d'écrire ; seuls `anon` et `authenticated` sont fermés dehors.
    """
    store = Store(dsn="postgresql://qui:que@ou.example:5432/postgres")

    await store.connect()

    sql = " ".join(asyncpg_factice.pool.connexion.executes)
    assert "ENABLE ROW LEVEL SECURITY" in sql, sql


@pytest.mark.asyncio
async def test_tout_rend_chaque_ligne_de_la_table(asyncpg_factice):
    """Sur Postgres, `tout` est le chemin qui sert vraiment au déménagement.

    Chaque ligne, et chaque valeur décodée : une seule ligne rendue perdrait
    quatre clés sur cinq, et du JSON laissé en texte se recopierait en base comme
    une chaîne — le bot relirait une config qu'il ne saurait pas lire.
    """
    store = Store(dsn="postgresql://qui:que@ou.example:5432/postgres")
    await store.connect()
    asyncpg_factice.pool.connexion.lignes = [
        {"cle": "config", "valeur": '{"heure": "20:30"}'},
        {"cle": "derniere_publication", "valeur": '"2026-08-17"'},
    ]

    assert await store.tout() == {
        "config": {"heure": "20:30"},
        "derniere_publication": "2026-08-17",
    }
