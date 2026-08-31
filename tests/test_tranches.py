"""Plafonner par tranche de prix, à l'intérieur d'une fourchette.

Le plafond de fourchette coupe la queue du post : les moins chères tombent
toujours. Sur une fourchette large, ça donne un post qui ne parle que du haut du
panier, et les affaires modestes n'ont plus jamais leur tour.

La tranche répond à ça : « au plus 3 promotions entre 100T et 500T, au plus 5
entre 500T et 1P ». Aucune tranche ne *choisit* de promotions — chacune se
contente d'en refuser au-delà de son compte.

Ce qui est éprouvé ici est le cœur, sans base ni Discord. Les pièges sont aux
croisements :

- une promotion **hors de toute tranche** passe toujours. Les tranches limitent,
  elles ne sélectionnent pas — sinon en régler une reviendrait à jeter tout le
  reste de la fourchette, ce que rien dans le mot « plafond » n'annonce.
- une promotion dans **deux** tranches compte dans les deux, et tombe dès que
  l'une est pleine. C'est la seule règle qui reste vraie quand des tranches se
  touchent ou se chevauchent, cas qu'un réglage à la main produira.
- les tranches coupent **avant** le plafond de fourchette, et `rang`/`total`
  comptent ce qui reste des deux.
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


#: Six promotions à six prix distincts, trois en bas et trois en haut. Distincts
#: parce qu'une tranche garde les plus chères : à prix égaux, le test ne dirait
#: pas si l'ordre a été respecté ou tiré au hasard.
SIX = (
    'zones,"Six cents",0,600,0,0,0,17,0,0,0',
    'zones,"Cinq cents",0,500,0,0,0,17,0,0,0',
    'zones,"Quatre cents",0,400,0,0,0,17,0,0,0',
    'zones,"Trois cents",0,300,0,0,0,17,0,0,0',
    'zones,"Deux cents",0,200,0,0,0,17,0,0,0',
    'zones,"Cent",0,100,0,0,0,17,0,0,0',
)

LARGE = (Decimal(0), Decimal("1e12"))

#: Le bas du panier : 100 à 300, donc trois des six.
BAS = (Decimal(100), Decimal(300))
#: Le haut : 400 à 600, les trois autres. Disjointe de `BAS`, pour que les tests
#: du chevauchement soient les seuls à en parler.
HAUT = (Decimal(400), Decimal(600))


# --- Sans tranche, rien ne change -------------------------------------------


def test_sans_tranche_tout_ce_qui_est_dedans_sort():
    """Le comportement d'avant, qui reste le défaut : un déploiement ne doit pas
    raccourcir les posts de tout le monde sans que personne l'ait demandé."""
    promos = find_promos(_batiments(*SIX), *LARGE)

    assert len(promos) == 6


def test_une_tranche_absurde_est_ignoree():
    """La configuration est du JSON retouchable à la main.

    Un `0` ou un négatif qui s'y glisse doit coûter la tranche du jour, jamais la
    publication : un post vide se lirait comme une panne du bot, alors que c'est
    une faute de frappe. Même règle que le plafond de fourchette.
    """
    for absurde in (0, -1):
        promos = find_promos(
            _batiments(*SIX), *LARGE, tranches=((*BAS, absurde),)
        )

        assert len(promos) == 6, absurde


def test_une_tranche_plus_large_que_sa_recolte_ne_coupe_rien():
    promos = find_promos(_batiments(*SIX), *LARGE, tranches=((*BAS, 10),))

    assert len(promos) == 6


# --- La coupe, et ce qu'elle garde ------------------------------------------


def test_une_tranche_limite_les_promotions_de_sa_plage():
    promos = find_promos(_batiments(*SIX), *LARGE, tranches=((*BAS, 1),))

    assert len(promos) == 4


