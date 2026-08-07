"""Tests de l'échelle monétaire du jeu (notation Ø)."""

from decimal import Decimal

import pytest

from src.money import (
    ECHELLE,
    MoneyError,
    TAUX_GESTION,
    convertir,
    frais_de_gestion,
    format_money,
    format_money_brut,
    format_money_long,
    parse_money,
)


# --- Les 15 exemples de la table officielle du jeu ---------------------------

@pytest.mark.parametrize(
    "montant, attendu",
    [
        ("10234", "10,23 KØ"),
        ("12250000", "12,25 MØ"),
        ("15450000000", "15,45 GØ"),
        ("18123000000000", "18,12 TØ"),
        # 23 890 TØ = 23,89 PØ
        ("23890" + "0" * 12, "23,89 PØ"),
        # 45 560 PØ = 45,56 EØ
        ("45560" + "0" * 15, "45,56 EØ"),
        # 68 920 EØ = 68,92 ZØ
        ("68920" + "0" * 18, "68,92 ZØ"),
        # 83 320 ZØ = 83,32 YØ
        ("83320" + "0" * 21, "83,32 YØ"),
        # 44 520 YØ = 44,52 RØ
        ("44520" + "0" * 24, "44,52 RØ"),
        # 50 632 RØ = 50,63 QØ
        ("50632" + "0" * 27, "50,63 QØ"),
        # 79 987 QØ = 79,99 UØ
        ("79987" + "0" * 30, "79,99 UØ"),
        # 35 123 UØ = 35,12 SØ
        ("35123" + "0" * 33, "35,12 SØ"),
        # 51 432 SØ = 51,43 XØ
        ("51432" + "0" * 36, "51,43 XØ"),
        # 42 928 XØ = 42,93 NØ
        ("42928" + "0" * 39, "42,93 NØ"),
        # 59 632 NØ = 59,63 DØ
        ("59632" + "0" * 42, "59,63 DØ"),
    ],
)
def test_exemples_officiels(montant, attendu):
    assert format_money(Decimal(montant)) == attendu


def test_table_complete():
    """Les 15 symboles du jeu, du plus grand au plus petit."""
    assert [s for _, s in ECHELLE] == list("DNXSUQRYZEPTGMK")


# --- Arrondi ----------------------------------------------------------------

def test_arrondi_au_plus_proche_pas_troncature():
    # 79 987 QØ -> 79,99 (une troncature donnerait 79,98)
    assert format_money(Decimal("79987" + "0" * 30)) == "79,99 UØ"
    # 42 928 XØ -> 42,93 (une troncature donnerait 42,92)
    assert format_money(Decimal("42928" + "0" * 39)) == "42,93 NØ"


def test_arrondi_demi_vers_le_haut():
    assert format_money(Decimal("1005000")) == "1,01 MØ"
    assert format_money(Decimal("1004000")) == "1,00 MØ"


def test_remontee_de_palier_apres_arrondi():
    """999,996 GØ ne doit pas s'afficher 1000,00 GØ mais 1,00 TØ."""
    assert format_money(Decimal("999996000000")) == "1,00 TØ"
    # 999 999 999 arrondit à 1000,00 MØ -> doit remonter en 1,00 GØ
    assert format_money(Decimal("999999999")) == "1,00 GØ"


# --- Petits montants et cas limites ----------------------------------------

def test_sous_mille_pas_de_symbole():
    assert format_money(Decimal("840")) == "840 Ø"
    assert format_money(Decimal("0")) == "0 Ø"
    assert format_money(Decimal("999")) == "999 Ø"


def test_pile_mille():
    assert format_money(Decimal("1000")) == "1,00 KØ"


def test_negatif():
    assert format_money(Decimal("-12250000")) == "-12,25 MØ"


def test_depassement_de_table_repli_scientifique():
    """Au-delà de 10^47, la table n'a plus de symbole -> notation scientifique."""
    resultat = format_money(Decimal("1" + "0" * 48))
    assert "E+" in resultat and resultat.endswith("Ø")


def test_dernier_palier_utilisable():
    # 999 DØ reste dans la table (10^45)
    assert format_money(Decimal("999" + "0" * 45)) == "999,00 DØ"


