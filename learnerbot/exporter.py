from __future__ import annotations
import csv
from pathlib import Path

def _write(path, rows, headers):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f); w.writerow(headers)
        for row in rows: w.writerow(row)

def _chain_rows(conn, chain, sql, headers):
    rows=[]
    for r in conn.execute(sql).fetchall():
        rows.append([chain.chain_id,chain.slug,chain.name]+[r[h] if h in r.keys() else '' for h in headers])
    return rows

def export_chain(conn, chain, csv_dir):
    auto=Path(csv_dir)/'auto'/chain.slug; outputs=[]
    defs=[
      ('wallet_candidates_auto.csv',"SELECT * FROM wallet_scores ORDER BY bot_score DESC",['wallet','tx_count','first_ts','last_ts','tx_per_min','repeat_to_ratio','repeat_selector_ratio','zero_value_ratio','builder_tx_count','bot_score','primary_executor','updated_at']),
      ('profit_evidence_auto.csv',"SELECT * FROM profit_evidence ORDER BY created_at DESC",['tx_hash','wallet','executor','base_token','base_symbol','gross_delta','gas_bnb','builder_payment_bnb','net_base','net_usd','proof_quality','classification','route_fingerprint','created_at']),
      ('learned_strategies_auto.csv',"SELECT * FROM strategy_patterns ORDER BY replicability DESC,confidence DESC",['pattern_id','executor','selector','route_fingerprint','strategy_class','tx_count','wallet_count','proven_profit_count','positive_count','avg_net_base','base_symbol','confidence','replicability','status','updated_at']),
      ('trade_behaviour_research_auto.csv',"SELECT * FROM trade_behaviour_evidence ORDER BY block_timestamp DESC",['tx_hash','wallet','behaviour','behaviour_confidence','profit_base','profit_usd','proof_quality','block_timestamp','executor','selector','notes','updated_at']),
      ('behaviour_rankings_auto.csv',"SELECT * FROM behaviour_rankings ORDER BY rank_overall",['behaviour','evidence_count','wallet_count','proven_count','positive_count','negative_count','total_net_base','total_net_usd','avg_net_base','median_positive_net_base','active_hours','profit_per_hour_base','profit_per_hour_usd','median_seconds_between_positive','positive_ratio','profit_score','speed_score','consistency_score','evidence_score','overall_score','rank_profit','rank_speed','rank_overall','updated_at']),
      ('wallet_behaviour_profit_auto.csv',"SELECT * FROM wallet_behaviour_rankings ORDER BY total_net_base DESC",['wallet','behaviour','evidence_count','proven_count','positive_count','negative_count','total_net_base','total_net_usd','avg_net_base','active_hours','profit_per_hour_base','profit_per_hour_usd','median_seconds_between_positive','positive_ratio','overall_score','updated_at']),
      ('copy_wallet_candidates_auto.csv',"SELECT * FROM copy_wallet_candidates ORDER BY status DESC,copy_score DESC",['wallet','behaviour','status','pass_checks','copy_score','bot_score','avg_behaviour_confidence','evidence_count','proven_count','positive_count','negative_count','positive_ratio','total_net_base','profit_per_hour_base','active_hours','avg_net_base','max_positive_base','max_loss_base','median_seconds_between_positive','rejection_reasons','updated_at']),
      ('copy_trade_recommendations_auto.csv',"SELECT * FROM copy_trade_recommendations ORDER BY created_at DESC",['recommendation_id','wallet','behaviour','route_id','action','recommendation_mode','reason','source_input_base','recommended_input_base','expected_gross_profit_base','captured_gross_profit_base','estimated_gas_base','builder_fee_base','slippage_reserve_base','conservative_net_profit_base','signal_age_seconds','checks_passed','checks_failed','check_summary','observed_at','created_at'])]
    for name,sql,h in defs:
        p=auto/name; _write(p,_chain_rows(conn,chain,sql,h),['chain_id','chain_slug','chain_name']+h); outputs.append(p)
    cluster_sql="""SELECT primary_executor,COUNT(*) wallet_count,ROUND(AVG(bot_score),2) avg_bot_score,MAX(bot_score) max_bot_score,SUM(tx_count) observed_tx_count FROM wallet_scores WHERE primary_executor IS NOT NULL GROUP BY primary_executor ORDER BY avg_bot_score DESC"""
    h=['primary_executor','wallet_count','avg_bot_score','max_bot_score','observed_tx_count']; p=auto/'wallet_clusters_auto.csv'; _write(p,_chain_rows(conn,chain,cluster_sql,h),['chain_id','chain_slug','chain_name']+h); outputs.append(p)
    return outputs

