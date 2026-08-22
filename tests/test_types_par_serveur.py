"""Chaque serveur écarte les types de bâtiments qu'il veut.

Le cœur sait déjà écarter un type (`tests/test_types_exclus.py`) ; il lui faut
une liste, et cette liste est un goût d'acheteur : une entreprise n'achète pas de
transport, une autre ne vit que de ça. Elle est donc rangée dans la configuration
**du serveur**, à côté des modules éteints, et un réglage fait ici ne touche pas
au voisin.

Deux listes, à ne pas confondre :

- **les types écartés**, par serveur, ce que le réglage écrit ;
- **les types connus**, communs, ce que le dernier export contenait. Ils
  décrivent le monde M8 et non un serveur, et ne servent qu'à proposer des noms
  justes sous le curseur — sans appel réseau, Discord n'accordant que trois
  secondes à une frappe.
"""

from src.db import Store

from tests.test_publication_par_serveur import EMPIRE, FILIALE

EMPIRE_ID = str(EMPIRE.id)
FILIALE_ID = str(FILIALE.id)


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


# --- Les types écartés, serveur par serveur ---------------------------------


async def test_un_serveur_neuf_necarte_rien():
    """Le déploiement ne doit rien filtrer : un post qui maigrit tout seul se
    lirait comme une panne de l'export, et rien ne dirait où regarder."""
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.types_exclus() == []


async def test_exclure_puis_remettre():
    """Le va-et-vient complet, et ce que chaque appel répond.

    Le booléen porte la réponse de la commande : sans lui, `/promos types
    exclure` dirait « ✅ » sur un type déjà écarté, et on chercherait ailleurs la
    raison d'un post inchangé.
    """
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.exclure_type("transport") is True
    assert await magasin.types_exclus() == ["transport"]
    assert await magasin.exclure_type("transport") is False

    assert await magasin.remettre_type("transport") is True
    assert await magasin.types_exclus() == []
    assert await magasin.remettre_type("transport") is False


async def test_remettre_le_dernier_ecrit_bien_la_liste_vide():
    """Le piège de `maj_config`, qui écarte les `None` mais pas les listes vides.

    Sautée parce qu'elle est vide, la liste garderait l'ancienne valeur en base :
    le type semblerait remis jusqu'au redémarrage, puis se réexcluerait tout
    seul là où personne ne regarde.
    """
    magasin = (await _store()).pour(EMPIRE_ID)
    await magasin.exclure_type("transport")

    await magasin.remettre_type("transport")

    assert (await magasin.config())["types_exclus"] == []


async def test_les_types_ecartes_sont_tries_et_dedoublonnes():
    """La liste est lue par `/promos types liste` et par `/reglages voir`. Dans
    l'ordre des saisies, elle changerait de forme à chaque réglage, et deux
    graphies du même type s'y liraient comme deux exclusions."""
    magasin = (await _store()).pour(EMPIRE_ID)

    await magasin.exclure_type("zones")
    await magasin.exclure_type("bureaux")
    assert await magasin.exclure_type(" ZONES ") is False

    assert await magasin.types_exclus() == ["bureaux", "zones"]


async def test_le_voisin_garde_ses_types():
    """Tout l'intérêt du réglage : deux entreprises, deux goûts.

    Lue dans la configuration commune, l'exclusion de l'une ferait maigrir le
    post de l'autre — et le post maigri ne dirait pas qui l'a décidé.
    """
    store = await _store()
    await store.pour(EMPIRE_ID).exclure_type("transport")

    assert await store.pour(FILIALE_ID).types_exclus() == []
    assert await store.types_exclus() == []


# --- Les types connus, communs à tous les serveurs --------------------------


async def test_les_types_connus_sont_ceux_du_dernier_export():
    """Mémorisés à chaque chargement, comme les noms de salons se corrigent au
    premier post : un type ajouté par le jeu doit devenir proposable sans qu'on
    y touche."""
    store = await _store()

    await store.memoriser_types(["zones", "bureaux"])

    assert await store.types_connus() == ["bureaux", "zones"]


async def test_les_types_connus_sont_les_memes_pour_tous():
    """Ils décrivent le monde, pas un serveur. Rangés par serveur, celui qui n'a
    encore rien publié n'aurait aucune proposition à offrir."""
    store = await _store()

    await store.pour(EMPIRE_ID).memoriser_types(["transport"])

    assert await store.pour(FILIALE_ID).types_connus() == ["transport"]
    assert await store.types_connus() == ["transport"]


async def test_un_export_sans_type_neffface_pas_ce_quon_connaissait():
    """Un export vide ou illisible ne doit pas vider les propositions.

    Elles ne se remplissent qu'au chargement suivant : effacées, la commande
    n'aurait plus rien à proposer alors que la panne est ailleurs, et il faudrait
    taper les noms de mémoire.
    """
    store = await _store()
    await store.memoriser_types(["zones"])

    await store.memoriser_types([])

    assert await store.types_connus() == ["zones"]


async def test_un_export_inchange_necrit_rien_en_base():
    """Chaque chargement passe par là, et une commande peut charger l'export.

    Les types du monde ne bougent presque jamais : réécrire la même liste à
    chaque fois ferait une écriture Postgres par `/promos chercher`, pour une
    valeur identique.
    """
    store = await _store()
    await store.memoriser_types(["bureaux", "zones"])
    ecritures = []
    vrai_set = store.set

    async def set_compte(cle, valeur):
        ecritures.append(cle)
        await vrai_set(cle, valeur)

    store.set = set_compte

    # Le même contenu, dans l'ordre de l'export et avec les espaces d'une saisie.
    await store.memoriser_types([" zones ", "bureaux"])

    assert ecritures == []
    assert await store.types_connus() == ["bureaux", "zones"]
