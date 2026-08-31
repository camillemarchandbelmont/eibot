"""Les tranches sont rangées **dans** la fourchette, comme son plafond.

Une fourchette de `100T → 1P` couvre un facteur dix : ses plus gros bâtiments
raflent tout le post, et le plafond de fourchette, qui coupe la queue de la
liste, ne fait qu'aggraver ça. Les tranches découpent cette fourchette en plages
de prix, chacune avec son propre nombre maximum.

Rangées là plutôt que dans la configuration du serveur pour la même raison que le
plafond : les plages ne veulent rien dire hors de la fourchette qui les contient.

Trois choses que ces tests figent : la lecture est défensive (la config est du
JSON retouchable à la main), régler la même plage deux fois **remplace** au lieu
d'empiler un doublon, et les tranches survivent à un changement de bornes —
`/promos prix` ne doit pas effacer un réglage qu'il ne mentionne pas.
"""

from decimal import Decimal

from src.db import Store, tranches_fourchette

from tests.test_publication_par_serveur import EMPIRE, FILIALE

EMPIRE_ID = str(EMPIRE.id)
FILIALE_ID = str(FILIALE.id)


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def _magasin(serveur_id: str = EMPIRE_ID):
    magasin = (await _store()).pour(serveur_id)
    await magasin.ajouter_fourchette("grosses", Decimal(100), Decimal(900))
    return magasin


async def _grosses(magasin) -> dict:
    return (await magasin.fourchettes())[0]


# --- Une fourchette neuve n'a pas de tranche --------------------------------


async def test_une_fourchette_neuve_na_aucune_tranche():
    """Le défaut est le comportement d'avant : un déploiement ne doit pas
    raccourcir les posts de tout le monde sans que personne l'ait demandé."""
    magasin = await _magasin()

    assert tranches_fourchette(await _grosses(magasin)) == []


async def test_des_tranches_retouchees_a_la_main_ne_cassent_rien():
    """Chaque entrée peut valoir n'importe quoi : la config est du JSON éditable.

    Une entrée illisible est ignorée, jamais levée : une faute de frappe doit
    coûter la tranche du jour, pas la publication.
    """
    abimees = [
        "abc",
        42,
        [{"min": "abc", "max": "300", "nombre": 2}],
        [{"min": "100", "nombre": 2}],
        [{"min": "100", "max": "300"}],
        [{"min": "100", "max": "300", "nombre": 0}],
        [{"min": "100", "max": "300", "nombre": -2}],
        [{"min": "", "max": "300", "nombre": 2}],
        ["100-300"],
    ]
    for absurde in abimees:
        assert tranches_fourchette({"tranches": absurde}) == [], absurde


async def test_une_tranche_en_texte_est_lue_en_nombres():
    """Le site et les versions antérieures écrivent volontiers des chaînes. Lu
    tel quel, `"3"` comparerait mal et ne plafonnerait rien."""
    lues = tranches_fourchette(
        {"tranches": [{"min": "100", "max": "300", "nombre": "3"}]}
    )

    assert lues == [(Decimal(100), Decimal(300), 3)]


async def test_une_tranche_aux_bornes_inversees_est_remise_dans_lordre():
    """`300 → 100` ne contiendrait jamais rien : la tranche serait inerte, et
    rien à l'écran ne dirait pourquoi. L'intention est évidente."""
    lues = tranches_fourchette(
        {"tranches": [{"min": "300", "max": "100", "nombre": 2}]}
    )

    assert lues == [(Decimal(100), Decimal(300), 2)]


async def test_les_tranches_sont_lues_de_la_plus_basse_a_la_plus_haute():
    """L'ordre d'écriture ne doit pas décider de l'affichage : `/promos liste`
    les montre les unes sous les autres, et une liste qui se réordonne à chaque
    réglage ne se relit pas."""
    lues = tranches_fourchette(
        {
            "tranches": [
                {"min": "400", "max": "600", "nombre": 1},
                {"min": "100", "max": "300", "nombre": 2},
            ]
        }
    )

    assert [bas for bas, _, _ in lues] == [Decimal(100), Decimal(400)]


