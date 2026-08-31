import asyncio, sys, json
sys.path.insert(0, '.')
async def main():
    from app.knowledge_base import kb_citations
    from app.agent import _rerank_citations
    q = "我总是很累没力气；持续一个多月了；睡眠还好，就是白天没精神"
    cits = kb_citations(q, top_k=3)
    print("候选:", [c['title'][:24] for c in cits])
    kept = await _rerank_citations(q, cits)
    print("保留:", [c['title'][:24] for c in kept])
asyncio.run(main())
