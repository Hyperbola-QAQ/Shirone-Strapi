# RVC 跨机实时变声：VCClient 服务端部署与 PipeWire 虚拟设备打通

> 记录日期：2026-10-31
> 目标：PC（有 N 卡）跑 RVC 推理，笔记本（无独显）负责采集与回放，并把变声结果
> 输出成一个**虚拟麦克风**，供腾讯会议 / Discord / OBS / 游戏语音直接选用。

---

## 0. 环境前提与现状速览

### 硬件与网络

| 角色 | 机器 | 关键配置 |
|---|---|---|
| 服务端 | 台式 PC | Arch Linux，RTX 4070 SUPER 12GB，局域网 `192.168.86.110`，另有 IPv6 公网地址 |
| 客户端 | ThinkBook 14+ | i5-13500H（无独显），PipeWire，WiFi 5G 接入同一局域网 |

笔记本没有可用的 N 卡，CPU 跑 RVC 推理的延迟不可接受，所以必须拆成「PC 推理 + 笔记本采集回放」的跨机结构。

### 软件版本（已验证可用）

```
VCClient (w-okada/voice-changer)  commit d8ef157  2026-09-26
Python          3.10.22            # 官方要求 3.10，详见 §2.1
torch           2.5.1+cu124        # 不能上 2.6+，详见 §2.2
torchaudio      2.5.1+cu124
onnxruntime-gpu 1.19.2
pyworld         0.3.4
numpy           1.26.4
setuptools      80.10.2
fairseq         0.12.2             # 纯 Python 部署，无 dist-info，pip list 看不到
```

### 当前可用状态

- PC 服务监听 `*:18888`（IPv4 + IPv6 双栈）+ 自签 HTTPS，显存占用约 5.5G
- 模型槽 0：`zhanzhanv2-xi`，v2，采样率 **40000**
- 模型槽 1：`tomori`（高松灯），v2，采样率 **48000**（模型 README 写 40k，服务端解析出来是 48k，以服务端为准）
- 笔记本虚拟设备：**已常驻并验证**。配置见 §4.5，跨重启自动重建。
  实测记录：配置写入当晚生效 → 手工 `rm` 掉启停脚本 → 次日 07:07 重新登录，
  `Virtual_Speaker_1`(36) / `Virtual_Mic_1`(37) 自动出现，`always-sink` + `device-manager` 均在，
  `pipewire` 的 `NRestarts=0`（是登录时随系统起的，不是被重启补救过）

---

## 1. 架构选型：为什么是 VCClient 远程方案

### 候选方案对比

| 方案 | 优点 | 为什么不选 |
|---|---|---|
| RVC-WebUI 全家桶 | 生态全，训练推理一体 | 体积大、启动慢，WebUI 面向本机单机，跨机没有现成通路 |
| so-vits-svc | 歌声转换质量好 | 实时链路与前端不如 VCClient 完整 |
| **VCClient** | **server / client 天然分离，前端有预构建 `dist` 可直接托管，专为实时变声设计** | 文档零散，依赖踩坑多（本文档主要价值就在这） |

VCClient 的仓库结构是 `server/`（Python + FastAPI + socket.io）与 `client/demo/dist`（预构建静态前端）两部分。
服务端跑推理，前端只做采集和播放，两者通过 WebSocket 传音频块 —— 这正好就是我们要的跨机形态。

### 链路

```
笔记本物理麦克风
      ↓ getUserMedia
笔记本浏览器（VCClient 前端）
      ↓ WebSocket（走 SSH 隧道到 127.0.0.1:18888）
PC：VCClient server → RVC 推理（CUDA）
      ↓ 变声后的音频块
笔记本浏览器播放
      ↓ 输出到虚拟扬声器
Virtual_Speaker_1（module-null-sink）
      ↓ .monitor
Virtual_Mic_1（module-remap-source）
      ↓
腾讯会议 / Discord / OBS / 游戏语音
```

关键点在于最后一跳：浏览器**无法把音频直接写进一个麦克风**，只能播放到某个 sink。
所以要构造一个 null-sink 当"虚拟扬声器"，再把它的 `.monitor` 重映射成一个 source，
这个 source 在应用看来就是一个真实的麦克风。

---

## 2. PC 服务端部署

