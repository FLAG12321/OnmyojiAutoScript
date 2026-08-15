# This Python file uses the following encoding: utf-8
# 测试 config_operations：tuple path、MISSING、三方合并与操作归一化
import copy
from types import SimpleNamespace

import pytest

from module.config.config_operations import (
    MISSING,
    BlockedChange,
    SetPath,
    ReplaceSubtree,
    ReplacePathSet,
    diff_trees,
    get_path,
    merge_operations,
    normalize_operations,
    set_path,
)


def test_missing_and_none_are_distinct():
    tree = {"task": {"group": {"empty": None}}}
    assert get_path(tree, ("task", "group", "missing")) is MISSING
    assert get_path(tree, ("task", "group", "empty")) is None


def test_disjoint_stale_changes_merge():
    result = merge_operations(
        {"a": {"x": 1, "y": 1}},
        {"a": {"x": 2, "y": 1}},
        {"a": {"x": 1, "y": 3}},
    )
    assert result.value == {"a": {"x": 2, "y": 3}}
    assert result.applied_paths == [("a", "x")]


def test_concurrent_new_dict_leaves_merge_without_subtree_conflict():
    base = {}
    local = {"a": {"x": 1}}
    disk = {"a": {"y": 2}}

    operations = diff_trees(base, local)
    assert operations == [SetPath(("a", "x"), 1)]

    result = merge_operations(base, local, disk)
    assert result.value == {"a": {"x": 1, "y": 2}}
    assert result.conflicted_paths == []


def test_new_empty_dict_still_has_an_operation():
    operations = diff_trees({}, {"a": {}})
    assert operations == [SetPath(("a",), {})]


def test_same_leaf_conflict_keeps_disk_and_records_fingerprint():
    result = merge_operations(
        {"a": {"x": 1}},
        {"a": {"x": 2}},
        {"a": {"x": 3}},
    )
    assert result.value["a"]["x"] == 3
    assert result.blocked == [
        BlockedChange(("a", "x"), "SET", 2, 3)
    ]


def test_ancestor_subtree_replacement_absorbs_descendants():
    operations = [
        ReplaceSubtree(("task",), {"group": {"x": 1}}, {"group": {"x": 0}}),
        SetPath(("task", "group", "x"), 2),
        ReplacePathSet(
            "task.list",
            {("task", "list_1"): {"v": 2}},
            {("task", "list_1"): {"v": 1}},
        ),
    ]
    normalized = normalize_operations(operations)
    assert normalized == [operations[0]]


def test_dynamic_path_set_blocks_whole_list_on_any_member_conflict():
    registry = [
        SimpleNamespace(
            key="find_jade.invite_info_list",
            member_path=("find_jade", "invite_info_list"),
            count_path=("find_jade", "find_jade_config", "invite_info_count"),
        )
    ]
    base = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "b"},
        }
    }
    local = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "B"},
        }
    }
    disk = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "X"},
            "invite_info_list_2": {"name": "b"},
        }
    }
    result = merge_operations(base, local, disk, dynamic_path_sets=registry)
    # 任一成员冲突即整体不应用，避免撕裂写入；冲突指纹记录真实磁盘值
    assert result.value == disk
    assert result.applied_paths == []
    assert result.conflicted_paths == [(
        ("find_jade", "find_jade_config", "invite_info_count"),
        ("find_jade", "invite_info_list_1"),
        ("find_jade", "invite_info_list_2"),
    )]
    blocked = result.blocked[0]
    assert blocked.operation == "REPLACE_PATH_SET"
    assert blocked.observed_disk_value[("find_jade", "invite_info_list_1")] == {"name": "X"}


def test_dynamic_path_set_applies_atomically_when_no_conflict():
    registry = [
        SimpleNamespace(
            key="find_jade.invite_info_list",
            member_path=("find_jade", "invite_info_list"),
            count_path=("find_jade", "find_jade_config", "invite_info_count"),
        )
    ]
    base = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "b"},
        }
    }
    local = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 3},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "b"},
            "invite_info_list_3": {"name": "c"},
        }
    }
    disk = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "b"},
        }
    }
    result = merge_operations(base, local, disk, dynamic_path_sets=registry)
    assert result.value == local
    assert set(result.applied_paths) == {
        ("find_jade", "find_jade_config", "invite_info_count"),
        ("find_jade", "invite_info_list_1"),
        ("find_jade", "invite_info_list_2"),
        ("find_jade", "invite_info_list_3"),
    }
    assert result.conflicted_paths == []


def test_dynamic_path_set_conflicts_when_disk_adds_unexpected_member():
    registry = [
        SimpleNamespace(
            key="find_jade.invite_info_list",
            member_path=("find_jade", "invite_info_list"),
            count_path=("find_jade", "find_jade_config", "invite_info_count"),
        )
    ]
    base = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1},
            "invite_info_list_1": {"name": "a"},
        }
    }
    local = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1},
            "invite_info_list_1": {"name": "A"},
        }
    }
    disk = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 2},
            "invite_info_list_1": {"name": "a"},
            "invite_info_list_2": {"name": "disk-new"},
        }
    }
    result = merge_operations(base, local, disk, dynamic_path_sets=registry)
    assert result.value == disk
    assert result.applied_paths == []
    assert result.blocked[0].observed_disk_value[("find_jade", "invite_info_list_2")] == {
        "name": "disk-new"
    }


