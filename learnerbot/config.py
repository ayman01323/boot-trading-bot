from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BOT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BOT_ROOT / ".env")

def _bool(v, default=False):
    if v is None: return default
    if isinstance(v, bool): return v
    return str(v).strip().lower() in {"1","true","yes","on","y"}

def _rows(path: Path):
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def _map(path: Path, key="setting", value="value"):
    out={}
    for row in _rows(path):
        k=(row.get(key) or "").strip(); v=(row.get(value) or "").strip()
        if k: out[k]=v
    return out

def _chat_ids():
    raw=(os.getenv("TELEGRAM_CHAT_IDS","").strip() or os.getenv("TELEGRAM_CHAT_ID","").strip())
    out=[]; seen=set()
    for x in raw.split(","):
        x=x.strip()
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

@dataclass(frozen=True)
class AppSettings:
    root: Path
    csv_dir: Path
    data_dir: Path
    telegram_bot_token: str
    telegram_chat_ids: list[str]
    etherscan_api_key: str

    @classmethod
    def load(cls):
        root=BOT_ROOT
        csv_dir=Path(os.getenv("CSV_DIR", str(root/"CSVbot"))).expanduser().resolve()
        data_dir=Path(os.getenv("DATA_DIR", str(root/"data"))).expanduser().resolve()
        return cls(root,csv_dir,data_dir,os.getenv("TELEGRAM_BOT_TOKEN","").strip(),_chat_ids(),os.getenv("ETHERSCAN_API_KEY","").strip())

    def general(self): return _map(self.csv_dir/"general_settings.csv")
    def telegram_settings(self): return _map(self.csv_dir/"telegram_settings.csv")
    def operator_settings(self): return _map(self.csv_dir/"operator_settings.csv")

    def get(self, name, default, cast=str):
        v=self.general().get(name, default)
        try: return cast(v)
        except Exception: return cast(default)

    def get_bool(self,name,default=False): return _bool(self.general().get(name),default)

@dataclass(frozen=True)
class ChainConfig:
    chain_id: int
    slug: str
    name: str
    type: str
    enabled: bool
    explorer_url: str
    native_symbol: str
    wrapped_base_symbol: str
    wrapped_base_address: str
    finality_lag_blocks: int
    scan_blocks_per_cycle: int
    rpc_urls: list[str]

@dataclass(frozen=True)
class ChainSettings:
    app: AppSettings
    chain: ChainConfig
    db_path: Path
    csv_dir: Path
    chain_id: int
    rpc_timeout_seconds: float
    rpc_delay_ms: int
    bot_score_threshold: float
    analyse_txs_per_wallet: int
    learn_min_pattern_txs: int
    telegram_min_bot_score: float
    telegram_report_top_wallets: int
    telegram_report_top_strategies: int
    telegram_protect_content: bool
    native_usd: object
    wrapped_base_symbol: str
    native_symbol: str

    @classmethod
    def from_app_chain(cls, app:AppSettings, chain:ChainConfig):
        g=app.general()
        def val(name,default,cast):
            try:return cast(g.get(name,default))
            except:return cast(default)
        native_usd=load_price_override(app.csv_dir,chain.chain_id,chain.native_symbol)
        strategy=load_kv_scoped(app.csv_dir/"strategy_settings.csv", chain.chain_id)
        min_pattern=int(strategy.get("min_pattern_txs",g.get("learn_min_pattern_txs","3")))
        return cls(
            app=app,chain=chain,db_path=app.data_dir/f"{chain.slug}.sqlite3",csv_dir=app.csv_dir,
            chain_id=chain.chain_id,rpc_timeout_seconds=val("rpc_timeout_seconds","15",float),
            rpc_delay_ms=val("rpc_delay_ms","35",int),bot_score_threshold=val("bot_score_threshold","55",float),
            analyse_txs_per_wallet=val("analyse_txs_per_wallet","60",int),learn_min_pattern_txs=min_pattern,
            telegram_min_bot_score=val("telegram_min_bot_score","80",float),
            telegram_report_top_wallets=val("telegram_report_top_wallets","8",int),
            telegram_report_top_strategies=val("telegram_report_top_strategies","8",int),
            telegram_protect_content=_bool(g.get("telegram_protect_content"),False),native_usd=native_usd,
            wrapped_base_symbol=chain.wrapped_base_symbol,native_symbol=chain.native_symbol,
        )


