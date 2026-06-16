from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import inspect_state  # noqa: E402
from board_core.state_store import state_json_content_from_records  # noqa: E402
from hooks.init_workspace import create_feature  # noqa: E402


def make_project(root: Path) -> Path:
    root = root.resolve()
    project = root / "project"
    (project / ".autobizdevops" / "features" / "alpha").mkdir(parents=True)
    (project / ".autobizdevops" / "PROJECT.md").write_text(
        "# Project Metadata\n\n- **SysId**: LF3905\n",
        encoding="utf-8",
    )
    (project / ".autobizdevops" / "state.json").write_text(
        state_json_content_from_records(
            {
                "alpha": {
                    "feature": "alpha",
                    "owner": "-",
                    "checkpoint": "discuss_in_progress",
                    "stage": "需求澄清",
                    "iteration": "-",
                    "updated_at": "2026-06-16 12:00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    return project


def make_empty_project(root: Path) -> Path:
    root = root.resolve()
    project = root / "project"
    (project / ".autobizdevops" / "features").mkdir(parents=True)
    (project / ".autobizdevops" / "PROJECT.md").write_text(
        "# Project Metadata\n\n- **SysId**: LF3905\n",
        encoding="utf-8",
    )
    (project / ".autobizdevops" / "state.json").write_text(
        state_json_content_from_records({}),
        encoding="utf-8",
    )
    return project


def inspect_run(project: Path) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = inspect_state.run_mode(project, "alpha", inspect_state._load_board_config())
    assert exit_code == 0
    return json.loads(stdout.getvalue())


class InspectFeatureContextTests(unittest.TestCase):
    def test_run_mode_emits_feature_context_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            feature_dir = project / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "feature_context.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "agentsmdLoadConf": {
                            "version": 1,
                            "active": True,
                            "systemId": "LF3905",
                            "loadSystemAgentsmd": False,
                            "systemAgentsmdDir": "",
                            "services": [
                                {
                                    "service": "LF3905_pccompliancemng",
                                    "agentsmdDir": "sys/LF3905/Front/LF3905_bcpccomplianceui",
                                },
                                {
                                    "service": "LF3905_compliancemng",
                                    "agentsmdDir": "sys/LF3905/Backend/services/LF3905_bccompliancemng",
                                }
                            ],
                        },
                        "serviceCodeDirectories": {
                            "LF3905_pccompliancemng": (
                                "D:\\mysoft\\pywork\\LF39.05_MarketUI\\PC\\LF39.05_bcpccomplianceui"
                            ),
                            "LF3905_compliancemng": (
                                "D:\\workspace\\LF39.05_BCWplus_cust\\后台服务\\零售客户经营\\LF39.05_bccompliancemng"
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = inspect_run(project)

            self.assertEqual(
                output["run"]["featureContext"],
                {
                    "version": 1,
                    "agentsmdLoadConf": {
                        "version": 1,
                        "active": True,
                        "systemId": "LF3905",
                        "loadSystemAgentsmd": False,
                        "systemAgentsmdDir": "",
                        "services": [
                            {
                                "service": "LF3905_pccompliancemng",
                                "agentsmdDir": "sys/LF3905/Front/LF3905_bcpccomplianceui",
                            },
                            {
                                "service": "LF3905_compliancemng",
                                "agentsmdDir": "sys/LF3905/Backend/services/LF3905_bccompliancemng",
                            }
                        ],
                    },
                    "serviceCodeDirectories": {
                        "LF3905_pccompliancemng": (
                            "D:\\mysoft\\pywork\\LF39.05_MarketUI\\PC\\LF39.05_bcpccomplianceui"
                        ),
                        "LF3905_compliancemng": (
                            "D:\\workspace\\LF39.05_BCWplus_cust\\后台服务\\零售客户经营\\LF39.05_bccompliancemng"
                        ),
                    },
                },
            )
            self.assertIn(
                {
                    "path": ".autobizdevops/features/alpha/feature_context.json",
                    "purpose": "feature-context",
                },
                output["run"]["watchRefs"],
            )

    def test_run_mode_filters_services_without_code_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            feature_dir = project / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "feature_context.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "agentsmdLoadConf": {
                            "version": 1,
                            "active": True,
                            "systemId": "LF3905",
                            "loadSystemAgentsmd": False,
                            "systemAgentsmdDir": "",
                            "services": [
                                {
                                    "service": "LF3905_compliancemng",
                                    "agentsmdDir": "sys/LF3905/Backend/LF3905_compliancemng",
                                },
                                {
                                    "service": "后端系统约束",
                                    "agentsmdDir": "sys/LF3905/Backend",
                                },
                                {
                                    "service": "LF3905_pccompliancemng",
                                    "agentsmdDir": "sys/LF3905/Front/LF3905_bcpccomplianceui",
                                },
                            ],
                        },
                        "serviceCodeDirectories": {
                            "LF3905_compliancemng": "D:\\workspace\\LF3905_compliancemng",
                            "后端系统约束": "",
                            "LF3905_pccompliancemng": "",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = inspect_run(project)

            self.assertEqual(
                output["run"]["featureContext"]["agentsmdLoadConf"]["services"],
                [
                    {
                        "service": "LF3905_compliancemng",
                        "agentsmdDir": "sys/LF3905/Backend/LF3905_compliancemng",
                    }
                ],
            )
            self.assertTrue(output["run"]["featureContext"]["agentsmdLoadConf"]["active"])

    def test_create_feature_writes_context_from_harness_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = make_empty_project(Path(tmp))

            result = create_feature(project, "alpha")
            context_path = project / ".autobizdevops" / "features" / "alpha" / "feature_context.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))

            self.assertTrue(result["initialized"])
            self.assertIn(str(context_path), result["created"])
            self.assertFalse((project / "sys" / "LF3905" / "harness.config").exists())
            self.assertEqual(
                context,
                {
                    "version": 1,
                    "agentsmdLoadConf": {
                        "version": 1,
                        "active": False,
                        "systemId": "LF3905",
                        "loadSystemAgentsmd": False,
                        "systemAgentsmdDir": "",
                        "services": [
                            {
                                "service": "LF3905_compliancemng",
                                "agentsmdDir": "sys/LF3905/Backend/LF3905_compliancemng",
                            },
                            {
                                "service": "后端系统约束",
                                "agentsmdDir": "sys/LF3905/Backend",
                            },
                            {
                                "service": "LF3905_pccompliancemng",
                                "agentsmdDir": "sys/LF3905/Front/LF3905_bcpccomplianceui",
                            },
                        ],
                    },
                    "serviceCodeDirectories": {
                        "LF3905_compliancemng": "",
                        "后端系统约束": "",
                        "LF3905_pccompliancemng": "",
                    },
                },
            )


if __name__ == "__main__":
    unittest.main()
