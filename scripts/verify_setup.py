"""Report actual provisioning, not simulated inference or device readiness."""

import json

from macbot.cli import doctor
from macbot.config import load

if __name__ == "__main__":
    result = doctor(load())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready_to_start"] else 1)