def test_une_tranche_garde_les_plus_cheres_de_sa_plage():
    """Comme le plafond de fourchette : on coupe la queue, pas le début.

    Le post est déjà trié du plus cher au moins cher ; couper ailleurs donnerait
    un ordre que rien à l'écran n'expliquerait.
    """
    promos = find_promos(_batiments(*SIX), *LARGE, tranches=((*BAS, 1),))

    assert _noms(promos) == ["Six cents", "Cinq cents", "Quatre cents", "Trois cents"]


def test_ce_qui_est_hors_de_toute_tranche_passe_toujours():
    """La règle qui fait la différence entre plafonner et sélectionner.

    Une tranche qui ne couvre que « Cent » ne doit rien dire des cinq autres. Si
    elles tombaient aussi, régler une tranche viderait la fourchette de tout ce
    qu'elle ne mentionne pas — un filtre déguisé en plafond.
    """
    promos = find_promos(
        _batiments(*SIX), *LARGE, tranches=((Decimal(0), Decimal(150), 1),)
    )

    assert _noms(promos) == [
        "Six cents", "Cinq cents", "Quatre cents", "Trois cents", "Deux cents", "Cent"
    ]


def test_les_bornes_dune_tranche_sont_incluses():
    """`[bas, haut]` fermé des deux côtés, comme la fourchette et la tolérance.

    Une seule convention dans tout le bot. La tranche `200 → 300` n'accepte
    qu'une promotion : « Trois cents » la remplit, « Deux cents » tombe. Une
    borne haute exclue laisserait « Trois cents » libre et ferait garder « Deux
    cents » ; une borne basse exclue laisserait passer « Deux cents ». Dans les
    deux cas, six promotions au lieu de cinq.
    """
    promos = find_promos(
        _batiments(*SIX), *LARGE, tranches=((Decimal(200), Decimal(300), 1),)
    )

    assert "Deux cents" not in _noms(promos)
    assert len(promos) == 5


def test_le_rang_et_le_total_comptent_ce_qui_reste():
    """`{rang}` et `{total}` sont des placeholders du template : un post de
    quatre promotions annoncé « 1/6 » ferait chercher les deux qui manquent."""
    promos = find_promos(_batiments(*SIX), *LARGE, tranches=((*BAS, 1),))

    assert [(p.rang, p.total) for p in promos] == [(1, 4), (2, 4), (3, 4), (4, 4)]


# --- Plusieurs tranches ------------------------------------------------------


def test_chaque_tranche_a_son_propre_compte():
    """Deux tranches disjointes, deux comptes : c'est tout l'intérêt du réglage.

    Un compteur unique partagé rendrait deux tranches équivalentes à un plafond
    de fourchette, et le haut du panier mangerait encore la part du bas.
    """
    promos = find_promos(
        _batiments(*SIX), *LARGE, tranches=((*BAS, 1), (*HAUT, 2))
    )

    assert _noms(promos) == ["Six cents", "Cinq cents", "Trois cents"]


def test_une_promotion_dans_deux_tranches_compte_dans_les_deux():
    """Deux tranches qui se chevauchent, et une promotion dans les deux.

    Comptée dans une seule, la tranche non créditée laisserait passer une
    promotion de plus que son nombre — un plafond qui ne plafonne pas dès que
    deux tranches se touchent, ce qu'un réglage à la main produit vite.
    """
    promos = find_promos(
        _batiments(*SIX),
        *LARGE,
        # « Six cents » est dans les deux. Chacune n'accepte qu'une promotion :
        # elle les remplit donc toutes les deux, et rien d'autre ne passe.
        tranches=((Decimal(500), Decimal(600), 1), (Decimal(600), Decimal(700), 1)),
    )

    assert _noms(promos) == ["Six cents", "Quatre cents", "Trois cents",
                             "Deux cents", "Cent"]


