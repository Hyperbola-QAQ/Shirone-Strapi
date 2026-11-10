---
title: Omarchy 4.5 升级后：优化几项配置
published: 2026-11-08
updated: 2026-11-08
pinned: false
description: 4.5 把工作区改成按显示器分区块、加了浮动工作区和 Quake 控制台、把标题栏拆成独立包，迁移队列还堵在 titlebars 的钉依赖上。记录每一项带来的冲突和我最后怎么调的
tags: [Linux, Desktop, Omarchy, Hyprland]
category: Desktop
author: Hyperbola
draft: false
---

# Omarchy 4.5 升级后：优化几项配置

## 升级怎么走完的

```text
[2026-11-07T12:59:02+0800] [ALPM] upgraded omarchy-settings (4.0.6-1 -> 4.5.0-3)
[2026-11-07T12:59:02+0800] [ALPM] installed omarchy-speedtest (0.2.1-1)
[2026-11-07T12:59:02+0800] [ALPM] upgraded omarchy          (4.0.6-1 -> 4.5.0-3)
[2026-11-07T20:00:44+0800] [ALPM] downgraded hyprland      (0.57.1-1 -> 0.56.2-4)
[2026-11-07T20:00:44+0800] [ALPM] installed omarchy-hyprland-titlebars (0.2.2-2)
```

包升级本身是成功的，卡住的是后面那步迁移：队列堵在 `1791403310`（装 titlebars）。`omarchy-hyprland-titlebars` 钉死 `hyprland=0.56.2`，而这次升级把 Hyprland 带到了 0.57.1-1，依赖不满足，迁移退出 1，**整个队列硬停**。

换镜像解决不了。`[omarchy]` 已经切到 `pkgs.omarchy.cn/stable`，配套的 `stable-mirror.omarchy.cn` 里 `extra` 是 **2026-11-02 的冻结快照**，本地 311 个包比它新 —— 指着它等于 Arch 侧停更，而本地 Hyprland 还是 0.57.1。pacman 不会自己降级。

所以那晚 20:00 走的是「退回快照版本」：一次性事务把 hypr 栈降下去、同一次把 titlebars 装上，再重跑迁移。

```bash
sudo pacman -S --needed \
  extra/hyprland=0.56.2-4 extra/hyprutils=0.14.2-1 extra/aquamarine=0.15.1-1 \
  extra/hyprgraphics=0.5.1-4 extra/hyprtoolkit=0.6.0-1 extra/hyprland-guiutils=0.2.2-4 \
  omarchy/omarchy-hyprland-titlebars
sudo -v && omarchy-migrate      # 剩下的 42 条全部跑完
```

只降 `hyprland` 一个包会被 soname 挡住，`hyprutils` / `aquamarine` / `hyprgraphics` / `hyprtoolkit` / `hyprland-guiutils` 必须一起降。降级不影响 Lua 配置层：0.56.2 同样带 `src/config/lua/`，`hyprctl eval` 和 `repl` 都在。

代价记在这儿：现在 `pacman -Qu` 是 0，本地比快照新 300 多个包，`pacman -Syu` 什么都不做，Arch 侧的安全更新要等 4.5.1 重切快照。回滚包全在 `/var/cache/pacman/pkg`，一条 `pacman -U` 能退回 0.57.1。

## 4.5 动了哪几处

拿 `omarchy-settings` 两个版本的包文件列表对一下，450 个新增文件里和我有关的就几样：工作区从手写映射改成按显示器分区块（`monitor-workspaces.lua`），多了一个浮动工作区模式（`workspace-modes.lua`），多了个 Quake 控制台（`qconsole.lua`，占 `SUPER + grave`），标题栏拆成独立的 `omarchy-hyprland-titlebars`，另外 `bindings/tiling.lua` 多了 `SUPER + Q` 关窗。

## 一、工作区：删掉手写的 workspace_rule

旧配置里我按屏幕手写了工作区映射（1-7 主屏、8-11 副屏）。4.5 的 `monitor-workspaces.lua` 改成按显示器身份分配区块：一台显示器一个 block，block 内 10 个槽位。

```lua
block = (id - 1) // SLOTS                        -- SLOTS = 10
workspace_id(monitor, slot) = block * SLOTS + slot
```

主屏拿区块 0（工作区 1-10），副屏拿区块 1（11-20），映射结果存在 `~/.local/state/omarchy/monitor-workspaces` 里，拔插屏也记得住。