# --- Les régler, les lire, les effacer --------------------------------------


async def test_regler_puis_lire_une_tranche():
    magasin = await _magasin()

    assert await magasin.regler_tranche_fourchette(
        "grosses", Decimal(100), Decimal(300), 3
    ) is True

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_regler_deux_tranches_les_garde_toutes_les_deux():
    magasin = await _magasin()

    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)
    await magasin.regler_tranche_fourchette("grosses", Decimal(400), Decimal(600), 5)

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3),
        (Decimal(400), Decimal(600), 5),
    ]


async def test_regler_la_meme_plage_remplace_son_nombre():
    """Sans ce remplacement, chaque correction empilerait une tranche de plus sur
    la même plage : la plus stricte gagnerait pour toujours, et la commande
    aurait confirmé un nombre que le post ne respecte pas."""
    magasin = await _magasin()

    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 8)

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 8)
    ]


async def test_regler_des_bornes_inversees_les_remet_dans_lordre():
    magasin = await _magasin()

    await magasin.regler_tranche_fourchette("grosses", Decimal(300), Decimal(100), 3)

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_regler_une_tranche_sur_une_fourchette_inconnue():
    """False plutôt qu'une exception : la commande doit pouvoir dire « inconnue »
    en citant les noms qui existent."""
    magasin = await _magasin()

    assert await magasin.regler_tranche_fourchette(
        "petits", Decimal(1), Decimal(50), 3
    ) is False


async def test_une_tranche_sous_une_promotion_est_refusee():
    """Zéro promotion sur une plage, c'est un filtre qui l'interdit — pas un
    plafond. Le mot ne l'annonce pas, et la fourchette a déjà ses bornes pour
    exclure une plage de prix."""
    magasin = await _magasin()

    for absurde in (0, -1):
        try:
            await magasin.regler_tranche_fourchette(
                "grosses", Decimal(100), Decimal(300), absurde
            )
        except ValueError:
            continue
        raise AssertionError(f"tranche de {absurde} acceptée")


async def test_effacer_une_tranche():
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    assert await magasin.effacer_tranche_fourchette(
        "grosses", Decimal(100), Decimal(300)
    ) is True

    assert tranches_fourchette(await _grosses(magasin)) == []


async def test_effacer_une_tranche_laisse_les_autres():
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)
    await magasin.regler_tranche_fourchette("grosses", Decimal(400), Decimal(600), 5)

    await magasin.effacer_tranche_fourchette("grosses", Decimal(100), Decimal(300))

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(400), Decimal(600), 5)
    ]


async def test_effacer_une_tranche_absente_le_dit():
    """Sans ce False, la commande annoncerait un effacement imaginaire et l'on
    croirait avoir changé quelque chose — typiquement après une borne mal
    retapée, cas où l'on croit corriger la tranche qu'on vient d'ajouter."""
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    assert await magasin.effacer_tranche_fourchette(
        "grosses", Decimal(100), Decimal(250)
    ) is False


async def test_effacer_une_tranche_sur_une_fourchette_inconnue():
    magasin = await _magasin()

    assert await magasin.effacer_tranche_fourchette(
        "petits", Decimal(100), Decimal(300)
    ) is False


# --- Ce qui ne doit pas les effacer -----------------------------------------


async def test_les_tranches_survivent_a_un_changement_de_bornes():
    """`/promos prix` ne parle pas des tranches : les effacer au passage serait
    une perte silencieuse, découverte le lendemain sur un post trop long."""
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    await magasin.majprix_fourchette("grosses", Decimal(50), Decimal(5000))

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_les_tranches_survivent_a_un_salon_ajoute():
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    await magasin.ajouter_salon_fourchette("grosses", "42")

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_les_tranches_survivent_a_un_plafond_regle():
    """Les deux réglages se composent dans `find_promos` : ils doivent donc
    pouvoir coexister en base, sans que le dernier réglé chasse l'autre."""
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    await magasin.regler_plafond_fourchette("grosses", 5)

    assert tranches_fourchette(await _grosses(magasin)) == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_le_voisin_garde_ses_tranches():
    """Les fourchettes sont déjà par serveur ; les tranches suivent, étant
    dedans."""
    magasin = await _magasin()
    voisin = await _magasin(FILIALE_ID)
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    assert tranches_fourchette(await _grosses(voisin)) == []


