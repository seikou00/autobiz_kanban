#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autodev-plan 死循环最小复现。

用法: python3 autoDevDoc/plan阶段死循环复现脚本.py
构造「部署单元只登记后端仓库根 + 前端在子目录」的 RunContext，
分别验证：工具链齐备 / 生成 catalog 时缺工具 / 事后装工具不刷新 / 正确刷新 / 手改 catalog。
情形 B 与 B2 即真实会话的处境；B3 是修复后的可执行出口。
"""
import json, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock
ROOT = Path("/Users/seikou/Documents/GitHub/autobiz_kanban")
sys.path.insert(0, str(ROOT))
from hooks.run_context import persist as persist_context
from hooks.validation_capabilities import persist as persist_caps
from hooks.plan_writer import _apply_runtime_validation_profiles

tmp = tempfile.mkdtemp()
root = Path(tmp)
ws = root / "output"
feature_dir = ws / ".autobizdevops" / "features" / "alpha"
feature_dir.mkdir(parents=True)
repo = root / "ruoyi-vue-pro"
(repo / "yudao-ui" / "yudao-ui-admin-vue3").mkdir(parents=True)
(repo / "pom.xml").write_text("<project/>", encoding="utf-8")
(repo / "yudao-ui" / "yudao-ui-admin-vue3" / "package.json").write_text(
    json.dumps({"scripts": {"dev": "vite", "build": "vite build", "typecheck": "vue-tsc --noEmit"}}), encoding="utf-8")
subprocess.run(["git", "init", "-q", str(repo)], check=True)

ctx = persist_context(ws, "alpha", [{"deployUnitId": "LB3920_ruoyi_backend", "localRepoPath": str(repo)}])
print("modules:", json.dumps(ctx["modules"], ensure_ascii=False))

def caps(which_map):
    with mock.patch("hooks.validation_capabilities.shutil.which", side_effect=lambda n: which_map.get(n)):
        return persist_caps(feature_dir, ctx)

plan = {"tasks": [
    {"id": "T001", "uiRequired": False, "workspaceRef": "default", "scope": {"workspaceRoots": {"default": "."}}},
    {"id": "T008", "uiRequired": True, "workspaceRef": "default", "scope": {"workspaceRoots": {"default": "yudao-ui/yudao-ui-admin-vue3"}}},
]}

print("--- case A: mvn+npm available")
c = caps({"mvn": "/usr/bin/mvn", "npm": "/usr/bin/npm"})
print(json.dumps(c["capabilities"], ensure_ascii=False, indent=1))
p = dict(plan); errs = _apply_runtime_validation_profiles(feature_dir, p)
print("errors:", errs)
print("profiles:", json.dumps(p.get("batchValidationProfiles"), ensure_ascii=False))

print("--- case B: npm NOT on PATH at catalog build time (the real session)")
c = caps({"mvn": "/usr/bin/mvn"})
print("caps:", [x["argv"] for x in c["capabilities"]])
p = dict(plan); errs = _apply_runtime_validation_profiles(feature_dir, p)
print("errors:", json.dumps(errs, ensure_ascii=False))
print("--- case B2: npm installed later, but catalog never regenerated -> same error")
p = dict(plan); errs = _apply_runtime_validation_profiles(feature_dir, p)
print("errors:", json.dumps(errs, ensure_ascii=False))
print("--- case B3: refresh catalog after npm is installed -> succeeds")
caps({"mvn": "/usr/bin/mvn", "npm": "/usr/bin/npm"})
p = dict(plan); errs = _apply_runtime_validation_profiles(feature_dir, p)
print("errors:", json.dumps(errs, ensure_ascii=False))
print("profiles:", json.dumps(p.get("batchValidationProfiles"), ensure_ascii=False))
print("--- case C: hand-edited catalog (what the model tried)")
cat = feature_dir / ".runtime" / "VALIDATION_CAPABILITIES.json"
data = json.loads(cat.read_text())
data["capabilities"].append({"capabilityId": "CAP-MANUAL", "moduleId": "LB3920_ruoyi_backend", "repositoryId": "repo-01",
                             "argv": ["npm", "run", "build"], "cwd": "yudao-ui/yudao-ui-admin-vue3", "kind": "build",
                             "required": True, "source": "package.json#scripts.build"})
cat.write_text(json.dumps(data, ensure_ascii=False, indent=2))
p = dict(plan); errs = _apply_runtime_validation_profiles(feature_dir, p)
print("errors:", json.dumps(errs, ensure_ascii=False))
print("WORKDIR", tmp)


# ---------------------------------------------------------------------------
# 情形 D：单仓多前端应用，workspaceRoots 精确指向 vue3 -> 只选择 vue3。
# ---------------------------------------------------------------------------
def case_d():
    root2 = Path(tempfile.mkdtemp())
    ws2 = root2 / "output"
    fd2 = ws2 / ".autobizdevops" / "features" / "alpha"
    fd2.mkdir(parents=True)
    repo2 = root2 / "ruoyi-vue-pro"
    for app in ("yudao-ui-admin-vue3", "yudao-ui-admin-vue2"):
        (repo2 / "yudao-ui" / app).mkdir(parents=True)
        (repo2 / "yudao-ui" / app / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8")
    (repo2 / "pom.xml").write_text("<project/>", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo2)], check=True)
    ctx2 = persist_context(ws2, "alpha", [{"deployUnitId": "LB3920_ruoyi_backend", "localRepoPath": str(repo2)}])
    with mock.patch("hooks.validation_capabilities.shutil.which", return_value="/usr/bin/x"):
        cat2 = persist_caps(fd2, ctx2)
    print("--- case D: 单仓两个前端应用")
    print("caps:", [(c["cwd"], c["argv"]) for c in cat2["capabilities"]])
    plan2 = {"tasks": [
        {"id": "T001", "uiRequired": False, "workspaceRef": "default", "scope": {"workspaceRoots": {"default": "."}}},
        {"id": "T008", "uiRequired": True, "workspaceRef": "default",
         "scope": {"workspaceRoots": {"default": "yudao-ui/yudao-ui-admin-vue3"}}},
    ]}
    errors = _apply_runtime_validation_profiles(fd2, plan2)
    print("errors:", json.dumps(errors, ensure_ascii=False))
    print("frontend profile:", json.dumps(plan2.get("batchValidationProfiles", {}).get("frontend"), ensure_ascii=False))


case_d()