**手写规则会和它抢**，症状是 `SUPER + 数字` 在副屏上跳回主屏。所以那 11 行 `hl.workspace_rule` 全部删掉，只在注释里留结论：

```lua
-- 不再手工绑定「工作区 → 显示器」。
-- Omarchy 4.5 起每块显示器自带 10 个工作区的区块（主屏 1-10、副屏 11-20），
-- 手写 workspace_rule 会和它抢，反而让 Super+数字 在副屏上跳回主屏。
```

单屏机器上 11 号工作区能正常创建在主屏，只是没有 `SUPER + 11` 这种按键，要切换得点 bar 上的标签或者：

```bash
hyprctl dispatch "o.workspaces.focus(11)"
```

Lua 分支里 dispatch 的参数按 Lua 表达式解析，`hyprctl dispatch workspace 11` 会报 `'(' expected near '11'`。

## 二、浮动工作区：正好是我想要的那种

`workspace-modes.lua` 顶部注释写得很直白：Hyprland 只有 dwindle 和 scrolling 两种平铺布局，没法让整个工作区的窗口都浮动。omarchy 用 tag 补上这一层：

```lua
local WORKSPACE_TAG = "omarchy-floating-workspace"
```

声明一行就能用：

```lua
o.workspace_mode({ workspace = "11", mode = "floating" })
```

我本来就想把 QQ / 微信放成「一叠」，主窗口铺满、Alt+Tab 轮换。4.5 给了这个原语，剩下的是对抗它的开窗规则（`workspace-modes.lua` 会给新窗口套一条 `size = min(1100, .64w) x min(760, .68h)` 并二次摆放），细节写在另一篇里。

## 三、标题栏：先是白杠，后来干脆全关

装上 titlebars 之后浮动窗口多了一条栏。第一版问题不是「多了条栏」而是**那条栏是纯白的**：`workspace-modes.lua` 取色顺序是 `lighter_background → selection → background`，aura 的 `colors.toml` 没有 `lighter_background`，于是一路退到 `#f8f8f2`。

我先写了 `zz-titlebar-color.lua` 自己算颜色（aura 该是 `#44475a`）。用了一阵觉得这套桌面本来就是浮动堆叠、窗口叠着放，栏纯属占地方，于是删掉取色文件，改成整体关掉：

```lua
-- ~/.config/hypr/apps/zz-no-titlebars.lua
if hl.plugin.hyprbars then
  hl.config({ plugin = { hyprbars = { enabled = false } } })
end
```

插件有窗口级开关（`strings titlebars.so` 能看到 `hyprbars:no_bar`），omarchy 自己也是按 `plugin:hyprbars:workspace_tag` 只给浮动工作区加栏。但我不要这个桌面有任何一条栏，整体关更省事。

文件名必须带 `zz-`。omarchy 按字典序加载 `apps/` 下所有 `*.lua`，而 `workspace-modes.lua` 在切到浮动工作区时才 lazy-load hyprbars 并把 `enabled` 打开 —— 刚装完 `hyprctl plugin list` 显示 `no plugins loaded` 是正常的。要压住它，就得有一个在所有 app 文件之后执行的文件再覆盖一次，插件重载后 Hyprland 会重跑配置，本文件再执行一遍，所以反复切浮动工作区栏也不会回来。放 `apps/` 而不是 `looknfeel.lua` 也是这个原因。

验证：

```bash
hyprctl getoption plugin:hyprbars:enabled    # false
```

## 四、键位：三处冲突、三处补齐

4.5 的默认绑定多了 `SUPER + Q` 关窗，和原有的 `SUPER + W` 重复；Quake 控制台占了 `SUPER + grave`，我原来在这键上的自定义绑定只能让出去。

| 动作 | 处理 |
|---|---|
| `SUPER + Q` 关窗 | `hl.unbind`，保留 `SUPER + W` |
| `SUPER + grave` | 让给 Quake 控制台 |
| `ALT + TAB` / `ALT + SHIFT + TAB` | 绑定窗口循环 |
| `SUPER + ALT + TAB` / `+SHIFT` | 切显示器（原来是组切换 `group.next/prev`） |
| `CTRL + ALT + TAB` / `+SHIFT` | 组内窗口切换（和上面那组整体对调） |
| `CTRL + PRINT` | 静默截图，直接进剪贴板 |