# --- Les tranches d'une recherche, qui ne porte sur aucune fourchette -------


async def test_sans_tranche_partout_la_recherche_nest_pas_tranchee():
    magasin = await _magasin()

    assert await magasin.tranches_de_recherche() == []


async def test_une_seule_fourchette_les_siennes_valent_pour_la_recherche():
    """Avec une fourchette unique, la recherche *est* cette fourchette : ses
    tranches décrivent bien ce qui va sortir."""
    magasin = await _magasin()
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    assert await magasin.tranches_de_recherche() == [
        (Decimal(100), Decimal(300), 3)
    ]


async def test_une_plage_reglee_sur_une_seule_fourchette_ne_tranche_pas():
    """`/promos chercher` couvre l'**union** des fourchettes.

    Y appliquer la tranche de l'une cacherait des promotions que l'autre publie
    bel et bien : la recherche annoncerait alors moins que ce qui sort le soir,
    et c'est exactement l'inverse de ce qu'on lui demande.
    """
    magasin = await _magasin()
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(50))
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)

    assert await magasin.tranches_de_recherche() == []


async def test_une_plage_reglee_partout_tranche_au_plus_large():
    """Le plus permissif des nombres, comme le plafond de recherche : la
    recherche ne doit jamais montrer moins que la fourchette la plus généreuse
    ne publiera."""
    magasin = await _magasin()
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(50))
    await magasin.regler_tranche_fourchette("grosses", Decimal(100), Decimal(300), 3)
    await magasin.regler_tranche_fourchette("petits", Decimal(100), Decimal(300), 8)

    assert await magasin.tranches_de_recherche() == [
        (Decimal(100), Decimal(300), 8)
    ]


async def test_sans_aucune_fourchette_la_recherche_nest_pas_tranchee():
    """Le cas d'un serveur neuf, où `/promos chercher` marche déjà avec des
    bornes données à la main. « Réglée partout » ne doit pas être vrai d'un
    ensemble vide, sinon la recherche se trancherait sur rien."""
    magasin = (await _store()).pour(EMPIRE_ID)

    assert await magasin.tranches_de_recherche() == []


# --- Sérialisation ----------------------------------------------------------


def test_les_tranches_sont_exposees_au_site():
    """Le site montre les fourchettes : sans les tranches, sa liste promettrait
    un post plus long que celui qui sort."""
    from src.serialisation import fourchette_en_json, montant_en_json

    rendu = fourchette_en_json(
        {
            "nom": "grosses",
            "prix_min": "1e14",
            "prix_max": "6e15",
            "salons": [],
            "tranches": [{"min": "1e14", "max": "5e14", "nombre": 3}],
        }
    )

    # Les bornes pré-formatées par `montant_en_json`, comme tous les montants du
    # site — attendu via la fonction elle-même plutôt que ses rendus recopiés, qui
    # feraient de ce test un doublon de ceux du formatage. Le nombre en entier nu :
    # c'est un compte, pas un montant.
    assert rendu["tranches"] == [
        {
            **montant_en_json("min", Decimal("1e14")),
            **montant_en_json("max", Decimal("5e14")),
            "nombre": 3,
        }
    ]


def test_une_fourchette_sans_tranche_nen_expose_pas():
    """Une liste vide se lirait comme un réglage fait puis vidé — même raison que
    pour les bornes tolérées absentes."""
    from src.serialisation import fourchette_en_json

    rendu = fourchette_en_json(
        {"nom": "grosses", "prix_min": "1e14", "prix_max": "6e15", "salons": []}
    )

    assert "tranches" not in rendu
