"""A stateless population module with an explicit, bounded motor-bias output."""
import json
from pathlib import Path

import numpy as np
from friskoli_cad.engine.module_api import ModuleProposal


class ConstantBias:
    world_access = 'read_only'
    execution_contract = {
        'api_version': '0.1.0', 'stage': 'physiology', 'reads': [],
        'writes': [], 'effects': [], 'backends': ['numpy-cpu'],
    }

    def __init__(self):
        self.manifest = json.loads(Path(__file__).with_name('module.json').read_text(encoding='utf-8'))
        self.declaration = {
            'label': 'Tutorial constant motor bias', 'category': 'closure',
            'mathematics': {
                'kind': 'equations', 'equations': [{'latex': r'b_i=b_0', 'symbols': {'b_0': 'Declared bias in [0, 1]'}}],
                'algorithm': 'Return the same bias for every cell in the owning population.',
                'implementation': f'{type(self).__module__}.{type(self).__name__}',
                'verification': {'status': 'unreviewed', 'tests': []},
                'assumptions': ['Teaching control; no receptor or adaptation mechanism.'],
            },
        }

    def initialize(self, context):
        return self.propose(context)

    def propose(self, context):
        bias = np.full(len(context.entity_ids), context.parameters['bias'], dtype=float)
        return ModuleProposal({'motor_bias': bias}, {})


def create():
    return ConstantBias()
