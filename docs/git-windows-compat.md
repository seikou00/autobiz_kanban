# Windows Git 兼容处理

插件的工作流 Git 操作在 Windows 上自动附加 `-c core.longpaths=true`，
无需修改 Git 全局配置。它只解决 Git 自身的路径限制；其他工具仍建议使用短路径。

## 默认绕过 Git LFS filter

插件默认绕过 LFS filter，无需用户设置环境变量。工作流的 Git 命令自动附加：

```text
-c filter.lfs.process=
-c filter.lfs.smudge=
-c filter.lfs.clean=
-c filter.lfs.required=false
```

这些参数临时覆盖系统、全局和仓库中的同名配置，不写入配置文件，不需要管理员权限。
覆盖范围包括状态和快照、worktree 创建、暂存和封存、合并、工作区同步及清理。
插件外独立执行的 Git 命令不会自动附加这些参数。

绕过模式不自动检测仓库是否使用 LFS。包含 LFS 指针的提交会检出指针文本；要求 LFS
clean 转换的新文件可能被直接暂存为普通 Git 文件。需要处理 LFS 文件或相关历史提交时，
应先关闭绕过。Git hook 保持原有行为；hook 显式调用 git-lfs 时仍可能失败。

## 需要正常 LFS 行为或遇到兼容问题时

完全退出插件宿主，在 PowerShell 中关闭绕过，再从同一会话启动宿主：

```powershell
$env:AUTOBIZ_GIT_LFS_BYPASS = "0"
# 从这个 PowerShell 启动插件宿主，使其继承环境变量。
```

`0`、`false`、`no`、`off` 均表示关闭绕过，大小写不敏感。
先完全退出已有进程，避免启动命令复用旧进程而没有继承开关。

恢复默认绕过：退出宿主，移除环境变量，再从同一会话启动：

```powershell
Remove-Item Env:AUTOBIZ_GIT_LFS_BYPASS -ErrorAction SilentlyContinue
```
