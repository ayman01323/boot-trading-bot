from pathlib import Path

# Compatibility shim for maintenance/utility scripts executed directly from
# the scripts/ directory. It points learnerbot.* imports at the real package.
__path__ = [str(Path(__file__).resolve().parents[2] / 'learnerbot')]
