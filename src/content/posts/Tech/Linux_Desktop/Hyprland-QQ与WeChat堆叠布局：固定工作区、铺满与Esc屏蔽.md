---
title: Hyprland QQ 与 WeChat 堆叠布局：固定工作区、铺满与 Esc 屏蔽
published: 2026-08-19
updated: 2026-11-08
pinned: false
description: 把 QQ / 微信调成贴合自己习惯的样子：固定进 11 号浮动工作区、主窗口铺满、弹窗跟着鼠标、Esc 不再误关窗口、Alt+Tab 来回轮换
tags: [Linux, Desktop, Hyprland]
category: Desktop
author: Hyperbola
draft: false
---

# Hyprland QQ 与 WeChat 堆叠布局：固定工作区、铺满与 Esc 屏蔽

## 我要的效果

QQ 和微信在平铺布局里一直很别扭：主窗口被切成一格一格的，聊天记录弹窗冒在角落，误按 `Esc` 直接把窗口关了。所以给这两个应用单独定了一套规矩：

1. **固定工作区** —— 永远待在 11 号工作区，不和别的窗口混；
2. **堆叠式** —— 该工作区用浮动布局，窗口互相重叠、自由摆放，不是平铺格子；
3. **铺满** —— 主窗口自动铺满所在显示器的可用区域（扣掉 bar）；
4. **弹窗跟随鼠标** —— 资料卡、图片查看器、聊天记录在光标旁边冒出来，不挡光标，贴边时自动翻到另一侧；
5. **Alt+Tab 轮换** —— 在这一叠窗口之间来回切，切到谁谁置顶；
6. **Esc 屏蔽** —— 只有主窗口的 `Esc` 被吞掉，子窗口保持原样。

布局逻辑全在 `~/.config/hypr/apps/qq_wechat.lua`，619 行，没有外部进程（Alt+Tab 那条在 `bindings.lua`）。

## 环境

```text
Hyprland 0.56.2（Lua 配置分支）
omarchy 4.5.0-3
配置入口 ~/.config/hypr/hyprland.lua
```

omarchy 在 `hyprland.lua` 末尾统一加载 `apps/` 下的所有 `.lua`：

```lua
local require_all = require("default.hypr.require_all")
require_all.files(os.getenv("HOME") .. "/.config/hypr/apps", "hypr.apps")
```

实现是 `find ... -name '*.lua' | sort`，**按文件名字典序**，所以同目录的 `zz-no-titlebars.lua` 一定最后执行。

## 一、固定工作区 + 堆叠式

omarchy 4.5 才有的浮动工作区原语：

```lua
o.workspace_mode({ workspace = "11", mode = "floating" })
```

它内部给工作区和窗口打 `omarchy-floating-workspace` 标签，Hyprland 本身没有「整块工作区浮动」这种布局。

### 还要躲开它的开窗规则

`workspace-modes.lua` 会给浮动工作区的新窗口套一条开窗规则（`size = min(1100, .64w) x min(760, .68h)`，居中 + 级联偏移），并在 `window.open` 里再 `arrange()`/`place()` 摆一次。主窗口要铺满，就得让它放过这两个 class：

```lua
for class in pairs(MAIN_TITLES) do
    o.window("^(" .. class .. ")$", { float = true, workspace = SOCIAL_WORKSPACE })
end
```

`o.window` 声明 float 会把 class 记进 `o.floating_apps`，`workspace-modes` 重新生成规则时会自动排除它们。用 `hl.window_rule` 写 `float = true` 没有这个副作用，所以两种写法不能互换。

### 主窗口规则里不要写 size / move

```lua
hl.window_rule({
    match = { class = "^(" .. class .. ")$", title = title },
    workspace = SOCIAL_WORKSPACE,
    float = true,
    no_initial_focus = true,
    suppress_event = "activate",
})
```

这条规则只负责「进 11 号 + 浮动 + 不抢焦点」。**定位完全交给后面的 dispatcher**，原因见下面踩坑第一条：规则里的 `move` 实测是相对偏移语义，窗口先被居中到 `x=1728`，规则再加 1536，直接变成 3264 飞出副屏。

## 二、铺满：算的是逻辑像素

`window` 的 `at`/`size` 是**逻辑像素**，`monitor.width/height` 是**物理像素**，缩放屏上差一个 `scale`。主屏 2560×1440@scale 1.67，逻辑就是 1536×864——直接拿物理值当窗口尺寸会大出 1.67 倍。

