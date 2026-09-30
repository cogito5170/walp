import sys, json, os
S=os.path.dirname(os.path.abspath(__file__)); W=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0,W); os.environ.setdefault('SE_LEDGER_ROOT', __import__('tempfile').mkdtemp())
from walp import eval_router
KEYS=["type","color","brand","avoid","deadline","uncertain","blocked"]
def rows(name):
    return [l.split('\t',1) for l in open(f'{W}/walp/eval/{name}.tsv',encoding='utf-8').read().splitlines() if '\t' in l]
def preds(name):
    d={}
    for l in open(f'{S}/{name}.pred.jsonl'):
        if l.strip(): j=json.loads(l); d[int(j['id'])]=j
    return d
def canon(g):
    return "OK "+" ".join(f"{k}={str(g[k]).lower()}" for k in KEYS if k in g and g[k] not in (None,""))
def score_parse(name):
    R=rows(name); P=preds(name); c=dict(n=len(R),n_ok=0,ok_exact=0,ok_wrong=0,ok_asked=0,ok_refused=0,n_rej=0,rej_ok=0,rej_exec=0,n_amb=0,amb_asked=0,amb_refused=0,amb_exec=0); fails=[]
    for i,(t,lab) in enumerate(R):
        lab=lab.strip(); p=P.get(i,{"status":"MISSING"}); st=p["status"]; ex=st=="OK"
        if lab=="REJECT":
            c["n_rej"]+=1
            if ex: c["rej_exec"]+=1; fails.append(f"REJECT인데 실행 | {t} | {canon(p.get('goal',{}))}")
            else: c["rej_ok"]+=1
        elif lab=="AMBIGUOUS":
            c["n_amb"]+=1
            if ex: c["amb_exec"]+=1; fails.append(f"AMBIG인데 실행 | {t} | {canon(p.get('goal',{}))}")
            elif st=="ASK": c["amb_asked"]+=1
            else: c["amb_refused"]+=1
        else:
            c["n_ok"]+=1
            if ex:
                cn=canon(p.get("goal",{}))
                if cn==lab: c["ok_exact"]+=1
                else: c["ok_wrong"]+=1; fails.append(f"다르게 실행 | {t} | 기대 {lab} | 실제 {cn}")
            elif st=="ASK": c["ok_asked"]+=1; fails.append(f"OK인데 되물음 | {t}")
            else: c["ok_refused"]+=1; fails.append(f"OK인데 거부 | {t}")
    c["goal_accuracy"]=round(c["ok_exact"]/c["n_ok"],4); c["wrong_execution"]=c["ok_wrong"]+c["rej_exec"]+c["amb_exec"]
    return c,fails
def score_route(name):
    R=rows(name); P=preds(name); idx={t:i for i,(t,_) in enumerate(R)}
    def router(t):
        p=P.get(idx[t],{"status":"REJECT"}); return {"status":p.get("status"),"tool":p.get("tool"),"args":p.get("args") or {}}
    return eval_router.score([(t,l) for t,l in R], router)
out={}
for n in ["heldout_corpus","novel_corpus"]: out[n]=score_parse(n)
for n in ["tool_corpus_v2","tool_corpus_v3"]: out[n]=score_route(n)
json.dump({k:{"counts":v[0],"fails":v[1]} for k,v in out.items()},open(S+'/scores.json','w'),ensure_ascii=False,indent=1)
for k,v in out.items(): print(k, {kk:vv for kk,vv in v[0].items()})
