import requests, time, json, os
from datetime import datetime, timezone

WATCHLIST   = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]
INTERVAL    = "1h"; LOOKBACK=3; FETCH_LIMIT=500
FEE_PCT=0.05; SLIPPAGE_PCT=0.02; RISK_PER_TRADE=1.0; START_EQUITY=10000.0
ER_WINDOW=30; ER_MIN=0.30; MIN_RR=2.0; RR_TARGET=2.5; MAX_HOLD_CANDLES=200
EMA_HTF=200

# Two strategies: MODIFIED base, and MODIFIED + higher-timeframe alignment.
STRATS = {
    "MOD":     dict(HTF=False, ledger="ledger_mod.json"),
    "MOD_HTF": dict(HTF=True,  ledger="ledger_mod_htf.json"),
}

TG_TOKEN=os.environ.get("TELEGRAM_TOKEN","").strip()
TG_CHAT =os.environ.get("TELEGRAM_CHAT","").strip()
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id":TG_CHAT,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},timeout=20)
    except Exception as e: print(f"(telegram failed: {e})")

# ---- multi-source fetch (geo-block proof) ----
def _binance_vision(sym,iv,limit):
    r=requests.get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval={iv}&limit={limit}",timeout=30)
    r.raise_for_status()
    return [{"t":x[0],"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4])} for x in r.json()]
def _okx(sym,iv,limit):
    inst=sym.replace("USDT","-USDT"); bar={"1m":"1m","5m":"5m","15m":"15m","1h":"1H","4h":"4H","1d":"1D"}.get(iv,"1H")
    r=requests.get(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar={bar}&limit={min(limit,300)}",timeout=30)
    r.raise_for_status(); rows=list(reversed(r.json().get("data",[])))
    return [{"t":int(x[0]),"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4])} for x in rows]
def _coinbase(sym,iv,limit):
    prod=sym.replace("USDT","-USD"); gran={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}.get(iv,3600)
    r=requests.get(f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity={gran}",timeout=30,headers={"User-Agent":"paper-bot"})
    r.raise_for_status(); rows=list(reversed(r.json()))
    return [{"t":int(x[0])*1000,"o":float(x[3]),"h":float(x[2]),"l":float(x[1]),"c":float(x[4])} for x in rows][-limit:]
def fetch(sym,iv,limit):
    for src in (_binance_vision,_okx,_coinbase):
        try:
            rows=src(sym,iv,limit)
            if rows and len(rows)>60: return rows[:-1]
        except Exception: continue
    raise RuntimeError("all sources failed")

def ema(vals,n):
    k=2/(n+1); out=[vals[0]]
    for v in vals[1:]: out.append(v*k+out[-1]*(1-k))
    return out
def eff_ratio(cl,i,n):
    if i<n: return None
    net=abs(cl[i]-cl[i-n]); path=sum(abs(cl[j]-cl[j-1]) for j in range(i-n+1,i+1))
    return net/path if path>0 else 0
def swings(c,lb=3):
    H,L=[],[]
    for i in range(lb,len(c)-lb):
        ih=il=True
        for j in range(i-lb,i+lb+1):
            if j==i:continue
            if c[j]["h"]>=c[i]["h"]:ih=False
            if c[j]["l"]<=c[i]["l"]:il=False
        if ih:H.append({"idx":i,"val":c[i]["h"]})
        if il:L.append({"idx":i,"val":c[i]["l"]})
    return H,L
def detect_coc(c,H,L):
    s=[]
    for i in range(10,len(c)-1):
        rh=[x for x in H if x["idx"]<i][-3:]; rl=[x for x in L if x["idx"]<i][-3:]
        if len(rh)<2 or len(rl)<2: continue
        down=rh[-1]["val"]<rh[-2]["val"] and rl[-1]["val"]<rl[-2]["val"]; up=rh[-1]["val"]>rh[-2]["val"] and rl[-1]["val"]>rl[-2]["val"]
        if down and c[i]["h"]>rh[-1]["val"] and c[i]["c"]>rh[-1]["val"]: s.append({"idx":i,"type":"bull","sl":rl[-1]["val"]})
        if up and c[i]["l"]<rl[-1]["val"] and c[i]["c"]<rl[-1]["val"]: s.append({"idx":i,"type":"bear","sl":rh[-1]["val"]})
    return s

def scan_signals(sym,c,cfg,since_ts):
    cl=[x["c"] for x in c]; eh=ema(cl,EMA_HTF)
    H,L=swings(c,LOOKBACK); sigs=detect_coc(c,H,L)
    if not sigs: return []
    liq_r=sorted(x["val"] for x in H); liq_s=sorted((x["val"] for x in L),reverse=True)
    prev=None; armed=[]
    for sig in sigs:
        if not prev or prev["type"]!=sig["type"] or sig["idx"]-prev["idx"]>20: prev=sig; continue
        armed.append(sig); prev=sig
    out=[]
    for sig in armed:
        ei=sig["idx"]+1
        if ei>=len(c): continue
        if c[ei]["t"]<=since_ts: continue
        er=eff_ratio(cl,ei,ER_WINDOW)
        if er is None or er<=ER_MIN: continue
        entry=c[ei]["o"]
        if cfg["HTF"]:
            if sig["type"]=="bull" and entry<eh[ei]: continue
            if sig["type"]=="bear" and entry>eh[ei]: continue
        if sig["type"]=="bull":
            sl=sig["sl"]*0.998; risk=entry-sl
            if risk<=0: continue
            desired=entry+risk*RR_TARGET; above=[v for v in liq_r if v>=desired*0.995]
            if not above: continue
            tgt=above[0]; rr=(tgt-entry)/risk
            if rr<MIN_RR: continue
            side="BUY"
        else:
            sl=sig["sl"]*1.002; risk=sl-entry
            if risk<=0: continue
            desired=entry-risk*RR_TARGET; below=[v for v in liq_s if v<=desired*1.005]
            if not below: continue
            tgt=below[0]; rr=(entry-tgt)/risk
            if rr<MIN_RR: continue
            side="SELL"
        out.append({"symbol":sym,"side":side,"entry_ts":c[ei]["t"],"entry":round(entry,6),
                    "target":round(tgt,6),"sl":round(sl,6),"rr":round(rr,2),
                    "opened_at":datetime.now(timezone.utc).isoformat(),"status":"OPEN"})
    return out

def load_ledger(f):
    if os.path.exists(f):
        with open(f) as fh: return json.load(fh)
    return {"equity":START_EQUITY,"open":[],"closed":[],"last_scan_ts":0}
def save_ledger(f,L):
    with open(f,"w") as fh: json.dump(L,fh,indent=2)
def close_trade(L,tr,net_pct,close_ts):
    rp=abs(tr["entry"]-tr["sl"])/tr["entry"]*100; rm=net_pct/rp if rp>0 else 0
    L["equity"]*=(1+rm*RISK_PER_TRADE/100)
    res="WIN" if net_pct>0 else "LOSS"
    tr=dict(tr); tr.update(status=res,net_pct=round(net_pct,3),r_mult=round(rm,3),closed_ts=close_ts,equity_after=round(L["equity"],2))
    L["closed"].append(tr); return res,net_pct
def update_open(L,cbs,name,closed_msgs):
    cost=(FEE_PCT+SLIPPAGE_PCT)*2; keep=[]
    for tr in L["open"]:
        c=cbs.get(tr["symbol"])
        if not c: keep.append(tr); continue
        after=[x for x in c if x["t"]>tr["entry_ts"]]; hit=False
        for x in after:
            if tr["side"]=="BUY":
                if x["l"]<=tr["sl"]: r,n=close_trade(L,tr,-(tr["entry"]-tr["sl"])/tr["entry"]*100-cost,x["t"]);hit=True;break
                if x["h"]>=tr["target"]: r,n=close_trade(L,tr,(tr["target"]-tr["entry"])/tr["entry"]*100-cost,x["t"]);hit=True;break
            else:
                if x["h"]>=tr["sl"]: r,n=close_trade(L,tr,-(tr["sl"]-tr["entry"])/tr["entry"]*100-cost,x["t"]);hit=True;break
                if x["l"]<=tr["target"]: r,n=close_trade(L,tr,(tr["entry"]-tr["target"])/tr["entry"]*100-cost,x["t"]);hit=True;break
        if hit:
            emoji="✅" if r=="WIN" else "❌"
            closed_msgs.append(f"{emoji} <b>{name}</b> {tr['side']} <b>{tr['symbol']}</b> closed {r}\nP&L {n:+.2f}%  |  equity ${L['equity']:,.0f}")
        else: keep.append(tr)
    L["open"]=keep
def have(L,sym,ts): return any(t["symbol"]==sym and t["entry_ts"]==ts for t in L["open"]+L["closed"])
def summary(name,L):
    cl=L["closed"]
    if not cl: return f"{name}: 0 closed, {len(L['open'])} open, equity ${L['equity']:,.0f}"
    w=sum(1 for t in cl if t["status"]=="WIN"); n=len(cl)
    wp=[t["net_pct"] for t in cl if t["status"]=="WIN"]; lp=[t["net_pct"] for t in cl if t["status"]=="LOSS"]
    pf=round(sum(wp)/abs(sum(lp)),2) if lp and sum(lp)!=0 else 99.9
    ex=round(sum(t["net_pct"] for t in cl)/n,3)
    return f"{name}: {n} closed | win {round(w/n*100)}% | PF {pf} | exp {ex:+.3f}% | {len(L['open'])} open | equity ${L['equity']:,.0f} ({(L['equity']/START_EQUITY-1)*100:+.1f}%)"

def run(fetch_fn=fetch):
    cbs={}; log=[]; fresh=[]; closed_msgs=[]
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    log.append(f"=== HTF TEST BOT — {stamp} ===")
    for sym in WATCHLIST:
        try: cbs[sym]=fetch_fn(sym,INTERVAL,FETCH_LIMIT)
        except Exception as e: log.append(f"{sym}: fetch failed ({e})")
    for name,cfg in STRATS.items():
        L=load_ledger(cfg["ledger"]); update_open(L,cbs,name,closed_msgs)
        max_ts=L.get("last_scan_ts",0)
        for sym in WATCHLIST:
            c=cbs.get(sym)
            if not c or len(c)<EMA_HTF+30: continue
            has_open=any(t["symbol"]==sym for t in L["open"])
            for sig in scan_signals(sym,c,cfg,L.get("last_scan_ts",0)):
                if has_open: break
                if not have(L,sym,sig["entry_ts"]):
                    L["open"].append(sig); has_open=True
                    et=datetime.fromtimestamp(sig["entry_ts"]/1000,timezone.utc).strftime("%m-%d %H:%M")
                    log.append(f"[{name}] NEW {sig['side']} {sym} @ {sig['entry']:.4f} tgt {sig['target']:.4f} sl {sig['sl']:.4f} RR {sig['rr']} ({et})")
                    fresh.append(f"<b>{name}</b> {sig['side']} <b>{sym}</b>\nEntry {sig['entry']:.4f}\nTarget {sig['target']:.4f}\nStop {sig['sl']:.4f}\nR:R {sig['rr']}  ({et} UTC)")
                max_ts=max(max_ts,sig["entry_ts"])
            if c: max_ts=max(max_ts,c[-1]["t"])
        L["last_scan_ts"]=max_ts; save_ledger(cfg["ledger"],L); cfg["_L"]=L
    for name,cfg in STRATS.items(): log.append(summary(name,cfg["_L"]))
    out="\n".join(log); print(out)
    with open("STATUS.txt","w") as f: f.write(out+"\n")
    if fresh or closed_msgs:
        score="\n".join(summary(n,c["_L"]) for n,c in STRATS.items()); parts=[]
        if closed_msgs: parts.append("🏁 <b>Trade(s) closed</b>\n\n"+"\n\n".join(closed_msgs))
        if fresh: parts.append("🔔 <b>New signal(s)</b>\n\n"+"\n\n".join(fresh))
        parts.append("📊 "+score); tg_send("\n\n".join(parts))
    return out

if __name__=="__main__": run()
