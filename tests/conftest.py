# SPDX-FileCopyrightText: 2026 keplertech.io
#
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
