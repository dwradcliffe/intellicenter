"""Tests for the handling of objects the system reports without a type.

IntelliCenter firmware 3.x returns objects (for example '_FDR') for which every
requested attribute is echoed back as its own key name, Pentair's way of saying
'undefined'. Pruning such an object used to leave it without an OBJTYP, which
in turn made the whole model fail to load.
"""

import os
import sys
import unittest

# pyintellicenter is a self contained package, import it without pulling in
# custom_components.intellicenter which requires Home Assistant
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "intellicenter"),
)

from pyintellicenter.attributes import (  # noqa: E402  isort:skip
    OBJTYP_ATTR,
    SNAME_ATTR,
)
from pyintellicenter.controller import prune  # noqa: E402  isort:skip
from pyintellicenter.model import PoolModel, PoolObject  # noqa: E402  isort:skip

# as captured from a panel running firmware 3.008
FDR_OBJECT = {
    "objnam": "_FDR",
    "params": {
        "OBJTYP": "OBJTYP",
        "SUBTYP": "SUBTYP",
        "SNAME": "SNAME",
        "PARENT": "PARENT",
    },
}

CIRCUIT_OBJECT = {
    "objnam": "C0001",
    "params": {
        "OBJTYP": "CIRCUIT",
        "SUBTYP": "POOL",
        "SNAME": "Pool",
        "PARENT": "B1101",
    },
}


class TestPrune(unittest.TestCase):
    """Test the pruning of undefined parameters."""

    def test_undefined_attributes_are_pruned(self):
        """Test that undefined attributes are removed."""
        pruned = prune([FDR_OBJECT])[0]
        self.assertNotIn(SNAME_ATTR, pruned["params"])

    def test_objtyp_is_preserved(self):
        """Test that OBJTYP survives even when reported as undefined."""
        pruned = prune([FDR_OBJECT])[0]
        self.assertEqual(pruned["params"][OBJTYP_ATTR], OBJTYP_ATTR)

    def test_defined_attributes_are_preserved(self):
        """Test that a well formed object goes through untouched."""
        self.assertEqual(prune([CIRCUIT_OBJECT])[0], CIRCUIT_OBJECT)


class TestModel(unittest.TestCase):
    """Test the model creation from a list of objects."""

    def test_untyped_object_is_ignored(self):
        """Test that an object without OBJTYP does not break the model."""
        model = PoolModel()
        model.addObjects([{"objnam": "_FDR", "params": {}}])
        self.assertEqual(model.numObjects, 0)

    def test_unknown_type_is_ignored(self):
        """Test that a pruned _FDR does not make it into the model."""
        model = PoolModel()
        model.addObjects(prune([FDR_OBJECT, CIRCUIT_OBJECT]))
        self.assertEqual(model.numObjects, 1)
        self.assertIsNone(model["_FDR"])
        self.assertIsNotNone(model["C0001"])

    def test_object_creation(self):
        """Test that a well formed object is correctly created."""
        object = PoolObject("C0001", dict(CIRCUIT_OBJECT["params"]))
        self.assertEqual(object.objtype, "CIRCUIT")
        self.assertEqual(object.subtype, "POOL")
        self.assertEqual(object.sname, "Pool")


if __name__ == "__main__":
    unittest.main()
