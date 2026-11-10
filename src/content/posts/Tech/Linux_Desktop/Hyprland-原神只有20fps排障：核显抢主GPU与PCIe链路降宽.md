---
title: 原神只有 20fps：一次四次假设被推翻的排障过程
published: 2026-11-07
updated: 2026-11-07
pinned: false
description: 从 19fps 到 103fps 的过程记录。依次怀疑过 PCIe 降宽、调试日志、异常风暴，全部被数据推翻；转折点是重启后问题自行消失，逼着去对比"好状态"和"坏状态"的现场差异，最终定位到 Hyprland 合成器被核显接管。附 PCIe x2 恢复 x16 的金手指接触问题
tags: [Linux, Omarchy, Hyprland, 原神, PCIe, Debugging]
category: Desktop
author: Hyperbola
draft: false
---

> 环境: Omarchy(Arch + Hyprland 0.57 + Aquamarine 0.15), AMD Ryzen 9 9950X(含 Radeon 610M 核显), NVIDIA RTX 4070 SUPER, 主板 B650M AORUS PRO AX, 双显示器(2560x1440@100 接 N 卡 / 1920x1080@75 接核显), DWProton 11.0 + DXVK 3.1.1

## 起因

原神跑在 19fps。降画质没用，重启游戏没用，稳定地卡在 20 附近。

这篇按时间顺序记录排查过程，包括三次走错的弯路。结论本身很简单，但**错的那几次更有参考价值**——它们每一个看上去都比真相更合理。

## 第一轮：先把现象量化，别急着猜

第一件事不是想原因，是拿到能证伪的数字。MangoHud 的 HUD 用截图取：

```bash
grim -o HDMI-A-1 ~/shot.png
magick ~/shot.png -crop 380x150+0+0 +repage -resize 400% ~/hud.png
```

```
FPS 19      Frametime 52.5 ms      min: 51.4 ms   max: 70.7 ms
GPU0  34%   DXVK                      CPU  9%
```

三个数字摆在一起信息量很大：

- **帧时下限 51.4ms**。算力不够的场景，帧时会有长有短；所有帧都挤在 51–53ms，说明它在**等**，不是算不完。
- **GPU 功耗 30–42W**（这卡满载 220W）。它在空转。
- 整机 CPU 9%，但 `dxvk-submit` 单线程 **106%**。

最后这条最刺眼：`dxvk-submit` 是 DXVK 负责提交命令、等待 present 的线程。它吃满一个核在自旋，而 GPU 闲着——这是"在等显示管线"的典型签名，不是渲染瓶颈。

**这一轮先踩了个坑。** `hyprctl clients` 报窗口尺寸 `1536x864`，我据此认为"游戏只渲染 864p，GPU 不该慢"。错了：Hyprland 报的是**逻辑尺寸**，该输出 scale 是 1.6667，真实缓冲是 `1536×1.6667 = 2560`、`864×1.6667 = 1440`。游戏一直是原生 1440p。判断渲染分辨率别信窗口尺寸，看游戏自己的配置或 GPU 负载。

## 第二轮：盯上 PCIe x2

顺手查链路，发现一个明显不对的：

```bash
cat /sys/bus/pci/devices/0000:01:00.0/current_link_width   # 2
cat /sys/bus/pci/devices/0000:01:00.0/max_link_width       # 16
```

内核启动日志说得很直接：

```
pci 0000:01:00.0: 31.506 Gb/s available PCIe bandwidth,
limited by 16.0 GT/s PCIe x2 link at 0000:00:01.1
(capable of 252.048 Gb/s with 16.0 GT/s PCIe x16 link)
```

写了个 20 行的 CUDA 程序实测（源码见附录）：

```
H2D: 2.92 GB/s
D2H: 3.02 GB/s
```

正常 x16 应该是 24–26 GB/s。**只有 1/8。**

为了排除"空闲省电"和"BIOS 分叉"两种可能，又查了三条：

