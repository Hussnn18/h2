"""The portal saves config files: they must stay valid and keep their comments."""

import shutil
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.config_store import _yaml, flow_list, load_raw, save_offers, save_rules, set_in_order
from engine.rules import CONFIG_DIR, load_offers, load_rules


@pytest.fixture
def config(tmp_path) -> Path:
    for name in ("rules.yaml", "offers.yaml"):
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    return tmp_path


@pytest.mark.parametrize("name", ["rules.yaml", "offers.yaml"])
def test_unchanged_files_round_trip_exactly(name):
    buf = StringIO()
    _yaml.dump(load_raw(name), buf)
    assert buf.getvalue() == (CONFIG_DIR / name).read_text(encoding="utf-8")


def test_switching_an_offer_off_keeps_the_file_tidy(config):
    raw = load_raw("offers.yaml", config)
    set_in_order(raw["offers"]["win_back"], "enabled", False)
    save_offers(raw, config)

    assert load_offers(config)["win_back"].enabled is False
    text = (config / "offers.yaml").read_text(encoding="utf-8")
    assert "#  OFFER CATALOGUE" in text and "eligible_when: all conditions must be true" in text
    assert "    name: Win-back discount\n    enabled: false\n" in text


def test_invalid_offer_is_rejected_and_file_untouched(config):
    before = (config / "offers.yaml").read_text(encoding="utf-8")
    raw = load_raw("offers.yaml", config)
    raw["offers"]["win_back"]["range"] = flow_list([20, 10])
    with pytest.raises(ValidationError):
        save_offers(raw, config)
    assert (config / "offers.yaml").read_text(encoding="utf-8") == before


def test_rule_change_is_saved_with_comments(config):
    raw = load_raw("rules.yaml", config)
    raw["limits"]["max_discount_percent"] = 15
    save_rules(raw, config)

    assert load_rules(config).limits.max_discount_percent == 15
    text = (config / "rules.yaml").read_text(encoding="utf-8")
    assert "max_discount_percent: 15        # highest % off any offer may give, ever" in text


def test_invalid_rule_is_rejected(config):
    raw = load_raw("rules.yaml", config)
    raw["limits"]["max_discount_percent"] = 95  # above the 90% hard ceiling in the schema
    with pytest.raises(ValidationError):
        save_rules(raw, config)
    assert load_rules(config).limits.max_discount_percent == 20
