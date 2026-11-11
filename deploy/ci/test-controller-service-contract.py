#!/usr/bin/env python3
import unittest
from pathlib import Path
import yaml


class ServiceContractTests(unittest.TestCase):
    def test_user_service_loads_the_exact_private_python_dependencies(self):
        playbook = Path(__file__).resolve().parents[1] / 'ansible/manage-shirone-release-controller.yml'
        play = yaml.safe_load(playbook.read_text())[0]
        tasks = {task['name']: task for task in play['tasks']}
        service = tasks['Stage an inactive controller service']['ansible.builtin.copy']['content']
        self.assertIn('Environment=PYTHONPATH=/home/hyperbola/.local/share/shirone-release/tools/root/usr/lib/python3/dist-packages', service)
        self.assertIn('Environment=PATH=/home/hyperbola/.local/share/shirone-release/tools/bin:', service)
        self.assertIn('--isolated-test-only', service)
        self.assertIn('--key {{ shirone_controller_state }}/identity/identity.agekey', service)
        self.assertFalse(play['vars']['shirone_controller_enable'])
        self.assertFalse(play['vars']['shirone_controller_install'])


if __name__ == '__main__':
    unittest.main()
