"""Une configuration par serveur : `Store.pour(serveur_id)`.

Jusqu'ici le bot avait **une** configuration pour tout le monde. Régler une
fourchette dans le serveur d'une entreprise la changeait chez les autres, et
personne ne pouvait le deviner en tapant la commande.

`store.pour("111")` rend le même stockage vu par un seul serveur : les mêmes
soixante accesseurs, mais chacun dans son tiroir. Ce qui se vérifie ici est
uniquement le **cloisonnement** — que le contenu du tiroir de l'un n'apparaisse
pas chez l'autre, et que ce qui est délibérément partagé le reste.

Deux choses ne sont pas cloisonnées, et c'est voulu :

- le **cache des noms de salons**, cosmétique et destiné au site : le nom d'un
  salon ne dépend pas de qui le regarde ;
- la **table des rôles mentionnés**, déjà rangée par serveur avant ce
  changement, et lue telle quelle par le site.

Il n'y a **pas de repli** : un serveur neuf ne voit rien de la configuration
commune. C'est assumé — `/reglages importer` est le pont, et un repli
silencieux ferait republier à un serveur ce qu'on venait de lui retirer.
"""

import inspect
from decimal import Decimal

import pytest

from src.db import Store, VueServeur


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


# --- Le cloisonnement lui-même ----------------------------------------------


async def test_deux_serveurs_ont_des_fourchettes_separees():
    """Le cœur de l'étape : régler chez l'un ne règle plus chez l'autre."""
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert [f["nom"] for f in await a.fourchettes()] == ["grosses"]
    assert await b.fourchettes() == []


async def test_chaque_serveur_a_son_heure_de_publication():
    """`/fourchette heure` dans un serveur ne déplace pas le post de l'autre."""
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.maj_config(heure="07:30")
    await b.maj_config(heure="21:00")

    assert (await a.config())["heure"] == "07:30"
    assert (await b.config())["heure"] == "21:00"


async def test_chaque_serveur_a_sa_marque_du_jour():
    """Une panne chez l'un n'annule plus la journée des autres.

    C'est le bénéfice qui vient avec le cloisonnement : la trace « déjà publié »
    était unique, si bien qu'un serveur ayant publié consommait la journée de
    tous.
    """
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.marquer_publie("2026-08-19")

    assert await a.derniere_publication() == "2026-08-19"
    assert await b.derniere_publication() is None


async def test_chaque_serveur_a_ses_filiales():
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.enregistrer_filiale("MEGAPOLE", Decimal("1e12"), "2026-08-19")

    assert [f.nom for f in await a.filiales()] == ["MEGAPOLE"]
    assert await b.filiales() == []


async def test_chaque_serveur_a_sa_liste_d_acces():
    """Autoriser quelqu'un chez soi ne lui ouvre pas les autres serveurs.

    C'était le plus surprenant des réglages partagés : inviter le bot quelque
    part donnait à ses administrateurs la main sur toutes les entreprises.
    """
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.autoriser("42")

    assert await a.autorises() == ["42"]
    assert await b.autorises() == []


async def test_chaque_serveur_a_son_template():
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")

    await a.set_template({"embeds": [{"title": "Chez A"}]})

    assert (await a.template())["embeds"][0]["title"] == "Chez A"
    assert (await b.template()) != (await a.template())


# --- La configuration commune n'est pas touchée -----------------------------


async def test_ecrire_dans_un_serveur_ne_change_pas_la_config_commune():
    """Le site lit encore la configuration commune : elle doit rester intacte.

    C'est aussi ce qui rend `/reglages importer` sans risque — si le résultat ne
    va pas, l'original est toujours là.
    """
    commun = await _store()
    await commun.ajouter_fourchette("historique", Decimal("0"), Decimal("1e12"))

    await commun.pour("111").ajouter_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    )

    assert [f["nom"] for f in await commun.fourchettes()] == ["historique"]


