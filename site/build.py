"""Build the AllBambaGuns player site.

    python site/build.py            # writes site/_build
    python site/build.py --serve    # builds, then serves it on http://localhost:8000
    python site/build.py --out DIR  # writes somewhere else (CI uses this)

Every stat, icon and research cost is read from the mod's own defs, the way the
game loads them: the root folder, then Launchers (vanilla) or CE (Combat
Extended), later defs replacing earlier ones with the same defName. The written
text lives in templates/index.html.
"""
from __future__ import annotations

import argparse
import copy
import functools
import http.server
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
BUILD_MARKER = ".abg-site-build"  # a rebuild only ever deletes a folder it made itself

WORKSHOP = "https://steamcommunity.com/sharedfiles/filedetails/?id=" + (ROOT / "About/PublishedFileId.txt").read_text().strip()
GITHUB = "https://github.com/databobek/AllBambaGuns"
BOR_SITE = "https://databobek.github.io/RimworldBeyondOurReach/"
SITE_URL = "https://databobek.github.io/AllBambaGuns/"

# Which folders each mode loads, from LoadFolders.xml (MuzzleFlash only adds visuals).
MODES = {"vanilla": ["", "Launchers"], "ce": ["", "CE"]}

# Weapon families, in page order. Defs are grouped by the file they come from;
# the CE copy of a file has the same name (launchers: CE_ prefix).
FAMILIES = [
    ("IndustrialCharged", "industrial"),
    ("SpacerBallistic", "spacer"),
    ("UltraCharged", "ultra"),
    ("UltraBallistic", "ultra"),
    ("SolarCharged", "solar"),
    ("QuasarCharged", "kwazaari"),
]
LAUNCHERS = "DisposableLaunchers"

# Order of the seven roles inside a family, by defName suffix.
ROLES = ["pistol", "bullpup", "battle rifle", "dmr", "shotgun", "lmg", "sniper"]
LAUNCHER_TIERS = ["industrial", "spacer", "ultra", "solar", "quasar"]
LAUNCHER_KINDS = ["(he)", "(heat)", "(aphe)", "(emp)", "(toxic)"]

PACKAGES = {
    "Ludeon.RimWorld.Biotech": "Biotech",
    "bambaryla.bor": "Beyond Our Reach",
    "bambaryla.bor.redux": "Beyond Our Reach",
}

# Research the mod builds on but doesn't define.
EXTERNAL_RESEARCH = {
    "MicroelectronicsBasics": "microelectronics",
    "PrecisionRifling": "precision rifling",
    "GasOperation": "gas operation",
    "ChargedShot": "charged shot",
    "MultiAnalyzer": "multi-analyzer",
    "Fabrication": "fabrication",
    "AdvancedFabrication": "advanced fabrication",
    "CE_Launchers": "simple launchers (CE)",
    "BOR_Research_Weapons_I": "solar weaponry (BOR)",
    "BOR_Research_Weapons_II": "kwazaari weaponry (BOR)",
}

DAMAGE_NAMES = {"Bomb": "explosive", "EMP": "EMP", "ToxGas": "tox gas", "Bullet": "", "ABG_ChargeBullet": "",
                "Bomb_Secondary": "explosive", "EMP_Secondary": "EMP", "Flame": "fire", "Flame_Secondary": "fire",
                "Electrical": "EMP", "Thermal": "heat", "Stun": "stun"}


# ---------------------------------------------------------------- loading

def load_defs(mode: str) -> dict[str, ET.Element]:
    """defName -> def element, inheritance resolved, for one mode."""
    named: dict[str, ET.Element] = {}  # Name="..." parents
    defs: dict[str, ET.Element] = {}
    for folder in MODES[mode]:
        base = ROOT / folder
        files = sorted((base / "Defs").rglob("*.xml"))
        for f in files:
            for d in ET.parse(f).getroot():
                d.set("_file", f.stem.removeprefix("CE_"))
                if d.get("Name"):
                    named[d.get("Name")] = d
                dn = d.findtext("defName")
                if dn and d.get("Abstract", "").lower() != "true":
                    defs[dn] = d
        # Patches that add whole defs (the BOR research projects)
        for f in sorted((base / "Patches").rglob("*.xml")):
            for d in ET.parse(f).getroot().iter("ResearchProjectDef"):
                d.set("_file", f.stem)
                defs[d.findtext("defName")] = d
    return {k: resolve(v, named) for k, v in defs.items()}


