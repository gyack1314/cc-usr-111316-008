"""仅核对输入形状，不生成分析结果。"""
import json
from .io import asset,unique,integer
def summarize(v,root):
    rows=[json.loads(x) for x in asset(root,v['transactions']).splitlines()];unique(rows,'id');integer(v['rolling']['window'],2)
    return {'transactions':len(rows),'units':len(v['units']),'product_mappings':len(v['product_map'])}
