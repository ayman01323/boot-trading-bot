from pathlib import Path

# One-time cleanup for the temporary Solana balance diagnostic.
Path('/tmp/learnerbot_solana_balance_probe.txt').unlink(missing_ok=True)