def resolve(d: ET.Element, named: dict[str, ET.Element]) -> ET.Element:
    parent = named.get(d.get("ParentName", ""))
    if parent is None:
        return d
    merged = copy.deepcopy(resolve(parent, named))
    merge(merged, d)
    merged.attrib = dict(d.attrib)
    return merged


def merge(into: ET.Element, child: ET.Element) -> None:
    """RimWorld-style inheritance: lists append, other nodes replace recursively."""
    for c in child:
        if c.tag == "li":
            into.append(copy.deepcopy(c))
            continue
        old = into.find(c.tag)
        if old is None or c.get("Inherit", "").lower() == "false" or len(c) == 0:
            if old is not None:
                into.remove(old)
            into.append(copy.deepcopy(c))
        else:
            merge(old, c)
    if child.text and child.text.strip():
        into.text = child.text


# ---------------------------------------------------------------- extraction

def num(el: ET.Element | None, path: str) -> float | None:
    if el is None:
        return None
    t = el.findtext(path)
    try:
        return float(t) if t is not None else None
    except ValueError:
        return None


def requires(d: ET.Element) -> list[str]:
    ids = (d.get("MayRequire", "") + "," + d.get("MayRequireAnyOf", "")).split(",")
    return sorted({PACKAGES.get(i.strip(), i.strip()) for i in ids if i.strip()})


def is_weapon(d: ET.Element) -> bool:
    return d.tag == "ThingDef" and d.find("verbs/li/defaultProjectile") is not None


def texture(d: ET.Element, used: set[str]) -> str | None:
    tp = d.findtext("graphicData/texPath")
    if not tp:
        return None
    src = ROOT / "Textures" / (tp + ".png")
    if not src.exists():
        return None
    used.add(tp)
    return "assets/tex/" + tp + ".png"


def order_key(label: str) -> tuple:
    """Rows go pistol to sniper; launchers industrial to quasar, then HE, HEAT, APHE, EMP, toxic."""
    def index(words):
        return next((i for i, w in enumerate(words) if w in label), 99)
    if " launcher" in label:
        return index(LAUNCHER_TIERS), index(LAUNCHER_KINDS), label
    return index(ROLES), 0, label


def making(d: ET.Element, recipes: dict[str, ET.Element]) -> ET.Element | None:
    """The recipeMaker on the def, or the RecipeDef that makes it (CE launchers)."""
    return d.find("recipeMaker") if d.find("recipeMaker") is not None else recipes.get(d.findtext("defName"))


def research_keys(rm: ET.Element | None) -> list[str]:
    if rm is None:
        return []
    keys = [rm.findtext("researchPrerequisite")] + [li.text for li in rm.findall("researchPrerequisites/li")]
    return [k for k in keys if k]


def research_label(key: str, defs: dict[str, ET.Element]) -> str:
    r = defs.get(key)
    if r is not None and r.findtext("label"):
        return r.findtext("label").lower()
    return EXTERNAL_RESEARCH.get(key, key)


def damage_name(key: str | None) -> str:
    return DAMAGE_NAMES.get(key or "", (key or "").lower())


def vanilla_stats(d: ET.Element, defs: dict[str, ET.Element]) -> dict:
    verb = d.find("verbs/li")
    proj = defs.get(verb.findtext("defaultProjectile"))
    p = proj.find("projectile") if proj is not None else None
    dmg = num(p, "damageAmountBase")
    ddef = p.findtext("damageDef") if p is not None else None
    ap = num(p, "armorPenetrationBase")
    if ap is None and dmg is not None and ddef in (None, "Bullet"):
        ap = dmg * 0.015  # the game's default for bullets
    sb = d.find("statBases")
    return {
        "damage": dmg,
        "damage_type": damage_name(ddef),
        "ap": ap,
        "explosion": num(p, "explosionRadius"),
        "stopping": num(p, "stoppingPower"),
        "burst": int(num(verb, "burstShotCount") or 1),
        "range": num(verb, "range"),
        "warmup": num(verb, "warmupTime"),
        "cooldown": num(sb, "RangedWeapon_Cooldown"),
        "accuracy": [num(sb, k) for k in ("AccuracyTouch", "AccuracyShort", "AccuracyMedium", "AccuracyLong")],
        "mass": num(sb, "Mass"),
    }


