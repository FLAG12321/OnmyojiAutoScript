# This Python file uses the following encoding: utf-8
"""repository / branch 校验器的行为门禁。

这两个值会被**未加引号**拼进 shell=True 的 git 命令
（module/server/updater.py 的 execute_stream / ensure_origin），
所以校验的目标是挡住 shell 元字符与参数注入，而不是限制地址长什么样。

本文件同时锁住两个方向，缺一不可：
  * 不得再误拒真实合法的地址与分支名（此前的白名单正则把私有仓库挡死了）；
  * 不得放过任何能改变命令语义的输入。
"""
import pytest

from module.server.updater import validate_branch, validate_repository

pytestmark = pytest.mark.unit


# ---------------- repository：必须接受 ----------------

@pytest.mark.parametrize('repository', [
    # 公开地址
    'https://github.com/runhey/OnmyojiAutoScript.git',
    'https://gitee.com/flag12321/OnmyojiAutoScript.git',
    'http://192.168.1.2:3000/lu/oas.git',
    # 带凭据：私有仓库的常规用法，此前被白名单误拒
    'https://ghp_0123456789abcdefABCDEF@github.com/owner/repo.git',
    'https://user:pa55word@github.com/owner/repo.git',
    'https://user:p%40ss@example.com/owner/repo.git',
    'https://user:p%40%23ss@example.com/owner/repo.git',
    'https://oauth2:glpat-xxxxxxxx@gitlab.com/owner/repo.git',
    # 显式 ssh 协议与端口，此前被误拒（只认 git@host:path）
    'ssh://git@github.com/owner/repo.git',
    'ssh://git@github.com:22/owner/repo.git',
    # 主机名含下划线的自建服务，此前被误拒
    'https://gitlab.my_corp.com/owner/repo.git',
    # scp 式短地址（git 的默认形态）
    'git@github.com:runhey/OnmyojiAutoScript.git',
    'git@gitlab.my_corp.com:group/sub/repo.git',
    # git 协议
    'git://github.com/owner/repo.git',
])
def test_validate_repository_accepts_real_addresses(repository):
    """真实世界里合法的地址一个都不能拒——拒了用户就用不了私有仓库。"""
    assert validate_repository(repository) == repository


def test_validate_repository_strips_whitespace():
    """前后空白是复制粘贴的常态，应清理而非报错。"""
    assert validate_repository('  https://github.com/o/r.git  ') == \
        'https://github.com/o/r.git'


# ---------------- repository：必须拒绝 ----------------

@pytest.mark.parametrize('repository, why', [
    ('', '空值'),
    ('   ', '仅空白'),
    # shell 元字符：会在 shell=True 下改变命令语义
    ('https://example.com/repo.git;whoami', '; 串命令'),
    ('https://github.com/o/r.git && calc.exe', '&& 串命令'),
    ('https://github.com/o/r.git & calc.exe', '& 后台执行'),
    ('https://github.com/o/r.git | more', '| 管道'),
    ('https://github.com/o/r.git > out.txt', '> 重定向'),
    ('https://github.com/o/r.git < in.txt', '< 重定向'),
    ('https://github.com/$(calc).git', '$( 命令替换'),
    ('https://github.com/`calc`.git', '反引号'),
    ('https://github.com/%PATH%.git', '% 变量展开'),
    ('https://github.com/o/r.git^', '^ 转义符'),
    ('https://github.com/o/r.git\nfetch', '换行拼第二条命令'),
    ('https://github.com/o/r.git\r\nfetch', 'CRLF'),
    ('https://github.com/o r.git', '空格拆参数'),
    ('https://github.com/o/r.git"', '引号'),
    # 参数注入
    ('--upload-pack=calc.exe', '前导 - 被当成 git 选项'),
    # 不支持的协议：能读写本地任意路径或执行外部命令
    ('file:///C:/Windows/System32', 'file 协议'),
    ('ext::sh -c calc', 'ext 协议'),
    ('/etc/passwd', '裸路径'),
    ('C:/Windows', '裸本地路径'),
    ('https://', '缺主机名'),
    ('https:///owner/repo.git', '缺主机名'),
])
def test_validate_repository_rejects_dangerous(repository, why):
    """任何能改变命令语义或越出协议范围的输入都必须拒。"""
    with pytest.raises(ValueError):
        validate_repository(repository)


def test_validate_repository_rejects_overlong():
    with pytest.raises(ValueError):
        validate_repository('https://github.com/' + 'a' * 3000)


# ---------------- branch：必须接受 ----------------

@pytest.mark.parametrize('branch', [
    'master',
    'run_now_2',
    'feature/x-1',
    '2.0',
    'release/v1.2.3',
    # 下划线开头：git check-ref-format 允许，此前被 ^[A-Za-z0-9] 误拒
    '_dev',
    '_',
    # 非 ASCII：合法 ref，此前被误拒
    '修复登录',
    'feature/修复-登录',
    'ветка',
    # 中间的点合法，只有开头/结尾与 /. 被 git 禁止
    'v1.2.3-rc.1',
    "feature/it's-ok",
    'foo,bar',
    'foo{bar}',
])
def test_validate_branch_accepts_real_names(branch):
    """git 允许的分支名一个都不能拒——中文分支与 _dev 都是真实用法。"""
    assert validate_branch(branch) == branch


# ---------------- branch：必须拒绝 ----------------

@pytest.mark.parametrize('branch, why', [
    ('', '空值'),
    ('   ', '仅空白'),
    # shell 元字符
    ('master;whoami', '; 串命令'),
    ('master && calc.exe', '&& 串命令'),
    ('master | more', '| 管道'),
    ('master > out.txt', '> 重定向'),
    ('$(calc)', '命令替换'),
    ('`calc`', '反引号'),
    ('%PATH%', '变量展开'),
    ('master^', '^ 既是 shell 转义符也是 git revision 语法'),
    ('master\nfetch', '换行'),
    ('my branch', '空格拆参数'),
    ('br"anch', '引号'),
    ('br*anch', '通配符'),
    ('br?anch', '通配符'),
    ('br[anch', '通配符'),
    # 参数注入
    ('--force', '前导 - 被当成 git 选项'),
    ('-b', '前导 -'),
    # Git 明令禁止的 ref 形态
    ('feature..x', '.. 是 revision range 语法'),
    ('feature//x', '连续斜杠'),
    ('branch@{0}', '@{ 是 reflog 语法'),
    ('branch~1', '~ 是 revision 语法'),
    ('branch:path', ': 是 refspec 分隔符'),
    ('branch\\x', '反斜杠'),
    ('/master', '以 / 开头'),
    ('master/', '以 / 结尾'),
    ('.hidden', '以 . 开头'),
    ('master.', '以 . 结尾'),
    ('master.lock', '.lock 结尾'),
    ('feature/.hidden', '路径段以 . 开头'),
    ('@', '单独的 @'),
    ('br\x01anch', '控制字符'),
    ('br\x7fanch', 'DEL'),
])
def test_validate_branch_rejects_dangerous(branch, why):
    """shell 元字符与 Git 禁止的 ref 形态都必须拒。"""
    with pytest.raises(ValueError):
        validate_branch(branch)


def test_validate_branch_rejects_overlong():
    with pytest.raises(ValueError):
        validate_branch('a' * 256)


def test_validate_branch_strips_whitespace():
    assert validate_branch('  run_now_2  ') == 'run_now_2'
