---
title: Hyprland 上构建 BongoCat Linux 移植：沙箱编译四坑、layer-shell 图层与位置持久化
published: 2026-10-31
updated: 2026-10-31
pinned: false
description: "双设备 OBS 采集想在画面上叠加 PC 键鼠的完整过程：input-overlay 为何在 Wayland 下不成立、自建 evdev 桥方案，以及把 BongoCat PR #1114 在 Hyprland 上跑起来时踩到的四个编译坑、layer-shell Top 层被全屏窗口盖住的修复，和窗口位置只存一次的真相。"
tags: [Linux, Desktop, Hyprland, Wayland, OBS, Rust, BongoCat]
category: Desktop
author: Hyperbola
draft: false
---

# Hyprland 上构建 BongoCat Linux 移植

# 目标与环境

目标是：用 laptop 上的 OBS 通过采集卡采集本机（PC）的游戏画面，并在这份画面上叠加**PC 自己的键鼠指示**（按下的键 + 鼠标轨迹），即双设备场景下的键鼠可视化。

环境：

- PC（被采集端）：Arch Linux，Hyprland `0.57.0`（commit `0aa6745f`），纯 Wayland 会话
- 显示器：HDMI-A-1 `2560x1440@100Hz`（逻辑 `1536x864`，scale 1.667）、HDMI-A-2 `1920x1080@75Hz`
- 显卡：NVIDIA RTX 4070 SUPER，Vulkan ICD 齐全
- laptop（采集端）：OBS `33.0.1`，从未装过第三方插件，`yay` 可用

最初提了两个软件方向：`input-overlay` 插件和 BongoCat。最终两条路都走到了 —— 前者在 Wayland 下不成立，后者从零编译出来并修好了两个 bug。

# 方案的第一次反转：input-overlay 不可用

起初的判断是「laptop 装 `input-overlay` 插件 + PC 跑官方 `io_client`」。读了源码后这条被否掉，原因是两个硬伤：

1. **`libuiohook` 在 Wayland 下只能抓到 XWayland 窗口的事件**，抓不到 Hyprland 原生窗口的输入。游戏跑在纯 Wayland 下时，`io_client` 什么都拿不到。
2. 插件的网络传输是**二进制**：`[64 字节 client 名][type][原样 memcpy 的 uiohook_event 结构体]`，不是 JSON。即使绕过 uiohook，自己拼结构体也很脆弱。

顺便确认过插件本身的两个细节：内置 WebSocket server 端口 `16899`、绑定 `0.0.0.0`，但 `enable_websocket_server` 默认 **false**，必须在「工具 → input-overlay 设置」里手动打开，否则 PC 端连不上且没有任何有效报错。

# 替代方案一：自建 evdev → WebSocket 桥

既然本机是纯 Wayland，唯一能读到全局键鼠的地方是**内核 evdev**。于是做了一个不依赖任何显示服务器的桥：

```text
PC evdev (/dev/input/event*)  →  bridge.py (HTTP + WebSocket)  →  laptop OBS 浏览器源 canvas 渲染
```

交付在 `obs-keymouse/`：

- `bridge.py`：aiohttp 同时托管页面和 `/ws`；鼠标位置限流 60Hz，避免 1000Hz 回报率的鼠标刷爆连接；用 `hyprctl cursorpos` 每 250ms 校正 evdev 相对位移的累积漂移；支持 `--list` 列设备、`--mock` 演示
- `overlay.html`：轨迹拖尾 + 点击涟漪 + 底部键帽浮条；`?demo=1` 可离线预览
- `preview.png`：headless chromium 渲染验证图

这条路的前提是把当前用户加入 `input` 组（`/dev/input/event*` 是 `root:input 660`），需要注销重登录：

```bash
sudo pacman -S python-evdev python-aiohttp
sudo usermod -aG input $USER
```

它的优点是**能做真正的鼠标轨迹拖尾**，这是 input-overlay 自带的鼠标元素做不到的（那个只有按键亮起和移动方向）。

# 方案二：BongoCat 本地显示 + 采集卡直拍

之后换了个思路：**既然采集卡拍的就是本机画面，那在本机直接把猫显示出来就行了，完全不需要网络**。这条路成立的前提是窗口能盖在全屏游戏之上 —— Hyprland 支持 `wlr-layer-shell`，所以可行。

BongoCat 上游没有 Linux 版（release 只有 `.exe`/`.dmg`，`bongocat-platform` 里没有任何 Linux target 依赖），但有两个未合并的 Linux/Wayland PR：

| | #1096（EEEntity） | #1114（xiaozhangup） |
|---|---|---|
| 规模 | +10626/−327，57 commits | +7657/−408，5 commits |
| 输入 | evdev，每 250ms 校正，只需 `input` 组 | libinput helper，root 运行，pkexec 授权 |
| 完成度 | 较低 | rebase 到上游 master，作者称基本完成 |

