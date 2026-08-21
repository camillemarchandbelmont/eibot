"""Tests de la zone de tolérance d'une fourchette.

La zone de tolérance élargit une fourchette **quand elle est trop pauvre** : au
lieu de repêcher le bâtiment le plus proche, quel qu'il soit, le bot va d'abord
chercher dans une plage que l'utilisateur a déclarée acceptable.

Deux risques propres à la persistance, que ces tests couvrent :

- une zone **plus étroite** que la fourchette rétrécirait silencieusement le
  budget au lieu de l'élargir ;
- `/promos prix` peut élargir les bornes idéales *après* coup, et laisser
  une zone incohérente que la boucle de publication traverserait sans rien dire.
"""

from decimal import Decimal

import pytest

from src.db import Store
from src.money import format_money
from src.promos import find_promos


@pytest.fixture
def store():
    return Store(dsn="")


# --- Lecture ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fourchette_sans_tolerance_expose_des_bornes_nulles(store):
    """Absente par défaut : rien à migrer, le comportement actuel est intact."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    fourchette = (await store.fourchettes())[0]
    assert fourchette["tolere_min"] == ""
    assert fourchette["tolere_max"] == ""


@pytest.mark.asyncio
async def test_config_plate_migree_sans_tolerance(store):
    """La migration ne doit pas inventer une zone que personne n'a réglée."""
    await store.set("config", {"prix_min": "1e14", "prix_max": "6e15", "salons": ["111"]})

    fourchette = (await store.fourchettes())[0]
    assert fourchette["tolere_min"] == ""
    assert fourchette["tolere_max"] == ""


# --- Écriture ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_regler_la_tolerance(store):
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.majtolerance_fourchette(
        "grosses", Decimal("5e13"), Decimal("8e15")
    ) is True

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("5e13")
    assert Decimal(fourchette["tolere_max"]) == Decimal("8e15")


@pytest.mark.asyncio
async def test_regler_la_tolerance_conserve_bornes_et_salons(store):
    """Elle s'ajoute à la fourchette, elle ne la remplace pas."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.ajouter_salon_fourchette("grosses", "111")

    await store.majtolerance_fourchette("grosses", Decimal("5e13"), Decimal("8e15"))

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["prix_min"]) == Decimal("1e14")
    assert Decimal(fourchette["prix_max"]) == Decimal("6e15")
    assert fourchette["salons"] == ["111"]


@pytest.mark.asyncio
async def test_tolerance_sur_fourchette_inconnue_renvoie_faux(store):
    assert await store.majtolerance_fourchette(
        "fantome", Decimal(1), Decimal(2)
    ) is False


@pytest.mark.asyncio
async def test_bornes_tolerees_inversees_remises_dans_l_ordre(store):
    """Même règle que `ajouter_fourchette` : l'intention est évidente."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    await store.majtolerance_fourchette("grosses", Decimal("8e15"), Decimal("5e13"))

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("5e13")
    assert Decimal(fourchette["tolere_max"]) == Decimal("8e15")


@pytest.mark.asyncio
async def test_zone_plus_etroite_refusee(store):
    """Rétrécir la fourchette au lieu de l'élargir est forcément une faute.

    Accepter `min:200T max:1P` sur une fourchette 100T-6P amputerait les
    bâtiments entre 1 PØ et 6 PØ **sans rien dire** — la tolérance n'ayant le
    droit que d'ajouter des candidats, jamais d'en retirer.
    """
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    with pytest.raises(ValueError, match="plus large"):
        await store.majtolerance_fourchette("grosses", Decimal("2e14"), Decimal("1e15"))

    # Et rien n'a été écrit.
    assert (await store.fourchettes())[0]["tolere_min"] == ""


@pytest.mark.asyncio
async def test_zone_identique_a_la_fourchette_acceptee(store):
    """Aux bornes exactes, la zone n'ajoute rien mais ne retire rien non plus."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.majtolerance_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    ) is True


@pytest.mark.asyncio
async def test_zone_elargie_d_un_seul_cote_acceptee(store):
    """Accepter de payer plus cher sans accepter d'acheter plus petit."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.majtolerance_fourchette(
        "grosses", Decimal("1e14"), Decimal("8e15")
    ) is True


