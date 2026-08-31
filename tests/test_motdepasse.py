"""Mot de passe d'écriture de la page, et cookie qui évite de le retaper.

La page est ouverte pour convertir et fermée pour enregistrer : sans ça, l'URL
suffirait à remplacer les relevés du jour de n'importe quelle entreprise. Un mot
de passe **par entreprise**, donc, puis un cookie pour ne le taper qu'une fois
par navigateur.

Ce qui est éprouvé ici est pur : ni HTTP, ni base. Les propriétés qui comptent :

- le mot de passe n'est **jamais stocké**, seulement son empreinte salée. La base
  est chez un hébergeur, et un mot de passe lisible en base serait le mot de passe
  de l'entreprise pour quiconque la lit ;
- **sans empreinte enregistrée, tout est refusé.** Une entreprise qui n'a pas
  réglé de mot de passe ne s'enregistre pas depuis la page — l'inverse, accepter
  n'importe quoi faute de mot de passe, ouvrirait toutes les entreprises neuves ;
- le cookie est signé **avec l'empreinte** : changer le mot de passe invalide donc
  tous les cookies déjà distribués, sans avoir à en tenir la liste.
"""

from src.motdepasse import (
    ALPHABET,
    DUREE_JETON,
    LONGUEUR_MAXIMALE,
    LONGUEUR_MINIMALE,
    empreinte,
    nouveau,
    refuse,
    signer,
    verifie,
    verifier_jeton,
)

EMPIRE = "111"
VOISIN = "222"


# --- Le mot de passe --------------------------------------------------------


def test_deux_mots_de_passe_tires_de_suite_diffèrent():
    """Tiré au hasard, et non dérivé du nom de l'entreprise ou de l'heure."""
    assert nouveau() != nouveau()


def test_le_mot_de_passe_se_retape_sans_ambiguite():
    """Il se lit dans Discord et se tape dans un navigateur.

    Ni `O` contre `0`, ni `l` contre `1` : un caractère ambigu se traduirait par
    un refus qu'on prendrait pour une panne de la page.
    """
    mdp = nouveau()

    assert set(mdp) <= set(ALPHABET + "-")
    assert len(mdp.replace("-", "")) >= 16


# --- Un mot de passe choisi -------------------------------------------------
#
# Choisi et non tiré : c'est le seul cas où le bot ne maîtrise plus la force de
# ce qu'il enregistre. La page est ouverte sur internet et personne ne compte
# les essais : ces règles sont donc le seul plancher qui existe.


def test_un_mot_de_passe_choisi_convenable_est_accepte():
    """Le cas courant : quelque chose qu'on retient, assez long pour tenir."""
    assert refuse("frais-du-soir") is None


def test_le_mot_de_passe_tire_par_le_bot_passe_ses_propres_regles():
    """Une règle que le tirage ne respecterait pas serait une règle absurde — et
    le jour où l'alphabet ou la longueur du tirage changent, c'est ici que ça se
    voit."""
    assert refuse(nouveau()) is None


def test_un_mot_de_passe_choisi_trop_court_est_refuse():
    """Sans plancher, `1234` suffirait à ouvrir les relevés en écriture.

    Sept caractères écrits en clair, et non déduits de la constante : un plancher
    ramené à un accepterait tout ce qui se déduit de lui, et le refus se
    vérifierait contre lui-même.
    """
    raison = refuse("abcdefg")

    assert raison is not None
    assert "minimum" in raison.casefold()


def test_le_plancher_reste_tapable_et_le_plafond_utile():
    """Deux bornes qui n'ont de sens que dans un intervalle.

    Trop bas, le plancher ne protège plus rien — la page est ouverte sur
    internet. Trop haut, il fait écrire sur un papier ce qu'on voulait retenir, et
    le tirage du bot lui-même n'y passerait plus.
    """
    assert 8 <= LONGUEUR_MINIMALE <= 16
    assert LONGUEUR_MINIMALE < LONGUEUR_MAXIMALE <= 128