### 2.1 Python 必须是 3.10

上游 `README_dev_en.md` 明确写的是 `python=3.10`。

> 踩坑记录：最初按经验选了 3.11，被用户当场质疑后核实README，确认是 3.10，改用 uv 托管的
> `cpython-3.10.22`。这类"凭印象选版本"的错误在依赖敏感的仓库里代价很高 —— 先查官方文件再动手。

```bash
uv python install 3.10
uv venv --python 3.10 venv-vcclient
```

### 2.2 torch 锁在 2.5.1+cu124，不能上 2.6+

torch 从 2.6 起把 `torch.load` 的 `weights_only` 默认值改成 `True`，
RVC 的 `.pth` 里带有 numpy 对象，直接 `unpickle` 会失败。
VCClient 代码没有显式传 `weights_only=False`，所以用 2.5.1 是**最低成本**的解，
比去改上游代码安全得多。

官方源在国内实测 0 B/s，换成阿里云镜像：

```bash
venv-vcclient/bin/pip install torch==2.5.1 torchaudio==2.5.1 \
  --index-url https://mirrors.aliyun.com/pytorch-wheels/cu124/
```

实测从 0 B/s 提到约 2.5 MB/s。

### 2.3 fairseq：纯 Python 部署，不编译

fairseq 0.12.2 在 PyPI 上只有 cp36–cp38 的 wheel，源码的 C++ 扩展又和 torch 2.5.1 的 ABI 对不上，
编译必然失败。

解法：直接把 sdist 解开后的 `fairseq/` 目录拷进 `site-packages`，跳过扩展编译。

```bash
# 结果位置
venv-vcclient/lib/python3.10/site-packages/fairseq/
```

RVC 加载 hubert 只用到 fairseq 的 Python 部分，实测可用。
两个副作用要记住：

1. 没有 `.dist-info`，所以 `pip list` 里**看不到 fairseq**，这是正常的，不要以为没装上。
2. fairseq 自带的 BLEU 等 C 扩展功能不可用 —— 与变声无关。

（更早的一轮尝试是给 fairseq 打 stub，失败于 `OnnxContentvec` 未实现，所以必须走"完整 Python 源码"这条路。）

### 2.4 预训练权重：走 hf-mirror，一个文件要占位

`server/pretrain/` 约 1.6G：`hubert_base.pt`、`rmvpe.pt`、`rmvpe.onnx`、`content_vec_500.onnx`、
`crepe_onnx_full.onnx`、`nsf_hifigan/`、`hubert/` 等，用 `scripts/dl_weights.sh` 从 hf-mirror 拉。

上游代码引用的 `rinna/japanese-hubert-base` 仓库在 HuggingFace 上已经 **404**，
`rinna_hubert_base_jp.pt` 拉不到。该文件只有日语特化模型会用到，这里用符号链接占位，
避免启动时触发下载并卡死：

```
pretrain/rinna_hubert_base_jp.pt -> pretrain/hubert_base.pt
```

**要跑日语模型必须自己补这个权重。**

### 2.5 启动必挂：huggingface.co 的无 timeout 请求

VCClient 启动时会无条件向 `huggingface.co` 拉一份示例目录 JSON，而 `requests` 调用**没有设 timeout**。
国内直连会永久挂住，日志停在那一行不动。

解法是在启动脚本里塞一个"必然拒绝连接"的代理，让它快速失败、走 `except` 分支；
所需的 JSON 已经预置在本地，功能不受影响：

```bash
export HTTPS_PROXY="http://127.0.0.1:9"
export HTTP_PROXY="http://127.0.0.1:9"
export NO_PROXY="127.0.0.1,localhost"
```

### 2.6 其他依赖坑

| 现象 | 处理 |
|---|---|
| fairseq 老 `setup.py` 需要 setuptools 的 `distutils` | 装 `setuptools<81` |
| `gin==0.1.6` 包名不符合 PEP 规范，pip 直接报错 | 跳过；只有 DDSP-SVC 用到，RVC 不需要 |
| 全量 clone 只有 47 KB/s | 稀疏克隆：`--filter=blob:none --sparse`，只取 `server/` 与 `client/demo/dist` |