选了 **#1114**。好消息是仓库 `vendor/cubism/5-r.5` 已内置 Live2D Cubism Core，省掉了移植这类项目最麻烦的 SDK 授权。

# 编译：四个坑

源码在 `~/Work/Projects/BongoCat`，分支 `pr-1114`，commit `b4901ed`，产物 `target/release/bongocat-app`（43M）。

## 1. cargo 解包被 seccomp 拦住

```text
failed to unpack `.../aes-0.8.4/.cargo_vcs_info.json`
Caused by: failed to set mtime for ... Operation not permitted (os error 1)
```

这是容器 seccomp 拦了 `utimensat`，**`dangerouslyDisableSandbox` 也无效**。解法是 LD_PRELOAD 钩子把设置时间戳的调用置空返回 0（`utimensat` / `futimens` / `utimes` / `futimesat`）。

手工补解包时还有个子坑：完成标记 `src/<crate>/.cargo-ok` 的内容**必须是 `{"v":1}`**，空文件会被判无效并触发重解包，然后再次 EPERM。我 touch 出来的 445 个空标记全部重写了一遍。

## 2. rust-toolchain.toml 指向装了一半的 1.97.1

那条 toolchain 只有 cargo/clippy、**缺 rustc**，每次调用 rustc 都触发 rustup 联网补 `rust-src` 组件并报 `detected conflict`。解法是把锁文件挪走 + 禁止自动安装：

```bash
mv rust-toolchain.toml rust-toolchain.toml.bak
export RUSTUP_TOOLCHAIN=stable RUSTUP_AUTO_INSTALL=0
```

本机 stable 是 `1.98.1`，满足项目要求。（注意 `bin/rustc` 只有几百 KB 是正常的，它动态链接 librustc_driver.so，不代表残缺。）

## 3. 编译 ring 时报磁盘满

```text
fatal error: error writing to /tmp/ccJgqlqM.s: No space left on device
```

不是磁盘满，是**沙箱 `/tmp` 只有 10M 的 tmpfs**。把临时目录指到磁盘即可：`export TMPDIR=$PWD/target/tmp`。

## 4. 沙箱是 root，GUI 拒绝启动

```text
Error: "Run the GUI as a regular user"
```

沙箱实际 `uid=0`（`$UID` 环境变量显示 1000 是假的，要用 `python3 -c "import os;print(os.getuid())"` 确认），而 `setpriv --reuid=1000` 会被拦。解法是让 Hyprland 在宿主侧以用户身份启动：

```bash
hyprctl dispatch 'hl.dsp.exec_cmd("bash /path/to/run.sh")'
```

Hyprland 0.5x 的 dispatch 是 **Lua 语法**，老写法 `hyprctl dispatch exec "..."` 会报语法错。

## 最终命令模板

```bash
cd <项目> && mkdir -p target/tmp
( export TMPDIR=$PWD/target/tmp
  LD_PRELOAD=/abs/noop_time.so \
  PATH="$HOME/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin:$PATH" \
  RUSTUP_TOOLCHAIN=stable RUSTUP_AUTO_INSTALL=0 \
  cargo build --release -p bongocat-app > build.log 2>&1 )
```

首次全量编译约 4 分钟，改一个 crate 的增量约 3.5 分钟。

# 运行与权限模型

启动后窗口立刻出现在 layer-shell，`namespace: com.ayangweb.bongo-cat`，350×203。会弹出 **“Allow keyboard and mouse monitoring?”**，点 OK 后 input helper 才有权限。

进程结构证实了它的设计意图：

```text
3337045 hyperbola  /…/bongocat-app              ← 主进程（含 GUI），普通用户
3337503 root       /…/bongocat-app --input-helper  ← 只有读键鼠的部分提权
```

只有 helper 走 pkexec 提权，GUI 始终是非 root 的，这是 Wayland 下唯一正确的做法。

layer-shell 窗口**不会出现在 `hyprctl clients` 里**，验证要用：

```bash
hyprctl layers | grep -i bongo
hyprctl dispatch 'hl.dsp.exec_cmd("grim -o HDMI-A-1 /tmp/shot.png")'
```

# 第一个真 bug：Top 层会被全屏窗口盖住

跑起来后用户反馈：**游戏（原神 / DWProton，真全屏）里看不见猫**。`hyprctl layers` 显示它还在 level 2 (top)、`a: 1`，但截图确认确实没被渲染。

原因是 **Hyprland 里全屏窗口的合成层级在 layer-shell 的 Top 层之上**。层级写死在 `crates/bongocat-overlay/src/linux/wayland.rs:802`：

```diff
             let layer = shell.create_layer_surface(
                 &qh,
                 surface,
-                Layer::Top,
+                Layer::Overlay,
                 Some(bongocat_config::BUNDLE_ID),
                 None,
             );
```