def test_les_espaces_ne_comptent_pas_dans_la_longueur():
    """`verifie` enlève les espaces des deux bouts avant de comparer : les garder
    ici laisserait régler un mot de passe de trois lettres et deux espaces, qu'il
    faudrait ensuite taper sans les espaces."""
    raison = refuse("   abc   ")

    assert raison is not None
    assert "minimum" in raison.casefold()


def test_un_mot_de_passe_vide_nest_pas_un_mot_de_passe():
    """Le champ laissé vide veut dire « tire-le pour moi » : c'est à la commande
    d'en décider, pas à cette règle de le bénir."""
    assert refuse("") is not None
    assert refuse("     ") is not None


def test_un_mot_de_passe_toujours_le_meme_caractere_est_refuse():
    """Assez long et pourtant deviné du premier coup : la longueur seule ne dit
    rien de la force."""
    for pauvre in ("aaaaaaaaaaaa", "abababababab", "111111111111"):
        raison = refuse(pauvre)
        assert raison is not None, pauvre
        assert "différents" in raison.casefold(), pauvre


def test_un_mot_de_passe_interminable_est_refuse():
    """Le champ de Discord s'arrête à cette longueur, mais rien ne garantit que
    ce qui arrive au bot en vienne : la même borne est tenue des deux côtés,
    sinon ce qui passerait la première serait coupé par la seconde."""
    raison = refuse("choisi-" + "a" * LONGUEUR_MAXIMALE)

    assert raison is not None
    assert "maximum" in raison.casefold()


def test_lempreinte_ignore_les_espaces_des_deux_bouts():
    """`verifie` les enlève : les garder au moment d'enregistrer donnerait un mot
    de passe que personne ne pourrait plus taper — collé avec son espace, il
    serait refusé, et le retaper sans ne marcherait pas davantage."""
    trace = empreinte("  frais-du-soir  ")

    assert verifie(trace, "frais-du-soir") is True


# --- L'empreinte ------------------------------------------------------------


def test_lempreinte_ne_contient_pas_le_mot_de_passe():
    """Ce qui part en base ne doit pas rendre le mot de passe à sa lecture."""
    mdp = nouveau()
    trace = str(empreinte(mdp))

    assert mdp not in trace
    assert mdp.replace("-", "") not in trace


def test_le_bon_mot_de_passe_est_reconnu():
    mdp = nouveau()

    assert verifie(empreinte(mdp), mdp) is True


def test_un_mauvais_mot_de_passe_est_refuse():
    assert verifie(empreinte(nouveau()), nouveau()) is False


def test_deux_empreintes_du_meme_mot_de_passe_different():
    """Le sel. Sans lui, deux entreprises au même mot de passe se verraient en
    base, et une table d'empreintes précalculées le rendrait."""
    mdp = nouveau()

    assert empreinte(mdp) != empreinte(mdp)
    assert verifie(empreinte(mdp), mdp) is True


def test_sans_empreinte_tout_est_refuse():
    """Une entreprise qui n'a pas réglé de mot de passe ne s'enregistre pas.

    Accepter faute d'empreinte — le `"" == ""` de l'API — ouvrirait en écriture
    toutes les entreprises qui n'ont rien réglé, c'est-à-dire toutes les neuves.
    """
    assert verifie(None, "") is False
    assert verifie(None, nouveau()) is False
    assert verifie({}, nouveau()) is False


def test_une_empreinte_abimee_refuse_tout():
    """La configuration est du JSON retouchable à la main.

    Une empreinte tronquée doit coûter l'accès, jamais l'ouvrir : une exception
    non rattrapée derrière un `try` trop large finirait par passer pour un refus,
    ou pire pour un succès.
    """
    for abimee in ({"sel": "zz"}, {"empreinte": "zz"}, {"sel": 1, "empreinte": 2}):
        assert verifie(abimee, nouveau()) is False


def test_un_mot_de_passe_vide_est_refuse_sans_calcul():
    """Le champ laissé vide, cas du clic sur « Enregistrer » avant de le taper."""
    assert verifie(empreinte(nouveau()), "") is False
    assert verifie(empreinte(nouveau()), "   ") is False