# --- Formes longue et brute -------------------------------------------------

def test_format_long_separateurs_milliers():
    assert format_money_long(Decimal("2710572934559948")) == (
        "2 710 572 934 559 948 Ø"
    )


def test_format_brut_chiffres_seuls():
    assert format_money_brut(Decimal("2710572934559948")) == "2710572934559948"


def test_format_brut_grand_entier_sans_perte():
    """Un entier de 21 chiffres doit ressortir intact (pas de float)."""
    grand = "138131471904669765329"
    assert format_money_brut(Decimal(grand)) == grand


# --- Saisie ----------------------------------------------------------------

@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("840", "840"),
        ("1K", "1000"),
        ("12.25M", "12250000"),
        ("12,25M", "12250000"),
        ("1.5G", "1500000000"),
        ("100T", "100" + "0" * 12),
        ("6P", "6" + "0" * 15),
        # symbole séparé du nombre
        ("6 P", "6" + "0" * 15),
        # espaces comme séparateurs de milliers : 50 6P = 506 PØ
        ("50 6P", "506" + "0" * 15),
        # casse indifférente, Ø optionnel
        ("1,5 gø", "1500000000"),
        ("1,5 go", "1500000000"),
        ("1,5g", "1500000000"),
        ("2,71 PØ", "2710" + "0" * 12),
        # espace insécable (copié depuis un message du bot)
        ("12,25 MØ", "12250000"),
        ("2 710 572", "2710572"),
        # négatif
        ("-1M", "-1000000"),
    ],
)
def test_parse_money(texte, attendu):
    assert parse_money(texte) == Decimal(attendu)


def test_parse_accepte_le_zero_final_comme_symbole_monetaire():
    """'1,5 g0' : le 0 final est un Ø mal tapé, pas un chiffre."""
    assert parse_money("1,5 g0") == Decimal("1500000000")


def test_aller_retour_format_puis_parse():
    """Un montant affiché par le bot doit être ré-injectable tel quel."""
    for brut in ["840", "10234", "12250000", "124467906332", "2710572934559948"]:
        texte = format_money(Decimal(brut))
        # L'arrondi à 2 décimales perd de la précision : on vérifie l'ordre de
        # grandeur, pas l'égalité stricte.
        reparse = parse_money(texte)
        ecart = abs(reparse - Decimal(brut)) / Decimal(brut) if brut != "0" else 0
        assert ecart < Decimal("0.01")


def test_aller_retour_forme_longue_exact():
    """La forme longue, elle, doit se reparser à l'identique."""
    for brut in ["840", "10234", "124467906332", "138131471904669765329"]:
        assert parse_money(format_money_long(Decimal(brut))) == Decimal(brut)


@pytest.mark.parametrize("mauvais", ["", "   ", "abc", "1,5 W", "M", "1.2.3M", "--5M"])
def test_parse_saisie_invalide(mauvais):
    with pytest.raises(MoneyError):
        parse_money(mauvais)


def test_message_erreur_liste_les_symboles():
    with pytest.raises(MoneyError) as exc:
        parse_money("1,5 W")
    assert "K" in str(exc.value) and "D" in str(exc.value)


def test_parse_grand_entier_sans_perte():
    grand = "138131471904669765329"
    assert parse_money(grand) == Decimal(grand)


# --- Conversion d'un palier à l'autre ---------------------------------------
#
# `format_money` choisit toujours le palier le plus grand qui tient. Convertir,
# c'est imposer le palier d'arrivée : on veut lire « 1 000 000 MØ » là où le bot
# écrirait « 1,00 TØ », parce que le jeu affiche parfois l'autre.

@pytest.mark.parametrize(
    "montant, symbole, attendu",
    [
        # Un palier vers le suivant, dans les deux sens.
        ("1" + "0" * 15, "T", "1 000,00 TØ"),
        ("1" + "0" * 12, "M", "1 000 000,00 MØ"),
        # Le cas du jeu : une promo à 2,71 PØ lue en TØ.
        ("2710572934559948", "T", "2 710,57 TØ"),
        # Palier d'arrivée plus grand que le montant : la mantisse descend
        # sous 1 plutôt que de basculer sur un autre symbole.
        ("1" + "0" * 12, "P", "0,00 PØ"),
        ("5" + "0" * 14, "P", "0,50 PØ"),
        # Vingt-et-un ordres de grandeur d'écart, la limite de la table.
        ("1" + "0" * 45, "K", "1 000 000 000 000 000 000 000 000 000 000 000 000 000 000,00 KØ"),
    ],
)
def test_convertir_vers_un_palier(montant, symbole, attendu):
    assert convertir(Decimal(montant), symbole) == attendu