```bash
git clone --filter=blob:none --sparse https://github.com/w-okada/voice-changer.git VCClient
cd VCClient && git sparse-checkout set server client/demo/dist
```

---

## 3. 服务启停与网络暴露

### 3.1 启停脚本

```bash
~/Projects/RVC/scripts/start-server.sh    # 默认 host=:: https=true
~/Projects/RVC/scripts/stop-server.sh
```

覆盖方式：

```bash
RVC_HOST=127.0.0.1 RVC_HTTPS=false ./start-server.sh   # 只本机 + HTTP
RVC_HOST=0.0.0.0   ./start-server.sh                   # 仅 IPv4
```

### 3.2 停服务必须连孤儿进程一起清

VCClient 用 `multiprocessing` 的 spawn 模式拉推理子进程。**只 kill 主进程，子进程会变孤儿，
继续占着 1～1.5 GB 显存**，下次启动直接 OOM 或报显存不足。

所以 `stop-server.sh` 除了杀 `MMVCServerSIO`，还要单独匹配并清理：

```
venv-vcclient/bin/python -c from multiprocessing ...
```

### 3.3 `0.0.0.0` 不会监听 `::`

实测：传 `--host 0.0.0.0` 只监听 IPv4。要双栈必须绑 `::`，
并且依赖内核参数 `net.ipv6.bindv6only=0`（默认即是），这样一个 socket 同时吃 IPv4 和 IPv6。

```bash
ss -lntp | grep 18888
# LISTEN  ...  *:18888   *:*     ← 双栈生效
```

服务用的是**自签证书，CN=Example**，而且每次重启都会重新生成 —— 浏览器必然报不安全。

### 3.4 最重要的一条约束：麦克风权限 ≠ 网络可达

这是整个部署里最容易误判的地方。

`getUserMedia` 要求页面处于 **trustworthy origin**（安全上下文）：

| 访问方式 | 是否安全上下文 | 麦克风能否授权 |
|---|---|---|
| `http://127.0.0.1:18888` | ✅ localhost 免证书受信 | ✅ 可以 |
| `https://192.168.86.110:18888`（自签） | ❌ 证书无效 | ❌ **拒绝** |
| `http://192.168.86.110:18888` | ❌ 明文非本机 | ❌ 拒绝 |

> 纠正一处我当时的误判：我一度以为"只有 Firefox / Zen 会拒，Chrome 能放行"。
> 用户实测 **Chrome 同样拒绝**。trustworthy origin 的判断对两家浏览器是一致的，
> 不存在"某家浏览器更宽松"。

**结论：即使服务端开了 `::` + HTTPS，笔记本浏览器依然要走 `http://127.0.0.1:18888`，
也就是必须建 SSH 隧道。** HTTPS + 双栈解决的是"服务可达/便于其他接入方式"，
**不能**替代隧道来绕过证书问题。

```bash
# 笔记本端，前台挂起，Ctrl-C 结束
ssh -N -L 18888:127.0.0.1:18888 hyperbola@192.168.86.110

# 端口被占时先清掉
pkill -f "ssh -N -L 18888"
```

（更早的方案是让服务只监听 127.0.0.1 + HTTP，配隧道。后来按用户要求改成对外可达的
`::` + HTTPS，隧道仍作为笔记本访问时的必需品保留。）

---

## 4. 笔记本侧：PipeWire 虚拟扬声器与虚拟麦克风

### 4.1 两个模块

```bash
pactl load-module module-null-sink \
  sink_name=Virtual_Speaker_1 \
  sink_properties=device.description=Virtual-Speaker-1 \
  rate=48000 channels=2

pactl load-module module-remap-source \
  master=Virtual_Speaker_1.monitor \
  source_name=Virtual_Mic_1 \
  source_properties=device.description=Virtual-Mic-1
```

> `device.description` 的值里不要带空格。`pactl` 的 property 解析按空白分词，
> 带空格要么加引号要么会截断，不如直接用连字符，显示名一样清楚。

`module-null-sink` 建一个"没有人真正听"的 sink；PipeWire 会为每个 sink 自动生成
`<name>.monitor` 这个 source；`module-remap-source` 再把它包装成一个独立命名的麦克风。

### 4.2 坑：channels 必须是 2

