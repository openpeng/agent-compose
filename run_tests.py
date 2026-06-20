"""Run all agent-compose tests."""

import sys
import unittest

from tests.test_definition_loader import TestDefinitionLoader
from tests.test_config_resolver import TestConfigResolver
from tests.test_mcp_builder import TestMCPBuilder
from tests.test_agent_loader import TestAgentLoader
from tests.test_team_loader import TestTeamLoader
from tests.test_workflow_loader import TestWorkflowLoader
from tests.test_orchestrator import TestOrchestrator
from tests.test_remote_loader import TestRemoteAgentLoader
from tests.test_packagers import TestAgentPackager, TestTeamPackager, TestWorkflowPackager
from tests.test_deployer import TestDeployer
from tests.test_cli import TestCLI


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestDefinitionLoader,
        TestConfigResolver,
        TestMCPBuilder,
        TestAgentLoader,
        TestTeamLoader,
        TestWorkflowLoader,
        TestOrchestrator,
        TestRemoteAgentLoader,
        TestAgentPackager,
        TestTeamPackager,
        TestWorkflowPackager,
        TestDeployer,
        TestCLI,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