Alt+Tab 这条有个必须的附加动作：

```lua
o.bind("ALT + TAB", "Next window", function()
    hl.dispatch(hl.dsp.window.cycle_next())
    hl.dispatch(hl.dsp.window.bring_to_top())
end)
```

只 `cycle_next` 的话焦点确实换到了下一层窗口，但它没被提到最前；鼠标一动，`follow_mouse` 立刻把焦点还回去，看起来像「按了没反应」。必须跟一个 `bring_to_top`。

改键之前先 `hl.unbind`：同一组键多绑会按注册顺序全部执行，不同写法（keysym 和 `code:49`）也都算注册。

## 五、输入：鼠标的假键盘接口 + 输入法切换键

EWEADN 鼠标（`a8a5:2255`）除了指点设备还暴露 3 个 HID 键盘接口，按键状态会卡住。Hyprland 用 `shareKeyFromAllKBs` 把所有键盘的按键状态合并成一份，一个卡住的条目就会让真键盘丢掉同键事件——症状是 **CapsLock 之后第一个键被吞**。

只关这三个接口，鼠标本体不受影响：

```lua
hl.device({ name = "yjx-chip-eweadn-e7", enabled = false })
hl.device({ name = "yjx-chip-eweadn-e7-keyboard", enabled = false })
hl.device({ name = "yjx-chip-eweadn-e7-1", enabled = false })
```

名字来自 `hyprctl devices` 里那几个多余的 keyboard 条目，别照抄。

另一处：4.5 的迁移把 fcitx5 的 `[Hotkey/TriggerKeys]` 改成了 `Zenkaku_Hankaku` / `Hangul`，中英切换直接失灵。加回 `2=Control+space` 恢复：

```ini
[Hotkey/TriggerKeys]
0=Zenkaku_Hankaku
1=Hangul
2=Control+space
```

## 六、显示器：采集卡热插拔后 X 侧 primary output 会悬空

主屏挂在 A/Vaux GC553G2 采集盒后面。采集盒每次重新协商 HDMI 都触发热插拔，xwayland 重建输出后，X 的 primary output 会指到空处，Wine / Proton 因此判不出主屏，游戏把 1080p 的副屏当成运行屏。

修法是一段带重试的脚本，挂在三个事件上：

```lua
local fix_x_primary = "bash " .. os.getenv("HOME") .. "/.config/hypr/apps/fix-x-primary.sh"

hl.on("monitor.added",          function() hl.dispatch(hl.dsp.exec_cmd(fix_x_primary)) end)
hl.on("monitor.removed",        function() hl.dispatch(hl.dsp.exec_cmd(fix_x_primary)) end)
hl.on("monitor.layout_changed", function() hl.dispatch(hl.dsp.exec_cmd(fix_x_primary)) end)
```

再加一条兜底：任何新窗口打开（典型是游戏启动）都再确认一次。脚本有快路径，primary 正常时直接退出，开销可忽略。

顺带在这轮里关掉了主屏的广色域。KTC H27T13 的 EDID 声明了 BT2020 + ST2084（面板规格按官方参数），但面板实际 8bit、输出恒为 XRGB8888，两者不一致会刷 `wide color gamut is enabled but the display is not in 10-bit mode` 警告；1440p@100 的像素时钟也已经顶满 EDID 声明的 400MHz TMDS 上限，物理上跑不了 10bit。

```lua
supports_wide_color = -1,   -- -1 强制按「不支持」处理
```

## 七、壁纸引擎：从 linux-wallpaperengine 换成 owe

不是「哪个更好」的问题，是这两个能力不重叠：这个构建的 LWE 没有链接 ffmpeg，**只能播 3D scene，播不了 mp4**；owe 只能播 mp4 / gif / 静图，播不了 `.pkg` 场景。我真正想要的是视频，所以换成 owe。

同一台机器、连续采样、Windows 应用开着，30–40 秒均值：

| 指标 | LWE 双场景 | owe 双屏 4K30 | 空载 |
|---|---|---|---|
| 进程 / 线程 | 2 / 36 | 1 渲染 + daemon / 20 | — |
| CPU | 4.30 % | 3.05 % | 0.20 % |
| RSS | 0.8–1.0 GB | 727 MB | — |
| GPU SM（进程级） | 5.1 % | 0.8 % | 0 % |
| 视频解码 | 无硬解，GL/CPU | NVDEC（dec 11 %） | — |
| 显存 | 3.6–3.9 GB | 2.3 GB | 2.5 GB |
| 整卡功耗 | 49–50 W | 38 W | 24 W |