- AER 计数器全 0（`RxErr / BadTLP / BadDLLP / Timeout`）——不是信号边缘化导致的持续重训
- PCI 拓扑 `01.1-[01]--+-00.0(显卡) \-00.1(音频)`，根端口下只有显卡，没有分叉给别的设备
- 满载 100%、220W 时连续采样，宽度**恒为 2**

于是写下结论：**lane 2–15 从未训练成功，物理接触问题。**

这个结论本身没错（后面验证了），但当时我把它当成了 20fps 的答案。**这是第一次误判。**

## 第三轮：重启之后，问题自己消失了

中间重启了一次机器。再进游戏：

```
FPS 103     Frametime 9.7 ms     GPU 51%     功耗 100W
```

同一份配置、同一个 1440p、**PCIe 还是 x2**。

这一下把上一轮的结论掀翻了：如果 x2 是病因，重启不会治好它。所以这不是配置问题，是**那一次会话进了一个坏状态**。

到这里方向才真正转对——从"找哪个参数不对"变成"找两种状态的差别"。

## 第四轮：想在日志里挖出坏状态的特征

启动脚本里有这么一段：

```bash
PROTON_LOG=1 MANGOHUD=1 "$PROTON" run "$GAME"
```

且 `WINEDEBUG` 带着 `+seh,+unwind,+debugstr,+loaddll`。日志 6 分钟写了 40MB。

看着非常像元凶：`+unwind` 对每次异常打印完整栈展开，Wine 每条 trace 是一次无缓冲 `write()`，一次异常几十行 → 每帧几十次写 → 卡住渲染线程。**这是第二次误判。**

两条数据把它推翻：

第一，翻老日志的头部：

```
===== steam-3541645069-dwproton-11.0-15.log =====
WINEDEBUG: +timestamp,+pid,+tid,+seh,+unwind,+threadname,+debugstr,+loaddll,+mscoree
```

11 月 2 日那次会话的 `WINEDEBUG` **一模一样**，而那次完全正常。同一份配置不能既正常又卡。

第二，实测日志文件上的小写入延迟：

```python
fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
for i in range(500):
    os.write(fd, b"x" * 70)
```

```
500 次 70 字节写入: 中位 0.000 ms   90分位 0.000 ms   最大 0.012 ms
```

**0 毫秒**，全走页缓存。日志的成本只是文本格式化，量级远不够撑起那 33ms 的缺口。

不过两份日志确实有个 13 倍的差异：

| | 卡顿会话(1528s) | 正常会话(99500s) |
|---|---|---|
| 栈展开次数 | 67,880 → **44 次/秒** | 338,119 → **3 次/秒** |
| `0x40010006` | 1.29 次/秒 | 0.10 次/秒 |
| `0x4001000A` | 1.26 次/秒 | 0.10 次/秒 |

`0x40010006` / `0x4001000A` 就是 `OutputDebugStringA/W` 抛的调试打印异常。那次会话里游戏在疯狂打调试字符串。

但按实测延迟折算，这部分只值 **~6ms/帧**，填不满 33ms。**这是第三次误判**——它只能算坏状态的一个伴随现象，不是主因。

三个假设全废。到这一步该承认：**从日志里挖不出答案了，得抓到坏状态的现场。**

## 第五轮：抓到第二次复现

等了几个小时，又一次重启后它复现了。这次没再猜，直接看合成器用了哪块显卡：

```bash
grep -a "becomes primary\|Renderer:\|Vendor:" /run/user/1000/hypr/*/hyprland.log
```

正常时：

```
drm: gpu /dev/dri/card1 becomes primary drm      ← card1 = 0000:01:00.0 = N 卡
Vendor: NVIDIA Corporation
Renderer: NVIDIA GeForce RTX 4070 SUPER
```

出问题时：

```
drm: gpu /dev/dri/card2 becomes primary drm      ← card2 = 0000:11:00.0 = Radeon 610M
Vendor: AMD
Using: OpenGL ES 3.2 Mesa 26.2.6
Renderer: AMD Ryzen 9 9950X (radeonsi, raphael_mendocino)
```

**合成器被核显接管了。** 而游戏窗口在接 N 卡的那块屏上，于是每一帧都要跨 GPU 搬过去合成再输出。