最初建的是 `channels=1`（单声道）。结果是浏览器把输出设备选成虚拟扬声器时**完全没声音**，
而选成笔记本物理扬声器却正常。改成 `channels=2` 后立刻恢复。

单声道 null-sink 在这条链路（浏览器 → sink → monitor → remap）上不被正确接受。
**别为了省资源建单声道。**

### 4.3 命名规范：`_1` 而不是 `_A`

设备名要"通用"有两个理由：

1. 这套虚拟设备是**通用音频路由**，不只服务于 RVC —— 任何变声/混音/转发场景都能用，
   名字里不该绑死 `RVC`。
2. 后缀用数字：`_1`。PipeWire / PulseAudio 处理重名设备时本身就是**追加数字后缀**
   （`...analog-stereo.2`，`module-null-sink` 重复加载也会自动加 `.2`）。
   用数字序号跟系统自身的去重约定一致；将来要开第二路通道时 `_1`/`_2` 可以自然扩展。
   `_A` 是字母编号，这套体系里没有先例，也无法递增。

所以最终命名：

```
sink   Virtual_Speaker_1   显示名 Virtual-Speaker-1
source Virtual_Mic_1       显示名 Virtual-Mic-1
```

### 4.4 从旧命名的迁移（已完成，历史记录）

改名意味着必须销毁旧设备再建新的，**node ID 会再次变化** —— 这正是本次"没声音"的根因（见 §6）。
所以当时没有直接重建，而是等到切常驻方案时一次性做完：旧脚本卸载旧设备 → 重启 pipewire →
新名由 `pulse.cmd` 自动建起来。

迁移后浏览器 F5 重选一次 Output，OBS / 会议软件重选一次麦克风，之后名字固定不再变。

**启停脚本 `rvc-audio-on.sh` / `rvc-audio-off.sh` 已删除**。常驻之后它们是冗余的，
留着反而多一份"以为要手动跑"的误解。唯一的真相来源就是 §4.5 的那个配置文件。
需要手工重建时直接重启 pipewire 即可。

### 4.5 常驻方案：`pulse.cmd`（已部署，已跨重启验证）

脚本启停最大的问题是笔记本一重启设备就没了，而且每次重建 node ID 都变。
真正的解法是让 **pipewire-pulse 启动时自己把设备建起来**。

PipeWire 的 `pipewire-pulse.conf` 里有个 `pulse.cmd` 键，官方注释写明：

> `load-module` : loads a module with args and flags
> `args = "<module-name> <module-args>"`

它语义上等价于 `pactl load-module`，但**在 pipewire-pulse 进程内部执行** ——
不需要外部 `pactl` 回连，不存在"pipewire 还没起来"的时序问题，
而且跟着 pipewire 生命周期走：pipewire 一重启就自动重建。

配置文件 `~/.config/pipewire/pipewire-pulse.conf.d/10-virtual-mic.conf`：

```conf
pulse.cmd = [
    { cmd = "load-module"
      args = "module-null-sink sink_name=Virtual_Speaker_1 sink_properties=device.description=Virtual-Speaker-1 rate=48000 channels=2"
      flags = [ nofail ] }

    { cmd = "load-module"
      args = "module-remap-source master=Virtual_Speaker_1.monitor source_name=Virtual_Mic_1 source_properties=device.description=Virtual-Mic-1"
      flags = [ nofail ] }
]
```

**为什么这样做是安全的：**

1. **数组是追加不是替换**。`man 5 pipewire.conf` 原文：
   *"Dictionary sections are merged, overriding properties if they already existed,
   and array sections are appended to."*
   所以不会冲掉默认的 `always-sink` / `device-manager` / `device-restore` / `stream-restore`。
   这一点已用 `pw-config` 实测确认（见下）。
2. **`flags = [ nofail ]`**：单个模块加载失败不会让 pipewire-pulse 起不来。
3. **回滚成本为零**：出问题删掉这个文件再重启 pipewire 即可完全恢复。
4. 设备本身零风险：null-sink 不发声、不采集，只是多一个未连接的空设备。

**验证方式（不需要重启，不打扰正在用的音频）：**

```bash
pw-config -n pipewire-pulse.conf -N -r merge pulse.cmd
```

实测输出里默认 4 条命令齐全，后面追加了 null-sink 与 remap-source 两条，顺序正确。

