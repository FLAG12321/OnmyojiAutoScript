# This Python file uses the following encoding: utf-8
# 测试 config_validation：严格持久化校验、legacy alias 迁移顺序与可注入 profile
import copy
from pathlib import Path
from typing import Annotated, Optional, Union

import pytest
from pydantic import BaseModel, Field, model_validator

from module.config.config_model import ConfigModel
from module.config.config_validation import (
    ConfigValidationError,
    DynamicFieldSet,
    DynamicPathSet,
    ValidationProfile,
    validate_persisted_config,
)
from module.config.utils import read_file
from tasks.FindJade.config import FindJadeConfig, InviteInfo


def canonical_template() -> dict:
    raw = read_file(Path.cwd() / "config" / "template.json")
    # template 仍含 MetaDemon count=0 的历史 _1 占位；Task 2 首次 migration 会删除它
    raw["meta_demon"].pop("md_strategies_1", None)
    return raw


class SyntheticTaskConfig(BaseModel, extra="allow"):
    limit_count: int = 1


class SyntheticTaskGroup(BaseModel):
    task: SyntheticTaskConfig = Field(default_factory=SyntheticTaskConfig)


class SyntheticDevice(BaseModel):
    serial: str = "old"


class SyntheticScript(BaseModel):
    device: SyntheticDevice = Field(default_factory=SyntheticDevice)


class SyntheticConfigModel(BaseModel):
    config_name: str = "synthetic"
    running_task: str = ""
    script: SyntheticScript = Field(default_factory=SyntheticScript)
    synthetic: SyntheticTaskGroup = Field(default_factory=SyntheticTaskGroup)


class OptionalChild(BaseModel):
    known: int = 1


class OptionalConfig(BaseModel):
    config_name: str = "optional"
    child: Optional[OptionalChild] = None
    children: list[Optional[OptionalChild]] = Field(default_factory=list)


class TypeErrorConfig(BaseModel):
    config_name: str = "type-error"

    @model_validator(mode="before")
    @classmethod
    def fail_with_type_error(cls, value):
        raise TypeError("validator type failure")


class KeyErrorConfig(BaseModel):
    config_name: str = "key-error"

    @model_validator(mode="before")
    @classmethod
    def fail_with_key_error(cls, value):
        # 直索引 before-validator：严格校验边界必须把 KeyError 包装为 ConfigValidationError
        return value["missing"]

class IndexErrorConfig(BaseModel):
    config_name: str = "index-error"

    @model_validator(mode="before")
    @classmethod
    def fail_with_index_error(cls, value):
        return value["list"][0]


class DivergentScalarBranch(BaseModel):
    kind: str = "scalar"
    value: int


class DivergentModelBranch(BaseModel):
    kind: str = "model"
    nested: int


class DivergentNormalizingGroup(BaseModel):
    choice: Union[DivergentScalarBranch, DivergentModelBranch]

    @model_validator(mode="before")
    @classmethod
    def decide_branch(cls, data):
        # 父 before-validator 在 Union 分支判定前补 discriminator；分支由归一化后的值决定
        if isinstance(data, dict) and isinstance(data.get("choice"), dict):
            choice = data["choice"]
            choice.setdefault("kind", "scalar" if "value" in choice else "model")
        return data


class ScalarBranch(BaseModel):
    value: int


class ModelBranch(BaseModel):
    value: OptionalChild


class UnionConfig(BaseModel):
    config_name: str = "union"
    choice: Union[ScalarBranch, ModelBranch]


class LeftBranch(BaseModel):
    value: int


class RightBranch(BaseModel):
    value: int
    right_only: int


class LeftToRightListConfig(BaseModel):
    config_name: str = "left-to-right"
    # 保留 item 上的 union_mode，验证 unknown 检查与 Pydantic 实际选支一致。
    choices: list[Annotated[Union[LeftBranch, RightBranch], Field(union_mode="left_to_right")]]


UNION_VALIDATOR_CALLS: list[str] = []


class SideEffectLeft(BaseModel):
    value: int

    @model_validator(mode="before")
    @classmethod
    def record_left(cls, value):
        UNION_VALIDATOR_CALLS.append("left")
        return value


class SideEffectRight(BaseModel):
    nested: int

    @model_validator(mode="before")
    @classmethod
    def record_right(cls, value):
        UNION_VALIDATOR_CALLS.append("right")
        return value


