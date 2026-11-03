---
title: Arch Linux 使用 Corepack 管理 pnpm：从启用到权限排障
published: 2026-10-29
updated: 2026-11-03
pinned: false
description: 从 Shirone 的启动命令出发，理解 Corepack 与系统 pnpm 的关系，排查启用时的权限问题，并用 AUR 占位包在保留 pacman 依赖的前提下让 Corepack 接管版本。
tags: [Arch Linux, Node.js, Corepack, pnpm, pacman]
category: 开发环境
author: Hyperbola
draft: false
---

# 从一条启动命令说起

准备运行 Shirone 时，安装步骤中有这样几条命令：

```bash
git clone https://github.com/LyraVoid/Shirone.git
cd Shirone
corepack enable
pnpm install
pnpm dev
```

其中最容易让人困惑的是 `corepack enable`：系统已经有了 `/bin/node` 和 `/bin/pnpm`，为什么还需要启用另一个工具？它会修改哪些文件？遇到权限错误后，是否应该加上 `sudo`？

这次排查的关键，是分清四件事：**系统软件包的归属、命令入口的位置、项目要求的 pnpm 版本，以及依赖关系由谁满足。**

# Corepack 负责什么

Corepack 连接项目与包管理器。项目可以在 `package.json` 中声明需要的 pnpm 版本，Corepack 据此准备并调用对应版本。