**启用 / 手工重建（会中断音频约 1 秒，挑没有会议 / 录音的时候做）：**

```bash
systemctl --user restart pipewire pipewire-pulse wireplumber  # 让 pulse.cmd 生效

pactl list short sinks   | grep Virtual_    # 期望看到 Virtual_Speaker_1
pactl list short sources | grep Virtual_    # 期望看到 Virtual_Mic_1 与 .monitor
pactl list short modules | grep -cE "always-sink|device-manager"   # 应为 2，确认默认模块没丢
```

日常**不需要做任何操作** —— 登录即自动就绪。

### 实测：跨重启自动恢复（2026-11-01）

```
配置写入当晚生效，随后删除了 on/off 脚本
次日 07:07 重新登录：
  sinks   → 36  Virtual_Speaker_1          SUSPENDED
  sources → 36  Virtual_Speaker_1.monitor  SUSPENDED
            37  Virtual_Mic_1             SUSPENDED
  modules → null-sink + remap-source + always-sink + device-manager 全部在
  pipewire NRestarts=0（随登录自起，不是被补救重启）
```

常驻成立。之后重启笔记本、重启 pipewire 都会自动恢复，**node.name 固定，应用按名字记住即可**。

---

## 5. 浏览器端配置与模型加载

打开 `http://127.0.0.1:18888/`（隧道已建立的前提下）。

| 项 | 选择 |
|---|---|
| 连接模式 | **Server**（不是 Standalone） |
| 设备模式 | **Client Device**（用本机浏览器采集 / 播放） |
| 输入设备 | 笔记本物理麦克风 |
| 输出设备 | **Virtual-Speaker-1**（旧命名是 `RVC-Virtual-Speaker`） |
| Monitor | 同样选虚拟扬声器，否则听不到自己的变声 |
| 模型槽 | 槽 0 `zhanzhanv2-xi`（40k）/ 槽 1 `tomori`（48k） |

点 Start 后说话，会议软件里把麦克风选成 **Virtual-Mic-1** 即可。

### 模型加载的两个细节

1. **分块上传必须带 `_0` 后缀**。上传接口按 `<名字>_<序号>` 拼接，
   少了 `_0` 会写出一个 0 字节文件，加载时抛 `EOFError`。
2. **`.pth` 会被移动，不是复制**：从 `upload_dir/` 移到 `server/model_dir/<slot>/`。
   所以同一模型要再加载一次得重新上传。
   原件统一放在 `~/Projects/RVC/models/`，不会丢。

### 参数调优

| 参数 | 起点 | 影响 |
|---|---|---|
| chunk | 512 | 调低＝延迟小但可能爆音；调高＝稳但延迟大 |
| extra | 与 chunk 同量级 | 增加上下文，改善断音 |
| 采样率 | 输入输出都设 48000 | 与模型采样率不符会走重采样，增加延迟 |
| index rate | 0.6–0.8 | 越高越贴近目标音色，过高出齿音 |
| f0 method | rmvpe | 质量最好；卡顿时换 fcpe |

延迟偏高先降 chunk 到 256；声音断续先加 extra。

---

## 6. 排障记录

### 6.1 本次"没声音"的真正原因

用户自己的判断是对的：**虚拟麦克风被重载了，而 OBS 对设备重载的处理有问题，于是采集不到声音。**

完整因果链：

```
我为了修 §4.2 的单声道问题，销毁并重建了 null-sink
        ↓
PipeWire 给新设备分配了新的 node ID（2585 → 3371）
        ↓
浏览器缓存里还是旧 ID，回退到默认扬声器  → 输出方向错了
OBS 里选的麦克风也指向已消失的旧节点      → 采集方向空了
        ↓
两个方向同时失效 → 无声
```

两条教训：

1. **重建设备前先算代价**。node ID 一变，所有已保存的引用全部失效。
2. 修好后浏览器必须 **F5 刷新**并重选 Output + Monitor；OBS 也要重新选一次设备。
   只刷新页面不够，缓存的是设备 ID 不是名字。

### 6.2 排查对照表

