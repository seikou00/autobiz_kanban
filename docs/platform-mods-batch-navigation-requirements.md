# 平台开发能力清单：Mods 批次导航与自有 Worktree 变更预览

日期：2026-10-29。状态：待实现的开发需求；已有能力按本地源码核查，不代表安装包联调通过。

目标：用户的脚本继续管理多仓库 worktree 和并行执行，用户通过 Mods 选择 Batch，在任务运行期间直接查看对应目录的代码变更。

## 1. 分工和交付边界

| 用户脚本与插件负责 | 平台负责 |
| --- | --- |
| 多仓库绑定、Batch 拆分、依赖和并行调度 | 在 Workflow 会话中承载 Mods 导航面板 |
| worktree 创建、分支、lease、提交、封存、恢复和清理 | 对已授权的外部目录提供只读检查引用 |
| Implement/Review/UTest、Merge Train 和最终确认 | 变更文件列表、文件内容和单文件 Diff 的查询与展示 |
| 从真实运行记录提供阶段、任务、阻塞和 worktree 路径 | 支持与正在执行的任务并行的只读交互及刷新 |
| 提供每个 Batch 的起始提交、run 和 generation | 权限、目录身份、分页、取消、错误和界面隔离 |

自有 worktree 不转换为平台管理的 worktree。平台不新增 Batch 状态机，不代替脚本执行 Git 写入、调度、恢复或合并。只读目录引用不具有资源写入 handle 的含义。

本期交付到“批次导航 + 变更列表 + 文件/Diff 只读预览”；代码编辑器、完整仓库文件树和新的 Agent 工具流页面不作为前提。

## 2. 必做能力总表

| 编号 | 优先级 | 平台能力 | 用户可见结果 |
| --- | --- | --- | --- |
| CAP-01 | P0 | Workflow 会话中的 Mods 导航入口与稳定容器 | 任务运行时可随时打开批次导航，并保留当前选择 |
| CAP-02 | P0 | 宿主强制约束的只读视图交互 | 筛选、选择和刷新立即处理，不等待任务结束 |
| CAP-03 | P0 | 多个外部 worktree 目录的只读授权与绑定 | 使用用户自己的目录，且不要求纳入平台资源管理 |
| CAP-04 | P0 | 只读 Git 变更列表与明确比较范围 | 看见当前 Batch 实际涉及的新增、修改、删除和重命名文件 |
| CAP-05 | P0 | 文件内容与单文件 Diff 预览 | 点击文件查看增删行、新文件内容及删除前内容 |
| CAP-06 | P0 | 刷新、取消、过期响应和视图生命周期 | 多 Batch 切换不串数据，面板关闭后停止旧读取 |
| CAP-07 | P0 | 对 Mods 开放稳定、版本化的调用协议 | 插件可通过公开能力选择目录、查询变更和打开预览 |
| CAP-08 | P1 | 在 IDE/文件管理器打开目录 | 从选中 Batch 一键进入自己的代码目录 |
| CAP-09 | P1 | worktree 清理后的提交版本阅读 | 在提交对象仍可读取时浏览封存版本 |

P0 必须同时完成 CAP-01 至 CAP-07。只有导航和目录复制，不能算“可以查看 worktree 代码变更”交付完成。

## 3. CAP-01：导航入口和容器

- 复用现有 Mods Surface/Pane，在 Workflow 父会话中提供常驻、可折叠入口。导航打开、关闭和切换不改变任务执行状态。
- 运行中允许打开，不依赖输入框发送一个会排队执行的插件命令。
- 入口按插件的通用 Surface 声明识别。平台不硬编码 `/batches`、AutoBiz 名称或 Batch 数据格式。
- 保留仓库、Feature/run、Batch 和文件选择；组件重绘不无故重置导航。
- 容器随当前会话绑定；没有已初始化组件时，通过用户显式打开的受控路径准备视图。列出入口本身不执行全部插件命令。

批次内容仍由插件通过现有文件读取或精简快照获得。平台不用读取、理解或推进 `.autobizdevops` 里的业务状态。

## 4. CAP-02：只读交互不等待执行锁

增加明确的只读视图交互契约，并在宿主和 SDK 调用边界执行约束。