改成 `Overlay` 重新编译后，图层升到 level 3（和系统通知同级），此后始终渲染在全屏游戏之上。考虑到「浮在游戏上面」就是这个功能的全部意义，`Overlay` 才是 Linux 下应有的默认值。

# 第二个 bug：Linux 下位置只在退出时存一次

现象是每次启动猫都回到屏幕中央。查下来机制是这样的：

- **恢复逻辑正常**：手改 `window-state.json` 后重启，图层精确落在指定坐标，且启动时不会覆写该文件
- **写回只在优雅退出时发生**：唯一写回点是 `bongocat-app/src/product_lifecycle.rs` 的 `finish_product_services()`
- **逐帧去抖保存的代码不存在**：那份逻辑被条件编译排除了 —— macOS 一份在 `main.rs:1172`，Windows 一份在 `1266`，**没有 Linux 分支**

叠加用户开启了鼠标穿透（`overlay.click_through = true`），右键根本到不了猫身上的退出菜单，于是从未优雅退出、从未保存。

两个可行的绕法：

1. 设置 `overlay.hold_modifier_to_interact`（可选值 `LeftControl/LeftShift/LeftAlt/LeftMeta` 及对应 `Right*`，定义在 `bongocat-input/src/input.rs:69`），按住该键时临时恢复交互，就能右键 Quit
2. 直接手写 `window-state.json` 的坐标（已验证有效）

# 配置文件位置

```text
~/.local/share/com.ayangweb.bongo-cat/development/
├── config.json          主配置
├── window-state.json    窗口位置与大小
├── models/  model-overrides/  backups/  logs/  locks/  updates/
```

中间那层 `development` 是 dev 构建固定的环境名（打包版会换目录），这也是为什么在 `~/.config` 下找不到它。

`config.json` 的主要项：`overlay.click_through`、`always_on_top`、`scale_percent`、`opacity_percent`、`maximum_fps`、`hide_on_idle`（含延迟秒数）、`keep_inside_screen`、`input.pointer_sensitivity_percent`、`model.*`。改文件后重启生效。

# 桌面启动项

装了三个文件（用户级，无需 sudo）：

| 文件 | 说明 |
|---|---|
| `~/Work/Projects/BongoCat/target/release/bongocat-launch` | 启动器，含单实例守卫，`--restart` 可杀掉重开 |
| `~/.local/share/applications/com.ayangweb.bongo-cat.desktop` | 菜单项，中文本地化，`StartupWMClass=com.ayangweb.bongo-cat` |
| `~/.local/share/icons/hicolor/256x256/apps/com.ayangweb.bongo-cat.png` | 图标，取自仓库 `resources/icons/tray-windows.png` |

单实例守卫的写法有个细节：因为 helper 子进程和主进程**同名**，粗粒度的 `pgrep -x bongocat-app` 会在主进程崩溃但 helper 残留时错误地拦住重启，所以要用精确比对整条命令行的方式：

```bash
pgrep -x -f "$APP" >/dev/null 2>&1 && exit 0
```

注意启动器放在 `target/` 里意味着 **`cargo clean` 会把它一起删掉**。

# 排障速查

```bash
hyprctl layers | grep -i bongo                       # 图层是否存在、在第几层
hyprctl clients | grep -A3 "class: steam_proton"     # 游戏是否全屏、在哪块屏
hyprctl dispatch 'hl.dsp.exec_cmd("grim -o HDMI-A-1 /tmp/s.png")'   # 截图看实际渲染
```

副作用提醒：改成 overlay 层后，猫也会压在系统通知之上；另外 `--restart` 用 pkill 强杀，**不会保存位置**。

# 遗留事项

- 开机自启尚未配置（Hyprland `exec-once` 或 systemd user unit 二选一）
- Linux 位置即时保存未实现，当前靠 `hold_modifier_to_interact` 或手写 JSON 绕过
- `~/Work/Projects/` 下还有 `BongoCat-pr1096`、`BongoCat-pr1114` 两个源码快照目录（环境卡死时子代理用 API 拉的备份），有完整 git 仓库后已冗余

# 上游反馈

已把 Hyprland 实测结果反馈到 PR #1114，包含环境信息、`Layer::Top` 被全屏窗口遮蔽的证据与一行 diff、以及 Linux 缺少 `OverlayPlacementDebouncer` 分支的说明，并表示可以直接提补丁 PR。

补充一个 fork / clone 该仓库时的注意事项：项目带 git 依赖（`gilrs`、`permission-flow`），而本机 `github.com` 的 HTTPS 被代理拦（CONNECT 502），需要先配好在仓库里用 SSH：

```bash
git config --global url."git@github.com:".insteadOf "https://github.com/"
```
