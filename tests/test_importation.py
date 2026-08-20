"""Reprendre la configuration commune dans le tiroir d'un serveur.

Le cloisonnement n'a pas de repli : un serveur qui n'a rien réglé ne publie
nulle part, ne laisse entrer que ses administrateurs et laisse son salon de logs
muet. `/reglages importer` est le pont, à taper une fois dans chaque serveur, et
tout ce qui suit décide de ce qu'il fait passer.

Deux dangers, et ce sont eux que ces tests visent :

- **Reprendre trop.** Une seule liste de salons couvrait les deux serveurs. Tout
  recopier ferait publier chaque serveur dans les salons de tous les autres — la
  garde de `src/tournee.py` les écarterait à l'envoi, mais chaque passage
  écrirait un signalement, et `/reglages voir` montrerait des salons qui ne sont
  pas là.
- **Écraser ce qui est déjà réglé.** La commande peut être tapée deux fois, ou
  après un premier réglage à la main. Le plan le dit : « ne touche pas à
  l'existant : si le résultat ne va pas, rien n'est perdu. »

Le calcul est ici, sans Discord ni base : ce qu'il reprend, ce qu'il écarte et ce
qu'il laisse tel quel se lit sur des dictionnaires nus.
"""

from src.importation import nommer, preparer


def _base(**cles):
    """La base commune, telle que `Store.tout()` la rend."""
    return dict(cles)


# --- Ce qui est repris ------------------------------------------------------


def test_les_reglages_du_commun_sont_repris():
    """Le besoin de base : l'heure, les fourchettes, le template."""
    base = _base(
        config={"heure": "09:00", "fuseau": "Europe/Paris"},
        template={"embeds": [{"title": "Promos"}]},
    )

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"] == {"heure": "09:00", "fuseau": "Europe/Paris"}
    assert reprise.a_ecrire["template"] == {"embeds": [{"title": "Promos"}]}


def test_les_releves_des_filiales_sont_repris():
    """Saisis à la main, un par un : les reperdre serait le plus cher."""
    base = _base(filiales=[{"nom": "ARMEE", "montant": "1000"}])

    reprise = preparer(base, "111", set())

    assert reprise.a_ecrire["filiales"] == [{"nom": "ARMEE", "montant": "1000"}]


def test_les_membres_autorises_sont_repris():
    """Sinon plus personne hors administrateurs n'entrerait après le
    déploiement, et la commande qui répare cela leur serait refusée."""
    base = _base(config={"autorises": ["42"]})

    reprise = preparer(base, "111", set())

    assert reprise.a_ecrire["config"]["autorises"] == ["42"]


def test_les_marques_du_jour_sont_reprises():
    """Sans elles, l'import ferait repartir dans la minute un post déjà sorti
    ce matin — un doublon, précisément le jour où l'on touche aux réglages."""
    base = _base(
        derniere_publication="2026-08-20",
        derniere_publication_filiales="2026-08-20",
    )

    reprise = preparer(base, "111", set())

    assert reprise.a_ecrire["derniere_publication"] == "2026-08-20"
    assert reprise.a_ecrire["derniere_publication_filiales"] == "2026-08-20"


def test_une_cle_inconnue_est_reprise_et_nommee():
    """Le tiroir d'un module qui n'existe pas encore.

    Reprendre seulement une liste de clés écrite en dur oublierait en silence
    celle qu'un module ajoutera, et le manque ne se verrait qu'au premier post
    absent.
    """
    base = _base(**{"bonjour:salutations": ["salut"]})

    reprise = preparer(base, "111", set())

    assert reprise.a_ecrire["bonjour:salutations"] == ["salut"]
    # Nommée, et non passée sous silence : le compte rendu doit permettre de
    # constater qu'elle est passée.
    assert "bonjour:salutations" in nommer("bonjour:salutations")


# --- Ce qui est écarté : les salons d'un autre serveur ----------------------


def test_les_salons_dun_autre_serveur_sont_ecartes():
    base = _base(config={"salons": ["1", "2"]})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"]["salons"] == ["1"]
    assert reprise.salons_ecartes == ("2",)
    assert reprise.salons_gardes == ("1",)


def test_un_salon_note_en_nombre_est_reconnu():
    """La base est du JSON : un salon peut y avoir été écrit en nombre.

    Comparé tel quel aux ids que Discord donne — que la commande passe en texte
    —, il ne correspondrait à rien et serait pris pour le salon d'un autre
    serveur, donc écarté. `Store` prend la même précaution (`_salons_servis`).
    """
    base = _base(config={"salons": [1]})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"]["salons"] == ["1"]
    assert reprise.salons_ecartes == ()


def test_les_salons_dune_fourchette_sont_filtres():
    """La fourchette reste : ses bornes sont un réglage, et les reperdre
    obligerait à les ressaisir alors qu'il n'y a qu'un salon à corriger."""
    base = _base(
        config={
            "fourchettes": [
                {"nom": "grosses", "prix_min": "1", "prix_max": "2", "salons": ["1", "2"]}
            ]
        }
    )

    reprise = preparer(base, "111", {"1"})

    fourchette = reprise.a_ecrire["config"]["fourchettes"][0]
    assert fourchette["salons"] == ["1"]
    assert fourchette["prix_min"] == "1"


def test_une_fourchette_qui_perd_tous_ses_salons_reste():
    """Elle ne publiera nulle part, et `/fourchette liste` le montrera — mieux
    qu'une fourchette disparue sans que rien ne le dise."""
    base = _base(config={"fourchettes": [{"nom": "grosses", "salons": ["2"]}]})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"]["fourchettes"][0]["salons"] == []


