"""Semantic ownership of test-only Maven changes, shared by guard and sealer.

A pom is a mixed production/test file. It must never be classified as a test
asset by its filename alone. Compare the production projection before allowing
test dependencies and Surefire/Failsafe configuration changes.
"""
from __future__ import annotations

import copy
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


TEST_PLUGINS = {"maven-surefire-plugin", "maven-failsafe-plugin"}


def local_tag(element):
    return element.tag.rsplit("}", 1)[-1]


def child(element, name):
    return next((item for item in element if local_tag(item) == name), None)


def value(element, name):
    item = child(element, name)
    return (item.text or "").strip() if item is not None else ""


def parse_pom(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("utest_pom_external_entities_forbidden")
    root = ET.fromstring(text)
    if local_tag(root) != "project":
        raise ValueError("utest_pom_project_required")
    return root


def _canonical(element):
    return (element.tag, tuple(sorted(element.attrib.items())),
            (element.text or "").strip(), tuple(_canonical(item) for item in element))


def _test_plugin(plugin):
    return (value(plugin, "artifactId") in TEST_PLUGINS
            and value(plugin, "groupId") in {"", "org.apache.maven.plugins"})


def _projection(root):
    result = copy.deepcopy(root)
    dependencies = child(result, "dependencies")
    if dependencies is not None:
        for dependency in list(dependencies):
            if local_tag(dependency) == "dependency" and value(dependency, "scope") == "test":
                dependencies.remove(dependency)
        if not list(dependencies) and not (dependencies.text or "").strip():
            result.remove(dependencies)
    build = child(result, "build")
    if build is not None:
        plugins = child(build, "plugins")
        if plugins is not None:
            for plugin in list(plugins):
                if local_tag(plugin) == "plugin" and _test_plugin(plugin):
                    plugins.remove(plugin)
            if not list(plugins) and not (plugins.text or "").strip():
                build.remove(plugins)
        if not list(build) and not (build.text or "").strip():
            result.remove(build)
    return _canonical(result)


def validate_test_pom_change(before, after):
    """Allow test-scoped dependencies and test plugins; preserve other XML."""
    try:
        old, new = parse_pom(before), parse_pom(after)
    except (ET.ParseError, ValueError) as exc:
        return {"allowed": False, "reason": "utest_pom_invalid", "detail": str(exc)}
    if _projection(old) != _projection(new):
        return {"allowed": False, "reason": "utest_pom_production_change_forbidden",
                "detail": "Only direct test dependencies and Surefire/Failsafe plugins may change; preserve parent, BOM, properties, profiles and production dependencies."}
    deps = child(new, "dependencies")
    seen = set()
    for dep in list(deps) if deps is not None else []:
        if local_tag(dep) != "dependency" or value(dep, "scope") != "test":
            continue
        coordinate = (value(dep, "groupId"), value(dep, "artifactId"),
                      value(dep, "type") or "jar", value(dep, "classifier"))
        if not coordinate[0] or not coordinate[1] or coordinate in seen:
            return {"allowed": False, "reason": "utest_pom_test_dependency_invalid"}
        seen.add(coordinate)
    # Existing exclusions remain valid. Adding exclusions or failure masking
    # must not turn an unexecuted/failed test into a passing Maven invocation.
    old_plugins, new_plugins = {}, {}
    for root, mapping in ((old, old_plugins), (new, new_plugins)):
        build = child(root, "build")
        plugins = child(build, "plugins") if build is not None else None
        for plugin in list(plugins) if plugins is not None else []:
            if _test_plugin(plugin):
                mapping[value(plugin, "artifactId")] = plugin
    for name, plugin in new_plugins.items():
        previous = old_plugins.get(name)
        for item in plugin.iter():
            tag, text = local_tag(item), (item.text or "").strip().lower()
            unsafe = (tag in {"skip", "skipTests", "skipITs", "testFailureIgnore"}
                      and text != "false") or (tag in {"failIfNoTests", "failIfNoSpecifiedTests"}
                                                and text != "true") or tag == "excludes"
            if unsafe:
                original = next((node for node in previous.iter() if local_tag(node) == tag), None) if previous is not None else None
                if original is None or _canonical(original) != _canonical(item):
                    return {"allowed": False, "reason": "utest_pom_test_masking_forbidden", "detail": tag}
    return {"allowed": True, "reason": "test_environment", "testDependencies": [list(item) for item in sorted(seen)]}


def validate_worktree_pom(worktree, relative_path, *, after=None):
    root = Path(worktree).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or path.name != "pom.xml":
        return {"allowed": False, "reason": "utest_pom_path_invalid"}
    result = subprocess.run(["git", "show", "HEAD:" + Path(relative_path).as_posix()],
                            cwd=root, text=True, capture_output=True, timeout=10)
    if result.returncode:
        return {"allowed": False, "reason": "utest_pom_baseline_missing"}
    try:
        content = path.read_text(encoding="utf-8-sig") if after is None else after
    except OSError as exc:
        return {"allowed": False, "reason": "utest_pom_missing", "detail": str(exc)}
    return validate_test_pom_change(result.stdout, content)


def prospective_content(path, tool_input):
    """Resolve write_file/edit_file payloads without applying a mutation."""
    for key in ("content", "contents", "new_content"):
        if isinstance(tool_input.get(key), str):
            return tool_input[key]
    old = tool_input.get("old_string", tool_input.get("oldString", tool_input.get("old_text")))
    new = tool_input.get("new_string", tool_input.get("newString", tool_input.get("new_text")))
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        raise ValueError("utest_pom_edit_payload_required: use write_file/edit_file with explicit content")
    content = Path(path).read_text(encoding="utf-8-sig")
    count = content.count(old)
    replace_all = bool(tool_input.get("replace_all", tool_input.get("replaceAll", False)))
    if count == 0 or (count != 1 and not replace_all):
        raise ValueError("utest_pom_edit_ambiguous")
    return content.replace(old, new, count if replace_all else 1)