```lua
local function monitor_area(mon)
    if not mon then return nil end
    local ok, area = pcall(function()
        local scale = mon.scale or 1
        if scale <= 0 then scale = 1 end
        local w, h = mon.width or 0, mon.height or 0
        if (mon.transform or 0) % 2 == 1 then   -- 竖屏交换宽高
            w, h = h, w
        end
        local r = mon.reserved or {}            -- 扣掉 bar
        local l, t = r.left or 0, r.top or 0
        local rr, b = r.right or 0, r.bottom or 0
        local pos = mon.position or {}
        local lw = math.floor(w / scale - l - rr)
        local lh = math.floor(h / scale - t - b)
        if lw < 100 or lh < 100 then return nil end
        return { x = math.floor((pos.x or 0) + l), y = math.floor((pos.y or 0) + t), w = lw, h = lh }
    end)
    if ok then return area end
    return nil
end
```

### 区域取不到时的回退链

`area_for(win)` 逐级退：窗口所在显示器 → 11 号工作区的显示器 → 光标所在显示器 → 活动工作区的显示器。任何一级算不出可用区域都继续往下退，保证总有地方能落。

### resize 再 move

`resize` **以中心为锚**，先 move 再 resize 位置就漂了。

```lua
if nw ~= w or nh ~= h then
    hl.dispatch(hl.dsp.window.resize({ x = nw, y = nh, relative = false, window = addr }))
end
-- 再挪，并夹进区域内
local nx = math.max(a.x, math.min(x, a.x + a.w - nw))
```

### 铺完要回读

应用不一定听话。微信有一个尺寸上限，逐轴量出来是 x≤1536、y≤864 —— 1700×800 被削成 1536×800，1200×1000 被削成 1200×864，正好等于主屏逻辑分辨率。它不在 Hyprland 的 maxsize/minsize 里（配置里没有这两个），`unset` 之后上限仍在，手动拖拽反而能突破。所以铺完 450ms 回读实际几何：

- 达标（差 2px 以内）→ 结束；
- 不达标 → 重试，最多 3 次、间隔 900ms；
- 还是不行 → 居中放着，别歪在左上角。

另外在窗口开出来后 250ms 和 1200ms 各再补一刀，因为 `workspace-modes` 的 `place()` 和应用自己的初始化都可能在首次铺满之后改几何。

> 这个 1536×864 的上限目前**没有根治**。全屏时看门狗不碰它（全屏尺寸由 Hyprland 自己管），手动拖拽路径也不检查尺寸，`set_prop max_size` 无用且危险（见踩坑）。现在的策略就是回读 + 重试 + 居中兜底。

## 三、弹窗跟随鼠标

判据只有一条：标题匹配主窗口正则的是主窗口，其余都算弹窗。

```lua
local MAIN_TITLES = { QQ = "^QQ$", wechat = "^微信$" }
```

```lua
hl.on("window.open", function(win)
    if not win or not win.class or not MAIN_TITLES[win.class] or not win.address then return end
    if (win.title or ""):match(MAIN_TITLES[win.class]) then
        fill_main(win, "open")
        fill_main_later(win, "open")
        return
    end
    place_popup(win, "open")
end)
```

弹窗定位：先强制 `float on`（平铺窗口的 `move` 是空操作），再按光标位置挑一个方向放下。

固定方向不行：光标本来就在左下角时，「放到左下」会把窗口顶出屏幕，只剩贴边挤进来这一种结局。所以改成四象限择一：按「右下 → 左下 → 右上 → 左上」找第一个**能完整放下、并且不盖住光标**的位置，间距 12px。

```lua
local CURSOR_GAP = 12

local function popup_origin(a, w, h, px, py)
    local cands = {
        { px + CURSOR_GAP, py + CURSOR_GAP },
        { px - w - CURSOR_GAP, py + CURSOR_GAP },
        { px + CURSOR_GAP, py - h - CURSOR_GAP },
        { px - w - CURSOR_GAP, py - h - CURSOR_GAP },
    }
    for _, c in ipairs(cands) do
        if c[1] >= a.x and c[2] >= a.y
            and c[1] + w <= a.x + a.w and c[2] + h <= a.y + a.h then
            return c[1], c[2]
        end
    end
    -- 四个方向都放不下：取夹取偏移最小的那个，交给 fit_into 收回屏幕内
    local bx, by, bd
    for _, c in ipairs(cands) do
        local x = math.max(a.x, math.min(c[1], a.x + a.w - w))
        local y = math.max(a.y, math.min(c[2], a.y + a.h - h))
        local d = math.abs(x - c[1]) + math.abs(y - c[2])
        if not bd or d < bd then bx, by, bd = x, y, d end
    end
    return bx, by
end
```

窗口比屏幕还大时先按可用区域裁到最大，再挑方向。光标落在别的显示器上时直接居中，不硬贴到弹窗所在屏幕的边缘。

按副屏可用区 `1536,26 / 1920×1054` 逐个象限试：资料卡 322×420 在左下、右下、右上三个角分别落到右上、左上、左下；图片查看器 900×700 贴近右下角时翻到左上方。所有用例都完整落在区域内，光标一次没被盖住——只有「窗口和可用区一样大」那条例外，光标必然落在窗口里，躲不开。

