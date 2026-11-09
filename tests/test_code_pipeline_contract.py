"""Code's execution contract excludes tests while accepting existing Plans."""
import copy
import unittest

from hooks.parallel_validation_ownership import build_pipeline_contract, validation_ownership_errors
from hooks.parallel_runtime import DELIVERY_STAGES


class CodePipelineContractTest(unittest.TestCase):
    def setUp(self):
        self.root = {"batches": [{"id": "B001", "taskIds": ["T001"]}]}
        self.batches = {"B001": {"tasks": [{"id": "T001", "testPoints": ["existing intent"]}]}}
        self.root["parallelBatchPipeline"] = build_pipeline_contract(self.root, self.batches)

    def test_new_plan_has_only_implementation_and_review(self):
        pipeline = self.root["parallelBatchPipeline"]
        self.assertEqual(pipeline["deliveryStages"], ["prepare", "implement", "review"])
        self.assertEqual(list(DELIVERY_STAGES), pipeline["deliveryStages"])
        self.assertEqual(pipeline["validationBatches"], [])
        self.assertEqual(set(pipeline["validationOwnership"]), {"REVIEW-B001"})
        self.assertEqual(validation_ownership_errors(self.root, self.batches), [])

    def test_existing_plan_can_project_without_rewriting_test_intents(self):
        legacy = self.root["parallelBatchPipeline"]
        legacy["deliveryStages"].append("test")
        legacy["validationBatches"] = [{"id": "V-E2E", "type": "validation", "stage": "e2e_test",
                                         "dependsOn": "all_delivery", "executionTarget": "merged_main"}]
        legacy["validationOwnership"]["UTEST-T001"] = {"ownerBatchId": "B001", "stage": "test",
                                                       "kind": "utest_target", "taskId": "T001", "sourceBatchId": "B001"}
        before = copy.deepcopy(self.root)
        self.assertEqual(validation_ownership_errors(self.root, self.batches), [])
        projected = build_pipeline_contract(self.root, self.batches)
        self.assertEqual(projected["validationBatches"], [])
        self.assertNotIn("test", projected["deliveryStages"])
        self.assertEqual(self.root, before)

    def test_removal_does_not_relax_review_ownership_or_plan_revision(self):
        self.root["parallelBatchPipeline"]["validationOwnership"].clear()
        self.assertIn("validation_ownership_missing_or_invalid:REVIEW-B001",
                      validation_ownership_errors(self.root, self.batches))
        self.batches["B001"]["tasks"][0]["testPoints"].append("changed intent")
        self.assertIn("parallel_batch_pipeline_revision_stale",
                      validation_ownership_errors(self.root, self.batches))
