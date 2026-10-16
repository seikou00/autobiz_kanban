"""Finite, version-aware test choices. Plan V2 owns behavior, not tooling."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from hooks.utest_test_environment import local_tag, value


from hooks.utest_policy import POLICY
PRESETS = {
    "java-unit": {"domain": "fundamentals", "capabilities": ["junit"], "springContext": False, "mockBoundary": "none; real inputs and values"},
    "java-mockito": {"domain": "fundamentals", "capabilities": ["junit", "mockito"], "springContext": False, "mockBoundary": "external collaborators only; never mock the subject"},
    "java-static-inline": {"domain": "legacy-static", "capabilities": ["junit", "staticMock"], "springContext": False, "mockBoundary": "scoped static mocks; close each MockedStatic"},
    "java-static-legacy": {"domain": "legacy-static", "capabilities": ["junit", "verifiedPowerMock"], "springContext": False, "mockBoundary": "reuse an existing validated PowerMock combination only"},
    "java-request-fixture": {"domain": "legacy-static", "capabilities": ["junit", "springTest"], "springContext": False, "mockBoundary": "explicit request/context/cache fixtures; restore all state; no external calls"},
    "spring-mvc": {"domain": "mvc", "capabilities": ["junit", "springTest"], "springContext": "slice", "mockBoundary": "slice-external services only"},
    "spring-security": {"domain": "security", "capabilities": ["junit", "springTest", "securityTest"], "springContext": "slice", "mockBoundary": "keep the security rules under test"},
    "mybatis-db": {"domain": "persistence", "capabilities": ["junit", "database"], "springContext": "minimal", "mockBoundary": "real mapper and database; validate SQL dialect; does not replace service coverage"},
    "jpa-db": {"domain": "persistence", "capabilities": ["junit", "springTest", "database"], "springContext": "slice", "mockBoundary": "real persistence semantics"},
    "spring-context": {"domain": "fundamentals", "capabilities": ["junit", "springTest"], "springContext": "minimal", "mockBoundary": "retain the transaction/AOP/wiring behavior under test"},
    "frontend-existing": {"domain": "component", "capabilities": [], "springContext": False, "mockBoundary": "reuse the configured runner; public behavior only"},
}


def declared_java_capabilities(root):
    """Declaration hints, not a claim that Maven has resolved these artifacts."""
    root = Path(root)
    pom = root / "pom.xml"
    content = pom.read_text(encoding="utf-8-sig") if pom.is_file() else ""
    dependencies = {}
    try:
        xml = ET.fromstring(content)
        for node in xml.iter():
            if local_tag(node) == "dependency":
                artifact = value(node, "artifactId")
                dependencies[artifact] = value(node, "version") or "managed"
    except ET.ParseError:
        # The inspector historically accepts fragments. Keep its public
        # contract while making absence/unknown resolution explicit.
        for artifact in re.findall(r"<artifactId>\s*([^<]+)\s*</artifactId>", content):
            dependencies[artifact.strip()] = "unknown"
    artifacts = set(dependencies)
    starter = "spring-boot-starter-test" in artifacts
    junit = "junit4" if "junit" in artifacts and not artifacts.intersection({"junit-jupiter", "junit-jupiter-api"}) and not starter else "jupiter" if starter or any(a.startswith("junit-jupiter") for a in artifacts) else "unknown"
    mockito_version = dependencies.get("mockito-core", dependencies.get("mockito-inline", "managed" if starter else "missing"))
    match = re.match(r"(\d+)\.(\d+)", mockito_version)
    static_candidate = bool(match and tuple(map(int, match.groups())) >= (3, 4)
                            and ("mockito-inline" in artifacts or int(match.group(1)) >= 5))
    return {"evidence": "manifest_declarations", "resolutionVerified": False,
            "junitGeneration": junit, "dependencies": dependencies,
            "junit": starter or junit != "unknown" or "junit-vintage-engine" in artifacts,
            "mockito": starter or bool(artifacts.intersection({"mockito-core", "mockito-inline"})),
            "staticMock": static_candidate, "staticMockRequiresProbe": True,
            "verifiedPowerMock": False,
            "springTest": starter or "spring-test" in artifacts,
            "springBoot": starter or "spring-boot" in content or "spring-boot-starter-parent" in content,
            "securityTest": "spring-security-test" in artifacts,
            "database": bool(artifacts.intersection({"h2", "hsqldb", "derby", "postgresql", "mysql-connector-j", "mysql-connector-java", "ojdbc8", "ojdbc6"})),
            "beanReplacement": "use the API available in the resolved Spring version; preserve legacy JUnit"}


def recommend_preset(points, *, frontend=False):
    if frontend:
        return "frontend-existing"
    text = " ".join(points).lower()
    if re.search(r"static|静态", text):
        return "java-static-inline"
    if re.search(r"事务|transaction|\baop\b|bean.*接线|wiring", text):
        return "spring-context"
    if re.search(r"mybatis|mapper|\bsql\b|数据库查询", text):
        return "mybatis-db"
    if re.search(r"\bjpa\b|repository.*查询|持久", text):
        return "jpa-db"
    if re.search(r"security|认证|授权.*过滤|安全过滤", text):
        return "spring-security"
    if re.search(r"controller|mvc|路由|序列化|http.*响应", text):
        return "spring-mvc"
    if re.search(r"service|编排|协作者|远程|依赖调用", text):
        return "java-mockito"
    return "java-unit"


def preset_recipe(preset, capabilities):
    if preset not in PRESETS:
        raise ValueError("utest_preset_unknown:" + preset)
    recipe = dict(PRESETS[preset], presetId=preset)
    recipe["missingCapabilities"] = [name for name in recipe["capabilities"] if not capabilities.get(name)]
    recipe["requiresResolutionProbe"] = bool(recipe["capabilities"])
    recipe["reference"] = "reference/legacy-static-test-patterns.md" if recipe["domain"] == "legacy-static" else "render the selected framework/domain reference only"
    recipe["junitGeneration"] = capabilities.get("junitGeneration", "unknown")
    if recipe["junitGeneration"] == "junit4":
        recipe["reference"] = "reference/legacy-static-test-patterns.md"
    if preset == "spring-mvc" and not capabilities.get("springBoot"):
        recipe["springContext"] = "standalone MockMvcBuilders; do not introduce Spring Boot"
        recipe["reference"] = "reference/legacy-static-test-patterns.md"
    return recipe