| 可以执行 | 仍沿用原任务执行路径 |
| --- | --- |
| 已授权文件读取、批次筛选和选择、面板重绘 | 启动任务、重试、恢复和取消执行 |
| 平台受限的只读 Git 查询、文件与 Diff 预览 | Guest 通用进程/工具执行、模型调用和业务文件写入 |
| 更新面板本地偏好、关闭面板、用户点击复制 | worktree 创建、提交、合并、丢弃和清理 |

只读回调不获取业务线程运行 lease，不排在 Workflow 任务之后，也不获取会阻塞工作树写入的长时间操作锁。平台内部可以异步执行固定只读 Git 命令；这不意味着向 Guest 开放 `process.run` 或任意 Git 命令。

不能把现有所有 Surface act 改为直接放行。只读限制必须覆盖间接能力调用、异步续接和迟到回包；插件自报“只读”不构成豁免。插件尝试副作用操作时明确拒绝，不自动转入有副作用的执行分支。

## 5. CAP-03：外部目录只读绑定

Mod 提供当前选中 Batch 的 worktree 路径。平台校验它是否在当前会话已有的授权范围内；没有授权时使用原生目录授权入口，然后返回只读 `directoryRef`。

要求：

- 支持一个 artifact workspace 对应多个物理仓库、多个 linked worktree；目录不要求位于当前会话 workspace 内。
- 导航路径、Workflow args 或插件 manifest 中出现路径不等于获得权限。复用现有许可，避免对已经授权的同一范围重复申请。
- 检查实际目录、Git 顶层目录和 linked-worktree/commonDir 关系；模块子目录可显示，但 Git 变更范围使用明确的仓库/worktree 根。
- 引用绑定会话、插件来源与授权代次、规范化后的目录身份和仓库身份。目录被替换、路径转向或授权撤销后，旧引用失效。
- 不把整个仓库读取权限隐式授予其他插件或会话；也不把工作树登记进平台的合并/清理生命周期。
- 文件读取、二进制识别和敏感文件处理复用已有权限规则。只读预览不切换用户分支或主会话 cwd。

`directoryRef` 的 generation 是平台读取身份，与脚本的 dispatch generation 分别处理。Batch/run/业务 generation 可作为插件上下文传回，平台不据此裁定调度状态。

## 6. CAP-04：变更列表与比较语义

提供按目录引用查询的通用 Git 读取服务，不依赖平台原生 WorkflowWorktreeRecord。

| 比较模式 | 比较对象 | 用途 |
| --- | --- | --- |
| `base-to-working` | 插件提供的冻结起点 → 当前工作目录的已跟踪内容 | Batch 当前累计有效改动，默认视图 |
| `base-to-head` | 冻结起点 → 本次读取的实际 HEAD | Batch 已提交的累计改动 |
| `head-to-index` | HEAD → index | 已暂存改动 |
| `index-to-working` | index → 当前工作目录 | 未暂存改动 |

涉及工作目录的模式另行提供未跟踪文件分组；未跟踪文件不能因普通 `git diff` 没有输出就被遗漏。模式、起点 SHA 和实际 HEAD 必须在界面可见，用户可以区分各层变化，不能把多层统计简单相加。

- 起点由插件提供，平台验证对象属于已绑定仓库并将其解析为固定提交。缺失或不可读时明确报错，不猜 `main`、不悄悄使用当前 HEAD，也不自动 fetch。
- 每项返回稳定文件引用、相对路径、旧路径、变更类型、可取得的增删行、二进制/内容可预览标记。新增、删除、重命名、类型变化和冲突有独立状态。
- 状态、路径和 patch 使用明确且一致的比较口径；文件名带中文、空格、引号和换行也应准确处理。
- 返回当前实际分支/HEAD、读取时间、分页游标、截断/不完整标志。冲突文件可显示状态和当前内容，不把冲突当普通完整 patch 或“无改动”。

Git 通过固定 argv 执行，不接受 Guest 传来的 shell 命令。禁用外部 diff、textconv、fsmonitor 等可能触发外部程序的扩展，关闭可选 index 刷新写入；不运行 checkout/add/commit/merge/reset/clean 等修改命令。

## 7. CAP-05：文件和 Diff 展示

点击 Mod 中的“查看变更”后，在可继续切换 Batch 的布局中打开平台通用只读变更预览。

