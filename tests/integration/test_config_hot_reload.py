# This Python file uses the following encoding: utf-8
# 集成测试：用可注入测试专用合成 Schema 验证通用 HOT 基础设施（Task 5）。
# 覆盖：HOT 两阶段 prepare 提交/失败转 WARM、revision 竞争丢弃、同指纹失败不重试、
# 磁盘变化解除失败标记、未声明字段拒绝、无 prepare hook 的 scalar 直生效与幂等性。
# 生产默认 HOT 白名单为空，真实任务不发生中途替换；本文件全部使用 tmp_path 注入 config root。
import pytest
from pydantic import BaseModel, Field

from module.config.config import Config
from module.config.config_operations import get_path
from module.config.config_reload import COLD, HOT, WARM, ReloadPolicy
from module.config.config_store import ConfigStore
from module.config.config_validation import ValidationProfile


class SyntheticTaskConfig(BaseModel):
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


SYNTHETIC_PROFILE = ValidationProfile(
    model_type=SyntheticConfigModel,
    legacy_migrations=(),
    dynamic_path_sets=(),
)
SYNTHETIC_POLICY = ReloadPolicy(
    hot_paths=frozenset({("synthetic", "task", "limit_count")}),
    cold_prefixes=(("script", "device"),),
)

LIMIT_PATH = ("synthetic", "task", "limit_count")


def synthetic_config(limit_count: int = 1) -> dict:
    return {
        "config_name": "synthetic",
        "running_task": "",
        "script": {"device": {"serial": "old"}},
        "synthetic": {"task": {"limit_count": limit_count}},
    }


def make_synthetic_session(tmp_path):
    """构造注入 SYNTHETIC_PROFILE 的 Store 与注入 SYNTHETIC_POLICY 的 Config session。"""
    store = ConfigStore(config_root=tmp_path / "config", profile=SYNTHETIC_PROFILE)
    store.create_from_template("synthetic", synthetic_config())
    session = Config("synthetic", store=store, reload_policy=SYNTHETIC_POLICY)
    task = SyntheticTaskRunner()
    return session, store, task


class SyntheticTaskRunner:
    """测试专用合成任务：声明 derived_limit 派生缓存并实现纯 prepare hook。"""

    HOT_RELOAD_DERIVED_FIELDS = frozenset({"derived_limit"})

    def __init__(self):
        self.derived_limit = 1
        self.prepare_error = None
        self.bump_revision = None
        self.prepare_calls = 0

    def prepare_config_reload(self, candidate, changed_paths):
        self.prepare_calls += 1
        if self.bump_revision is not None:
            self.bump_revision()
        if self.prepare_error is not None:
            raise self.prepare_error
        return {"derived_limit": candidate.synthetic.task.limit_count}


# ---------- 两阶段提交成功/失败/竞争 ----------

