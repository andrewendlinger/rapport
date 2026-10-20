import pytest

from conftest import write
from rapport_core import config as config_mod
from rapport_core.util import RapportError


def load(d, sets=None):
    cfg, _ = config_mod.load_for_report(d, sets)
    return cfg


def test_defaults(report):
    cfg = load(report("hello\n"))
    assert cfg.get("theme.name") == "default"
    assert cfg.get("layout.side") == ["toc"]
    assert cfg.module("code")["light_style"] == "friendly"      # module manifest default
    assert cfg.enabled("feedback") and cfg.enabled("runtime")
    assert not cfg.warnings


def test_layer_order_and_origins(report, isolated_home):
    d = report("+++\ntitle = \"T\"\n[theme]\nmode = \"dark\"\n+++\nbody\n")
    write(isolated_home / ".config/rapport/config.toml", '[theme]\nname = "paper"\nmode = "light"\n[serve]\nport = 5000\n')
    write(isolated_home / "proj/rapport.toml", '[serve]\nport = 6000\n')
    cfg = load(d, ["serve.open_browser=false"])
    assert cfg.get("theme.name") == "paper"          # user
    assert cfg.get("theme.mode") == "dark"           # front matter beats user
    assert cfg.get("serve.port") == 6000             # project beats user
    assert cfg.get("serve.open_browser") is False    # --set, parsed as TOML
    assert cfg.get("meta.title") == "T"              # non-section front matter → meta
    assert cfg.origins["theme.mode"] == "front matter"
    assert "proj/rapport.toml" in cfg.origins["serve.port"]
    assert cfg.origins["serve.open_browser"] == "--set"


def test_writing_rules_append_and_learned(report, isolated_home):
    d = report("x\n")
    write(isolated_home / ".config/rapport/config.toml", '[writing]\nrules = ["user rule"]\n')
    write(isolated_home / ".config/rapport/learned.toml", '[[learned]]\nrule = "learned rule"\n'
                                                          '[[learned]]\nrule = "synergy"\nkind = "avoid"\n')
    write(isolated_home / "proj/rapport.toml", '[writing]\nrules = ["project rule"]\n')
    cfg = load(d)
    assert cfg.get("writing.rules") == ["user rule", "learned rule", "project rule"]
    assert "synergy" in cfg.get("writing.avoid") and "delve" in cfg.get("writing.avoid")


def test_feedback_actions_merge_by_id(report, isolated_home):
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml", """
[[modules.feedback.actions]]
id = "verbose"
label = "Shorter please"
[[modules.feedback.actions]]
id = "unclear"
enabled = false
[[modules.feedback.actions]]
id = "cite"
label = "Needs source"
instruction_file = "prompts/cite.md"
""")
    cfg = load(d)
    acts = {a["id"]: a for a in cfg.module("feedback")["actions"]}
    assert acts["verbose"]["label"] == "Shorter please"
    assert acts["verbose"]["key"] == "v"                       # untouched fields survive
    assert "unclear" not in acts
    assert acts["cite"]["instruction_file"] == str((isolated_home / "proj/prompts/cite.md").resolve())
    assert list(acts) == ["delete", "slop", "verbose", "cite"]


def test_preset_resolved_first_and_slotted_low(report, isolated_home):
    d = report("+++\npreset = \"dense\"\n+++\nx\n")
    write(isolated_home / "proj/rapport-presets/dense.toml", '[layout]\nwidth = "60ch"\nside = []\n')
    write(isolated_home / "proj/rapport.toml", '[layout]\nside = ["toc"]\n')
    cfg = load(d)
    assert cfg.preset == "dense"
    assert cfg.get("layout.width") == "60ch"     # from preset
    assert cfg.get("layout.side") == ["toc"]     # project beats preset


def test_missing_preset_is_an_error(report):
    with pytest.raises(RapportError, match="preset 'nope' not found"):
        load(report("+++\npreset = \"nope\"\n+++\nx\n"))


def test_unknown_keys_and_types_warn(report, isolated_home):
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml",
          '[theme]\nnmae = "x"\n[serve]\nport = "high"\n[modules.calout]\nenabled = false\n'
          '[modules.code]\nline_numbrs = true\n[theme.tokens]\naccnt = "#000"\n')
    w = "\n".join(load(d).warnings)
    assert "unknown config key 'theme.nmae' (did you mean 'name'?)" in w
    assert "serve.port: expected int" in w
    assert "no module named 'calout' (did you mean 'callout'?)" in w
    assert "modules.code.line_numbrs" in w
    assert "no token 'accnt' (did you mean 'accent'?)" in w


def test_front_matter_lines_are_blanked_not_removed():
    fm, body, lines = config_mod.split_front_matter('+++\ntitle = "x"\n+++\n\nfirst\n')
    assert fm == {"title": "x"} and lines == (1, 3)
    assert body.split("\n")[4] == "first"        # line 5 is still line 5


def test_bad_front_matter(report):
    with pytest.raises(RapportError, match="front matter"):
        load(report("+++\ntitle = \n+++\n"))


def test_feedback_action_keys_are_checked(report, isolated_home):
    from conftest import write
    from rapport_core.config import load_for_report
    write(isolated_home / "proj/rapport.toml",
          '[[modules.feedback.actions]]\nid = "cite"\nlabel = "Cite"\nkey = "v"\n'
          '[[modules.feedback.actions]]\nid = "chat"\nlabel = "Chat"\nkey = "c"\n')
    cfg, _ = load_for_report(report("x\n"))
    joined = "\n".join(cfg.warnings)
    assert "actions 'verbose' and 'cite' share the key 'v'" in joined
    assert "key 'c' is taken by Comment" in joined