账单上是省一半：功耗 −12 W、GPU SM −85 %、显存 −1.4 GB、CPU −1.3 pp。原因是 owe 用 `libmpv` 只解码一次、走 NVDEC、再画到多个输出；LWE 是每块屏一个 OpenGL 进程各自渲染。

用法也简单：`owe set <文件>` 只改 `~/.local/state/omarchy/current/background` 这个软链，`owed.service`（已 enable 到 `graphical-session.target`）登录后读它，不用写 autostart。静图时 owe 直接把图层还给 shell，`owe-render` 退出，额外开销为 0。laptop 上也换了，`hwdec: "vaapi"` 走核显，CPU 1.4–1.9 %。

代价：

- **全局只有一个背景源**，双屏必须同一个视频（LWE 能给每屏不同壁纸）；
- 切主题会重写 background 软链，得再 `owe set` 一次；
- 没有粒子 / 视差 / 鼠标交互，README 自述 experimental。

### 4K 源 + 截图 = 整个 compositor 崩掉

这条是这次付出代价最大的一条。owe 播 3840×2160 时执行一次 `grim -o HDMI-A-2`：

```text
CScreenshareManager::onOutputCommit → CScreenshareFrame::copyShm
  → CGLFramebuffer::readPixels → libnvidia-eglcore → SIGBUS / BUS_ADRERR
```

崩的是 Hyprland 自己，`xdg-desktop-portal-hyprland` 跟着从属崩溃，uwsm 以 `--safe-mode` 把桌面重启，所有窗口丢光。同一条命令在静图背景、LWE 场景下都没事 —— 唯一变量是 4K 视频层加 `readPixels`。

用 NVENC 转一版 1080p25（`scale=1920:1080,fps=25`，105 MB / 7.3 Mbps）之后，主屏 / 副屏 / 全屏交替截了 **79 次全部成功**（34 次快速 + 45 次浸泡），Hyprland pid 全程未变，`coredumpctl` 无记录。**触发条件是帧尺寸 3840×2160，不是帧率**，分数缩放不是变量。

降分辨率几乎不省电：4K 净增 17.4 W，1080p 净增 16.1 W，只差 1.3 W（7 %）。省下来的是 CPU（−27 %）和显存（−520 MB）。所以这是「不崩」的问题，不是省电的问题。

## 八、壁纸选择器：只显示 12 个，和一张被写坏的缓存

4.5 的背景选择器（左键双击桌面空白）只扫两个目录：

```bash
# /usr/bin/omarchy-theme-bg-switcher
omarchy-menu-images --selected "$current" \
  "$HOME/.local/state/omarchy/current/theme/backgrounds" \
  "$HOME/.config/omarchy/backgrounds/$theme_name"
```

`omarchy-menu-images` 用 `find -L`（跟随软链），认 jpg/png/webp/gif + mp4/m4v/mov/webm/mkv/avi。所以正确做法是给视频建软链，不复制那 12 GB。我建了 12 条，指向 `~/Videos/Wallpapers/` 下的文件。

那 12 条命名对得挺费劲：工坊目录里 `project.json` 只有 ID，视频文件在本地是我自己命名的。可靠做法是拿 `project.json` 的**字节数**去 `~/Videos/Wallpapers/` 找同大小的候选，再 `cmp` 逐字节确认。**只按文件名匹配会错**——`合成 1_1.mp4`（114 MB）和 `合成 1_1 (2).mp4`（58 MB）是两部不同的片。

选择器里的名字就是文件名：去扩展名，`-` 和 `_` 换成空格，每个词首字母大写（`ImagePickerModel.js` 的 `labelForPath`），**数字前缀不会被剥**。所以软链名带工坊 ID 的话，列表里就跟着显示 ID——`3016818296-绮良良 KIRARA 2K` 会原样出现。要按中文名搜得到，软链名就写干净。

**列表整个空了**那次是我自己写坏的缓存：

```bash
T=$(cat ~/.local/state/omarchy/current/theme.name)
omarchy-menu-images --print-rows ~/.local/state/omarchy/current/theme/backgrounds \
  ~/.config/omarchy/backgrounds/$T
```