def ce_round(proj: ET.Element | None) -> dict:
    if proj is None:
        return {}
    p = proj.find("projectile")
    secondary = []
    for li in p.findall("secondaryDamage/li") if p is not None else []:
        secondary.append(f"+{fmt(num(li, 'amount'))} {damage_name(li.findtext('def'))}".strip())
    blast = None
    for comp in proj.findall("comps/li"):
        if comp.find("explosiveRadius") is not None:
            blast = {"damage": num(comp, "damageAmountBase"), "radius": num(comp, "explosiveRadius"),
                     "type": damage_name(comp.findtext("explosiveDamageType"))}
    label = proj.findtext("label") or ""
    m = re.search(r"\(([^)]*)\)\s*$", label)
    return {
        "name": m.group(1) if m else "standard",
        "damage": num(p, "damageAmountBase"),
        "damage_type": damage_name(p.findtext("damageDef") if p is not None else None),
        "pellets": num(p, "pelletCount"),
        "ap_sharp": num(p, "armorPenetrationSharp"),
        "ap_blunt": num(p, "armorPenetrationBlunt"),
        "secondary": secondary,
        "explosion": num(p, "explosionRadius"),
        "blast": blast,
    }


def ce_stats(d: ET.Element, defs: dict[str, ET.Element]) -> dict:
    verb = d.find("verbs/li")
    sb = d.find("statBases")
    ammo_comp = next((c for c in d.findall("comps/li") if c.find("ammoSet") is not None), None)
    modes = next((c for c in d.findall("comps/li") if c.find("aiAimMode") is not None), None)
    ammo, caliber = [], None
    if ammo_comp is not None:
        aset = defs.get(ammo_comp.findtext("ammoSet"))
        if aset is not None:
            caliber = aset.findtext("label")
            ammo = [ce_round(defs.get(t.text)) for t in aset.find("ammoTypes")]
    default = ce_round(defs.get(verb.findtext("defaultProjectile")))
    burst = int(num(verb, "burstShotCount") or 1)
    aimed = num(modes, "aimedBurstShotCount")
    return {
        "caliber": caliber,
        "default": default,
        "ammo": [a for a in ammo if a],
        "magazine": int(num(ammo_comp, "magazineSize")) if ammo_comp is not None else None,
        "reload": num(ammo_comp, "reloadTime"),
        "burst": burst,
        "aimed_burst": int(aimed) if aimed and aimed != burst else None,
        "range": num(verb, "range"),
        "warmup": num(verb, "warmupTime"),
        "cooldown": num(sb, "RangedWeapon_Cooldown"),
        "sights": num(sb, "SightsEfficiency"),
        "spread": num(sb, "ShotSpread"),
        "sway": num(sb, "SwayFactor"),
        "bulk": num(sb, "Bulk"),
        "mass": num(sb, "Mass"),
    }


def weapon(d: ET.Element, defs: dict[str, ET.Element], recipes: dict[str, ET.Element], mode: str,
           used: set[str]) -> dict:
    rm = making(d, recipes)
    return {
        "def": d.findtext("defName"),
        "label": d.findtext("label"),
        "description": (d.findtext("description") or "").strip(),
        "tech": d.findtext("techLevel"),
        "icon": texture(d, used),
        "requires": requires(d),
        "research_keys": research_keys(rm),
        "research": [research_label(k, defs) for k in research_keys(rm)],
        "skill": num(rm, "skillRequirements/Crafting"),
        "stats": vanilla_stats(d, defs) if mode == "vanilla" else ce_stats(d, defs),
    }


