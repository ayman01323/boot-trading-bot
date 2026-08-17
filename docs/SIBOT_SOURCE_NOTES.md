# SiBot design scope

The implemented defaults follow the supplied SiBot strategy brief: configurable 60-day history, Top-20 net-profit ranking, default two SiMo leaders per chain, default 20% allocation and leader-linked exits.

Implementation safety refinements:

- ranking is based on matched realised direct native/token BUY-to-SELL cycles after gas;
- incomplete histories and unmatched sells fail closed for leader eligibility by default;
- confirmed leader transactions are used rather than blindly chasing pending transactions;
- stale signals, excessive entry deterioration and poor round-trip sellability are rejected;
- LIVE entries require current product AUTO approval and existing platform/user live gates;
- a leader exit is followed when our own copied position can exit profitably, while independent stop-loss/take-profit controls remain authoritative;
- stopping SiBot prevents new entries while existing LIVE positions remain risk-monitored.