class SideEffectUnionConfig(BaseModel):
    config_name: str = "side-effect-union"
    choice: Union[SideEffectLeft, SideEffectRight]


class GuardedItem(BaseModel):
    value: int = Field(ge=1)


PARENT_VALIDATOR_CALLS: list[str] = []


class GuardedParent(BaseModel):
    items: list[GuardedItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def record_parent_call(cls, value):
        PARENT_VALIDATOR_CALLS.append("parent")
        return value


class GuardedRoot(BaseModel):
    config_name: str = "guarded"
    parent: GuardedParent


def synthetic_config(limit_count: int = 1) -> dict:
    return {
        "config_name": "synthetic",
        "running_task": "",
        "script": {"device": {"serial": "old"}},
        "synthetic": {"task": {"limit_count": limit_count}},
    }


def test_legacy_alias_runs_before_unknown_rejection():
    raw = canonical_template()
    group = raw["master_disciple"]["master_disciple_config"]
    group.pop("master_coin_exit_after_prepare")
    group.pop("master_exp_exit_after_prepare")
    group["master_battle_mode"] = "normal_battle"
    model, canonical = validate_persisted_config(raw, "oas-test")
    migrated = canonical["master_disciple"]["master_disciple_config"]
    assert "master_battle_mode" not in migrated
    assert migrated["master_coin_exit_after_prepare"] is False
    assert migrated["master_exp_exit_after_prepare"] is False


def test_legacy_alias_preserves_existing_new_fields():
    raw = canonical_template()
    group = raw["master_disciple"]["master_disciple_config"]
    group["master_battle_mode"] = "normal_battle"
    group["master_coin_exit_after_prepare"] = True
    group["master_exp_exit_after_prepare"] = True
    _, canonical = validate_persisted_config(raw, "oas-test")
    migrated = canonical["master_disciple"]["master_disciple_config"]
    assert "master_battle_mode" not in migrated
    assert migrated["master_coin_exit_after_prepare"] is True
    assert migrated["master_exp_exit_after_prepare"] is True


def test_legacy_orochi_team_fields_run_before_unknown_rejection():
    raw = canonical_template()
    orochi = raw["orochi"]
    old_team = orochi.pop("team_config")
    section = orochi["orochi_config"]
    section["leader_instance"] = old_team["leader_instance"]
    section["epoch"] = old_team["epoch"]
    section["total_limit_time"] = old_team["total_limit_time"]
    section["total_limit_count"] = old_team["total_limit_count"]
    section["user_status"] = "member"

    _, canonical = validate_persisted_config(raw, "oas-test")

    migrated = canonical["orochi"]["team_config"]
    assert migrated["team_mode"] == "team"
    assert migrated["leader_instance"] == old_team["leader_instance"]
    assert migrated["epoch"] == old_team["epoch"]
    assert migrated["total_limit_time"] == old_team["total_limit_time"]
    assert migrated["total_limit_count"] == old_team["total_limit_count"]
    assert "leader_instance" not in canonical["orochi"]["orochi_config"]
    assert "epoch" not in canonical["orochi"]["orochi_config"]
    assert "total_limit_time" not in canonical["orochi"]["orochi_config"]
    assert "total_limit_count" not in canonical["orochi"]["orochi_config"]


def test_legacy_orochi_team_fields_preserve_existing_team_config():
    raw = canonical_template()
    orochi = raw["orochi"]
    orochi["team_config"]["team_mode"] = "alone"
    orochi["team_config"]["leader_instance"] = "KEEP"
    orochi["team_config"]["epoch"] = "keep"
    orochi["team_config"]["total_limit_time"] = "01:00:00"
    orochi["team_config"]["total_limit_count"] = 1
    section = orochi["orochi_config"]
    section["leader_instance"] = "OAS2"
    section["epoch"] = "abc"
    section["total_limit_time"] = "02:00:00"
    section["total_limit_count"] = 100
    section["user_status"] = "member"

    _, canonical = validate_persisted_config(raw, "oas-test")

    migrated = canonical["orochi"]["team_config"]
    assert migrated["team_mode"] == "alone"
    assert migrated["leader_instance"] == "KEEP"
    assert migrated["epoch"] == "keep"
    assert migrated["total_limit_time"] == "01:00:00"
    assert migrated["total_limit_count"] == 1
    assert "leader_instance" not in canonical["orochi"]["orochi_config"]


def test_legacy_orochi_enable_team_switch_is_converted():
    raw = canonical_template()
    team = raw["orochi"]["team_config"]
    team.pop("team_mode")
    team["enable_team"] = True
    team["leader_instance"] = "OAS2"

    _, canonical = validate_persisted_config(raw, "oas-test")

    migrated = canonical["orochi"]["team_config"]
    assert migrated["team_mode"] == "team"
    assert migrated["leader_instance"] == "OAS2"
    assert "enable_team" not in migrated


def test_strict_validation_rejects_range_while_runtime_constructor_keeps_fallback():
    raw = canonical_template()
    raw["find_jade"]["find_jade_config"]["invite_info_count"] = 0
    raw["find_jade"].pop("invite_info_list_1", None)
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")
    assert FindJadeConfig(invite_info_count=0).invite_info_count == 1


def test_strict_validation_rejects_incomplete_dynamic_list():
    # 声明 3 个成员但只给 1 个：若放行会被 before-validator 静默补齐，造成 canonical 漂移
    raw = canonical_template()
    raw["find_jade"]["find_jade_config"]["invite_info_count"] = 3
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_extra_or_gapped_dynamic_members():
    raw = canonical_template()
    raw["find_jade"]["find_jade_config"]["sup_account_count"] = 1
    raw["find_jade"]["sup_account_list_3"] = raw["find_jade"]["sup_account_list_1"].copy()
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_non_canonical_member_index():
    raw = canonical_template()
    raw["find_jade"]["sup_account_list_01"] = raw["find_jade"].pop("sup_account_list_1")
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_bool_or_string_count():
    for value in (True, "1"):
        raw = canonical_template()
        raw["find_jade"]["find_jade_config"]["invite_info_count"] = value
        with pytest.raises(ConfigValidationError):
            validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_huge_count_without_materializing_range():
    raw = canonical_template()
    raw["find_jade"]["find_jade_config"]["invite_info_count"] = 1_000_000_000

    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_unknown_under_extra_allow_model():
    raw = canonical_template()
    raw["find_jade"]["find_jade_config"]["typo_field"] = 1
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_invalid_dynamic_member_payload():
    for payload in (
        1,
        {"name": 123, "default_invite_type": "INVALID"},
    ):
        raw = canonical_template()
        raw["find_jade"]["invite_info_list_1"] = payload
        with pytest.raises(ConfigValidationError):
            validate_persisted_config(raw, "oas-test")


def test_strict_validation_rejects_unknown_in_optional_nested_models():
    profile = ValidationProfile(OptionalConfig, (), ())
    for raw in (
        {"child": {"known": 1, "typo": 2}},
        {"children": [{"known": 1, "typo": 2}]},
    ):
        with pytest.raises(ConfigValidationError):
            validate_persisted_config(raw, "optional", profile)


def test_union_candidate_must_accept_value_before_unknown_check():
    profile = ValidationProfile(UnionConfig, (), ())
    raw = {"choice": {"value": {"known": 1, "typo": 2}}}
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "union", profile)