def test_prepare_success_commits_model_cache_and_base(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is True
    assert session.model.synthetic.task.limit_count == 2
    assert task.derived_limit == 2
    assert get_path(session.base, LIMIT_PATH) == 2
    assert session.pending_warm_paths == set()


def test_prepare_failure_keeps_runtime_and_defers(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    task.prepare_error = RuntimeError("boom")
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is False
    assert session.model.synthetic.task.limit_count == 1
    assert task.derived_limit == 1
    assert get_path(session.base, LIMIT_PATH) == 1
    # prepare 失败只标 WARM deferred，运行态保持原值
    assert session.pending_warm_paths == {LIMIT_PATH}


def test_revision_change_discards_prepared_candidate(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    # prepare 执行期间推进 revision，模拟会话被其他刷新修改 → 候选丢弃
    task.bump_revision = session._increment_refresh_revision_for_test
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is False
    assert session.model.synthetic.task.limit_count == 1
    assert task.derived_limit == 1
    assert get_path(session.base, LIMIT_PATH) == 1


def test_generation_mismatch_discards_prepared_candidate(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    calls = []
    session._state_reporter = lambda: calls.append(1)
    # prepare 执行期间置 generation mismatch（不推进 revision）：提交段重取锁时
    # 必须一并检查 mismatch，丢弃候选，且不上报 state、不推进 revision
    task.bump_revision = lambda: setattr(session, "_generation_mismatch", True)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    rev_before = session._refresh_revision

    assert session.refresh_hot_at_checkpoint(task) is False
    assert session.model.synthetic.task.limit_count == 1
    assert task.derived_limit == 1
    assert get_path(session.base, LIMIT_PATH) == 1
    assert session._refresh_revision == rev_before
    assert calls == []


def test_commit_exception_keeps_runtime_and_defers(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    # 提交段抛错（模拟模型无对应属性等结构性不匹配）：异常被吞并按失败转 WARM，
    # 运行 model/派生缓存保持原值、指纹进入失败集，且不向调用方穿出。
    # 打在 _resolve_model_slots 上：原地赋值方案里它是提交段唯一可能失败的一趟，
    # 且失败发生在任何 setattr 之前，正是「零写入」不变量的守护点。
    def boom_resolve(model, paths, candidate):
        raise AttributeError("synthetic commit boom")

    session._resolve_model_slots = boom_resolve
    assert session.refresh_hot_at_checkpoint(task) is False
    assert session.model.synthetic.task.limit_count == 1
    assert task.derived_limit == 1
    assert get_path(session.base, LIMIT_PATH) == 1
    assert session.pending_warm_paths == {LIMIT_PATH}
    assert len(session._hot_failed_fingerprints) == 1


# ---------- 同一指纹失败不重试 / 磁盘变化解除失败标记 ----------

def test_failed_fingerprint_not_reprepared(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    task.prepare_error = RuntimeError("boom")
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    assert session.refresh_hot_at_checkpoint(task) is False

    # 同一 disk/local 指纹：即使错误标记已清除也不再调用 prepare，交给 WARM 边界
    task.prepare_error = None
    assert session.refresh_hot_at_checkpoint(task) is False
    assert task.prepare_calls == 1


def test_disk_change_releases_failed_fingerprint(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    task.prepare_error = RuntimeError("boom")
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    assert session.refresh_hot_at_checkpoint(task) is False

    # 磁盘再次变化 → 指纹改变 → 重新 prepare 并成功提交，失败标记解除
    task.prepare_error = None
    store.patch_user_field("synthetic", LIMIT_PATH, 3)
    assert session.refresh_hot_at_checkpoint(task) is True
    assert session.model.synthetic.task.limit_count == 3
    assert task.derived_limit == 3
    assert get_path(session.base, LIMIT_PATH) == 3
    assert session.pending_warm_paths == set()


# ---------- 声明字段校验 / 无 hook scalar 直生效 / 幂等 ----------

def test_undeclared_prepare_fields_are_rejected(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    def bad_prepare(candidate, changed_paths):
        return {"undeclared_field": 1}

    task.prepare_config_reload = bad_prepare
    assert session.refresh_hot_at_checkpoint(task) is False
    assert session.model.synthetic.task.limit_count == 1
    assert task.derived_limit == 1
    assert session.pending_warm_paths == {LIMIT_PATH}


def test_task_without_prepare_hook_applies_scalars(tmp_path):
    session, store, _task = make_synthetic_session(tmp_path)

    class PlainTask:
        pass

    task = PlainTask()
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is True
    assert session.model.synthetic.task.limit_count == 2
    assert get_path(session.base, LIMIT_PATH) == 2


def test_successful_commit_is_idempotent(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    assert session.refresh_hot_at_checkpoint(task) is True

    # base 已推进到磁盘值：再次刷新无新 HOT 变更，不再调用 prepare
    assert session.refresh_hot_at_checkpoint(task) is False
    assert task.prepare_calls == 1


def test_no_disk_change_returns_false(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    # 磁盘 mtime 未变化：mtime 节流快速返回，不调用 prepare
    assert session.refresh_hot_at_checkpoint(task) is False
    assert task.prepare_calls == 0


def test_no_hot_candidate_advances_mtime_baseline(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    # 只改 WARM 字段（running_task）：HOT 候选为空 → 推进已检查 mtime 基线，
    # 避免每帧全量 load+校验（规格 §12）
    store.patch_user_field("synthetic", ("running_task",), "Orochi")
    assert session.refresh_hot_at_checkpoint(task) is False
    assert task.prepare_calls == 0
    # 基线已推进：磁盘未再变化时不重新 load（mtime 节流直接返回）
    baseline = session._mtime_ns
    assert session.refresh_hot_at_checkpoint(task) is False
    assert session._mtime_ns == baseline
    assert task.prepare_calls == 0


def test_failed_fingerprint_advances_mtime_baseline(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    task.prepare_error = RuntimeError("boom")
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    assert session.refresh_hot_at_checkpoint(task) is False

    # prepare 失败立即推进已检查 mtime 基线：下一次同 mtime 检查直接短路，
    # 不重复 load，也不再次调用 prepare（指纹同时记录在失败集，双保险）
    task.prepare_error = None
    baseline = session._mtime_ns
    assert session.refresh_hot_at_checkpoint(task) is False
    assert session._mtime_ns == baseline
    assert task.prepare_calls == 1


# ---------- guard 清理 / 状态上报 ----------

def test_base_exception_in_prepare_clears_guard(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)

    def raise_keyboard(candidate, changed_paths):
        raise KeyboardInterrupt()

    task.prepare_config_reload = raise_keyboard
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    # BaseException 不被打包为 prepare 失败，但 finally 必须清除 guard
    with pytest.raises(KeyboardInterrupt):
        session.refresh_hot_at_checkpoint(task)
    assert session._refresh_in_progress is False

    # guard 已清除：后续 HOT 可正常执行
    del task.prepare_config_reload
    store.patch_user_field("synthetic", LIMIT_PATH, 3)
    assert session.refresh_hot_at_checkpoint(task) is True
    assert session.model.synthetic.task.limit_count == 3
    assert task.derived_limit == 3


def test_state_reporter_called_after_commit(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    calls = []
    session._state_reporter = lambda: calls.append(1)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is True
    # HOT 提交成功后锁外上报一次 config_state
    assert calls == [1]


def test_state_reporter_called_after_failure(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    calls = []
    session._state_reporter = lambda: calls.append(1)
    task.prepare_error = RuntimeError("boom")
    store.patch_user_field("synthetic", LIMIT_PATH, 2)

    assert session.refresh_hot_at_checkpoint(task) is False
    # prepare 失败转 WARM 后同样锁外上报一次 config_state
    assert calls == [1]


def test_state_reporter_noop_when_unset(tmp_path):
    session, store, task = make_synthetic_session(tmp_path)
    store.patch_user_field("synthetic", LIMIT_PATH, 2)
    # 未注册 reporter：提交成功不抛错
    assert session.refresh_hot_at_checkpoint(task) is True
    assert session.model.synthetic.task.limit_count == 2


# ---------- 分类优先级 ----------

def test_cold_prefix_wins_over_hot_allowlist():
    # script.device 是 COLD prefix：即使声明进 hot_paths 仍按 COLD，不中途替换
    policy = ReloadPolicy(hot_paths=frozenset({("script", "device", "serial")}))
    assert policy.classify(("script", "device", "serial")) == COLD


def test_synthetic_policy_classification():
    assert SYNTHETIC_POLICY.classify(LIMIT_PATH) == HOT
    assert SYNTHETIC_POLICY.classify(("script", "device", "serial")) == COLD
    assert SYNTHETIC_POLICY.classify(("running_task",)) == WARM


# ---------- 原地赋值：捕获引用可见性与提交原子性 ----------

def test_hot_commit_visible_through_captured_submodel(tmp_path):
    """本次改动的核心命题：捕获子模型对象的读法必须立即看到 HOT 新值。

    任务里大量存在 `con = self.config.<task>` 与 `cached_property conf` 这类把配置
    子模型捕获到局部变量/实例属性的写法。原先 HOT 用 model_copy 自底向上重建模型，
    会换掉对象标识使捕获者读到旧副本——白名单开了也不生效。改原地赋值后必须可见。
    """
    session, store, task = make_synthetic_session(tmp_path)
    # 模拟任务在 run() 开头捕获子模型对象（不是标量）
    captured_group = session.model.synthetic
    captured_leaf_owner = session.model.synthetic.task
    root_before = session.model

    store.patch_user_field("synthetic", LIMIT_PATH, 7)
    assert session.refresh_hot_at_checkpoint(task) is True

    # 根模型与各层子模型的对象标识不变，捕获者读到的就是新值
    assert session.model is root_before
    assert captured_group.task.limit_count == 7
    assert captured_leaf_owner.limit_count == 7


def test_hot_commit_atomic_across_multiple_paths(tmp_path):
    """多路径提交必须原子：解析阶段任一路径失败，已解析路径也不得落盘。

    原实现逐路径 `self._model = ...` 边解析边提交，第 N 条抛错会留下前 N-1 条已生效。
    两趟结构（先全解析、后全赋值）保证要么全成要么零写入。
    构造方式：两条都是模型里真实存在的 HOT 路径，按 sorted 顺序 running_task 先解析成功、
    limit_count 后解析抛错，于是能断言「先成功那条同样没被写入」。
    """
    session, store, task = make_synthetic_session(tmp_path)
    session.reload_policy = ReloadPolicy(
        hot_paths=frozenset({LIMIT_PATH, ("running_task",)}),
        cold_prefixes=(("script", "device"),),
    )
    store.patch_user_field("synthetic", ("running_task",), "Orochi")
    store.patch_user_field("synthetic", LIMIT_PATH, 9)

    # 让排在后面的 limit_count 在解析阶段抛错；running_task 已解析成功
    real_model_get = Config._model_get

    def selective_boom(model, path):
        if path == LIMIT_PATH:
            raise AttributeError("synthetic resolve boom")
        return real_model_get(model, path)

    session._model_get = selective_boom
    assert session.refresh_hot_at_checkpoint(task) is False

    # 两条都必须保持原值：解析失败发生在任何 setattr 之前
    assert session.model.running_task == ""
    assert session.model.synthetic.task.limit_count == 1
    assert get_path(session.base, ("running_task",)) == ""
    assert get_path(session.base, LIMIT_PATH) == 1


def test_set_model_value_writes_in_place(tmp_path):
    """_set_model_value 契约已从「重建返回新模型」改为「原地写并返回同一实例」。"""
    session, store, task = make_synthetic_session(tmp_path)
    root = session.model
    leaf_owner = root.synthetic.task

    returned = session._set_model_value(root, LIMIT_PATH, 5)

    assert returned is root
    assert leaf_owner.limit_count == 5