微信有个坑：登录窗和主窗**同一个 class、标题也可能一直是「微信」**，登录完成后窗口重新 map 一次标题才变。所以还挂了 `window.title` 事件兜底——之前被当成弹窗处理过的窗口（记录在 `opened_social[addr]`），标题变成主窗口时补一次铺满；已经处理过主窗口的则直接跳过，避免重复 dispatch。

## 四、Esc 屏蔽

```lua
local escape_blocked = { QQ = "QQ", wechat = "微信" }
local esc_blocked = false

local function on_active_window_changed()
    local win = hl.get_active_window()
    local block = win ~= nil and escape_blocked[win.class] == win.title
    if block == esc_blocked then return end      -- 只在翻转时动绑定
    esc_blocked = block
    if block then
        hl.bind("Escape", hl.dsp.no_op(), { replace = true })
    else
        hl.unbind("Escape")
    end
end
hl.on("window.active", on_active_window_changed)
on_active_window_changed()
```

三个细节：

- **只在状态翻转时改绑定。** Hyprland 不去重，每次 `window.active` 都无脑 `hl.bind` 会堆出几十条同样的绑定；
- **加载时先跑一次。** 否则重载配置后必须先切一次窗口才生效；
- **解绑不能传 `nil`。** `hl.bind("Escape", nil)` 无效，必须 `hl.unbind("Escape")`。

## 五、Alt+Tab 在这套布局里的作用

堆叠布局的窗口互相遮挡，靠鼠标点不到下面那层，所以 Alt+Tab 是主力操作。它不在 `qq_wechat.lua` 里，而在 `bindings.lua`：

```lua
o.bind("ALT + TAB", "Next window", function()
    hl.dispatch(hl.dsp.window.cycle_next())
    hl.dispatch(hl.dsp.window.bring_to_top())
end)

o.bind("ALT + SHIFT + TAB", "Previous window", function()
    hl.dispatch(hl.dsp.window.cycle_next({ next = false }))
    hl.dispatch(hl.dsp.window.bring_to_top())
end)
```

**`bring_to_top` 不是可选项。** 只 `cycle_next` 的话焦点确实换到了下一层窗口，但它没被提到最前；鼠标一动，`follow_mouse` 立刻把焦点还回去，用起来就像「按了没反应」。

相关三组键各管一层，别混：

| 快捷键 | 作用 |
|---|---|
| `ALT + TAB` | 当前工作区内轮换 |
| `CTRL + ALT + TAB` | 只在当前**窗口组**内轮换，不动层级 |
| `SUPER + ALT + TAB` | 焦点切到下一块显示器 |

## 六、显示器变化后自己爬回来

副屏掉线时 Hyprland 会把 11 号工作区的窗口搬到别的工作区，还会缩成奇怪的尺寸。

```lua
for _, ev in ipairs({ "monitor.added", "monitor.removed", "monitor.layout_changed" }) do
    hl.on(ev, function()
        last_sig = layout_sig()
        rescue_later(ev)      -- 400ms / 1500ms 两次
    end)
end
```

两个必须注意的点：

- 判断「窗口是否在 11 号」时，工作区的 `name` 和 `id` 各试一遍；
- **重铺要用工作区的显示器算区域，不能信 `win.monitor`**。窗口刚被搬回 11 号时这个字段还是旧值。

搬的时候决定跟不跟着切视图：窗口正显示在眼前就 `follow`，否则静默搬走。

### 看门狗：事件会漏

`monitor.*` 事件不是 100% 送达，兜底是每秒一次的巡检，用「布局指纹」判断屏幕结构变没变：

```lua
local function layout_sig()
    local parts = {}
    for _, m in ipairs(hl.get_monitors()) do
        local a = monitor_area(m)
        parts[#parts + 1] = a and (m.name .. "@" .. a.x .. "," .. a.y .. ":" .. a.w .. "x" .. a.h)
                               or (m.name .. "@off")
    end
    table.sort(parts)
    return table.concat(parts, "|")
end
```

用指纹而不是「记住窗口上次多大」，是因为屏幕一变 Hyprland 自己也会搬窗口、改尺寸，窗口的几何完全不可信。

同一个看门狗还顺手纠正越界窗口，但**只巡检主窗口**（`is_main_window(w)` 才进 `ensure_inside`，弹窗从不动），而且只处理跑出屏幕的，正常大小的小窗口一律不碰，免得和手动摆放打架。必须连续两次看到同一个越界坐标才动手——移动有动画，中间帧也在界外，逐帧纠正会和动画互拉，窗口发颤。全屏窗口直接跳过。

## 七、微信子窗口去装饰