def test_dynamic_payload_is_rejected_before_parent_validator_runs():
    PARENT_VALIDATOR_CALLS.clear()
    profile = ValidationProfile(
        GuardedRoot,
        (),
        (DynamicPathSet("guarded.items", ("parent", "items"), mode="contiguous"),),
    )
    raw = {"parent": {"items_1": {"value": 1, "typo": 2}}}
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "guarded", profile)
    assert PARENT_VALIDATOR_CALLS == []


def test_dynamic_semantic_validation_rejects_non_default_invalid_payloads():
    raw = canonical_template()
    raw["find_jade"]["invite_info_list_1"]["invite_history_1"] = "2024-01-01 00:00:00"
    with pytest.raises(ConfigValidationError, match="semantic validation failed"):
        validate_persisted_config(raw, "oas-test")

    raw = canonical_template()
    raw["meta_demon"]["meta_demon_config"]["md_strategy_count"] = 1
    raw["meta_demon"]["md_strategies_1"] = {
        "md_match_names": "",
        "md_preset_group_team_1": "2,2",
        "md_preset_group_team_2": "1,1",
    }
    with pytest.raises(ConfigValidationError, match="semantic validation failed"):
        validate_persisted_config(raw, "oas-test")


def test_exact_default_dynamic_placeholder_is_allowed():
    raw = canonical_template()
    _, canonical = validate_persisted_config(raw, "oas-test")
    assert canonical["find_jade"]["invite_info_list_1"] == InviteInfo().model_dump(mode="json")