- 变更列表可按文件选择，展示新增/删除行；单文件支持统一 Diff，分栏 Diff 可复用已有组件后评估。
- 支持读取 working、base、head、index 的指定文件版本。新文件展示当前内容；已删除文件可显示比较起点对应内容。
- 使用 CAP-04 返回的文件引用和比较模式，不能直接复用当前主项目 Git 面板的 cwd。
- 二进制文件显示类型和可取得的大小信息；不强行按文本解码。大文件、大 patch、过长行按行数和字节分页，明确显示截断。
- 使用受限渲染组件显示内容，不接受任意 HTML。代码读取结果不自动进入主 Agent 的模型上下文。
- 预览页展示仓库/Batch 标签、精确目录、模式与基准。Batch 标签由 Mod 提供，仅用于展示。

可以复用现有 DiffDisplay 等展示部件，但查询与作用域须适配外部目录。这里交付的是通用只读代码变更预览，不是新的平台 worktree 管理页。

## 8. CAP-06：实时性和生命周期

- 首期提供手动刷新，并支持面板可见期间的有界刷新。关闭、切换会话、禁用插件或撤销授权后取消旧请求和订阅。
- 仅查询当前选中的目录/文件；不为每次 UI 刷新遍历所有仓库。请求可合并、可取消，后台处理不能阻塞 Electron 主线程。
- 结果绑定视图实例、目录引用、比较模式与请求序号。切换 Batch 后，旧响应不得覆盖新选择。
- 运行中代码持续变化，返回读取时间和文件内容/版本标识。读 patch 或文件时发现版本改变，明确提示刷新；不宣称整仓库事务快照。
- 列表分页发现比较身份已经变化时失效旧游标，不能把不同版本的页拼成一个完整结果。
- 刷新失败可保留明确标记为过期的旧内容；加载失败、无改动、目录消失和权限不足分别展示。
- worktree 被脚本清理后显示“目录已清理/不可用”，不自动重建。历史提交阅读作为 CAP-09 单独交付。

## 9. CAP-07：公开协议草案

以下名称表示接口职责，均为待设计/实现契约，不是当前已经可调用的 SDK。具体命名应随平台实际 SDK 版本确定。

| 接口职责 | 主要入参 | 主要返回 |
| --- | --- | --- |
| 打开导航 Surface | 已授权 Surface 引用 | 当前视图实例 |
| `directories.bindReadOnly` | 目录、可选的仓库预期身份 | `directoryRef`、实际根目录/仓库身份、读取 generation |
| `git.listChanges` | `directoryRef`、比较模式、适用时的 `baseCommit`、游标 | 文件列表、文件引用、实际 HEAD/比较身份、下一页 |
| `git.readDiff` | 目录/文件引用、比较身份、游标 | 有界 patch、内容版本、完整性信息 |
| `files.readVersion` | 目录/文件引用、版本选择、范围 | 有界文本、内容摘要、下一段 |
| `ui.openChanges` | `directoryRef`、比较模式、适用时的起点、展示标签 | 原生只读变更预览实例 |
| `directories.openInIde`（P1） | 已验证目录引用 | 打开结果，不启动 Agent |

宿主从实际 IPC/SDK 调用绑定 thread、workspace 和插件来源，不依靠 Mod 填写其他线程身份来访问目录。平台侧所有 API 都以通用目录和 Git 对象为目标，不新增 `batch.start`、`batch.retry` 或 `batch.merge`。

共享类型、作者声明校验、权限目录、preload、IPC、QuickJS 桥和渲染组件需同时接通。接口需支持取消、合理期限、条目与字节双重分页，并适配现有消息容量；patch/源码不得塞进 capability 状态广播。

错误至少区分：授权缺失/撤销、目录不存在/被替换、非预期仓库、起点对象缺失、文件不可读/二进制、读取中发生变化、游标过期、查询失败和查询超时。不得将失败统一映射为“没有代码变更”。

## 10. 平台现有基础与建议接入点

路径以 `/Users/liuxuyang/WebstormProjects/CmbCoworkAgent` 为根，以下列举接入点，不表示要重写整份模块。