async def test_un_serveur_neuf_ne_voit_rien_de_la_config_commune():
    """Pas de repli silencieux : un serveur part vide, et `/reglages importer`
    est le seul pont.

    Un repli serait pire qu'un vide : un serveur qui aurait délibérément
    supprimé ses fourchettes hériterait de celles de la config commune, et
    publierait ce qu'on venait de lui retirer.
    """
    commun = await _store()
    await commun.ajouter_fourchette("historique", Decimal("0"), Decimal("1e12"))
    await commun.maj_config(heure="06:00")

    serveur = commun.pour("111")

    assert await serveur.fourchettes() == []
    # L'heure, elle, retombe sur le défaut d'usine et non sur celle du commun :
    # une publication sans salon ne part nulle part, l'heure est sans effet.
    assert (await serveur.config())["heure"] != "06:00"


# --- Ce qui reste délibérément partagé --------------------------------------


async def test_le_cache_des_noms_de_salons_est_partage():
    """Cosmétique et destiné au site : le nom d'un salon ne dépend pas du lecteur.

    Cloisonné, il se remplirait en double et le site n'en verrait qu'une moitié.
    """
    commun = await _store()
    a = commun.pour("111")

    await a.memoriser_salon("1", "promos", "111", "Empire Immo")

    attendu = {"1": {"nom": "promos", "serveur": "111"}}
    assert await commun.salons_connus() == attendu
    # Depuis les vues aussi, y compris celle d'un serveur qui n'a rien écrit :
    # comparer deux vues entre elles laisserait passer un cache cloisonné, où
    # les deux répondraient un `{}` identique.
    assert await a.salons_connus() == attendu
    assert await commun.pour("222").salons_connus() == attendu

    assert await commun.serveurs() == {"111": "Empire Immo"}
    assert await a.serveurs() == {"111": "Empire Immo"}


async def test_le_menage_du_cache_regarde_tous_les_serveurs():
    """Le cache étant commun, son ménage ne peut pas se décider depuis un seul.

    Sinon retirer un salon dans un serveur effacerait les noms des salons de
    tous les autres — ils n'apparaissent dans aucune de *ses* fourchettes.
    """
    commun = await _store()
    a = commun.pour("111")
    b = commun.pour("222")
    await a.ajouter_fourchette("a", Decimal("0"), Decimal("1e15"))
    await a.ajouter_salon_fourchette("a", "1")
    await b.ajouter_fourchette("b", Decimal("0"), Decimal("1e15"))
    await b.ajouter_salon_fourchette("b", "2")
    await a.memoriser_salon("1", "chez-a", "111", "A")
    await b.memoriser_salon("2", "chez-b", "222", "B")

    assert await a.oublier_salons_orphelins() == 0
    assert sorted(await commun.salons_connus()) == ["1", "2"]


async def test_le_menage_garde_les_salons_des_autres_publications():
    """Un salon ne servant qu'au tableau des frais n'est pas un orphelin.

    Le ménage ne regardait que les fourchettes : le salon du tableau était
    effacé à chaque passage, et le site perdait son nom.
    """
    commun = await _store()
    serveur = commun.pour("111")
    await serveur.ajouter_salon_filiales("7")
    await serveur.memoriser_salon("7", "frais", "111", "A")

    assert await serveur.oublier_salons_orphelins() == 0
    assert list(await commun.salons_connus()) == ["7"]


async def test_le_menage_garde_les_salons_dune_publication_de_module():
    """Un module qui déclare sa publication range ses salons dans son tiroir.

    `publication:<cle>:salons` : la convention du stockage, celle que le ménage
    doit reconnaître pour ne pas effacer les noms d'une publication qu'il ne
    connaît pas.
    """
    commun = await _store()
    serveur = commun.pour("111")
    await serveur.set("publication:bonjour:salons", ["3"])
    await serveur.memoriser_salon("3", "matin", "111", "A")

    assert await serveur.oublier_salons_orphelins() == 0
    assert list(await commun.salons_connus()) == ["3"]


async def test_le_menage_garde_les_salons_dune_config_pas_encore_migree():
    """La prod tourne encore avec ses salons à la racine, migrés à la lecture.

    Un ménage passé avant la première lecture d'une fourchette effacerait le nom
    du salon déjà configuré.
    """
    for champ, valeur, salon in (("salons", ["5"], "5"), ("salon_id", "6", "6")):
        commun = await _store()
        await commun.set(
            "config", {"prix_min": "0", "prix_max": "1e15", champ: valeur}
        )
        await commun.memoriser_salon(salon, "promos", "111", "A")

        assert await commun.oublier_salons_orphelins() == 0, champ