# --- Effacement -------------------------------------------------------------


@pytest.mark.asyncio
async def test_effacer_la_tolerance(store):
    """La forme nue de la commande. `maj_config` ignorerait la chaîne vide."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.majtolerance_fourchette("grosses", Decimal("5e13"), Decimal("8e15"))

    assert await store.effacer_tolerance_fourchette("grosses") is True

    fourchette = (await store.fourchettes())[0]
    assert fourchette["tolere_min"] == ""
    assert fourchette["tolere_max"] == ""


@pytest.mark.asyncio
async def test_effacer_une_tolerance_absente_renvoie_faux(store):
    """Pour que la commande puisse dire « il n'y en avait pas » au lieu de
    confirmer un effacement imaginaire."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    assert await store.effacer_tolerance_fourchette("grosses") is False


@pytest.mark.asyncio
async def test_effacer_sur_fourchette_inconnue_renvoie_faux(store):
    assert await store.effacer_tolerance_fourchette("fantome") is False


# --- Cohérence avec les bornes idéales --------------------------------------


@pytest.mark.asyncio
async def test_prix_elargis_repoussent_la_tolerance(store):
    """`/promos prix` peut dépasser la zone réglée avant lui.

    Laisser `tolere_max` sous `prix_max` produirait une zone qui *exclut* une
    partie de la fourchette : la passe tolérée ne trouverait rien là où la passe
    idéale trouve, ce que rien ne signalerait. On repousse la borne.
    """
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.majtolerance_fourchette("grosses", Decimal("5e13"), Decimal("8e15"))

    await store.majprix_fourchette("grosses", Decimal("1e13"), Decimal("9e15"))

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("1e13")
    assert Decimal(fourchette["tolere_max"]) == Decimal("9e15")


@pytest.mark.asyncio
async def test_prix_resserres_laissent_la_tolerance_en_place(store):
    """Resserrer la fourchette ne touche pas à la zone, qui reste plus large."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await store.majtolerance_fourchette("grosses", Decimal("5e13"), Decimal("8e15"))

    await store.majprix_fourchette("grosses", Decimal("2e14"), Decimal("5e15"))

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("5e13")
    assert Decimal(fourchette["tolere_max"]) == Decimal("8e15")


@pytest.mark.asyncio
async def test_prix_sans_tolerance_n_en_cree_pas(store):
    """Élargir les prix d'une fourchette sans zone ne doit pas en inventer une."""
    await store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))

    await store.majprix_fourchette("grosses", Decimal("1e13"), Decimal("9e15"))

    fourchette = (await store.fourchettes())[0]
    assert fourchette["tolere_min"] == ""
    assert fourchette["tolere_max"] == ""


