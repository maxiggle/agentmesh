import pytest

from ehor.service.plugins import PluginError, check_method_name


@pytest.mark.parametrize("name", ["near.bounty_post", "a.b", "pkg2.list_all"])
def test_namespaced_names_are_accepted(name: str) -> None:
    check_method_name(name)


@pytest.mark.parametrize(
    "name",
    ["post", "inbox", "roster.add", "Near.post", ".post", "near.", "near.bounty.post", "near-x.y"],
)
def test_names_that_could_shadow_core_methods_are_refused(name: str) -> None:
    with pytest.raises(PluginError, match=r"<plugin>\.<name>"):
        check_method_name(name)
