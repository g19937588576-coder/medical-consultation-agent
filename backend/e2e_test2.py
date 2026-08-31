import asyncio, json, httpx
BASE = "http://127.0.0.1:8000"

async def sse_chat(client, sid, message, tag=""):
    print(f"\n>>> [{tag}] 用户：{message}")
    async with client.stream("POST", f"{BASE}/api/chat", json={"session_id": sid, "message": message}) as r:
        r.raise_for_status()
        buffer = ""
        result = None
        async for chunk in r.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)
                etype = "message"; data = ""
                for line in raw.split("\n"):
                    if line.startswith("event:"): etype = line[6:].strip()
                    elif line.startswith("data:"): data += line[5:].strip()
                try: ev = json.loads(data) if data else {}
                except Exception: ev = {"text": data}
                if etype in ("result", "question", "guardrail"):
                    result = ev
                elif etype == "tool_call":
                    print(f"   [tool] {ev.get('label')}")
    if result:
        kind = result.get('triage_level') and 'result' or 'question'
        if result.get('reason') in ('forgot','view','emergency','refusal','diagnosis'):
            kind = result.get('reason')
        print(f"   [{kind}] triage={result.get('triage_level')} cites={len(result.get('citations') or [])}")
        print("   ", (result.get('text') or result.get('detail') or '')[:320].replace("\n", " "))
    return result

async def main():
    async with httpx.AsyncClient(timeout=240) as client:
        # ① 牙疼复现（4 轮）
        sid = (await client.post(f"{BASE}/api/sessions", json={})).json()["id"]
        await sse_chat(client, sid, "我现在牙上排中间往右数第二颗比较疼", "牙疼1")
        await sse_chat(client, sid, "可以，我今年22岁，持续一天了，就是阵痛，没有过敏史，没在吃药", "牙疼2-同意+信息")
        await sse_chat(client, sid, "查看我的信息", "查看档案")
        await sse_chat(client, sid, "忘记我的信息", "清除档案")
        await sse_chat(client, sid, "查看我的信息", "再查看(应已清空)")

        # ② 拒绝同意
        sid2 = (await client.post(f"{BASE}/api/sessions", json={})).json()["id"]
        await sse_chat(client, sid2, "我最近肚子不太舒服", "拒绝-第1句")
        await sse_chat(client, sid2, "算了，不用记了", "拒绝-第2句")
        await sse_chat(client, sid2, "就是偶尔有点隐痛", "拒绝-第3句")

asyncio.run(main())