def test_find_jade_dynamic_registries_are_isolated():
    registry = [
        SimpleNamespace(
            key="find_jade.invite_info_list",
            member_path=("find_jade", "invite_info_list"),
            count_path=("find_jade", "find_jade_config", "invite_info_count"),
        ),
        SimpleNamespace(
            key="find_jade.sup_account_list",
            member_path=("find_jade", "sup_account_list"),
            count_path=("find_jade", "find_jade_config", "sup_account_count"),
        ),
    ]
    base = {
        "find_jade": {
            "find_jade_config": {"invite_info_count": 1, "sup_account_count": 1},
            "invite_info_list_1": {"name": "invite"},
            "sup_account_list_1": {"character": "old"},
        }
    }
    local = copy.deepcopy(base)
    local["find_jade"]["sup_account_list_1"]["character"] = "new"

    operations = diff_trees(base, local, dynamic_path_sets=registry)
    assert len(operations) == 1
    assert isinstance(operations[0], ReplacePathSet)
    assert operations[0].registry_key == "find_jade.sup_account_list"

    result = merge_operations(base, local, copy.deepcopy(base), dynamic_path_sets=registry)

    # 两个同父节点 registry 必须按完整 field_N 前缀隔离，不能吞掉小号成员变更。
    assert result.value["find_jade"]["sup_account_list_1"] == {"character": "new"}
    assert result.blocked == []
    assert ("find_jade", "sup_account_list_1") in result.applied_paths



def test_normalize_keeps_only_last_operation_for_same_path():
    operations = [
        SetPath(("task", "group", "x"), 1),
        SetPath(("task", "group", "x"), 2),
    ]
    assert normalize_operations(operations) == [operations[-1]]


def test_normalize_keeps_partially_overlapped_path_set():
    subtree = ReplaceSubtree(
        ("task", "config"),
        {"count": 2},
        {"count": 1},
    )
    path_set = ReplacePathSet(
        "task.list",
        {
            ("task", "config", "count"): 2,
            ("task", "list_1"): {"v": 2},
        },
        {
            ("task", "config", "count"): 1,
            ("task", "list_1"): {"v": 1},
        },
    )

    with pytest.raises(ValueError, match="partially overlaps"):
        normalize_operations([subtree, path_set])


def test_normalize_ancestor_subtree_absorbs_descendant_subtree():
    ancestor = ReplaceSubtree(("task",), {"group": {"x": 1}}, {"group": {"x": 0}})
    descendant = ReplaceSubtree(("task", "group"), {"x": 2}, {"x": 1})
    assert normalize_operations([ancestor, descendant]) == [ancestor]


def test_missing_remains_singleton_after_deepcopy():
    assert copy.deepcopy(MISSING) is MISSING
    blocked = BlockedChange(("task", "missing"), "DELETE", MISSING, {"v": 1})
    assert copy.deepcopy(blocked).blocked_local_value is MISSING


def test_merge_result_does_not_alias_local_mutable_values():
    local = {"a": {"items": [1]}}
    result = merge_operations({"a": {}}, local, {"a": {}})
    local["a"]["items"].append(2)
    assert result.value == {"a": {"items": [1]}}


def test_normalize_merges_later_member_set_into_path_set():
    path_set = ReplacePathSet(
        "task.list",
        {("task", "list_1"): {"v": 1}},
        {("task", "list_1"): {"v": 0}},
    )
    later = SetPath(("task", "list_1", "v"), 2)
    normalized = normalize_operations([path_set, later])
    assert normalized == [ReplacePathSet(
        "task.list",
        {("task", "list_1"): {"v": 2}},
        {("task", "list_1"): {"v": 0}},
    )]


def test_normalize_path_set_absorbs_earlier_exact_member_set():
    earlier = SetPath(("task", "list_1"), {"v": 1})
    path_set = ReplacePathSet(
        "task.list",
        {("task", "list_1"): {"v": 2}},
        {("task", "list_1"): {"v": 0}},
    )
    assert normalize_operations([earlier, path_set]) == [path_set]


def test_normalize_merges_later_member_subtree_into_path_set():
    path_set = ReplacePathSet(
        "task.list",
        {("task", "list_1"): {"v": 1}},
        {("task", "list_1"): {"v": 0}},
    )
    later = ReplaceSubtree(("task", "list_1"), {"v": 3}, {"v": 1})
    normalized = normalize_operations([path_set, later])
    assert normalized == [ReplacePathSet(
        "task.list",
        {("task", "list_1"): {"v": 3}},
        {("task", "list_1"): {"v": 0}},
    )]


def test_hot_scalar_canonical_path_roundtrip():
    # HOT 字段（合成模型 canonical 形状）get/set 往返、diff 与三方应用
    base = {
        "config_name": "synthetic",
        "running_task": "",
        "script": {"device": {"serial": "old"}},
        "synthetic": {"task": {"limit_count": 1}},
    }
    hot_path = ("synthetic", "task", "limit_count")
    assert get_path(base, hot_path) == 1

    disk = set_path(base, hot_path, 2)
    assert get_path(disk, hot_path) == 2
    # set_path 返回深拷贝，不修改入参
    assert base["synthetic"]["task"]["limit_count"] == 1

    # 磁盘相对基线只产生该 HOT scalar 的单叶子 SET 操作
    assert diff_trees(base, disk) == [SetPath(hot_path, 2)]
    result = merge_operations(base, disk, base)
    assert result.value["synthetic"]["task"]["limit_count"] == 2
    assert result.applied_paths == [hot_path]
    assert result.conflicted_paths == []

