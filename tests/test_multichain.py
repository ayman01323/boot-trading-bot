from pathlib import Path
from tempfile import TemporaryDirectory
from learnerbot.config import AppSettings, load_chains
from learnerbot.db import connect
from learnerbot.scanner import scan

class FakeRPC:
    def __init__(self, latest=100): self.latest=latest; self.blocks=[]
    def latest_block(self): return self.latest
    def block(self,n,full_transactions=True): self.blocks.append(n); return {'number':hex(n),'timestamp':hex(1000+n),'hash':'0x'+'1'*64,'transactions':[]}

def test_scanner_progresses_without_rescanning():
    with TemporaryDirectory() as td:
        conn=connect(Path(td)/'x.sqlite3');rpc=FakeRPC(100)
        txs,start,target=scan(conn,rpc,None,3,max_blocks=3)
        assert (start,target)==(95,97)
        rpc.latest=103
        txs,start,target=scan(conn,rpc,None,3,max_blocks=3)
        assert (start,target)==(98,100)
        assert rpc.blocks==[95,96,97,98,99,100]

def test_chain_csv_enable_is_reloaded():
    with TemporaryDirectory() as td:
        root=Path(td);csvdir=root/'CSVbot';csvdir.mkdir();data=root/'data';data.mkdir()
        (csvdir/'chains.csv').write_text('chain_id,slug,name,type,enabled,explorer_url,native_symbol,wrapped_base_symbol,wrapped_base_address,finality_lag_blocks,scan_blocks_per_cycle\n56,bsc,BSC,EVM,true,https://bscscan.com,BNB,WBNB,0x0,3,10\n8453,base,Base,EVM,false,https://base.blockscout.com,ETH,WETH,0x0,3,10\n')
        (csvdir/'rpc_endpoints.csv').write_text('chain_id,name,url,enabled,priority\n56,x,https://example.invalid,true,1\n8453,y,https://example.invalid,true,1\n')
        app=AppSettings(root,csvdir,data,'',[],'')
        assert [c.slug for c in load_chains(app,True)]==['bsc']
        p=csvdir/'chains.csv';p.write_text(p.read_text().replace('8453,base,Base,EVM,false','8453,base,Base,EVM,true'))
        assert [c.slug for c in load_chains(app,True)]==['bsc','base']
