"""Écarter des types de bâtiments de la recherche de promotions.

Le jeu range chaque bâtiment sous un type — `zones`, `bureaux`, `transport`,
`industriels`, `commerciaux` dans M8. Une entreprise qui n'achète jamais de
transport voyait quand même les promotions de transport tous les soirs, et la
seule façon de ne pas les voir était de resserrer la fourchette de prix jusqu'à
les manquer, ce qui manquait le reste avec.

Ce qui est éprouvé ici est le cœur : une liste de types à écarter, et la
sélection qui n'en tient plus compte. Ni base, ni Discord — que des `Decimal` et
des dataclasses, comme le reste de `src/promos.py`.

**Le point de vigilance :** l'exclusion doit valoir pour les trois passes. La
recherche complète une fourchette trop pauvre en tolérant, puis en repêchant ;
une exclusion posée sur la seule passe idéale ferait revenir un transport par la
porte de derrière, le jour où il n'y a rien d'autre — c'est-à-dire le jour où on
ne s'y attend pas.
"""

from decimal import Decimal

from src.promos import find_promos, parse_csv, types_disponibles

ENTETE = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-28 08:00:07
# -----------------------------
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
"""


def _csv(*lignes: str) -> str:
    return ENTETE + "\n".join(lignes) + "\n"


def _batiments(*lignes: str):
    _, batiments = parse_csv(_csv(*lignes))
    return batiments


def _noms(promos) -> list[str]:
    return [promo.building.nom for promo in promos]


#: Deux promotions dans la même fourchette, de deux types différents. La borne
#: haute est large : ce qui décide de la sélection ici est le type, jamais le
#: prix.
DEUX_TYPES = (
    'transport,"Gare de fret",0,500,0,0,0,17,0,0,0',
    'bureaux,"Local",0,400,0,0,0,17,0,0,0',
)


# --- Les types que l'export contient ----------------------------------------


def test_les_types_sont_lus_dans_lexport():
    """La liste des types vient des données, jamais d'une liste écrite en dur.

    Écrite dans le code, elle ne suivrait pas le passage de M8 à M9 : un type
    ajouté par le jeu ne serait pas proposable, et rien ne dirait pourquoi.
    """
    batiments = _batiments(*DEUX_TYPES)

    assert types_disponibles(batiments) == ["bureaux", "transport"]


def test_les_types_comptent_tous_les_batiments_et_pas_les_seules_promos():
    """Sinon la liste des types changerait d'un jour à l'autre.

    Les promotions tournent ; les types du monde, non. Réduite aux bâtiments en
    promotion, la liste ne proposerait `transport` que les jours où il s'en
    trouve un en promotion — donc jamais le jour où l'on veut l'exclure.
    """
    batiments = _batiments(
        'transport,"Gare de fret",0,500,0,0,0,0,0,0,0',
        'bureaux,"Local",0,400,0,0,0,17,0,0,0',
    )

    assert types_disponibles(batiments) == ["bureaux", "transport"]


def test_les_types_sont_dedoublonnes_et_tries():
    """Vingt bâtiments de transport ne font qu'un type à proposer, et l'ordre est
    stable : une liste dans l'ordre du fichier changerait de place à chaque
    export, et la proposition sous le curseur ne serait jamais la même."""
    batiments = _batiments(
        'zones,"Mégapôle",0,500,0,0,0,17,0,0,0',
        'bureaux,"Local",0,400,0,0,0,17,0,0,0',
        'zones,"Technopôle",0,600,0,0,0,17,0,0,0',
    )

    assert types_disponibles(batiments) == ["bureaux", "zones"]


def test_un_type_vide_nest_pas_un_type():
    """Une ligne d'export sans type ne doit pas donner un type sans nom, qu'on
    pourrait exclure sans jamais savoir ce qu'il écarte."""
    batiments = _batiments(
        ',"Sans type",0,500,0,0,0,17,0,0,0',
        'bureaux,"Local",0,400,0,0,0,17,0,0,0',
    )

    assert types_disponibles(batiments) == ["bureaux"]


# --- L'exclusion dans la passe idéale ---------------------------------------


def test_un_type_exclu_ne_sort_pas_de_la_fourchette():
    """L'épreuve de base : le type écarté n'est plus proposé."""
    promos = find_promos(
        _batiments(*DEUX_TYPES),
        Decimal(0),
        Decimal("1e12"),
        types_exclus=("transport",),
    )

    assert _noms(promos) == ["Local"]


def test_sans_exclusion_rien_ne_change():
    """Le défaut ne doit rien filtrer : un serveur qui n'a rien réglé, et le
    déploiement lui-même, doivent voir exactement ce qu'ils voyaient."""
    promos = find_promos(_batiments(*DEUX_TYPES), Decimal(0), Decimal("1e12"))

    assert _noms(promos) == ["Gare de fret", "Local"]


