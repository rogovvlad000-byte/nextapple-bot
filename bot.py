import os,re,asyncio,logging,httpx
from datetime import datetime
TELEGRAM_TOKEN=os.environ["TELEGRAM_TOKEN"]
OBSIDIAN_API_KEY=os.environ["OBSIDIAN_API_KEY"]
OBSIDIAN_URL=os.environ.get("OBSIDIAN_URL","http://127.0.0.1:27123")
TELEGRAM_API=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
logging.basicConfig(level=logging.INFO)
logger=logging.getLogger(__name__)
def n(t):
    t=t.replace("\u2014","-").replace("\u2013","-").replace("\u2212","-")
    return re.sub(r'\s*-\s*',' - ',t)
def parse_report(raw):
    data={}
    text=n(raw).lower()
    fp={"план":[r"план - ([\d\s]+)₽",r"план[^\d]*([\d][\d\s]*)"],"выручка":[r"общая\s*(?:сумма|выручка) - ([\d\s]+)",r"\d+\)\s*общая\s*(?:сумма|выручка)[^\d]*([\d\s]+)"],"наличные":[r"\d+\)\s*наличные - ([\d\s]+)",r"наличные - ([\d\s]+)"],"переводы":[r"\d+\)\s*переводы? - ([\d\s]+)",r"переводы? - ([\d\s]+)"],"qr":[r"\d+\)\s*qr[\s-]*код - ([\d\s]+)",r"qr[\s-]*код - ([\d\s]+)"],"рассрочка":[r"\d+\)\s*рассрочка - ([\d\s]+)",r"рассрочка - ([\d\s]+)"],"счет":[r"\d+\)\s*оплата\s*по\s*счет[уу] - ([\d\s]+)",r"оплата\s*по\s*счет[уу][^\d]*([\d\s]+)"],"терминал":[r"\d+\)\s*терминал - ([\d\s]+)",r"терминал - ([\d\s]+)"],"сдача":[r"сдача - ([\d\s]+)"],"наличных_в_магазине":[r"наличных\s*в\s*магазине - ([\d\s]+)"]}
    for key,patterns in fp.items():
        for p in patterns:
            m=re.search(p,text)
            if m:
                v=m.group(1).replace(" ","").replace("₽","").strip()
                try:data[key]=int(v);break
                except:pass
    for key,patterns in {"продаж":[r"[•\*]\s*продаж[:\s]*(\d+)",r"продаж[:\s]*(\d+)"],"броней":[r"[•\*]\s*броней[:\s]*(\d+)",r"броней[:\s]*(\d+)"],"предзаказов":[r"[•\*]\s*предзаказов[:\s]*(\d+)",r"предзаказов[:\s]*(\d+)"]}.items():
        for p in patterns:
            m=re.search(p,text)
            if m:
                try:data[key]=int(m.group(1));break
                except:pass
    out=re.search(r"закончил(?:ись|ся)\s*товары?[:\.\s]*(.*?)(?=\n\n|\d+[\.\)]|\Z)",raw,re.DOTALL|re.IGNORECASE)
    if out:data["закончились"]=out.group(1).strip()
    asked=re.search(r"спросили?\s*сегодня[:\s]*(.*?)(?=\n\n|\d+[\.\)]|📊|\Z)",raw,re.DOTALL|re.IGNORECASE)
    if asked:data["спрашивали"]=asked.group(1).strip()
    comment=re.search(r"(?:^|\n)2[\.\)]\s*(.*?)(?=\n\n|\n\d+[\.\)]|спросили?|📊|\Z)",raw,re.DOTALL|re.IGNORECASE)
    if comment:data["комментарий"]=comment.group(1).strip()
    if "выручка" not in data:return None
    return data