def test_les_salons_du_tableau_des_frais_sont_filtres():
    base = _base(config={"filiales_salons": ["1", "2"]})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"]["filiales_salons"] == ["1"]


def test_le_salon_unique_dune_config_plate_est_filtre():
    """`salon_id` est le réglage d'avant le multi-salon, encore lu à la
    migration : laissé tel quel, il ferait publier chez l'autre."""
    base = _base(config={"salon_id": "2"})

    reprise = preparer(base, "111", {"1"})

    assert "salon_id" not in reprise.a_ecrire["config"]
    assert reprise.salons_ecartes == ("2",)


def test_le_salon_de_logs_dun_autre_serveur_est_ecarte():
    """Le journal du serveur raconterait sa tournée chez le voisin, en lui
    donnant les ids de ses salons."""
    base = _base(config={"logs_salon_id": "2"})

    reprise = preparer(base, "111", {"1"})

    assert "logs_salon_id" not in reprise.a_ecrire["config"]
    assert reprise.salons_ecartes == ("2",)


def test_le_tiroir_generique_dune_publication_est_filtre():
    """Une publication déclarée par un module range ses salons là."""
    base = _base(**{"publication:bonjour:salons": ["1", "2"]})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["publication:bonjour:salons"] == ["1"]
    assert reprise.salons_ecartes == ("2",)


def test_un_salon_ecarte_deux_fois_nest_nomme_quune_fois():
    """Le même salon peut être cité par deux fourchettes et le tableau : le
    répéter dans le compte rendu ferait croire à plusieurs erreurs."""
    base = _base(
        config={"salons": ["3", "2"], "filiales_salons": ["2"]},
        **{"publication:bonjour:salons": ["3"]},
    )

    reprise = preparer(base, "111", {"1"})

    assert reprise.salons_ecartes == ("2", "3")


# --- Ce qui n'a rien à faire dans un tiroir de serveur ----------------------


def test_le_cache_des_noms_de_salons_nest_pas_recopie():
    """Il est commun, et le reste : un nom de salon ne dépend pas de qui le
    regarde. Recopié dans le tiroir, il y dormirait sans lecteur — et aucun
    ménage ne viendrait jamais l'y nettoyer."""
    base = _base(
        config={
            "heure": "09:00",
            "salons_connus": {"1": {"nom": "promos", "serveur": "111"}},
            "serveurs": {"111": "Empire Immo"},
        }
    )

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"] == {"heure": "09:00"}


def test_la_mention_reste_dans_la_configuration_commune():
    """`roles` est déjà une table par serveur, lue depuis le commun : la
    recopier donnerait deux réglages pour une seule mention, dont un que
    `/reglages mention` ne toucherait jamais."""
    base = _base(config={"roles": {"111": "7"}, "role_id": "9", "heure": "09:00"})

    reprise = preparer(base, "111", {"1"})

    assert reprise.a_ecrire["config"] == {"heure": "09:00"}


def test_le_tiroir_dun_autre_serveur_nest_pas_repris():
    """Sans cette garde, importer donnerait à un serveur les réglages d'un
    autre, et enfermerait un tiroir dans un tiroir."""
    base = _base(
        config={"heure": "09:00"},
        **{"serveur:222:config": {"heure": "21:00"}},
    )

    reprise = preparer(base, "111", set())

    assert list(reprise.a_ecrire) == ["config"]
    assert reprise.a_ecrire["config"]["heure"] == "09:00"


# --- Ce qui est déjà réglé ici n'est pas écrasé -----------------------------


def test_une_cle_deja_reglee_dans_le_serveur_est_laissee_telle_quelle():
    """« Ne touche pas à l'existant » : la commande doit pouvoir être retapée,
    et un réglage fait à la main ne doit pas disparaître au profit de l'ancien."""
    base = _base(
        config={"heure": "09:00"},
        **{"serveur:111:config": {"heure": "21:00"}},
    )

    reprise = preparer(base, "111", set())

    assert "config" not in reprise.a_ecrire
    assert reprise.deja_reglees == ("config",)


def test_ce_qui_manque_est_repris_meme_si_le_reste_est_regle():
    """Un import partiel doit compléter, pas renoncer : sinon un serveur réglé
    à moitié n'aurait plus aucun moyen de reprendre le template."""
    base = _base(
        config={"heure": "09:00"},
        template={"embeds": []},
        **{"serveur:111:config": {"heure": "21:00"}},
    )

    reprise = preparer(base, "111", set())

    assert list(reprise.a_ecrire) == ["template"]
    assert reprise.deja_reglees == ("config",)


def test_un_commun_vide_ne_reprend_rien():
    """Rien à reprendre n'est pas une panne, et doit se dire : sans ça, la
    commande répondrait « ✅ » à un import qui n'a rien fait."""
    reprise = preparer({}, "111", {"1"})

    assert reprise.a_ecrire == {}
    assert reprise.salons_ecartes == ()
    assert reprise.deja_reglees == ()


# --- Nommer ce qui est passé ------------------------------------------------


def test_chaque_cle_connue_a_un_nom_lisible():
    """Le compte rendu s'adresse à quelqu'un qui n'a jamais vu la base."""
    assert "réglages" in nommer("config")
    assert "template" in nommer("template")
    assert "filiales" in nommer("filiales")


def test_le_nom_dune_publication_dit_sa_cle():
    """Deux publications d'un même module se distinguent par là."""
    assert "bonjour" in nommer("publication:bonjour:heure")
    assert "bonjour" in nommer("publication:bonjour:salons")
    assert "bonjour" in nommer("publication:bonjour:derniere")