def load_chains(app:AppSettings, enabled_only=False):
    endpoints={}
    for row in _rows(app.csv_dir/"rpc_endpoints.csv"):
        if not _bool(row.get("enabled"),True): continue
        try: cid=int((row.get("chain_id") or "0").strip())
        except: continue
        url=(row.get("url") or "").strip()
        if not url: continue
        try: pri=int(row.get("priority") or 999)
        except: pri=999
        endpoints.setdefault(cid,[]).append((pri,url))
    out=[]
    for row in _rows(app.csv_dir/"chains.csv"):
        try: cid=int((row.get("chain_id") or "0").strip())
        except: continue
        enabled=_bool(row.get("enabled"),False)
        if enabled_only and not enabled: continue
        urls=[u for _,u in sorted(endpoints.get(cid,[]))]
        c=ChainConfig(
            chain_id=cid,slug=(row.get("slug") or str(cid)).strip().lower(),name=(row.get("name") or str(cid)).strip(),
            type=(row.get("type") or "EVM").strip().upper(),enabled=enabled,
            explorer_url=(row.get("explorer_url") or "").strip().rstrip('/'),native_symbol=(row.get("native_symbol") or "NATIVE").strip(),
            wrapped_base_symbol=(row.get("wrapped_base_symbol") or "").strip(),wrapped_base_address=(row.get("wrapped_base_address") or "").strip().lower(),
            finality_lag_blocks=int(row.get("finality_lag_blocks") or 3),scan_blocks_per_cycle=int(row.get("scan_blocks_per_cycle") or 10),rpc_urls=urls,
        )
        out.append(c)
    return out

def config_fingerprint(app:AppSettings):
    names=["chains.csv","rpc_endpoints.csv","builders.csv","wallet_watchlist.csv","wallet_blocklist.csv","scoring_weights.csv","price_overrides.csv","general_settings.csv","risk_settings.csv","strategy_settings.csv","dex_registry.csv","tokens.csv","telegram_settings.csv","behaviour_settings.csv","behaviour_registry.csv","copy_settings.csv","operator_settings.csv","live_trading_settings.csv","auto_trading_settings.csv","users.csv","fee_plans.csv","master_wallets.csv","activation_codes.csv","user_trading_settings.csv"]
    h=hashlib.sha256()
    for name in names:
        p=app.csv_dir/name
        h.update(name.encode()); h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()

def load_addresses(csv_dir:Path, filename:str, chain_id=None):
    out={}
    for row in _rows(csv_dir/filename):
        if not _bool(row.get("enabled"),True): continue
        scope=(row.get("chain_id") or "*").strip()
        if chain_id is not None and scope not in {"","*","0",str(chain_id)}: continue
        address=(row.get("address") or "").strip().lower()
        name=(row.get("name") or row.get("label") or address).strip()
        if address.startswith("0x") and len(address)==42: out[address]=name
    return out

def load_scoring_weights(csv_dir:Path, chain_id=None):
    defaults={"tx_count":20.0,"tx_rate":25.0,"repeat_to":20.0,"repeat_selector":20.0,"zero_value":10.0,"builder":5.0}
    global_rows=[]; chain_rows=[]
    for row in _rows(csv_dir/"scoring_weights.csv"):
        scope=(row.get("chain_id") or "*").strip()
        if scope in {"","*","0"}: global_rows.append(row)
        elif chain_id is not None and scope==str(chain_id): chain_rows.append(row)
    for row in global_rows+chain_rows:
        f=(row.get("feature") or "").strip()
        if f in defaults:
            try: defaults[f]=float(row.get("weight") or defaults[f])
            except: pass
    return defaults

def load_price_override(csv_dir:Path,chain_id:int,symbol:str):
    for row in _rows(csv_dir/"price_overrides.csv"):
        if not _bool(row.get("enabled"),False): continue
        if (row.get("chain_id") or "").strip()!=str(chain_id): continue
        if (row.get("symbol") or "").strip().upper()!=symbol.upper(): continue
        try:return float(row.get("usd"))
        except:return None
    return None

def load_kv_scoped(path:Path,chain_id:int):
    out={}
    rows=_rows(path)
    for row in rows:
        scope=(row.get("chain_id") or "*").strip()
        if scope in {"","*","0"}:
            k=(row.get("setting") or "").strip();
            if k: out[k]=(row.get("value") or "").strip()
    for row in rows:
        if (row.get("chain_id") or "").strip()==str(chain_id):
            k=(row.get("setting") or "").strip();
            if k: out[k]=(row.get("value") or "").strip()
    return out

def load_dex_registry(csv_dir:Path,chain_id:int):
    out=[]
    for row in _rows(csv_dir/"dex_registry.csv"):
        if not _bool(row.get("enabled"),False): continue
        if (row.get("chain_id") or "").strip()!=str(chain_id): continue
        out.append(row)
    return out

def resolve_dex_label(csv_dir:Path, chain_id:int, address=None):
    if not address:return None
    a=address.lower()
    for row in load_dex_registry(csv_dir,chain_id):
        for field in ('router','factory'):
            if (row.get(field) or '').strip().lower()==a:
                name=(row.get('dex_name') or '').strip(); version=(row.get('version') or '').strip()
                return f"{name} {version}".strip()
    return None

def token_override(csv_dir:Path,chain_id:int,address:str):
    a=(address or '').lower()
    for row in _rows(csv_dir/'tokens.csv'):
        if not _bool(row.get('enabled'),True):continue
        if (row.get('chain_id') or '').strip()!=str(chain_id):continue
        if (row.get('address') or '').strip().lower()!=a:continue
        try:dec=int(row.get('decimals') or 18)
        except:dec=18
        return ((row.get('symbol') or a[:10]).strip(),dec)
    return None
