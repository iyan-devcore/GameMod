"""Who speaks each line, for translators choosing Hindi verb forms.

A line's key starts with a speaker code (SPID_M_A1_GALLERY_009 is Spider-Man), and the game's
NAME_SUBTITLE_<code> entry gives the name it shows. Genders below are from the story. For
other speakers the translator gets the official Russian line, whose verbs show gender.
"""
from __future__ import annotations

KNOWN = {
    "SPID": "Spider-Man (Peter Parker), male",
    "PETE": "Peter Parker, male",
    "MARY": "Mary Jane (MJ), female",
    "MILE": "Miles Morales, teenage boy",
    "YURI": "Captain Yuri Watanabe, female",
    "SCRW": "Screwball, female",
    "OTTO": "Dr. Otto Octavius, male",
    "DOCK": "Doctor Octopus (Otto), male",
    "AUNT": "Aunt May, female",
    "JJJ": "J. Jonah Jameson, male",
    "MART": "Martin Li, male",
    "MNEG": "Mister Negative (Martin Li), male",
    "JEFF": "Officer Jefferson Davis, male",
    "SABL": "Silver Sable, female",
    "BCAT": "Black Cat (Felicia Hardy), female",
    "NORM": "Mayor Norman Osborn, male",
    "FISK": "Wilson Fisk, male",
    "HAMM": "Hammerhead, male",
    "TOMB": "Tombstone, male",
    "ELEC": "Electro, male",
    "RHIN": "Rhino, male",
    "SCOR": "Scorpion, male",
    "VULT": "Vulture, male",
    "SHOC": "Shocker, male",
    "MYST": "Mysterio, male",
    "TASK": "Taskmaster, male",
    "HOWA": "Howard, male",
    "PHIL": "Philip Chang, male",
    "HARY": "Harry Osborn, male",
    "RIO": "Rio Morales, female",
    "GLOR": "Gloria, female",
    "STEF": "Stephanie, female",
    "EILE": "Eileen, female",
    "TESS": "Tessa, female",
    "CARM": "Carmen, female",
    "MIA": "Mia, female",
    "LMOM": "Martin Li's mother, female",
    "MLIF": "Martin Li's father, male",
    "WAIT": "waitress, female",
    "SODR": "shop owner's daughter, female",
    "TONY": "Tony, male",
    "ERNI": "Ernie, male",
    "DESH": "Deshaun, male",
    "CRLS": "Carlos, male",
    "RODR": "Rodrigo, male",
    "STEV": "Steve, male",
    "ETHA": "Ethan, male",
    "NIKO": "Niko, male",
    "HARO": "Harold, male",
    "BRAD": "Brad Davis, male",
    "CHAR": "Charles Standish, male",
    "DIAB": "diabetic man, male",
    "FAKE": "a fake Spider-Man impostor, male",
    "MDON": "Maggia boss, male",
    "DOBA": "David Obademi, male",
}

# Generic speakers whose shown name already gives the gender.
_BY_NAME = {
    "Man": "male",
    "Woman": "female",
    "Homeless Man": "male",
    "Homeless Woman": "female",
    "Diabetic Man": "male",
    "Waitress": "female",
}


def describe(code: str, shown_name: str) -> tuple[str, bool]:
    """A description for translators, and whether it states the speaker's gender."""
    if code in KNOWN:
        return KNOWN[code], True
    if shown_name in _BY_NAME:
        return f"{shown_name.lower()}, {_BY_NAME[shown_name]}", True
    return shown_name or "unknown speaker", False