def test_dynamic_members_are_canonicalized_in_numeric_index_order():
    raw = canonical_template()
    node = raw["find_jade"]
    first = node.pop("invite_info_list_1")
    first["name"] = "ONE"
    second = first.copy()
    second["name"] = "TWO"
    node["find_jade_config"]["invite_info_count"] = 2
    # 故意按 _2、_1 插入，strict canonical 仍必须保持索引到 payload 的原映射。
    node["invite_info_list_2"] = second
    node["invite_info_list_1"] = first

    _, canonical = validate_persisted_config(raw, "oas-test")

    assert canonical["find_jade"]["invite_info_list_1"]["name"] == "ONE"
    assert canonical["find_jade"]["invite_info_list_2"]["name"] == "TWO"


def test_multiple_default_placeholders_cannot_be_silently_dropped():
    raw = canonical_template()
    node = raw["master_disciple"]
    node["disciple_account_list_2"] = node["disciple_account_list_1"].copy()

    with pytest.raises(ConfigValidationError, match="changed members during canonicalization"):
        validate_persisted_config(raw, "oas-test")


def test_single_default_contiguous_placeholder_is_allowed():
    raw = canonical_template()
    _, canonical = validate_persisted_config(raw, "oas-test")

    assert "disciple_account_list_1" in canonical["master_disciple"]
    assert "disciple_account_list_2" not in canonical["master_disciple"]


def test_left_to_right_list_union_rejects_unknown_on_selected_branch():
    profile = ValidationProfile(LeftToRightListConfig, (), ())
    raw = {"choices": [{"value": 1, "right_only": 2}]}
    with pytest.raises(ConfigValidationError, match="right_only"):
        validate_persisted_config(raw, "left-to-right", profile)


def test_invalid_parent_shape_is_wrapped():
    raw = canonical_template()
    raw["find_jade"] = 1
    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "oas-test")


def test_dynamic_logical_and_flattened_forms_are_rejected_together():
    raw = canonical_template()
    raw["find_jade"]["invite_info_list"] = [raw["find_jade"]["invite_info_list_1"].copy()]
    with pytest.raises(ConfigValidationError, match="must use flattened members"):
        validate_persisted_config(raw, "oas-test")


def test_validator_type_error_is_wrapped():
    profile = ValidationProfile(TypeErrorConfig, (), ())
    with pytest.raises(ConfigValidationError, match="validator type failure"):
        validate_persisted_config({}, "type-error", profile)


def test_validator_key_error_is_wrapped():
    # 记忆覆盖项：新增直索引 before-validator 会泄漏 KeyError，必须被包装为 ConfigValidationError
    profile = ValidationProfile(KeyErrorConfig, (), ())
    with pytest.raises(ConfigValidationError):
        validate_persisted_config({}, "key-error", profile)


def test_validator_index_error_is_wrapped():
    profile = ValidationProfile(IndexErrorConfig, (), ())
    with pytest.raises(ConfigValidationError):
        validate_persisted_config({"list": []}, "index-error", profile)


def test_selected_branch_matches_full_model_validate_after_parent_normalization():
    """记忆覆盖项：父 before-validator 先归一化才决定 Union 分支时，strict 的选支必须与
    完整 model_validate 一致，防止未知字段检查打到错误分支。"""
    from module.config.config_validation import _selected_model_type

    profile = ValidationProfile(DivergentNormalizingGroup, (), ())
    raw = {"choice": {"value": 3, "extra_field": 1}}
    selected = _selected_model_type(
        DivergentNormalizingGroup.model_fields["choice"], raw["choice"], ("choice",))
    full = DivergentNormalizingGroup.model_validate(copy.deepcopy(raw))
    # 分支选择一致：父 validator 归一化后 Pydantic 实际选择的类型与预选类型相同
    assert selected is type(full.choice)
    assert selected is DivergentScalarBranch
    # 未知字段按所选分支拒绝（strict 比 runtime 更保守，fail closed）
    with pytest.raises(ConfigValidationError, match="extra_field"):
        validate_persisted_config(raw, "divergent", profile)


