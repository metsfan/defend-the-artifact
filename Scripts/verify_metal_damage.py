import re
import unreal

# Does each derived damage type actually inherit BASE's metal behaviour?
# A property BASE sets, but that the derived type also sets locally, means the
# local value wins and the bite may not damage metal.
BASE = "/DefendTheArtifact/Dino/DmgType_Melee_DmgMetal_ArtifactDino"
FOLDER = "/DefendTheArtifact/Dino"

eal = unreal.EditorAssetLibrary
ADDR = re.compile(r"0x[0-9A-Fa-f]{6,}")
ARRAYISH = tuple(t for t in (getattr(unreal, n, None)
                             for n in ("Array", "Set", "FixedArray")) if isinstance(t, type)) \
           + (list, tuple, set)
MAPPISH = tuple(t for t in (getattr(unreal, "Map", None),) if isinstance(t, type)) + (dict,)


def canon(v, depth=0):
    if depth > 6:
        return "<deep>"
    try:
        if isinstance(v, (bool, int, str, type(None))):
            return v
        if isinstance(v, float):
            return round(v, 6)
        if isinstance(v, unreal.StructBase):
            return tuple(canon(x, depth + 1) for x in v.to_tuple())
        if isinstance(v, unreal.Object):
            return v.get_path_name()
        if isinstance(v, MAPPISH):
            return tuple(sorted((str(canon(k, depth + 1)), str(canon(x, depth + 1)))
                                for k, x in v.items()))
        if isinstance(v, ARRAYISH):
            return tuple(canon(x, depth + 1) for x in v)
    except Exception:
        pass
    try:
        return ADDR.sub("", repr(v))
    except Exception:
        return "<unprintable>"


def is_delegate(v):
    if "delegate" in type(v).__name__.lower():
        return True
    try:
        return repr(v).startswith(("<Multicast delegate", "<Delegate"))
    except Exception:
        return False


def cdo_of(path):
    a = eal.load_asset(path)
    if not isinstance(a, unreal.Blueprint) or a.generated_class() is None:
        return None
    return unreal.get_default_object(a.generated_class())


def parent_cdo_of(path):
    try:
        raw = eal.find_asset_data(path).get_tag_value("ParentClass")
        if not raw:
            return None
        cls = unreal.load_object(None, raw.split("'")[-2] if "'" in raw else raw)
        return unreal.get_default_object(cls) if cls else None
    except Exception:
        return None


def readable_props(obj):
    for n in dir(obj):
        if n.startswith("_") or "guid" in n.lower():
            continue
        if callable(getattr(type(obj), n, None)):
            continue
        yield n


base_cdo = cdo_of(BASE)
base_parent = parent_cdo_of(BASE)
if base_cdo is None or base_parent is None:
    raise Exception("could not resolve BASE or its parent")

# Whatever BASE overrides relative to its own parent IS its metal configuration.
key_props = []
for n in readable_props(base_cdo):
    try:
        a, b = base_cdo.get_editor_property(n), base_parent.get_editor_property(n)
    except Exception:
        continue
    if is_delegate(a):
        continue
    if canon(a) != canon(b):
        key_props.append(n)

unreal.log("=" * 76)
unreal.log("BASE %s" % BASE.rsplit("/", 1)[-1])
unreal.log("  overrides %d propert(ies) vs its parent -- these carry the metal config:"
           % len(key_props))
for n in key_props:
    unreal.log("     %-46s = %s" % (n, base_cdo.get_editor_property(n)))
unreal.log("=" * 76)

if not key_props:
    unreal.log_warning("BASE overrides NOTHING vs its parent. Either the metal behaviour")
    unreal.log_warning("comes from its parent chain (so any child inherits it fine), or")
    unreal.log_warning("BASE is not configured. Check BASE by hand before trusting this.")

clean, broken = [], []
for p in eal.list_assets(FOLDER, recursive=True, include_folder=False):
    obj = p.split(".")[0]
    if obj == BASE or "dmgtype" not in obj.rsplit("/", 1)[-1].lower():
        continue
    cdo = cdo_of(obj)
    if cdo is None or not isinstance(cdo, unreal.DamageType):
        continue

    conflicts = []
    for n in key_props:
        try:
            mine, want = cdo.get_editor_property(n), base_cdo.get_editor_property(n)
        except Exception:
            continue
        if canon(mine) != canon(want):
            conflicts.append((n, want, mine))

    if conflicts:
        broken.append((obj, conflicts))
    else:
        clean.append(obj)

unreal.log("")
unreal.log("INHERITS BASE's metal config correctly (%d):" % len(clean))
for o in clean:
    unreal.log("   %s" % o.rsplit("/", 1)[-1])

unreal.log("")
if broken:
    unreal.log_warning("LOCAL OVERRIDE DEFEATS BASE (%d) -- these will NOT behave like BASE:"
                       % len(broken))
    for o, conflicts in broken:
        unreal.log_warning("   %s" % o.rsplit("/", 1)[-1])
        for n, want, mine in conflicts:
            unreal.log_warning("        %-40s BASE=%s   this=%s" % (n, want, mine))
else:
    unreal.log("No conflicts -- every derived type inherits BASE's metal config.")
unreal.log("=" * 76)
