"""Plafonner le nombre de promotions retenues.

Une fourchette large trouve tout ce qu'elle contient : dix, quarante, sans
limite. Le post partait alors en plusieurs messages, et la trentième promotion
n'intéressait personne. `find_promos` gagne donc un plafond.

Ce qui est éprouvé ici est le cœur, sans base ni Discord. Deux règles qui se
croisent, et c'est là que se cachent les fautes :

- le plafond **gagne contre le plancher**. `CIBLE_MINIMUM` complète une
  fourchette trop pauvre en tolérant puis en repêchant ; un plafond de 1 doit
  couper ce que ce plancher voulait ajouter, sinon régler « une promotion » en
  donnerait deux les jours creux.
- `rang` et `total` comptent **ce qui reste**. Calculés avant la coupe, les
  posts s'annonceraient « 1/40 » pour une liste de cinq.
"""

from decimal import Decimal

from src.promos import find_promos, parse_csv

ENTETE = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-28 08:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
"""


def _csv(*lignes: str) -> str:
    return ENTETE + "\n".join(lignes) + "\n"


def _batiments(*lignes: str):
    _, batiments = parse_csv(_csv(*lignes))
    return batiments


def _noms(promos) -> list[str]:
    return [promo.building.nom for promo in promos]


#: Quatre promotions dans la même fourchette, à quatre prix distincts. Distincts
#: parce que le plafond garde les plus chères : à prix égaux, le test ne dirait
#: pas si l'ordre a été respecté ou tiré au hasard.
QUATRE = (
    'zones,"Quatre cents",0,400,0,0,0,17,0,0,0',
    'zones,"Cent",0,100,0,0,0,17,0,0,0',
    'zones,"Trois cents",0,300,0,0,0,17,0,0,0',
    'zones,"Deux cents",0,200,0,0,0,17,0,0,0',
)

LARGE = (Decimal(0), Decimal("1e12"))


# --- Sans plafond, rien ne change -------------------------------------------


def test_sans_plafond_tout_ce_qui_est_dedans_sort():
    """Le comportement d'avant, qui reste le défaut : un déploiement ne doit pas
    raccourcir les posts de tout le monde sans que personne l'ait demandé."""
    promos = find_promos(_batiments(*QUATRE), *LARGE)

    assert len(promos) == 4


def test_un_plafond_absurde_est_ignore():
    """La configuration est du JSON retouchable à la main.

    Un `0` ou un nombre négatif qui s'y glisse doit coûter le plafond du jour,
    jamais la publication : un post vide se lirait comme une panne du bot, alors
    que c'est une faute de frappe.
    """
    for absurde in (0, -1, None):
        promos = find_promos(_batiments(*QUATRE), *LARGE, plafond=absurde)

        assert len(promos) == 4, absurde


def test_un_plafond_plus_large_que_la_recolte_ne_coupe_rien():
    promos = find_promos(_batiments(*QUATRE), *LARGE, plafond=10)

    assert len(promos) == 4


# --- La coupe, et ce qu'elle garde ------------------------------------------


def test_le_plafond_limite_le_nombre_de_promotions():
    promos = find_promos(_batiments(*QUATRE), *LARGE, plafond=2)

    assert len(promos) == 2


def test_le_plafond_garde_les_plus_cheres():
    """Le choix assumé : on coupe la queue de la liste, pas son début.

    Le post est déjà trié du plus cher au moins cher ; couper ailleurs
    donnerait un ordre que rien à l'écran n'expliquerait.
    """
    promos = find_promos(_batiments(*QUATRE), *LARGE, plafond=2)

    assert _noms(promos) == ["Quatre cents", "Trois cents"]


def test_le_rang_et_le_total_comptent_ce_qui_reste():
    """`{rang}` et `{total}` sont des placeholders du template : un post de deux
    promotions annoncé « 1/4 » ferait chercher les deux qui manquent."""
    promos = find_promos(_batiments(*QUATRE), *LARGE, plafond=2)

    assert [(p.rang, p.total) for p in promos] == [(1, 2), (2, 2)]


# --- Le plafond contre le plancher ------------------------------------------


def test_le_plafond_coupe_ce_que_le_plancher_voulait_ajouter():
    """Aucune promotion dans la fourchette : le plancher en repêche deux.

    Un plafond de 1 doit l'emporter — c'est un réglage explicite, là où le
    plancher n'est qu'un défaut. Sinon « une promotion » en donnerait deux les
    jours creux, c'est-à-dire les seuls jours où le repêchage se voit.
    """
    promos = find_promos(
        _batiments(*QUATRE), Decimal(5000), Decimal(9000), plafond=1
    )

    assert len(promos) == 1
    assert promos[0].zone != "ideale"


def test_le_plafond_garde_dabord_ce_qui_est_dans_la_fourchette():
    """Une promotion idéale et une repêchée pour un plafond de 1 : c'est
    l'idéale qui reste. Couper dans l'ordre du tri suffit à le garantir, mais
    rien ne le dirait si l'ordre des trois passes changeait un jour."""
    promos = find_promos(
        _batiments(*QUATRE), Decimal(150), Decimal(250), plafond=1
    )

    assert _noms(promos) == ["Deux cents"]
    assert promos[0].dans_fourchette


# --- Le plafond et les types écartés ----------------------------------------


def test_le_plafond_compte_apres_les_types_ecartes():
    """Les deux réglages se composent, dans cet ordre.

    Compté avant l'exclusion, un plafond de 2 sur une liste dont la deuxième
    promotion est d'un type écarté ne rendrait qu'une seule promotion : le
    plafond deviendrait un maximum aléatoire, et rien à l'écran ne dirait
    pourquoi le post est plus court que réglé.
    """
    promos = find_promos(
        _batiments(
            'zones,"Gardée",0,400,0,0,0,17,0,0,0',
            'transport,"Écartée",0,300,0,0,0,17,0,0,0',
            'zones,"Gardée aussi",0,200,0,0,0,17,0,0,0',
        ),
        *LARGE,
        plafond=2,
        types_exclus=("transport",),
    )

    assert _noms(promos) == ["Gardée", "Gardée aussi"]
