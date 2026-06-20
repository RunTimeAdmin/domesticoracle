"""
Add the backend directory to sys.path so pytest can import the modules
directly (ledger, consent, policy, crypto, limits, db) without an installed package.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
