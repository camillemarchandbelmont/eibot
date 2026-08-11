"""Cœur des frais par filiale : calcul, enregistrement, total.

Ce module ne connaît ni Discord ni Postgres. Ce qui se vérifie ici, c'est la
règle du jeu — les frais valent 7 % des bénéfices, une filiale en perte ne paie
rien — et le fait qu'une filiale ressaisie **remplace** la précédente au lieu
d'en créer une seconde.

Le nom est la clé d'import du jeu : il doit ressortir caractère pour caractère,
doubles espaces compris.
"""

from decimal import Decimal

import pytest

from src.filiales import (
    Filiale,
    FilialeError,
    calculer,
    depuis_json,
    enregistrer,
    index_de,
    total_frais,
    vers_json,
)


# --- calculer ---------------------------------------------------------------


def test_les_frais_valent_sept_pour_cent_des_benefices():
    filiale = calculer("ARMEE DE TERRE", Decimal("2710572934559948"), "2026-08-11")

    # La même valeur que `/frais` sur ce montant : la règle n'est pas recopiée.
    assert filiale.frais == Decimal("189740105419196")


def test_les_frais_sont_arrondis_au_plus_proche_sans_decimales():
    """Le jeu ne facture pas de fraction d'Ø, et arrondit au plus proche.

    1 007 Ø × 7 % = 70,49 → 70. 1 008 Ø × 7 % = 70,56 → 71.
    """
    assert calculer("A", Decimal(1007), "2026-08-11").frais == Decimal(70)
    assert calculer("A", Decimal(1008), "2026-08-11").frais == Decimal(71)


def test_une_filiale_en_perte_ne_paie_rien():
    """Le jeu ne rend pas d'argent : des frais négatifs seraient inventés."""
    filiale = calculer("EN PERTE", Decimal(-5000), "2026-08-11")

    assert filiale.frais == Decimal(0)
    assert filiale.en_perte


def test_des_benefices_nuls_comptent_comme_une_perte():
    """Zéro n'est pas un bénéfice : rien à prélever, et la filiale doit être
    signalée comme les autres qui ne rapportent rien."""
    filiale = calculer("A ZERO", Decimal(0), "2026-08-11")

    assert filiale.frais == Decimal(0)
    assert filiale.en_perte


def test_une_filiale_qui_gagne_n_est_pas_en_perte():
    assert not calculer("A", Decimal(1), "2026-08-11").en_perte


def test_le_nom_garde_ses_doubles_espaces():
    """C'est la clé d'import du jeu : « ARMEE  DE TERRE » n'est pas
    « ARMEE DE TERRE », et une normalisation silencieuse le rendrait
    introuvable."""
    filiale = calculer("  ARMEE  DE TERRE  ", Decimal(100), "2026-08-11")

    assert filiale.nom == "ARMEE  DE TERRE"


def test_un_nom_vide_est_refuse():
    """Sans nom, la ligne serait anonyme dans le tableau et impossible à
    remplacer par une ressaisie."""
    with pytest.raises(FilialeError):
        calculer("   ", Decimal(100), "2026-08-11")


def test_la_date_de_saisie_est_conservee():
    """Elle rend visible une filiale qu'on a oublié de mettre à jour."""
    assert calculer("A", Decimal(100), "2026-08-09").date == "2026-08-09"


# --- enregistrer ------------------------------------------------------------


def test_une_filiale_inconnue_est_ajoutee_a_la_fin():
    liste = enregistrer([], calculer("A", Decimal(100), "2026-08-11"))
    liste = enregistrer(liste, calculer("B", Decimal(200), "2026-08-11"))

    assert [f.nom for f in liste] == ["A", "B"]


def test_une_ressaisie_remplace_la_precedente():
    """Sinon le tableau afficherait deux lignes pour la même filiale, et le
    total serait compté deux fois."""
    liste = enregistrer([], calculer("A", Decimal(100), "2026-08-09"))
    liste = enregistrer(liste, calculer("A", Decimal(200), "2026-08-11"))

    assert len(liste) == 1
    assert liste[0].benefices == Decimal(200)
    assert liste[0].date == "2026-08-11"


def test_la_ressaisie_garde_la_place_de_la_filiale():
    """Remonter en fin de liste ferait danser le tableau d'un jour à l'autre."""
    liste = [
        calculer("A", Decimal(1), "2026-08-11"),
        calculer("B", Decimal(2), "2026-08-11"),
        calculer("C", Decimal(3), "2026-08-11"),
    ]
    liste = enregistrer(liste, calculer("B", Decimal(9), "2026-08-11"))

    assert [f.nom for f in liste] == ["A", "B", "C"]
    assert liste[1].benefices == Decimal(9)