def test_la_casse_et_les_espaces_ne_comptent_pas():
    """La liste est retouchable à la main dans la config, et le nom vient parfois
    d'une saisie. Un « Transport » qui ne filtre rien serait une exclusion
    silencieuse : le pire cas, puisque rien à l'écran ne la dément."""
    promos = find_promos(
        _batiments(*DEUX_TYPES),
        Decimal(0),
        Decimal("1e12"),
        types_exclus=(" TRANSPORT ",),
    )

    assert _noms(promos) == ["Local"]


def test_un_type_exclu_absent_de_lexport_ne_gene_pas():
    """Le monde change, le goût non : un type disparu de l'export reste dans la
    liste. Lever ici ferait sauter la publication pour un réglage devenu inutile.
    """
    promos = find_promos(
        _batiments(*DEUX_TYPES),
        Decimal(0),
        Decimal("1e12"),
        types_exclus=("aeroports",),
    )

    assert _noms(promos) == ["Gare de fret", "Local"]


def test_une_entree_vide_necarte_pas_les_batiments_sans_type():
    """La liste est du JSON retouchable à la main, et un `""` s'y glisse.

    Comparée sans garde, une entrée vide vaudrait le type vide et écarterait tous
    les bâtiments dont l'export ne dit pas le type — une exclusion que personne
    n'a demandée, sur un critère invisible à l'écran.
    """
    promos = find_promos(
        _batiments(
            ',"Sans type",0,500,0,0,0,17,0,0,0',
            'bureaux,"Local",0,400,0,0,0,17,0,0,0',
        ),
        Decimal(0),
        Decimal("1e12"),
        types_exclus=("", "   "),
    )

    assert _noms(promos) == ["Sans type", "Local"]


# --- L'exclusion dans les deux passes de secours ----------------------------


def test_un_type_exclu_ne_revient_pas_par_la_tolerance():
    """La zone de tolérance cherche hors de la fourchette quand elle est trop
    pauvre. Sans exclusion sur cette passe, le transport écarté reviendrait par
    là — et l'exclusion ne tiendrait que les jours où elle ne sert à rien."""
    batiments = _batiments(
        'bureaux,"Local",0,400,0,0,0,17,0,0,0',
        'transport,"Gare de fret",0,900,0,0,0,17,0,0,0',
    )

    promos = find_promos(
        batiments,
        Decimal(0),
        Decimal("500"),
        minimum=2,
        tolere_min=Decimal(0),
        tolere_max=Decimal("1000"),
        types_exclus=("transport",),
    )

    assert _noms(promos) == ["Local"]


def test_un_type_exclu_ne_revient_pas_par_le_repechage():
    """Le repêchage prend le plus proche de la fourchette, faute de mieux. C'est
    le chemin le plus discret : il ne se déclenche que les jours creux, donc une
    exclusion qui ne le couvre pas semble tenir pendant des semaines."""
    batiments = _batiments(
        'bureaux,"Local",0,400,0,0,0,17,0,0,0',
        'transport,"Gare de fret",0,1e9,0,0,0,17,0,0,0',
    )

    promos = find_promos(
        batiments,
        Decimal(0),
        Decimal("500"),
        minimum=2,
        types_exclus=("transport",),
    )

    assert _noms(promos) == ["Local"]


def test_tout_exclure_ne_donne_aucune_promotion():
    """Le choix assumé : une exclusion est une règle, pas une préférence.

    Plutôt qu'un post qui repêche ce qu'on a écarté, aucune promotion — et le
    message de repli, qui dit lui-même qu'il n'y a rien à montrer.
    """
    promos = find_promos(
        _batiments(*DEUX_TYPES),
        Decimal(0),
        Decimal("1e12"),
        minimum=2,
        tolere_min=Decimal(0),
        tolere_max=Decimal("1e15"),
        types_exclus=("transport", "bureaux"),
    )

    assert promos == []


# --- Ce que l'exclusion ne change pas ---------------------------------------


def test_le_rang_et_le_total_comptent_ce_qui_reste():
    """Le rang est affiché dans l'embed (« 2/3 »). Compté avant l'exclusion, il
    annoncerait un total que le post ne montre pas, et on chercherait la promo
    manquante."""
    batiments = _batiments(
        'transport,"Gare de fret",0,900,0,0,0,17,0,0,0',
        'bureaux,"Local",0,500,0,0,0,17,0,0,0',
        'bureaux,"Bureau",0,400,0,0,0,17,0,0,0',
    )

    promos = find_promos(
        batiments, Decimal(0), Decimal("1e12"), types_exclus=("transport",)
    )

    assert [(p.rang, p.total) for p in promos] == [(1, 2), (2, 2)]