因果链终于闭合，而且能解释全部现象：

- 游戏 18ms 画完，卡在等 present → 帧时锁死在 ~51ms
- GPU 只剩 35W → 在等，不是在算
- `dxvk-submit` 吃满一个核 → 自旋等合成器放行
- 降分辨率没用 → 卡的是像素的搬运和合成，不是渲染量
- 重启就好 → 合成器的选择只在启动时做一次

还有个现场佐证：那次游戏都没开，桌面空转，N 卡却是 **39% 占用、12W 功耗**——它在持续接收核显合成好的画面。

## 第六轮：为什么会被选错

我先后猜过两个错误的解释：一是"DRM 枚举顺序随机"，二是"卡号最大者优先"。两个都站不住——被纠正后才知道真实规则是：

> **Aquamarine 在启动时，看哪块 GPU 有在线显示器，就把它设为 primary。**

那次主显示器上电比系统启动慢，合成器初始化时只有接在核显上的副屏在线，核显自然成了主 GPU；等主屏亮起来，已经晚了。

要钉死的话：

```bash
# ~/.config/environment.d/10-gpu.conf
AQ_DRM_DEVICES=/dev/dri/by-path/pci-0000:01:00.0-card:/dev/dri/by-path/pci-0000:11:00.0-card
```

第一个路径即 primary。用 `by-path` 而不是 `card1/card2`，因为 DRM 卡号会在不同启动之间变（本机见过 `card0=核显` 和 `card2=核显` 两种编号）。

要确认 `environment.d` 真的被会话导入，不必猜——查一个已知来自那里的变量：

```bash
tr "\0" "\n" < /proc/$(pgrep -x Hyprland)/environ | grep INPUT_METHOD
# INPUT_METHOD=fcitx        ← 来自 ~/.config/environment.d/fcitx.conf，说明这条路有效
```

## 第七轮：回头收拾被搁置的 x2

20fps 的谜解开了，但第二轮的 x2 是真缺陷，不是幻觉。拆机擦金手指：

- 擦一次：**x2 → x4**
- 彻底擦拭 + 清理插槽灰尘：**x4 → x16**

| 项目 | x2 | x4 | x16 |
|---|---|---|---|
| CUDA H2D / D2H | 2.92 / 3.02 GB/s | 5.83 / 6.39 GB/s | **26.22 / 26.40 GB/s** |
| 满载(220W)宽度 | 2 | 4 | **16，恒定** |
| AER 可纠正错误 | 0 | 0 | **0** |

x16 下 26.2 GB/s 对理论值 31.5 GB/s，效率约 83%，正常水平。重启 3 次后复查每次都是 `16`——**稳定性比单次测量更有说服力**：接触不良的特征是训练结果跳变，始终停在一个偏低值不动反而更像硬损伤或虚焊。

顺手也证伪了一个流传的说法——"x4 是显卡休眠机制，高负载会自己升到 x8/x16"：

```
4, 4, 100 %, 194.25 W
4, 4, 100 %, 195.26 W      ← CUDA 满载 196W，宽度纹丝不动
```

随负载变的是**代数**（空闲 Gen2 5.0 GT/s ↔ 满载 Gen4 16.0 GT/s），**宽度不变**。宽度就是训练结果。

这一轮又错了一次：中途看到 BIOS 从 FB8 刷到了 FC3、宽度同时从 2 变 4，我把功劳记给了固件，还专门"修正"了自己原本正确的判断。实际变量是擦了金手指，BIOS 从头到尾没参与。

> 归因之前先问清楚：这段时间里动过什么硬件。

## 复盘：四次假设，三次错

| 假设 | 当时的支持证据 | 推翻它的证据 | 教训 |
|---|---|---|---|
| PCIe x2 导致 20fps | 带宽只有 1/8，异常得离谱 | 重启后同一 x2 链路跑出 103fps | 异常项 ≠ 根因 |
| 调试日志开销过大 | `+unwind` + 6 分钟 40MB | 老日志同款 WINEDEBUG 却正常；实测写入 0.000ms | 用字节数估延迟是错的直觉 |
| 异常风暴 | 异常频率高 13 倍 | 折算只有 6ms/帧，填不满 33ms | 量级对不上就不是主因 |
| 核显抢了主 GPU | 日志里 `Vendor: AMD`、空转 N 卡 39% | ——（复现验证） | 能自洽解释全部现象才算 |

