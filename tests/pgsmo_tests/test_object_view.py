# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import unittest

from pgsmo.objects.view.view import View
from tests.pgsmo_tests.node_test_base import NodeObjectTestBase


class TestView(NodeObjectTestBase, unittest.TestCase):
    NODE_ROW = {
        'name': 'viewname',
        'oid': 123
    }

    @property
    def class_for_test(self):
        return View

    @property
    def basic_properties(self):
        return {}

    @property
    def collections(self):
        return [
            '_columns', 'columns',
            '_rules', 'rules',
            '_triggers', 'triggers'
        ]

    @property
    def node_query(self) -> dict:
        return TestView.NODE_ROW
