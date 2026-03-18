# 校园网自动登录工具

适用于当前仓库抓包确认过的 **Eportal 动态公钥认证流程**。这个项目的目标不是做跨学校通用适配器，而是把当前这套协议整理成一个可安装、可配置、可排障的 CLI 工具。

## 定位

- 正式入口是 `campus-login`
- `python3 campus_login.py` 仍然可用，但仅作为兼容入口，已处于弃用状态
- 根目录 `security.py` 仅为历史兼容 shim
- 唯一维护中的 RSA 实现在 `src/campus_login_tool/security.py`
- 不提供 GUI，也不抽象多学校协议差异

## 安装

推荐直接从 PyPI 安装：

```bash
python3 -m pip install campus-login
```

如果是在项目目录下本地安装：

```bash
python3 -m pip install .
```

安装完成后可用：

```bash
campus-login --help
```

如果只是从源码目录临时运行：

```bash
PYTHONPATH=src python3 -m campus_login_tool --help
```

## 快速开始

1. 生成配置模板：

```bash
campus-login init-config
```

默认会创建 `~/.campus_login.conf`，并尝试把权限设为 `600`。

2. 编辑配置文件，至少填入用户名。密码推荐按以下顺序提供：

- 直接运行时交互输入
- `--password-stdin`
- 环境变量
- 配置文件

3. 运行诊断：

```bash
campus-login doctor
```

4. 单次登录：

```bash
campus-login login
```

5. 前台持续监控并自动重连：

```bash
campus-login watch
```

## 命令说明

### `campus-login login`

执行一次完整登录流程。

```bash
campus-login login --username your_name
campus-login login --password-stdin
campus-login login --config /path/to/config.conf
campus-login login --force
campus-login login -v --log-file ./campus-login.log
```

如果用户名或密码未在 CLI / 环境变量 / 配置文件中提供，且当前在 TTY 中运行，程序会交互提示输入。

### `campus-login watch`

前台循环检测网络状态，连续探测失败后自动重登。它不是系统级 daemon，不会自动注册到 `launchd`、`systemd` 或任务计划程序。

```bash
campus-login watch --interval 30 --retries 3
```

### `campus-login doctor`

用于确认本机配置和当前网络环境是否还符合预期，会检查：

- 配置文件是否存在
- 配置文件权限是否安全
- 用户名和密码是否已解析
- 联网探测地址是否可访问
- 认证触发地址是否还能解析出登录页

### `campus-login init-config`

生成与运行时模板一致的配置文件：

```bash
campus-login init-config
campus-login init-config --config ./campus_login.conf --force
```

仓库中的 [campus_login.conf.example](./campus_login.conf.example) 只是同一模板的文档镜像，真正的单一来源在 `src/campus_login_tool/config.py` 的 `CONFIG_TEMPLATE`。

## 配置规则

统一优先级：

```text
CLI 参数 > 环境变量 > 配置文件 > 默认值
```

支持的环境变量：

```text
CAMPUS_LOGIN_CONFIG
CAMPUS_LOGIN_USERNAME
CAMPUS_LOGIN_PASSWORD
CAMPUS_LOGIN_CHECK_URL
CAMPUS_LOGIN_INTERVAL
CAMPUS_LOGIN_RETRIES
```

默认配置文件路径：

```text
~/.campus_login.conf
```

如果配置文件中保存了密码，在 macOS / Linux 上权限必须是 `600`，否则程序会拒绝读取。

## 故障排查

### `doctor` 说互联网探测失败

通常意味着以下情况之一：

- 当前还没完成认证
- 当前不在校园网环境
- `check_url` 在本地网络中不可达

可先尝试：

```bash
campus-login login --force
```

如果环境里有更稳定的外部地址，也可以调整 `check_url`。

### `doctor` 说认证触发地址解析失败

通常说明：

- 认证页跳转脚本格式发生变化
- 当前网络不再需要这套认证流程
- 学校更新了协议实现

这时优先查看 [认证流程分析.md](./认证流程分析.md)，确认抓包记录与当前代码实现是否仍一致。

### 参数看起来都对，但仍登录失败

优先检查：

- 账号密码是否正确
- `pageInfo` 返回的公钥字段是否变化
- `src/campus_login_tool/security.py` 的加密逻辑是否仍与认证页 JS 一致
- `login` 接口返回的错误消息

需要更多细节时请加 `-v`，并在分享日志前先脱敏。

## 兼容入口

以下历史用法仍可用，但会打印弃用提示：

```bash
python3 campus_login.py -u your_name
python3 campus_login.py --daemon
```

建议迁移到：

```bash
campus-login login
campus-login watch
```

根目录 [campus_login.py](./campus_login.py) 只是 CLI 兼容包装层；根目录 [security.py](./security.py) 只是 RSA 兼容 shim，避免历史引用直接失效。

## 开发

安装开发依赖：

```bash
python3 -m pip install -e ".[dev]"
```

本地和 CI 使用同一组基础检查：

```bash
python3 -m ruff check .
python3 -m compileall src campus_login.py security.py
python3 -m unittest discover -s tests -v
```

如果需要本地构建并检查发布包：

```bash
python3 -m pip install -e ".[dev]"
python3 -m build
python3 -m twine check dist/*
```

## 发布到 PyPI

当前仓库已配置为使用 GitHub Actions + PyPI Trusted Publishing 发布。建议流程：

1. 修改 `src/campus_login_tool/__init__.py` 中的 `__version__`
2. 本地执行构建检查：

```bash
python3 -m pip install -e ".[dev]"
python3 -m build
python3 -m twine check dist/*
```

3. 提交变更后创建并推送版本 tag：

```bash
git tag v0.1.2
git push origin v0.1.2
```

4. GitHub 会触发 `.github/workflows/publish.yml` 自动上传到 PyPI

首次启用前，需要在 PyPI 项目侧把仓库 `RustyPiano/campus-login` 配置为 Trusted Publisher，并允许工作流 `.github/workflows/publish.yml` 使用 `pypi` 环境。

## 仓库结构

- `src/campus_login_tool/`: 当前维护中的 CLI、配置解析、协议客户端、watch 模式和加密实现
- `campus_login.py`: 历史单文件入口的兼容包装层
- `security.py`: 历史 RSA 模块的兼容 shim
- `campus_login.conf.example`: 配置模板镜像
- `认证流程分析.md`: 协议抓包、字段和接口说明

## 免责声明

- 该工具只针对当前仓库分析过的认证流程，不保证适用于其他学校
- 校园网认证流程可能随时变化，导致脚本失效
- 自动化登录可能违反学校网络使用规定，请自行确认风险
- 请妥善保管校园网密码