| 接入点 | 处理事项 |
| --- | --- |
| [PlatformWorkspace.tsx](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/renderer/src/components/platform/PlatformWorkspace.tsx:483)、[ChatContainer.tsx](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/renderer/src/components/chat/ChatContainer.tsx:8930) | 复用通用 sidebar Surface；Workflow 中稳定承载导航与预览 |
| [platform-surfaces.ts 共享契约](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/shared/mods/v2/platform-surfaces.ts:19) | 增加可校验的只读视图交互声明，保留现有声明兼容性 |
| [mods-platform.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/ipc/mods-platform.ts:184)、[platform-user-action.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/mods/v2/platform-user-action.ts:6) | 现有 Surface act 进入任务队列；新增只读分支，并在调用链约束副作用 |
| `main/mods/v2/manager.ts`、`session.ts`、权限与 Guest 桥 | 接入目录引用、只读能力和生命周期；不整体放开旧任务队列 |
| [file-access.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/mods/v2/file-access.ts:131) | 现有 fs 访问限 workspace；外部目录用单独授权引用，不直接扩大原 fs 根范围 |
| [DiffDisplay.tsx](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/renderer/src/components/chat/DiffDisplay.tsx) | 评估复用 Diff 展示；补通用原生变更预览容器 |
| `main/services/` 新增通用只读 Git 服务，配套 shared/preload/IPC | 查询变更、文件版本和 patch；与平台管理的 worktree 生命周期分开 |
| [path-openers.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/ipc/path-openers.ts:80) | P1 复用现有 IDE/目录打开能力，补面向 Mod 的受控公开桥接 |

不能把当前 `workflow:worktree-action` 当成外部目录预览接口：它依赖平台的运行/worktree 记录，而且先等待 run 生命周期结束，见 [workflows.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/ipc/workflows.ts:383)。旧 diff 服务还会更新管理记录，见 [git-worktree.ts](/Users/liuxuyang/WebstormProjects/CmbCoworkAgent/src/main/services/git-worktree.ts:2394)。

## 11. 平台验收用例

| 场景 | 必须满足 |
| --- | --- |
| Workflow 持有线程运行 lease，多个 Batch 同时执行 | 导航打开、筛选、切换、查询 Diff 均能完成；任务继续运行 |
| artifact 与业务仓库不在同一目录，两个仓库各有多个 worktree | 能分别读取已授权目录；不误用主会话或另一 Batch 的 cwd |
| 两个 Batch 改同名文件，快速切换且返回顺序颠倒 | 文件、Diff、比较起点与标签始终属于当前选中项 |
| 存在已提交、暂存、未暂存、未跟踪、删除、重命名、二进制及冲突 | 各比较模式准确；不能把未跟踪文件和读取错误当无改动 |
| baseline 与当前 HEAD 不同，或起点提交不可读 | 明确显示实际比较对象；不可读就报错，不猜替代提交 |
| 代理持续修改文件，用户刷新并翻页 | 刷新时间可见；版本变化使旧页/游标失效，不伪造稳定快照 |
| 未授权目录、跨会话引用、路径越界、目录替换、授权撤销 | 拒绝读取；旧结果和引用不能继续用于当前视图 |
| 导航回调尝试启动进程、模型、写文件或有副作用能力 | 宿主拒绝；普通执行动作仍沿用原队列和权限 |
| 大量变更、大文件、超长行与超时 | 有界分页、明确截断和可取消；不冻结界面 |
| 关闭侧栏、切项目、禁用 Mod，随后旧请求完成 | 不继续轮询，不发布到新视图 |
| worktree 被脚本清理 | 展示目录不可用，不创建目录、不切分支、不恢复任务 |
| 打开/刷新/查看 Diff 前后比较执行状态 | manifest、lease、阶段证据和 Git index 不因预览被改写；不调用 scheduler 的状态推进入口 |

P0 验收操作链：**Workflow 持续运行 → 打开 Mods 导航 → 选仓库/Batch → 查看变更文件列表 → 点击单文件 Diff → 刷新看到新代码，其他 Batch 继续执行。**

## 12. 可直接交给平台开发的任务描述

为已授权 Mods 提供 Workflow 会话中的稳定导航容器和可与任务并行的只读视图交互。支持插件提供自有多仓库 worktree 路径，在原生目录授权与真实 Git 身份校验后取得只读引用，通过公开 SDK 查询变更列表、单文件 patch 和文件版本，并打开原生只读变更预览。覆盖比较基准、分页、取消、数据过期、权限撤销及多会话隔离。批次状态由插件提供；不接管插件的 worktree 创建、并行调度、lease、提交、恢复、合并或清理。