`corepack enable` 主要负责创建命令代理入口，也就是 shim。它默认在 Corepack 安装位置旁创建入口，也可以通过 `--install-directory` 指定目录；真正调用包管理器时，才可能触发下载和缓存。[Corepack 官方说明](https://github.com/nodejs/corepack#utility-commands)

因此，安装 Corepack、启用命令入口和安装项目依赖，是三个不同的步骤。

另外，“Node.js 自带 Corepack”有版本边界：上游从 Node.js 14.19.0 开始附带 Corepack，到 Node.js 25 起不再附带，不能假定所有环境都有它。[Corepack 安装说明](https://github.com/nodejs/corepack#how-to-install)

# 已有 pnpm，先确认它来自哪里

看到 `/bin/pnpm`，还不足以判断它属于哪个安装方式。可以先检查命令解析结果、链接目标和软件包归属：

```bash
type -a node pnpm corepack
ls -l /bin/pnpm /usr/bin/pnpm
pacman -Qo /usr/bin/pnpm
```

重点看两点：

- 当前终端实际使用哪个 `pnpm`。
- 对应文件是否由 pacman 软件包管理。

**不要直接把“已有 pnpm”理解为“必须卸载”。** 如果决定让 Corepack 提供同一个系统路径下的命令入口，就需要先处理文件归属；如果使用用户目录入口，或者显式运行 `corepack pnpm`，则可以采用不同的配置方式。

Arch 官方 Extra 仓库提供独立的 `corepack` 软件包，但这并不意味着安装后普通用户就获得了 `/usr/bin` 的写权限。[Arch Linux 软件包页面](https://archlinux.org/packages/extra/any/corepack/)

# 实际遇到的权限错误

原始记录中，执行：

```bash
corepack enable
```

出现了以下错误：

```text
Internal Error: EACCES: permission denied, symlink
'../lib/node_modules/corepack/dist/pnpm.js' -> '/usr/bin/pnpm'
```

这条日志已经指出失败位置：Corepack 正在尝试创建 `/usr/bin/pnpm`，而当前用户没有写入权限。

问题发生在**创建命令入口阶段**，尚不能归因于项目依赖、下载源或 pnpm 版本。

排查过程中，一个需要纠正的判断是：

> 通过 pacman 安装 Corepack 后，执行 `corepack enable` 就不会触碰系统目录。

这与日志直接矛盾。安装来源决定软件包如何管理，入口位置决定启用操作需要什么权限，两者不能混为一谈。

# 第二次失败：从 symlink 变成 unlink

换了安装方式之后，同样的命令又失败了一次，但报错的动词换了：

```text
Internal Error: EACCES: permission denied, unlink '/usr/bin/pnpm'
    at async Object.unlink (node:internal/fs/promises:1959:16)
    at async EnableCommand.generatePosixLink (/usr/lib/node_modules/corepack/dist/lib/corepack.cjs:14320:7)
```

两次失败的原因完全相同，只是走的分支不同：

- 目标不存在时，Corepack 直接创建链接，失败的调用是 `symlink`。
- 目标已存在时，它先删除旧文件再重建，失败的调用是 `unlink`。

所以“换了包、报错也换了词”并不等于“问题变了”。判断依据始终只有一条：**Corepack 准备写的那个路径，是否归当前用户所有。** `/usr/bin` 属于 root，这一点不会因为 pnpm 换了个来源而改变。

这里还藏着一个不容易注意到的副作用。`sudo corepack enable` 成功之后，`/usr/bin/pnpm` 的链接会从包安装时的绝对路径变成 Corepack 生成的相对路径（`../lib/node_modules/corepack/dist/pnpm.js`）。两种写法解析结果一致，`pnpm` 照常可用，但文件内容确实被改过了：

```text
$ pacman -Qkk pnpm-corepack
warning: pnpm-corepack: /usr/bin/pnpx (UID mismatch)
warning: pnpm-corepack: /usr/bin/pnpx (Symlink path mismatch)
warning: pnpm-corepack: /usr/bin/pnpx (Modification time mismatch)
pnpm-corepack: 5 total files, 5 altered files
```

功能不受影响，但这个输出说明“包管理的文件”和“Corepack 生成的链接”已经开始混在一起了。这种混管不是不能用，而是要知道代价，后文的方式四会把它变成一种可控的取舍。

# 四种使用方式

**方式一：直接调用 Corepack**

如果只是运行项目，可以先不创建 `pnpm` 代理入口：

```bash
cd Shirone
corepack pnpm --version
corepack pnpm install
corepack pnpm dev
```

这种方式能明确表达“本次通过 Corepack 调用 pnpm”，也能避免当前 PATH 中已有 `pnpm` 带来的歧义。[Corepack 命令说明](https://github.com/nodejs/corepack#utility-commands)

**方式二：把入口放到用户目录**

希望直接输入 `pnpm`，又不想修改系统目录，可以指定安装位置：

```bash
mkdir -p "$HOME/.local/bin"
corepack enable --install-directory "$HOME/.local/bin" pnpm
```

随后确保 `~/.local/bin` 位于 PATH 中，并排在系统 pnpm 所在目录之前。当前终端可临时设置：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

检查是否解析到预期入口：

```bash
type -a pnpm
```

撤销这些入口时，使用同一目录：

```bash
corepack disable --install-directory "$HOME/.local/bin" pnpm
```

**方式三：创建系统级入口**

原始对话随后建议执行：

```bash
sudo corepack enable pnpm
```

这能为写入 `/usr/bin` 提供权限，但应先确认目标路径没有需要保留的 pacman 管理文件。不能仅凭“Corepack 是 pacman 安装的”，就认定覆盖系统入口一定合适。

如果决定移除系统 pnpm，应先检查软件包管理器给出的依赖影响；不要手动删除它管理的文件。系统级入口的撤销同样需要对应权限：

```bash
sudo corepack disable pnpm
```

这里的撤销只负责移除 Corepack 入口，不会自动恢复此前的软件包。

**方式四：用 AUR 占位包填住 pacman 依赖**

前三种方式都只解决了“命令从哪来”，没有解决“别的包还需要这个命令名”。这就是真正卡住的地方。

以 `deepseek-harness-bin` 为例，它在 AUR 元数据里的依赖是 `nodejs` 和 `pnpm`。如果直接 `pacman -R pnpm`，让 Corepack 提供 `pnpm`，pacman 的依赖图就会认为缺东西；如果保留系统 pnpm，又回到版本不由项目决定的老问题。

AUR 上的 `npm-yarn-pnpm-corepack` 正好填这个位置。一个 PKGBUILD 拆成三个包，核心只有两行：

```bash
package_pnpm-corepack() {
    provides=(pnpm)
    conflicts=(pnpm)
    _package pnpm
}

_package() {
    corepack enable --install-directory "shims/$_pkgname" "$_pkgname"
    # ...把 shims/ 里的链接规范化后装进 $pkgdir/usr/bin
}
```

关键在于：装包时并不下载 pnpm，而是在**构建阶段**用 Corepack 生成 shim，再把链接打进 `/usr/bin`。于是 `/usr/bin/pnpm` 仍然存在、仍然归 pacman 管，依赖链完整；但真正执行时装的是哪个版本，由 Corepack 按项目决定。

实际的操作顺序是：

```bash
# 1. 先撤掉 Corepack 之前直接写进 /usr/bin 的链接
sudo corepack disable pnpm
sudo corepack disable npm
sudo corepack disable yarn

# 2. 装占位包，pacman 会用 provides/conflicts 完成替换
yay -S pnpm-corepack npm-corepack yarn-corepack

# 3. 把真正的二进制装进 Corepack 缓存（按安装时的提示）
corepack install -g pnpm npm yarn
```

第 1 步不能省。如果 `/usr/bin/pnpm` 还是 Corepack 早先写下的链接，装包时 pacman 会报 `/usr/bin/pnpm exists in filesystem` —— 这是文件冲突，不是依赖冲突，两者在输出里长得很像，但处理方式完全不同。先 disable 把文件交还，再从包里取，才是干净路径。

装完后的归属可以逐项核对：

```bash
$ which pnpm
/usr/bin/pnpm
$ pacman -Qo /usr/bin/pnpm
/usr/bin/pnpm is owned by pnpm-corepack 0.1.0-1
$ pacman -Qi pnpm-corepack | grep -E "Provides|Conflicts|Depends"
Provides        : pnpm
Depends On      : corepack
Conflicts With  : pnpm
```

`Provides: pnpm` 没有版本号，这足以满足 `Depends=pnpm` 这种不带版本约束的依赖，所以再装 `deepseek-harness-bin` 不会提示缺 pnpm。反过来说，这个方案成立的前提是上游只写包名、不写版本区间。

代价也要提前说清楚：

- 三个包的版本号永远是 `0.1.0-1`，与真实使用的 pnpm 版本无关，`pacman -Q` 看不出你实际在跑哪个版本。
- 卸载要走 pacman（`pacman -R pnpm-corepack`）。**不要用 `corepack disable` 撤它们**：这些入口现在归包管理，被 Corepack 删掉后 pacman 会认为文件丢失，`pacman -Qkk` 会报 missing。
- 需要全局默认版本时（比如离开项目目录、或脚本里写死 `pnpm@x`），仍要靠 `corepack install -g` 提前装好。

# 验证时必须进入项目目录

入口可用之后，还要确认项目实际使用的版本：

```bash
cd Shirone
node -p "require('./package.json').packageManager"
corepack pnpm --version
```

第一条命令检查项目声明，第二条检查 Corepack 的实际选择。

有个很直接的例子能说明这两条命令为什么要成对使用：`corepack install -g pnpm npm yarn` 装进缓存的是 `pnpm@12.17.0`，但在 `deepseek-harness` 目录里执行 `pnpm --version`，返回的是 `11.28.5`，因为该仓库的 `package.json` 写着 `"packageManager": "pnpm@11.28.5"`。同一个入口、同一条命令，版本随当前目录变化，这正是 Corepack 在做的事。换个没有 `packageManager` 声明的目录再执行一次，拿到的才是全局默认版本。

原始对话先后提到了 `pnpm@9.14.4` 和 `pnpm@12.16.1`，但没有提供足够证据证明它们就是当时项目声明的版本。本文整理时查询到的 Shirone `main` 分支声明为 `pnpm@11.28.2`，后续也可能变化，因此应以**本地检出的代码**为准。[Shirone 的 package.json](https://github.com/LyraVoid/Shirone/blob/main/package.json)

在主目录执行 `pnpm --version`，不能单独证明项目的版本选择正确。完成版本检查后，再安装依赖并启动开发服务器，按终端实际输出验证访问结果。

原始材料没有完整的安装完成日志和页面验证记录，因此这里不把项目成功启动写成已经证实的结果。

# 经验小结

**版本更新不等于更适合项目。** Corepack 的价值在于按项目选择版本。一个比系统仓库更新的 pnpm，如果与项目要求不符，也不能据此认定更好。

**启用入口时使用 sudo，不代表以后都要使用 sudo。** 创建系统链接与日常安装依赖是两类操作。后续应以普通用户运行项目命令，也不能仅凭一次 `sudo corepack enable` 就断言缓存一定落在 root 家目录。

**不要把个人配置方案写成整个社区的统一结论。** 官方软件包页面能证明 Arch 提供 Corepack；具体选择系统入口、用户入口还是显式调用，仍取决于现有工具链和维护方式。

**入口归属和版本来源是两条独立的线。** 用占位包之后，文件归 pacman 管，版本归 Corepack 管。排障时要分别查：`pacman -Qo` 看入口归谁，项目里的 `packageManager` 看版本从哪来。把两者混成一句“pnpm 是不是装好了”，问题就永远说不清。

**`corepack install -g` 装的版本，不等于项目会用的版本。** 它只设全局默认。只要当前目录有 `packageManager` 声明，Corepack 就按项目走，缓存里那个更新的版本会安静地待着不被使用。

**让两个包管理器管同一批文件，代价是可预期的。** 实测 `pacman -Qkk pnpm-corepack` 报 5 个文件全部 altered，因为链接被 Corepack 重写过。这不是故障，而是混管的正常痕迹；知道它会出现在哪里，比事后去猜“系统是不是坏了”要省事得多。

最终可迁移的判断方法是：先确认命令来自哪里，再看它准备写到哪里，然后弄清还有谁依赖这个命令名，最后进入项目验证版本。这样才能把权限问题、命令冲突、依赖完整性和版本选择分别处理清楚。
