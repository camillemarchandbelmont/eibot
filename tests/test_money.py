"""Tests de l'échelle monétaire du jeu (notation Ø)."""

from decimal import Decimal

import pytest

from src.money import (
    ECHELLE,
    MoneyError,
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