def test_la_ressaisie_ignore_la_casse_et_garde_le_nom_saisi():
    """« armee » et « ARMEE » sont la même filiale du jeu ; deux lignes
    indistinguables à l'œil seraient pires qu'un remplacement."""
    liste = enregistrer([], calculer("ARMEE", Decimal(100), "2026-08-09"))
    liste = enregistrer(liste, calculer("armee", Decimal(200), "2026-08-11"))

    assert len(liste) == 1
    assert liste[0].nom == "armee"


def test_enregistrer_ne_modifie_pas_la_liste_donnee():
    """La liste vient de la base : la muter en place ferait divergier ce qui est
    affiché de ce qui est enregistré si l'écriture échoue ensuite."""
    origine = [calculer("A", Decimal(1), "2026-08-11")]

    enregistrer(origine, calculer("B", Decimal(2), "2026-08-11"))

    assert [f.nom for f in origine] == ["A"]


# --- index_de ---------------------------------------------------------------


def test_index_de_trouve_la_filiale_sans_egard_a_la_casse():
    liste = [calculer("ARMEE  DE TERRE", Decimal(1), "2026-08-11")]

    assert index_de(liste, "armee  de terre") == 0
    assert index_de(liste, "  ARMEE  DE TERRE ") == 0


def test_index_de_renvoie_moins_un_quand_la_filiale_est_absente():
    assert index_de([], "A") == -1


def test_index_de_ne_confond_pas_les_espaces_internes():
    """Un espace en moins, c'est une autre filiale pour le jeu."""
    liste = [calculer("ARMEE  DE TERRE", Decimal(1), "2026-08-11")]

    assert index_de(liste, "ARMEE DE TERRE") == -1


# --- total_frais ------------------------------------------------------------


def test_le_total_additionne_les_frais():
    liste = [
        calculer("A", Decimal(1000), "2026-08-11"),  # 70
        calculer("B", Decimal(2000), "2026-08-11"),  # 140
    ]

    assert total_frais(liste) == Decimal(210)


def test_le_total_d_une_liste_vide_vaut_zero():
    assert total_frais([]) == Decimal(0)


def test_le_total_ne_perd_pas_de_precision():
    """Vingt-un chiffres : les bénéfices du Mégapôle, où un `float` casse.

    Dix-sept chiffres ne suffisaient pas à ce test : ils tiennent encore dans la
    mantisse d'un `float64`, donc une somme en flottants y donnait le bon
    résultat et le test passait quand même.
    """
    liste = [calculer("A", Decimal("173019538387120000000"), "2026-08-11")] * 3

    assert total_frais(liste) == Decimal("36334103061295200000")


# --- JSON -------------------------------------------------------------------


def test_l_aller_retour_json_conserve_tout():
    liste = [
        calculer("ARMEE  DE TERRE", Decimal("2710572934559948"), "2026-08-11"),
        calculer("EN PERTE", Decimal(-1), "2026-08-09"),
    ]

    relues = depuis_json(vers_json(liste))

    assert relues == liste


def test_les_montants_sont_stockes_en_chaine():
    """JSONB n'a pas d'entier de 17 chiffres : un nombre JSON passerait par un
    flottant et perdrait les derniers."""
    json = vers_json([calculer("A", Decimal("2710572934559948"), "2026-08-11")])

    assert json[0]["benefices"] == "2710572934559948"
    assert isinstance(json[0]["frais"], str)


def test_depuis_json_ignore_une_ligne_illisible():
    """La base est du JSON retouchable à la main : une ligne cassée doit coûter
    sa filiale, pas le tableau du jour."""
    liste = depuis_json(
        [
            {"nom": "A", "benefices": "100", "frais": "7", "date": "2026-08-11"},
            {"nom": "B", "benefices": "pouet", "frais": "7", "date": "2026-08-11"},
            "pas un objet",
            {"nom": "", "benefices": "100", "frais": "7", "date": "2026-08-11"},
        ]
    )

    assert [f.nom for f in liste] == ["A"]


def test_depuis_json_recalcule_les_frais_incoherents():
    """Les frais enregistrés viennent du calcul ; s'ils ne collent plus aux
    bénéfices (retouche à la main, taux changé), le calcul fait foi — sinon le
    tableau afficherait un montant que le jeu ne réclamera pas."""
    liste = depuis_json(
        [{"nom": "A", "benefices": "1000", "frais": "999", "date": "2026-08-11"}]
    )

    assert liste[0].frais == Decimal(70)


def test_depuis_json_accepte_une_liste_absente():
    assert depuis_json(None) == []