# --- Le cookie --------------------------------------------------------------


def test_un_jeton_signe_est_reconnu():
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)

    assert verifier_jeton(trace, jeton, EMPIRE, maintenant=999) is True


def test_un_jeton_expire_est_refuse():
    """La durée est celle du cookie ; le vérifier ici aussi, parce qu'un cookie
    se rejoue à la main bien après que le navigateur l'aurait effacé."""
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)

    assert verifier_jeton(trace, jeton, EMPIRE, maintenant=1001) is False


def test_un_jeton_dune_autre_entreprise_est_refuse():
    """Le cookie porte l'entreprise dans sa signature.

    Sans elle, le mot de passe d'une entreprise donnerait le droit d'écrire dans
    toutes les autres — la page en propose la liste dans un menu déroulant.
    """
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)

    assert verifier_jeton(trace, jeton, VOISIN, maintenant=999) is False


def test_un_jeton_retouche_est_refuse():
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)

    assert verifier_jeton(trace, jeton + "a", EMPIRE, maintenant=999) is False
    assert verifier_jeton(trace, jeton[:-1], EMPIRE, maintenant=999) is False


def test_une_date_dexpiration_retouchee_est_refusee():
    """L'expiration est en clair dans le jeton : elle doit être signée.

    Sinon prolonger un cookie périmé serait une retouche de texte, et un cookie
    volé vaudrait pour toujours.
    """
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)
    _, _, signature = jeton.partition(".")

    assert verifier_jeton(trace, f"9999.{signature}", EMPIRE, maintenant=1001) is False


def test_changer_de_mot_de_passe_invalide_les_cookies():
    """La propriété qui dispense de tenir une liste de sessions.

    Le jeton est signé avec l'empreinte : la remplacer coupe tous les
    navigateurs déjà identifiés, ce qui est exactement ce qu'on attend d'un
    changement de mot de passe.
    """
    ancienne = empreinte(nouveau())
    jeton = signer(ancienne, EMPIRE, expiration=1000)
    nouvelle = empreinte(nouveau())

    assert verifier_jeton(nouvelle, jeton, EMPIRE, maintenant=999) is False


def test_un_jeton_illisible_est_refuse():
    """Un cookie d'une version précédente, tronqué, ou fabriqué à la main."""
    trace = empreinte(nouveau())

    for illisible in ("", ".", "abc", "abc.def", "1000.", ".signature", "x.y.z"):
        assert verifier_jeton(trace, illisible, EMPIRE, maintenant=999) is False


def test_sans_empreinte_aucun_jeton_ne_vaut():
    """Le mot de passe retiré ferme la page, cookies compris."""
    jeton = signer(empreinte(nouveau()), EMPIRE, expiration=1000)

    assert verifier_jeton(None, jeton, EMPIRE, maintenant=999) is False


def test_le_jeton_ne_contient_pas_lempreinte():
    """Le cookie voyage chez le navigateur et se lit dans ses outils.

    Il porte une signature, pas la clé qui l'a produite : l'empreinte permettrait
    de forger un cookie pour n'importe quelle date d'expiration.
    """
    trace = empreinte(nouveau())
    jeton = signer(trace, EMPIRE, expiration=1000)

    assert trace["empreinte"] not in jeton
    assert trace["sel"] not in jeton


def test_la_duree_du_jeton_ne_depasse_pas_ce_quun_navigateur_retient():
    """Le cookie est reposé à chaque enregistrement : cette durée est un délai
    d'**inactivité**, pas une date de fin.

    Le plafond n'est pas un choix : les navigateurs ramènent d'eux-mêmes à
    quatre cents jours tout cookie qui demande plus. Au-delà, le cookie
    disparaîtrait avant la date pour laquelle il est signé, et le mot de passe
    serait à retaper un jour où rien ne l'annonce.
    """
    assert 300 * 24 * 3600 <= DUREE_JETON <= 400 * 24 * 3600