def test_lordre_decriture_des_tranches_ne_change_rien():
    """Les deux mêmes tranches, écrites dans un ordre puis dans l'autre.

    Le témoin qui manquait au test précédent : là, la promotion à cheval remplit
    les deux tranches, si bien que ne créditer que la première donnerait le même
    post. Ici la tranche haute est déjà pleine quand la promotion à cheval se
    présente, et c'est la basse qu'il reste à créditer.

    L'ordre d'écriture est celui des réglages successifs et ne décrit rien du
    post : s'il comptait, régler les deux mêmes plages dans l'autre sens
    donnerait un autre post, sans que rien ne le dise.
    """
    batiments = _batiments('zones,"Six cent cinquante",0,650,0,0,0,17,0,0,0', *SIX)
    basse = (Decimal(500), Decimal(600), 1)
    haute = (Decimal(600), Decimal(700), 1)

    dans_un_sens = find_promos(batiments, *LARGE, tranches=(basse, haute))
    dans_lautre = find_promos(batiments, *LARGE, tranches=(haute, basse))

    # « Six cent cinquante » remplit la haute ; « Six cents », qui est dans les
    # deux, tombe donc, et c'est « Cinq cents » qui occupe la basse.
    assert _noms(dans_un_sens) == ["Six cent cinquante", "Cinq cents",
                                   "Quatre cents", "Trois cents", "Deux cents",
                                   "Cent"]
    assert _noms(dans_lautre) == _noms(dans_un_sens)


def test_une_tranche_pleine_suffit_a_ecarter_une_promotion():
    """Deux tranches, l'une pleine, l'autre non : la pleine gagne.

    « Au plus 1 entre 500 et 600 » doit rester vrai même si une autre tranche
    plus large accepterait encore du monde — sinon la tranche la plus permissive
    annulerait les autres, et il suffirait d'en ajouter une pour tout ouvrir.
    """
    promos = find_promos(
        _batiments(*SIX),
        *LARGE,
        tranches=((Decimal(500), Decimal(600), 1), (Decimal(0), Decimal(1000), 6)),
    )

    assert _noms(promos) == ["Six cents", "Quatre cents", "Trois cents",
                             "Deux cents", "Cent"]


# --- Les tranches et le plafond de fourchette --------------------------------


def test_les_tranches_coupent_avant_le_plafond_de_fourchette():
    """Les deux réglages se composent, dans cet ordre.

    Le plafond appliqué d'abord, la tranche ne verrait qu'un morceau de la
    récolte et son compte serait faux : « au plus 1 en bas » sur une liste déjà
    coupée à ses deux plus chères ne couperait plus rien, et rien à l'écran ne
    dirait pourquoi la tranche est inerte.
    """
    promos = find_promos(
        _batiments(*SIX), *LARGE, tranches=((*HAUT, 1),), plafond=2
    )

    assert _noms(promos) == ["Six cents", "Trois cents"]


def test_les_tranches_coupent_ce_que_le_plancher_voulait_ajouter():
    """Aucune promotion dans la fourchette : le plancher en repêche deux.

    Une tranche qui les couvre doit l'emporter — c'est un réglage explicite, là
    où le plancher n'est qu'un défaut. Même arbitrage que le plafond de
    fourchette, sinon les deux réglages se contrediraient les jours creux, les
    seuls où le repêchage se voit.
    """
    promos = find_promos(
        _batiments(*SIX),
        Decimal(5000),
        Decimal(9000),
        tranches=((Decimal(0), Decimal(1000), 1),),
    )

    assert len(promos) == 1
    assert promos[0].zone != "ideale"


def test_les_tranches_comptent_apres_les_types_ecartes():
    """Compté avant l'exclusion, « au plus 2 en bas » sur une liste dont la
    deuxième est d'un type écarté n'en rendrait qu'une : la tranche deviendrait
    un maximum aléatoire, et rien à l'écran ne dirait pourquoi."""
    promos = find_promos(
        _batiments(
            'zones,"Gardée",0,300,0,0,0,17,0,0,0',
            'transport,"Écartée",0,200,0,0,0,17,0,0,0',
            'zones,"Gardée aussi",0,100,0,0,0,17,0,0,0',
        ),
        *LARGE,
        tranches=((*BAS, 2),),
        types_exclus=("transport",),
    )

    assert _noms(promos) == ["Gardée", "Gardée aussi"]