@pytest.mark.asyncio
async def test_zone_incoherente_ecrite_a_la_main_est_corrigee_a_la_lecture(store):
    """La config est du JSON qu'on peut retoucher, et une version antérieure a
    pu l'écrire autrement. La lecture recadre plutôt que de propager l'incohérence
    dans la boucle de publication."""
    await store.set(
        "config",
        {
            "fourchettes": [
                {
                    "nom": "grosses",
                    "prix_min": "1e14",
                    "prix_max": "6e15",
                    "salons": [],
                    # Zone à l'intérieur de la fourchette : impossible par
                    # commande, possible à la main.
                    "tolere_min": "2e14",
                    "tolere_max": "1e15",
                }
            ]
        },
    )

    fourchette = (await store.fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("1e14")
    assert Decimal(fourchette["tolere_max"]) == Decimal("6e15")


@pytest.mark.asyncio
async def test_tolerance_illisible_ignoree_plutot_que_de_couper_la_publication(store):
    """Un `tolere_min` non numérique ne doit pas lever dans la boucle du matin."""
    await store.set(
        "config",
        {
            "fourchettes": [
                {
                    "nom": "grosses",
                    "prix_min": "1e14",
                    "prix_max": "6e15",
                    "salons": [],
                    "tolere_min": "n'importe quoi",
                    "tolere_max": "8e15",
                }
            ]
        },
    )

    fourchette = (await store.fourchettes())[0]
    # Une seule borne ne suffit pas : `find_promos` ignore la zone à moitié
    # réglée, et le repêchage habituel reprend la main.
    assert fourchette["tolere_min"] == ""
    assert fourchette["tolere_max"] == ""


# --- Publication ------------------------------------------------------------
#
# Le `Store` peut stocker la zone sans que la boucle du matin la lise : la
# tolérance serait alors réglable, visible dans `/promos liste`, et sans
# aucun effet sur ce qui est publié. Ces tests passent par la publication réelle.

CSV_TOLERANCE = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Dedans",0,150,0,0,0,17,0,0,0
zones,"Juste en-dessous",0,95,0,0,0,17,0,0,0
zones,"Tolere loin",0,400,0,0,0,17,0,0,0
"""


class SourceFigee:
    def __init__(self, texte: str = CSV_TOLERANCE):
        self.texte = texte

    async def fetch(self) -> str:
        return self.texte


@pytest.mark.asyncio
async def test_la_boucle_de_publication_lit_la_tolerance():
    """Sans ça, la zone serait réglable et sans effet sur le post du matin."""
    from tests.test_publication_fourchettes import JournalFactice, SalonFactice

    from src.bot import EmpireBot

    salon = SalonFactice(1)
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.source = SourceFigee()
    bot.journal = JournalFactice()
    bot.get_channel = {1: salon}.get

    await store.ajouter_fourchette("cible", Decimal(100), Decimal(200))
    await store.majtolerance_fourchette("cible", Decimal(100), Decimal(500))
    await store.ajouter_salon_fourchette("cible", "1")

    await bot.publier_si_lheure(forcer=True)

    # « Juste en-dessous » (95) est le plus proche mais hors zone ; c'est
    # « Tolere loin » (400) qui doit compléter. Les titres portent l'emoji du
    # template par défaut, d'où la recherche par sous-chaîne.
    assert len(salon.titres) == 2
    assert "Dedans" in salon.titres[0]
    assert "Tolere loin" in salon.titres[1]


# --- Sérialisation ----------------------------------------------------------


def test_la_zone_est_exposee_au_site():
    """Le site doit pouvoir dessiner la zone, sinon son rail contredirait le
    post Discord."""
    from src.serialisation import fourchette_en_json

    rendu = fourchette_en_json(
        {
            "nom": "grosses",
            "prix_min": "1e14",
            "prix_max": "6e15",
            "salons": [],
            "tolere_min": "5e13",
            "tolere_max": "8e15",
        }
    )
    assert rendu["tolere_min_brut"] == "50000000000000"
    assert rendu["tolere_max_brut"] == "8000000000000000"
    # Formaté par le bot, comme toutes les bornes : le site ne reformate jamais.
    assert rendu["tolere_min"] == format_money(Decimal("5e13"))


def test_une_fourchette_sans_zone_n_expose_pas_de_bornes_a_zero():
    """`0 Ø` donnerait à voir une zone réglée que personne n'a demandée — et,
    étant sous `prix_min`, une zone qui élargit vers le bas."""
    from src.serialisation import fourchette_en_json

    rendu = fourchette_en_json(
        {"nom": "grosses", "prix_min": "1e14", "prix_max": "6e15", "salons": []}
    )
    assert "tolere_min" not in rendu
    assert "tolere_max" not in rendu


def test_la_zone_d_une_promo_est_exposee_au_site():
    """Trois valeurs et non un booléen : `dans_fourchette` ne distingue pas une
    promo tolérée d'une repêchée, alors que le site les colore autrement."""
    from src.promos import parse_csv as _parse
    from src.serialisation import promo_en_json

    _, batiments = _parse(CSV_TOLERANCE)
    promos = find_promos(
        batiments, Decimal(100), Decimal(200),
        tolere_min=Decimal(100), tolere_max=Decimal(500),
    )
    assert [promo_en_json(p)["zone"] for p in promos] == ["ideale", "toleree"]