async def test_le_menage_efface_encore_ce_que_plus_personne_ne_sert():
    """Le ménage reste un ménage : sinon la table grossit indéfiniment."""
    commun = await _store()
    serveur = commun.pour("111")
    await serveur.memoriser_salon("9", "oublie", "111", "A")

    assert await serveur.oublier_salons_orphelins() == 1
    assert await commun.salons_connus() == {}


async def test_les_roles_mentionnes_restent_dans_la_table_commune():
    """Déjà rangés par serveur avant ce changement, et lus tels quels par le site.

    Les cloisonner les rangerait deux fois par serveur — `serveur:111:roles` ne
    contenant qu'une entrée `111` — et le site ne les trouverait plus.
    """
    commun = await _store()

    await commun.pour("111").definir_role("111", "42")

    assert await commun.roles() == {"111": "42"}
    assert await commun.pour("111").roles() == {"111": "42"}
    assert await commun.pour("222").role_du_serveur("111") == "42"


# --- La vue est un `Store` comme un autre -----------------------------------


async def test_la_vue_partage_la_base_du_magasin_commun():
    """`/reglages voir` affiche « Postgres » ou « mémoire » : il lit `persistant`.

    Une vue qui répondrait toujours « mémoire » ferait croire à une
    configuration perdue au prochain redémarrage — et l'inverse ferait croire
    l'inverse.
    """
    commun = await _store()
    assert commun.pour("111").persistant is False

    # `persistant` est exactement « le pool existe » : un jeton suffit à jouer la
    # base connectée, sans Postgres sous la main.
    commun._pool = object()
    assert commun.pour("111").persistant is True


async def test_une_vue_ne_se_connecte_pas_toute_seule():
    """Elle n'a pas de base à elle : ouvrir un second pool serait une fuite."""
    commun = await _store()

    with pytest.raises(RuntimeError):
        await commun.pour("111").connect()


async def test_une_vue_ne_ferme_pas_la_base_des_autres():
    """Fermer depuis un serveur couperait la base de tous les autres."""
    commun = await _store()

    with pytest.raises(RuntimeError):
        await commun.pour("111").close()


async def test_une_vue_de_vue_est_refusee():
    """`pour(a).pour(b)` ne peut être qu'une confusion.

    Renvoyer silencieusement la vue de `b` cacherait le malentendu de celui qui
    croyait resserrer davantage.
    """
    commun = await _store()

    with pytest.raises(RuntimeError):
        commun.pour("111").pour("222")


async def test_la_migration_voit_toute_la_base_depuis_une_vue():
    """`tout()` sert au déménagement d'une base à l'autre : il ne se cloisonne pas.

    Cloisonné, il ne recopierait que le tiroir d'un serveur, et le manque ne se
    verrait qu'une fois l'ancienne base éteinte.
    """
    commun = await _store()
    await commun.pour("111").maj_config(heure="07:30")
    await commun.maj_config(heure="09:00")

    tout = await commun.pour("111").tout()

    assert "config" in tout
    assert "serveur:111:config" in tout


# --- Le cloisonnement ne peut pas se percer par mégarde ---------------------


def test_toute_porte_vers_la_base_est_redefinie_par_la_vue():
    """La vue ne redirige que `get`/`set` : tout le reste en hérite.

    Ce qui suppose qu'aucun autre membre de `Store` ne touche `_pool` ou
    `_memoire` sans passer par eux. Un accesseur ajouté plus tard qui lirait la
    base en direct **percerait le cloisonnement en silence** : il rendrait la
    valeur commune à un serveur qui croit lire la sienne.

    D'où ce test structurel plutôt qu'un commentaire : il nomme le coupable au
    lieu de laisser découvrir la fuite dans un post.
    """
    portes = {
        nom
        for nom, membre in vars(Store).items()
        if callable(getattr(membre, "fget", membre))
        and ("_pool" in inspect.getsource(getattr(membre, "fget", membre))
             or "_memoire" in inspect.getsource(getattr(membre, "fget", membre)))
    }

    assert portes, "aucune porte trouvée : le test ne vérifie plus rien"
    non_redefinies = portes - set(vars(VueServeur))
    assert not non_redefinies, (
        f"ces membres de Store parlent à la base sans passer par get/set, "
        f"et VueServeur ne les redéfinit pas : {sorted(non_redefinies)}"
    )