def fmt(v):return f"{v:,}".replace(","," ")+" ₽" if isinstance(v,int) else "—"
def format_note(data,author,raw):
    today=datetime.now().strftime("%d.%m.%Y");now=datetime.now().strftime("%H:%M")
    v=data.get("выручка",0);p=data.get("план",0)
    pct=round(v/p*100) if p>0 else 0
    st="✅" if pct>=100 else "⚠️" if pct>=80 else "🔴"
    lines=[f"# 📊 Отчёт за {today}",f"**Менеджер:** {author}",f"**Время:** {now}","","---",""]
    if data.get("закончились"):lines+=["## 📦 Закончились товары","",data["закончились"],"","---",""]
    if data.get("комментарий"):lines+=["## 💬 Как прошёл день","",data["комментарий"],"","---",""]
    if data.get("спрашивали"):lines+=["## ❓ Спрашивали, но не было","",data["спрашивали"],"","---",""]
    if any(k in data for k in ["продаж","броней","предзаказов"]):
        lines+=["## 📈 Статистика","","| Показатель | Значение |","|------------|----------|"]
        for k,l in [("предзаказов","Предзаказов"),("броней","Броней"),("продаж","Продаж")]:
            if k in data:lines.append(f"| {l} | {data[k]} |")
        lines+=["","---",""]
    lines+=["## 💰 Итоги по кассе","","| Метрика | Сумма |","|---------|-------|",f"| Общая выручка | {fmt(v)} |",f"| План | {fmt(p)} |",f"| Выполнение | {pct}% {st} |",f"| Наличные | {fmt(data.get('наличные'))} |",f"| Переводы | {fmt(data.get('переводы'))} |",f"| QR code | {fmt(data.get('qr'))} |",f"| Рассрочка | {fmt(data.get('рассрочка'))} |",f"| Терминал | {fmt(data.get('терминал'))} |",f"| Оплата по счёту | {fmt(data.get('счет'))} |",f"| Сдача | {fmt(data.get('сдача'))} |",f"| Наличных в магазине | {fmt(data.get('наличных_в_магазине'))} |","","---","","## 📝 Исходный отчёт","",raw]
    return "\n".join(lines)
async def save_to_obsidian(content,filepath):
    url=f"{OBSIDIAN_URL}/vault/{filepath}"
    headers={"Authorization":f"Bearer {OBSIDIAN_API_KEY}","Content-Type":"text/markdown","ngrok-skip-browser-warning":"true"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.put(url,content=content.encode("utf-8"),headers=headers)
            return r.status_code in(200,201,204)
    except Exception as e:logger.error(f"Obsidian:{e}");return False
async def send_message(chat_id,text):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage",json={"chat_id":chat_id,"text":text})
async def get_updates(offset=None):
    params={"timeout":30,"allowed_updates":["message"]}
    if offset:params["offset"]=offset
    async with httpx.AsyncClient(timeout=40) as client:
        r=await client.get(f"{TELEGRAM_API}/getUpdates",params=params)
        return r.json()
async def process_update(update):
    message=update.get("message",{});text=message.get("text","");chat_id=message.get("chat",{}).get("id");user=message.get("from",{})
    author=f"{user.get('first_name','')} {user.get('last_name','')}".strip() or user.get("username","Неизвестно")
    if not text or not chat_id:return
    data=parse_report(text)
    if data is None:return
    note=format_note(data,author,text);today=datetime.now().strftime("%Y-%m-%d");safe=re.sub(r"[^\w\-]","_",author)
    success=await save_to_obsidian(note,f"Отчёты/{today}_{safe}.md")
    v=data.get("выручка",0);p=data.get("план",0);pct=round(v/p*100) if p>0 else 0
    st="✅" if pct>=100 else "⚠️" if pct>=80 else "🔴"
    obs="📥 Сохранён в Obsidian" if success else "⚠️ Obsidian недоступен"
    await send_message(chat_id,f"📋 Отчёт принят | {obs}\n👤 {author}\n💰 Выручка: {f'{v:,}'.replace(',',' ')} ₽\n📊 План: {pct}% {st}\n🛒 Продаж: {data.get('продаж','—')}")
async def main():
    logger.info("✅ NextApple бот запущен...")
    offset=None
    while True:
        try:
            result=await get_updates(offset);updates=result.get("result",[])
            for u in updates:await process_update(u);offset=u["update_id"]+1
        except Exception as e:logger.error(f"Polling:{e}");await asyncio.sleep(5)
if __name__=="__main__":asyncio.run(main())