def test_default_model_dump_is_accepted_by_strict_validation():
    raw = ConfigModel().model_dump(mode="json")

    _, canonical = validate_persisted_config(raw, "default")

    assert canonical["meta_demon"]["meta_demon_config"]["md_strategy_count"] == 0
    assert "md_strategies_1" not in canonical["meta_demon"]


def test_union_validators_run_only_once_during_strict_validation():
    UNION_VALIDATOR_CALLS.clear()
    profile = ValidationProfile(SideEffectUnionConfig)

    validate_persisted_config({"choice": {"value": 1}}, "side-effect-union", profile)

    assert UNION_VALIDATOR_CALLS == ["left", "right"]


def test_default_dynamic_field_set_rejects_non_bool_for_discovered_field():
    raw = canonical_template()
    key = next(iter(raw["multi_account_sign_in"]["account_config_selection"]))
    raw["multi_account_sign_in"]["account_config_selection"][key] = 1

    with pytest.raises(ConfigValidationError, match="invalid dynamic field"):
        validate_persisted_config(raw, "oas-test")


def test_dynamic_field_set_preserves_declared_shape_without_runtime_discovery():
    profile = ValidationProfile(
        model_type=SyntheticConfigModel,
        dynamic_field_sets=(
            DynamicFieldSet(
                path=("synthetic", "task"),
                key_pattern=r"config_[0-9a-f]{16}",
                value_type=bool,
            ),
        ),
    )
    raw = synthetic_config()
    raw["synthetic"]["task"]["config_0123456789abcdef"] = True

    model, canonical = validate_persisted_config(raw, "synthetic", profile)

    assert model.synthetic.task.model_extra == {"config_0123456789abcdef": True}
    assert canonical["synthetic"]["task"]["config_0123456789abcdef"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    (("config_not_hex", True), ("config_0123456789abcdef", 1)),
)
def test_dynamic_field_set_rejects_invalid_key_or_value(key, value):
    profile = ValidationProfile(
        model_type=SyntheticConfigModel,
        dynamic_field_sets=(
            DynamicFieldSet(
                path=("synthetic", "task"),
                key_pattern=r"config_[0-9a-f]{16}",
                value_type=bool,
            ),
        ),
    )
    raw = synthetic_config()
    raw["synthetic"]["task"][key] = value

    with pytest.raises(ConfigValidationError):
        validate_persisted_config(raw, "synthetic", profile)


def test_synthetic_profile_is_injectable():
    profile = ValidationProfile(
        model_type=SyntheticConfigModel,
        legacy_migrations=(),
        dynamic_path_sets=(),
    )
    model, canonical = validate_persisted_config(
        synthetic_config(limit_count=2),
        "synthetic",
        profile=profile,
    )
    assert model.synthetic.task.limit_count == 2
    assert canonical["synthetic"]["task"]["limit_count"] == 2


def test_synthetic_hot_paths_classify_under_default_deny():
    from module.config.config_reload import (
        COLD,
        HOT,
        ReloadPolicy,
        WARM,
        default_reload_policy,
    )

    policy = ReloadPolicy(
        hot_paths=frozenset({("synthetic", "task", "limit_count")}),
        cold_prefixes=(("script", "device"),),
    )
    # HOT exact-path 命中；COLD prefix 优先；未声明路径默认 WARM
    assert policy.classify(("synthetic", "task", "limit_count")) == HOT
    assert policy.classify(("script", "device", "serial")) == COLD
    assert policy.classify(("synthetic", "task", "other_field")) == WARM
    # 生产默认策略的 hot 由 ConfigModel schema 派生，不含合成 Schema 的路径：
    # 合成路径在生产策略下仍按 WARM 处理，两套策略互不污染
    production = default_reload_policy()
    assert ("synthetic", "task", "limit_count") not in production.hot_paths
    assert production.classify(("synthetic", "task", "limit_count")) == WARM
    # 生产策略确实开放了真实任务字段（与首版空白名单的契约已不同）
    assert production.classify(("orochi", "orochi_config", "limit_count")) == HOT