输出为空说明枚举缓存有问题，和软链无关。`~/.cache/omarchy/image-selector/` 下同一 key 有 `.rows` / `.signature` / `.fast-signature` 三个文件；当时 `.fast-signature` 1876 B、列了 12 个 mp4，`.signature` 只有 137 B、一行文件都没有，`.rows` 是 **0 字节** —— 打开时签名比对通过，读到空 rows，界面就是空的。删掉该 key 的三个文件，在宿主会话里重跑一次，得到 25 行（13 图 + 12 视频），二次运行 0.145 s 命中缓存。

> 不要在受限环境里跑这类会写缓存和状态的 omarchy 命令。枚举依赖进程替换 `<(...)`，没有 `/dev/fd` 时跑不起来，却仍会把空结果当合法缓存发布出去 —— 那张坏缓存就是这么来的。

## 九、顺手挪的两处

- `apps/steam.lua`：Steam 从 11 区挪到 12 区，11 区只留给 QQ / 微信。
- `fcitx5` 的 `EnumerateGroupForwardKeys` 保持 `Super+space`，不动。

## 验证清单

```bash
hyprctl reload                                   # 输出里不应有 error
hyprctl configerrors                             # 空
hyprctl getoption plugin:hyprbars:enabled        # false
hyprctl binds | grep "SUPER + Q"                 # 应该没有
hyprctl monitors                                 # 主屏 scale 1.6667、副屏 1，无 WCG 警告
pacman -Qu                                       # 0（指着冻结快照）
owe render-status                                # swap_failures 0，time_pos 在涨
omarchy-menu-images --print-rows ...             # 行数非空
```

## 踩坑

| 症状 | 原因 |
|---|---|
| `omarchy update` 卡在迁移不动 | titlebars 钉 `hyprland=0.56.2`，本地 0.57.1 不满足，队列硬停；换镜像不解决，pacman 不会自动降级 |
| `SUPER + 数字` 在副屏跳回主屏 | 手写 `workspace_rule` 与 4.5 的区块制互相覆盖 |
| 浮动窗口标题栏又回来了 | `zz-no-titlebars.lua` 改名或没放 `apps/`，加载顺序被 hyprbars 反超 |
| titlebar 是一条白杠 | aura 没有 `lighter_background`，取色退到 `#f8f8f2` |
| 按 Alt+Tab 像没反应 | 只 `cycle_next` 没 `bring_to_top`，`follow_mouse` 把焦点还回去了 |
| CapsLock 之后第一个键被吞 | 鼠标的假键盘接口状态卡住，`shareKeyFromAllKBs` 把它合进了所有键盘 |
| 中英切换失灵 | 4.5 迁移把 fcitx5 的 TriggerKeys 改成了 Zenkaku/Hangul |
| 微信「启动不了」 | 进程都在，窗口 `at=3892,350` 落在副屏逻辑范围外，`hl.dsp.window.move` 移回来即可 |
| 截图直接把桌面打死 | 4K 视频层 + `readPixels` → `libnvidia-eglcore` SIGBUS；换 1080p 源 |
| `hyprctl dispatch workspace 11` 报 Lua 语法错 | dispatch 参数按 Lua 表达式解析，改用 `o.workspaces.focus(11)` |

还有一条不是配置问题、但代价最大：**别在活会话里用未文档化的 `hl.dsp.*` 试探参数**。为了突破微信那个尺寸上限，我发了一次 `hl.dsp.window.set_prop({prop="max_size", ...})`，Hyprland 0.56.2 的 Lua 分支对它直接 `throw` C++ 异常，穿透 `guardedPCall` 的 `lua_pcall` 到 `std::terminate`，整个 compositor 注销，窗口连同正在跑的游戏一起没了。**`pcall` 挡不住 `hl.dsp.*` 里的 C++ throw。** 新原语先用 `hyprctl eval` 打印成员，或者查文档。

## 收尾

这一堆改动最后回写进了 chezmoi（模板按 hostname 分支、脚本一并纳管），本机 `chezmoi status` 为空，另一台 `apply` 完就是同一套环境。

重点是上面这些：4.5 换了工作区模型、加了浮动工作区和 Quake 控制台、拆出标题栏包，再加上那条被钉依赖卡住的迁移队列，每一条都和我原来的习惯有冲突。逐条调完，这套桌面才回到顺手的样子。
