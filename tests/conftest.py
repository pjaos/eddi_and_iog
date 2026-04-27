import sys
from unittest.mock import MagicMock

# Stub out p3lib modules before any project code imports them
for mod in ["p3lib", "p3lib.uio", "p3lib.helper", "p3lib.boot_manager"]:
    sys.modules.setdefault(mod, MagicMock())