三次误判有同一个毛病：**抓到一个"看起来很不正常"的相关项就当成病因**。而正确的路径是第三轮被迫走出来的——发现两次运行的配置完全一样、结果却不同，才意识到要找的不是"哪个参数错了"，而是"两个状态的差别在哪"。

另外两条具体的：

- **`utilization.gpu` 会骗人。** 本机见过 34% 占用配 35W 功耗（在等 present）和 100% 占用配 220W 功耗（真在算），读数都"正常"。判断 GPU 是否真的在干活，看 `power.draw`。
- **拿不准的时候别再加假设，去加观测维度。** 这次卡了很久，是因为一直在同一份日志里换解释。

## 附录：这次用到的命令

```bash
# 线程级 CPU 分布（top -H -b -n1 单次采样全是 0，需要差分）
declare -A A
for t in /proc/$PID/task/*; do A[$(basename $t)]=$(awk '{print $14+$15}' $t/stat); done
sleep 3
for t in /proc/$PID/task/*; do
  tid=$(basename $t); B=$(awk '{print $14+$15}' $t/stat)
  u=$(( (B-${A[$tid]:-0})/3 )); [ $u -gt 8 ] && printf "%4d%%  %s\n" $u "$(cat $t/comm)"
done | sort -rn | head -6

# PCIe 体检
cat /sys/bus/pci/devices/0000:01:00.0/{max_link_width,current_link_width}
cat /sys/bus/pci/devices/0000:00:01.1/current_link_width   # 上游根端口
cat /sys/bus/pci/devices/0000:01:00.0/aer_dev_correctable  # 全 0 才是好事
journalctl -k -b | grep "available PCIe bandwidth"

# 合成器用的哪块 GPU（/run/user/*/hypr 重启即清，要当场看）
grep -a "becomes primary\|Renderer:\|Vendor:" /run/user/1000/hypr/*/hyprland.log
```

带宽实测，`nvcc -O2 -o pcie_bw pcie_bw.cu`：

```cuda
#include <cstdio>
#include <cuda_runtime.h>
int main(){
  const size_t N = 256ull<<20;
  void *h=nullptr,*d=nullptr;
  cudaMallocHost(&h,N); cudaMalloc(&d,N); cudaMemset(h,1,N);
  cudaEvent_t a,b; cudaEventCreate(&a); cudaEventCreate(&b);
  for(int i=0;i<3;i++) cudaMemcpy(d,h,N,cudaMemcpyHostToDevice);
  cudaEventRecord(a);
  for(int i=0;i<5;i++) cudaMemcpy(d,h,N,cudaMemcpyHostToDevice);
  cudaEventRecord(b); cudaEventSynchronize(b);
  float ms; cudaEventElapsedTime(&ms,a,b);
  printf("H2D: %.2f GB/s\n", (5.0*N)/(ms/1000.0)/1e9);
  return 0;
}
```

两个容易绊倒自己的坑：

- `hyprctl dispatch` 在 Omarchy 的 Lua 配置下是 Lua 语法：`hyprctl dispatch 'hl.dsp.focus({ workspace = "2" })'`。传统写法报 `expected a dispatcher`。
- `pkill -f "foo.sh"` 会连**承载它的那条 ssh 远程命令**一起杀掉（命令行里含同一字符串），表现为 SSH 直接退出 255。

## 相关

- [[Hyprland-原神低帧率触发薇斯纳空中回体力]] — 同环境下的另一处原神调优，涉及 MangoHud 限帧切换与配置重载会复位 HUD 状态
- [[MangoHud-CPU功耗不显示排查：RAPL权限与capability边界]] — 这次读 HUD 数值的前提，`.conf` 会整体覆盖默认值这条同样适用
