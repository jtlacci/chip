"""The brick's repository-internal entry point."""

from bricks import _runtime

from ..contract import BrickInput, BrickOutput
from ..src.logic import execute

BRICK = __name__.split(".")[1]


def run(inputs: BrickInput) -> BrickOutput:
    """Run the brick and return its output.

    The runtime builds the run context (run ID, configuration, io), calls the
    brick's logic and records the run. There are no mode options: production
    and evaluation differ only in the io the runtime provides.
    """
    return _runtime.run_brick(BRICK, inputs, execute)
