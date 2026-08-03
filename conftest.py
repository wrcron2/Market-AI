import sys
from pathlib import Path

# Make repo-root packages (data_layer) importable from tests/ regardless of
# pytest's import mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))