```lua
local wechat_exclude = "朋友圈|设置|聊天文件|预览|图片和视频"
hl.window_rule({
    match = { class = "^(wechat)$", title = "negative:^(" .. wechat_exclude .. ")\\W*" },
    no_blur = true, border_size = 0, no_shadow = true,
})

hl.window_rule({
    match = { class = "^wechat$", title = "^微信发送给$" },
    no_blur = true, border_size = 0, no_shadow = true,
})
```

用 `negative:` 排除需要完整界面的子窗口，比白名单省事——微信新增一个弹窗标题时不会漏。

## 踩坑

### `move` 的判据是窗口当前位置，不是目标坐标

`fit_into()` 收尾那句「没被夹取就不 move」，省的是重复 dispatch。但拿来跟夹取结果比对的必须是**窗口现在在哪**：

```lua
local function fit_into(addr, a, w, h, x, y, cx, cy)  -- cx/cy = 窗口当前左上角
    local nx = math.max(a.x, math.min(x, a.x + a.w - nw))
    local ny = math.max(a.y, math.min(y, a.y + a.h - nh))
    if nx ~= cx or ny ~= cy then
        hl.dispatch(hl.dsp.window.move({ x = nx, y = ny, window = addr }))
    end
end
```

换成跟目标 `x, y` 比，只要目标本身在屏幕内、不需要夹取，两个值就恒等，move 一次都不会发出去——窗口停在应用自己给的位置上（QQ 的弹窗默认冒在屏幕正中），日志里却老老实实记着算出来的坐标。

### 窗口规则里的 `move` 是相对偏移

```lua
-- 不要这样定位
hl.window_rule({ match = {...}, move = "100 100", size = "800 600" })
```

实测窗口会先被居中，再叠加偏移，最后飞出屏幕。定位一律用 `hl.dsp.window.move` 给绝对坐标。

### dispatch 的 window 参数要 `address:` 前缀

裸地址字符串 `"0x55d6..."` 会被**静默忽略**（还返回 ok，更坑），必须 `"address:0x..."`，或者直接传窗口对象。

### `pcall` 挡不住 `hl.dsp.*` 里的 C++ 异常

试图用 `set_prop` 改 `max_size` 突破微信尺寸上限，Hyprland 0.56.2 的 Lua 分支直接 `throw`，穿透 `pcall` 到 `std::terminate`，整个 compositor 注销——所有窗口连同正在跑的游戏一起没了。

**任何参数形态不对的 dispatch 都可能等于一次强制注销。** 新原语先用 `hyprctl eval` 打印成员或查文档，别在活会话里试探。

### `hyprctl dispatch workspace 11` 会报 Lua 语法错

dispatch 参数按 Lua 表达式解析：

```bash
hyprctl dispatch workspace 11               # '(' expected near '11'
hyprctl dispatch "o.workspaces.focus(11)"   # ok
```

单屏上 11 号完全能创建，只是要这么切。

### 调试日志记得关

文件里留了 `log_place()`，每次铺位/校验都往文件追加一行，排障很好用（谁触发的、算出的区域、实际几何、重试几次），但每次事件都 `io.open`。稳定之后应改成开关或删掉。

## 验证

```bash
hyprctl reload
```

1. 打开 QQ / 微信，主窗口应铺满 11 号工作区所在显示器的可用区域（含 bar 预留）；
2. 打开聊天记录、图片查看器，弹窗出现在光标旁边、光标露在外面；把光标移到屏幕右下角再点一次，弹窗翻到左上方且完整可见；
3. 聚焦主窗口按 `Esc`，窗口不关；聚焦子窗口，`Esc` 保持应用自身行为；
4. `Alt+Tab` 在 11 号工作区里的两个窗口之间来回切，每次切到的窗口都应在最前，且鼠标不动时焦点不回弹；
5. 反复切十几轮后 `hyprctl binds | grep -c Escape` 不应增长；
6. 拔掉副屏，QQ / 微信主窗口应被收回主屏并重新铺满。

## 总结

这套配置全部是从使用习惯倒推出来的：

| 习惯 | 实现 |
|---|---|
| QQ / 微信不跟代码混在一起 | 固定 11 号工作区 |
| 窗口叠着放，不做格子 | 浮动工作区 + `o.floating_apps` 排除 |
| 主窗口必须占满 | 自己算逻辑像素区域，resize→move→回读重试 |
| 弹窗别跑偏 | 四象限落位 + 夹进可用区域 |
| 切窗口不留残影 | `cycle_next` + `bring_to_top` |
| 误按 Esc 不关窗 | 只在主窗口状态翻转时替换绑定 |

代价是它绑死了 Hyprland Lua API 的细节：`monitor.reserved` 的结构、`dsp.window.move` 的坐标语义、事件名。升级后先 `hyprctl reload` 看日志，再跑一遍上面的验证清单。