def test_convertir_accepte_le_symbole_en_minuscule():
    """On tape vite dans Discord ; la saisie des montants tolère déjà la casse."""
    assert convertir(Decimal("1" + "0" * 15), "t") == "1 000,00 TØ"


def test_convertir_vers_l_unite():
    """`Ø` seul est un palier légitime : c'est ce que le jeu affiche en dur."""
    assert convertir(Decimal("2500"), "Ø") == "2 500,00 Ø"


def test_convertir_symbole_inconnu_liste_les_valides():
    """Le message doit donner la table : `B` n'existe pas dans ce jeu, et rien
    dans l'interface ne dit lesquels existent."""
    with pytest.raises(MoneyError, match="B"):
        convertir(Decimal(1000), "B")

    with pytest.raises(MoneyError) as capture:
        convertir(Decimal(1000), "B")
    assert "billion" in str(capture.value)


def test_convertir_arrondit_au_plus_proche():
    """Même règle que `format_money` : une troncature aurait donné 2 710,57.

    Discriminant, contrairement aux autres cas de la table : 2 710,578 tombe
    au-dessus de la moitié, là où 2 710,5729 s'arrondit pareil dans les deux
    sens et ne prouverait donc rien.
    """
    assert convertir(Decimal("2710578000000000"), "T") == "2 710,58 TØ"


def test_convertir_garde_le_signe():
    assert convertir(Decimal("-1" + "0" * 15), "T") == "-1 000,00 TØ"


def test_convertir_zero():
    assert convertir(Decimal(0), "T") == "0,00 TØ"


# --- Frais de gestion -------------------------------------------------------
#
# 7 % du montant, sans décimales : c'est ce que le jeu prélève. Le taux vit dans
# `money.py` avec la table de l'échelle plutôt que dans la commande, pour que le
# calcul soit testable sans Discord.

def test_frais_de_gestion_sur_un_montant_rond():
    assert frais_de_gestion(Decimal(1000)) == Decimal(70)


def test_frais_de_gestion_sans_decimales():
    """La raison d'être de la fonction : le jeu ne facture pas de fraction."""
    # 7 % de 1 001 = 70,07
    assert frais_de_gestion(Decimal(1001)) == Decimal(70)


def test_frais_de_gestion_arrondi_au_plus_proche():
    """Même règle que `format_money` partout ailleurs dans le bot."""
    # 7 % de 1 008 = 70,56 -> 71, et non 70.
    assert frais_de_gestion(Decimal(1008)) == Decimal(71)
    # 7 % de 1 007 = 70,49 -> 70.
    assert frais_de_gestion(Decimal(1007)) == Decimal(70)


def test_frais_de_gestion_sur_un_montant_du_jeu_sans_perte():
    """21 chiffres : un float aurait perdu les derniers avant l'arrondi."""
    # 7 % de 2 710 572 934 559 948 = 189 740 105 419 196,36 -> 189 740 105 419 196
    assert frais_de_gestion(Decimal("2710572934559948")) == Decimal("189740105419196")


def test_frais_de_gestion_de_zero():
    assert frais_de_gestion(Decimal(0)) == Decimal(0)


def test_frais_de_gestion_renvoie_un_entier_exact():
    """Un `Decimal` à exposant nul, pas `70.00` : il est reformaté ensuite, et
    `format_money_long` ne doit pas avoir à réarrondir."""
    frais = frais_de_gestion(Decimal(1001))
    assert frais == frais.to_integral_value()
    assert str(frais) == "70"


def test_taux_de_gestion_expose():
    """Pour que la commande affiche le taux sans le recopier en dur : deux
    valeurs différentes dans le message et dans le calcul seraient invisibles."""
    assert TAUX_GESTION == Decimal(7)
