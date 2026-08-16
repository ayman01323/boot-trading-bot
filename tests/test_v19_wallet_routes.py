from learnerbot.route_scanner import _historical_cycle_variants

def test_cycle_variants_rotate_wrapped_and_close_cycle():
    w='0x0000000000000000000000000000000000000001'
    a='0x0000000000000000000000000000000000000002'
    b='0x0000000000000000000000000000000000000003'
    routes=_historical_cycle_variants([a,w,b],w,5)
    assert len(routes)==2
    for r in routes:
        assert r[0].lower()==w.lower()
        assert r[-1].lower()==w.lower()
        assert len(r)==4
