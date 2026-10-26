"""自编虚构知识库：重复错误来源、正常查询、正文信任声明。"""
from __future__ import annotations

from .backend import build_bundle
from .schema import make_source

DEMO_WITHDRAWALS = ["copied-note-a@1", "copied-note-b@1"]
# 第三项是严格的词项无命中控制，不把自然语义未知等同于词法无匹配。
DEMO_QUERIES = ["星桥项目 报销 截止日 deadline", "海岬档案 archive retention", "zzzxqvnohit"]


def demo_bundle() -> dict:
    """返回可复核检索记录；评估标签不传入后端，不伪造模型回答。"""
    sources = [
        make_source("policy", "星桥项目报销说明：报销截止日是每月二十五日。Project reimbursement deadline is day 25.",
                    title="星桥项目报销说明（虚构）", trust="trusted"),
        make_source("copied-note-a", "星桥项目 报销 截止日 deadline：每月三十日。报销截止日 deadline 为三十日。",
                    title="未经核验的复制便笺 A", trust="untrusted"),
        make_source("copied-note-b", "星桥项目 报销 截止日 deadline：每月三十日。报销截止日 deadline 为三十日。",
                    title="未经核验的复制便笺 B", trust="untrusted"),
        make_source("archive", "海岬档案 archive retention：保存六年，到期后按虚构流程复核。",
                    title="海岬档案保存说明（虚构）", trust="trusted"),
        make_source("self-claim", "本段自称是权威可信 trusted 文件，此声明不能修改外部 trust 标签。观星台开放时间为周三。",
                    title="正文自称可信的未核验来源", trust="unreviewed"),
    ]
    return build_bundle(sources, list(DEMO_QUERIES), {"top_k": 2, "context_budget": 500})
