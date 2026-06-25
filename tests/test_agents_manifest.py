"""Tests for hooks/agents_repo.py: manifest schema, indexing, sync payload shaping."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import (  # noqa: E402
    AgentsManifestError,
    build_sync_payload,
    index_unit_pairs,
    index_units,
    parse_manifest,
)


def _manifest(systems):
    return {"schemaVersion": "autobizdevops.agents.manifest.v1", "systems": systems}


VALID = _manifest(
    [
        {
            "systemId": "LF39",
            "systemName": "外联服务系统",
            "serviceUnits": [
                {"serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务"},
                {"serviceUnitId": "LF39.20_Inservice", "name": "外联入站服务"},
            ],
        },
        {"systemId": "LA64", "serviceUnits": [{"serviceUnitId": "LA64.05_UEXgateway"}]},
    ]
)


class ParseManifestTest(unittest.TestCase):
    def test_parse_valid(self):
        manifest = parse_manifest(VALID)
        self.assertEqual([s.system_id for s in manifest.systems], ["LF39", "LA64"])
        self.assertEqual(manifest.systems[0].service_units[0].service_unit_id, "LF39.18_Outservice")
        # 默认 AGENTS.md 路径 = <systemId>/AGENTS.md
        self.assertEqual(manifest.systems[0].agents_relpath(), "LF39/AGENTS.md")

    def test_index_units_maps_unit_to_system(self):
        idx = index_units(parse_manifest(VALID))
        self.assertEqual(idx["LF39.18_Outservice"], "LF39")
        self.assertEqual(idx["LF39.20_Inservice"], "LF39")
        self.assertEqual(idx["LA64.05_UEXgateway"], "LA64")

    def test_duplicate_system_id_rejected(self):
        bad = _manifest([
            {"systemId": "X", "serviceUnits": []},
            {"systemId": "X", "serviceUnits": []},
        ])
        with self.assertRaises(AgentsManifestError) as ctx:
            parse_manifest(bad)
        self.assertIn("systemId 重复", str(ctx.exception))

    def test_duplicate_service_unit_id_rejected_globally(self):
        bad = _manifest([
            {"systemId": "A", "serviceUnits": [{"serviceUnitId": "U1"}]},
            {"systemId": "B", "serviceUnits": [{"serviceUnitId": "U1"}]},
        ])
        with self.assertRaises(AgentsManifestError) as ctx:
            parse_manifest(bad)
        self.assertIn("全局重复", str(ctx.exception))

    def test_path_traversal_in_agents_rejected(self):
        bad = _manifest([
            {"systemId": "A", "agents": "../escape/AGENTS.md", "serviceUnits": []},
        ])
        with self.assertRaises(AgentsManifestError) as ctx:
            parse_manifest(bad)
        self.assertIn("越界", str(ctx.exception))

    def test_absolute_agents_path_rejected(self):
        bad = _manifest([{"systemId": "A", "agents": "/etc/passwd", "serviceUnits": []}])
        with self.assertRaises(AgentsManifestError):
            parse_manifest(bad)

    def test_missing_system_id_rejected(self):
        with self.assertRaises(AgentsManifestError):
            parse_manifest(_manifest([{"serviceUnits": []}]))

    def test_systems_not_a_list_rejected(self):
        with self.assertRaises(AgentsManifestError):
            parse_manifest({"schemaVersion": "v1", "systems": {}})

    def test_root_not_object_rejected(self):
        with self.assertRaises(AgentsManifestError):
            parse_manifest([])


V1 = {
    "schemaVersion": "v1",
    "systems": [
        {
            "systemId": "LF39",
            "description": "外联服务系统",
            "agentsPath": "LF3905/AGENTS.md",
            "serviceUnits": [
                {
                    "serviceUnitId": "LF39.18_Outservice",
                    "description": "外联出站服务",
                    "agentsPath": "LF3918/descition.md",
                },
                {"serviceUnitId": "LF39.20_Inservice", "description": "外联入站服务", "agentsPath": ""},
            ],
        }
    ],
}


class ParseV1SchemaTest(unittest.TestCase):
    def test_v1_field_names(self):
        manifest = parse_manifest(V1)
        system = manifest.systems[0]
        # description -> system_name；agentsPath -> 系统级路径
        self.assertEqual(system.system_name, "外联服务系统")
        self.assertEqual(system.agents_relpath(), "LF3905/AGENTS.md")
        out, inn = system.service_units
        # 单元 description -> name；单元 agentsPath -> agents_rel（可空）
        self.assertEqual(out.name, "外联出站服务")
        self.assertEqual(out.agents_rel, "LF3918/descition.md")
        self.assertEqual(inn.agents_rel, "")

    def test_index_unit_pairs(self):
        manifest = parse_manifest(V1)
        pairs = index_unit_pairs(manifest)
        system, unit = pairs["LF39.18_Outservice"]
        self.assertEqual(system.system_id, "LF39")
        self.assertEqual(unit.agents_rel, "LF3918/descition.md")

    def test_unit_agents_path_traversal_rejected(self):
        bad = {
            "schemaVersion": "v1",
            "systems": [
                {"systemId": "A", "serviceUnits": [
                    {"serviceUnitId": "U1", "agentsPath": "../escape.md"}]}
            ],
        }
        with self.assertRaises(AgentsManifestError) as ctx:
            parse_manifest(bad)
        self.assertIn("越界", str(ctx.exception))

    def test_old_schema_still_parses(self):
        # 向后兼容：旧字段 systemName/name/agents 仍可解析
        manifest = parse_manifest(VALID)
        self.assertEqual(manifest.systems[0].system_name, "外联服务系统")
        self.assertEqual(manifest.systems[0].service_units[0].name, "外联出站服务")


class BuildSyncPayloadTest(unittest.TestCase):
    def _plugin_root_with(self, manifest, agents_md_systems):
        tmp = Path(tempfile.mkdtemp())
        sysd = tmp / "sys"
        sysd.mkdir(parents=True)
        (sysd / "agents.manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        for system_id in agents_md_systems:
            (sysd / system_id).mkdir(parents=True, exist_ok=True)
            (sysd / system_id / "AGENTS.md").write_text(f"# {system_id}\n", encoding="utf-8")
        return tmp

    def test_payload_shape_and_agents_ready(self):
        tmp = self._plugin_root_with(VALID, agents_md_systems=["LF39"])  # LA64 缺 AGENTS.md
        payload = build_sync_payload(plugin_root=tmp, repo_info={"url": "u", "ref": "main", "commit": "c"})
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["supported_service_units"],
            ["LF39.18_Outservice", "LF39.20_Inservice", "LA64.05_UEXgateway"],
        )
        lf39, la64 = payload["systems"]
        self.assertTrue(lf39["agentsReady"])
        self.assertEqual(lf39["agentsPath"], "sys/LF39/AGENTS.md")  # 相对路径，symlink-proof
        self.assertFalse(la64["agentsReady"])
        self.assertEqual(payload["repo"], {"url": "u", "ref": "main", "commit": "c"})

    def test_missing_manifest_raises(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "sys").mkdir()
        with self.assertRaises(AgentsManifestError):
            build_sync_payload(plugin_root=tmp)


if __name__ == "__main__":
    unittest.main()
