import asyncio, json, httpx
BASE = "http://127.0.0.1:8000"

async def sse_chat(client, sid, message):
    print(f"\n>>> 用户：{message}")
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
                if etype in ("result", "question"):
                    result = ev
                elif etype == "tool_call":
                    print(f"   [tool] {ev.get('label')}")
    if result:
        kind = 'question' if result.get('triage_level') is None else 'RESULT'
        print(f"   [{kind}] triage={result.get('triage_level')} cites={len(result.get('citations') or [])}")
        print("   ", (result.get('text') or '')[:500].replace("\n", " "))
    return result

async def main():
    async with httpx.AsyncClient(timeout=240) as client:
        sid = (await client.post(f"{BASE}/api/sessions", json={})).json()["id"]
        await sse_chat(client, sid, "我现在牙上排中间往右数第二颗比较疼")
        await sse_chat(client, sid, "可以，我今年22岁，持续一天了，就是阵痛，不严重，没有过敏史，没有慢性病史，没在吃药")

asyncio.run(main())