def research(defs: dict[str, ET.Element], unlocks: set[str]) -> list[dict]:
    """The mod's research projects that lead to something in this mode."""
    needed, todo = set(), list(unlocks)
    while todo:
        k = todo.pop()
        if k in needed or k not in defs:
            continue
        needed.add(k)
        todo += [li.text for li in defs[k].findall("prerequisites/li")]
    rows = []
    for d in defs.values():
        if d.tag != "ResearchProjectDef" or d.findtext("defName") not in needed:
            continue
        if not d.findtext("defName").startswith("ABG_"):
            continue
        rows.append({
            "def": d.findtext("defName"),
            "label": d.findtext("label"),
            "description": (d.findtext("description") or "").strip(),
            "cost": num(d, "baseCost"),
            "tech": d.findtext("techLevel"),
            "requires": requires(d),
            "prereqs": [research_label(li.text, defs) for li in d.findall("prerequisites/li")],
            "x": num(d, "researchViewX") or 0,
            "y": num(d, "researchViewY") or 0,
        })
    rows.sort(key=lambda r: (bool(r["requires"]), r["x"], r["y"]))
    return rows


def collect() -> dict:
    """Everything the page shows: families with vanilla and CE stats side by side."""
    used: set[str] = set()
    families = {key: {} for key, _ in FAMILIES + [(LAUNCHERS, "")]}
    research_by_mode = {}
    for mode in MODES:
        defs = load_defs(mode)
        recipes = {li.tag: r for r in defs.values() if r.tag == "RecipeDef" for li in r.findall("products/*")}
        unlocks = set()
        for d in defs.values():
            fam = d.get("_file")
            if not is_weapon(d) or fam not in families:
                continue
            w = weapon(d, defs, recipes, mode, used)
            unlocks.update(w["research_keys"])
            # Guns pair up by defName; launchers by label, since the CE ones are separate defs.
            key = w["label"].lower() if fam == LAUNCHERS else w["def"]
            families[fam].setdefault(key, {"order": order_key(w["label"].lower())})[mode] = w
        research_by_mode[mode] = research(defs, unlocks)
    out = []
    for key, tier in FAMILIES + [(LAUNCHERS, "")]:
        items = sorted(families[key].values(), key=lambda r: r["order"])
        out.append({"key": key, "tier": tier, "items": items})
    return {"families": out, "research": research_by_mode, "textures": used}


# ---------------------------------------------------------------- rendering

def fmt(v, digits: int = 2) -> str:
    """1234.50 -> "1,234.5"; None -> "—"."""
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s.replace("-", "−")


def pct(v) -> str:
    return "—" if v is None else f"{round(v * 100)}%"


def build(out: Path) -> None:
    data = collect()
    if out.exists():
        if not (out / BUILD_MARKER).exists():
            sys.exit(f"{out} exists and wasn't made by this script; refusing to delete it.")
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / BUILD_MARKER).write_text("")
    shutil.copytree(SITE / "static", out / "assets", dirs_exist_ok=True)
    shutil.copy(ROOT / "About/Preview.png", out / "assets/preview.png")
    for tp in sorted(data["textures"]):
        dst = out / "assets/tex" / (tp + ".png")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "Textures" / (tp + ".png"), dst)

    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=select_autoescape(),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["num"] = fmt
    env.filters["pct"] = pct
    env.filters["slug"] = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    fam = {f["key"]: f for f in data["families"]}
    about = ET.parse(ROOT / "About/About.xml").getroot()
    html = env.get_template("index.html").render(
        fam=fam, families=data["families"], research=data["research"],
        about={"versions": [li.text for li in about.findall("supportedVersions/li")],
               "license": (ROOT / "LICENSE").read_text(encoding="utf-8").strip()},
        favicon=fam["UltraCharged"]["items"][2]["vanilla"]["icon"],
        workshop=WORKSHOP, github=GITHUB, bor_site=BOR_SITE, site_url=SITE_URL,
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    n = sum(len(f["items"]) for f in data["families"])
    print(f"Built {out} ({n} weapons, {len(data['textures'])} textures)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=SITE / "_build")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--dump", action="store_true", help="print the extracted data and stop")
    args = ap.parse_args()
    if args.dump:
        import pprint
        d = collect()
        d.pop("textures")
        pprint.pprint(d, width=160, sort_dicts=False)
        return
    build(args.out)
    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.out))
        print("Serving on http://localhost:8000")
        http.server.ThreadingHTTPServer(("localhost", 8000), handler).serve_forever()


if __name__ == "__main__":
    main()
