from pathlib import Path
import importlib.util
import re
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The build sandbox used for release validation may not have web3/eth-account and
# has no internet access.  Supply a tiny import shim only in that case.  On the
# target server the real dependencies are installed and these shims are unused.
if importlib.util.find_spec("web3") is None:
    web3 = types.ModuleType("web3")
    class _Web3:
        @staticmethod
        def is_address(v):
            return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", str(v or "")))
        @staticmethod
        def to_checksum_address(v):
            if not _Web3.is_address(v):
                raise ValueError("bad address")
            return "0x" + str(v)[2:].lower()
        @staticmethod
        def to_hex(*args, **kwargs):
            v=kwargs.get("hexstr") or (args[0] if args else "0x")
            if isinstance(v,(bytes,bytearray)): return "0x"+bytes(v).hex()
            return v
        @staticmethod
        def keccak(*, text=None, hexstr=None):
            import hashlib
            if text is not None: data=str(text).encode()
            elif hexstr is not None: data=bytes.fromhex(str(hexstr).removeprefix("0x"))
            else: data=b""
            return hashlib.sha3_256(data).digest()
        class HTTPProvider:
            def __init__(self, *args, **kwargs): pass
        def __init__(self, *args, **kwargs): pass
    web3.Web3 = _Web3
    sys.modules["web3"] = web3
    middleware = types.ModuleType("web3.middleware")
    middleware.ExtraDataToPOAMiddleware = object()
    sys.modules["web3.middleware"] = middleware

if importlib.util.find_spec("eth_account") is None:
    eth_account = types.ModuleType("eth_account")
    class _Account:
        @staticmethod
        def from_key(key):
            raise RuntimeError("eth-account not installed in dependency-light build sandbox")
        @staticmethod
        def create(*args, **kwargs):
            raise RuntimeError("eth-account not installed in dependency-light build sandbox")
    eth_account.Account = _Account
    sys.modules["eth_account"] = eth_account
