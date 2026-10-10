"""``python -m omnisync``: equivalent to the ``omnisync`` command.

Useful where Windows application control blocks the generated ``omnisync.exe`` launcher.
"""

import sys

from omnisync.cli import main

sys.exit(main())