def export_aggregate(contexts,csv_dir):
    root=Path(csv_dir)/'auto'; outputs=[]
    specs=[
      ('wallet_candidates_all_chains.csv','SELECT * FROM wallet_scores ORDER BY bot_score DESC',['wallet','tx_count','first_ts','last_ts','tx_per_min','repeat_to_ratio','repeat_selector_ratio','zero_value_ratio','builder_tx_count','bot_score','primary_executor','updated_at']),
      ('profit_evidence_all_chains.csv','SELECT * FROM profit_evidence ORDER BY created_at DESC',['tx_hash','wallet','executor','base_token','base_symbol','gross_delta','gas_bnb','builder_payment_bnb','net_base','net_usd','proof_quality','classification','route_fingerprint','created_at']),
      ('learned_strategies_all_chains.csv','SELECT * FROM strategy_patterns ORDER BY replicability DESC,confidence DESC',['pattern_id','executor','selector','route_fingerprint','strategy_class','tx_count','wallet_count','proven_profit_count','positive_count','avg_net_base','base_symbol','confidence','replicability','status','updated_at']),
      ('trade_behaviour_research_all_chains.csv','SELECT * FROM trade_behaviour_evidence ORDER BY block_timestamp DESC',['tx_hash','wallet','behaviour','behaviour_confidence','profit_base','profit_usd','proof_quality','block_timestamp','executor','selector','notes','updated_at']),
      ('behaviour_rankings_all_chains.csv','SELECT * FROM behaviour_rankings ORDER BY rank_overall',['behaviour','evidence_count','wallet_count','proven_count','positive_count','negative_count','total_net_base','total_net_usd','avg_net_base','median_positive_net_base','active_hours','profit_per_hour_base','profit_per_hour_usd','median_seconds_between_positive','positive_ratio','profit_score','speed_score','consistency_score','evidence_score','overall_score','rank_profit','rank_speed','rank_overall','updated_at']),
      ('wallet_behaviour_profit_all_chains.csv','SELECT * FROM wallet_behaviour_rankings ORDER BY total_net_base DESC',['wallet','behaviour','evidence_count','proven_count','positive_count','negative_count','total_net_base','total_net_usd','avg_net_base','active_hours','profit_per_hour_base','profit_per_hour_usd','median_seconds_between_positive','positive_ratio','overall_score','updated_at']),
      ('copy_wallet_candidates_all_chains.csv','SELECT * FROM copy_wallet_candidates ORDER BY status DESC,copy_score DESC',['wallet','behaviour','status','pass_checks','copy_score','bot_score','avg_behaviour_confidence','evidence_count','proven_count','positive_count','negative_count','positive_ratio','total_net_base','profit_per_hour_base','active_hours','avg_net_base','max_positive_base','max_loss_base','median_seconds_between_positive','rejection_reasons','updated_at']),
      ('copy_trade_recommendations_all_chains.csv','SELECT * FROM copy_trade_recommendations ORDER BY created_at DESC',['recommendation_id','wallet','behaviour','route_id','action','recommendation_mode','reason','source_input_base','recommended_input_base','expected_gross_profit_base','captured_gross_profit_base','estimated_gas_base','builder_fee_base','slippage_reserve_base','conservative_net_profit_base','signal_age_seconds','checks_passed','checks_failed','check_summary','observed_at','created_at'])]
    for name,sql,h in specs:
        rows=[]
        for ctx in contexts: rows += _chain_rows(ctx.conn,ctx.config,sql,h)
        p=root/name; _write(p,rows,['chain_id','chain_slug','chain_name']+h); outputs.append(p)
    return outputs