| 现象 | 原因 / 处理 |
|---|---|
| 浏览器不弹麦克风授权 | 没走 `127.0.0.1`（安全上下文不满足），或本地 18888 被占、隧道没起来 |
| 服务启动卡住无输出 | 看 `logs/server.log`；确认 `HTTPS_PROXY` 黑洞已设 |
| `ModuleNotFoundError` | 缺依赖，用 uv pip 装进 venv |
| 输出选虚拟扬声器但没声音 | 检查 `channels=2`；检查浏览器 Output 与 Monitor 都指向它 |
| 会议软件里没声音 | 确认麦克风选的是 `Virtual-Mic-1`，且浏览器输出是 `Virtual-Speaker-1` |
| 设备重建后一律失效 | node ID 变了，浏览器 F5 + 应用内重选 |
| 声音断续 | 加 chunk / extra；笔记本插网线（WiFi 实测抖动更大） |
| 显存不足 | 当前约占 5.5G；关掉 ComfyUI 等其他推理进程 |
| 停了服务显存没释放 | 孤儿进程还活着，用 `stop-server.sh`（它会连子进程一起清） |

日志位置：PC 端 `~/Projects/RVC/logs/server.log`

---

## 7. 已知限制

1. **fairseq 为纯 Python 部署**，C++ 扩展未编译（BLEU 等不可用，与变声无关）。
2. **`pretrain/rinna_hubert_base_jp.pt` 是占位符号链接**，上游仓库已 404。跑日语模型需自补权重。
3. **启动脚本强制设置了黑洞代理** `HTTPS_PROXY=http://127.0.0.1:9`，这是为了绕开无 timeout 的
   huggingface.co 请求，不是笔误。
4. **模型 `.pth` 加载时会被移动**，重复加载需重新上传（原件在 `~/Projects/RVC/models/`）。
5. **自签证书每次重启重新生成**，CN=Example，浏览器一律报不安全 —— 所以浏览器访问仍须走隧道。
6. **服务对外可达**：监听 `::` 双栈，局域网乃至 IPv6 公网可达。
   它没有任何鉴权，仅限可信网络内使用；不需要时应切回
   `RVC_HOST=127.0.0.1 RVC_HTTPS=false` 只走隧道。
7. **只适用于自己有权使用的声音**。RVC 官方示例音色（kiki / guanguan / keran / zhanzhan）
   来自 RVC 发行包，附研究用途条款。

---

## 附录 A：命令速查

```bash
# ── PC 端 ──────────────────────────────
~/Projects/RVC/scripts/start-server.sh
~/Projects/RVC/scripts/stop-server.sh
nvidia-smi --query-gpu=memory.used --format=csv
tail -f ~/Projects/RVC/logs/server.log
ss -lntp | grep 18888                      # 确认双栈

# ── 笔记本端 ───────────────────────────
# 虚拟设备常驻，无需启停
ssh -N -L 18888:127.0.0.1:18888 hyperbola@192.168.86.110   # 前台挂起，Ctrl-C 结束
pkill -f "ssh -N -L 18888"                                 # 端口被占时先清

pactl list short sinks   | grep -i virtual
pactl list short sources | grep -i virtual
pactl list short modules | grep -Ei "null-sink|remap"

systemctl --user restart pipewire pipewire-pulse wireplumber   # 手工重建 / 改配置后生效
pw-config -n pipewire-pulse.conf -N -r merge pulse.cmd         # 离线验证配置合并结果
```

## 附录 B：文件清单

```
~/Projects/RVC/
├── VCClient/                  # 上游源码（稀疏克隆）
│   ├── server/pretrain/       # 预训练权重，约 1.6G
│   └── client/demo/dist/      # 预构建前端
├── venv-vcclient/             # Python 3.10.22 venv
├── models/                    # 模型原件（备份用）
│   ├── zhanzhanv2-xi.pth / .index
│   └── tomori/tomori.pth / .index
├── scripts/
│   ├── start-server.sh
│   ├── stop-server.sh
│   └── dl_weights.sh
└── logs/server.log

笔记本（虚拟设备常驻，无脚本）：
~/.config/pipewire/pipewire-pulse.conf.d/10-virtual-mic.conf
```

> 曾用过 `~/.local/bin/rvc-audio-on.sh` / `off.sh`，常驻后已删除。
> SSH 隧道一直是直接敲命令，没有做成脚本（当时就是按这个要求来的）。
